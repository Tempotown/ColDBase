#!/usr/bin/env python3
"""Run a minimal Hello-World E2E workflow through the coordinator."""

import argparse
import json
import sys
import time
from pathlib import Path

import requests


DEFAULT_TIMEOUT = 60
TASK_PATHS = ("/task", "/run_task")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Exercise the coordinator -> coder -> tester Hello-World flow."
    )
    parser.add_argument(
        "--coordinator-url",
        default="http://localhost:8001",
        help="Coordinator base URL.",
    )
    parser.add_argument(
        "--coder-url",
        default="http://localhost:8002",
        help="Coder base URL for health checks.",
    )
    parser.add_argument(
        "--tester-url",
        default="http://localhost:8004",
        help="Tester base URL for health checks.",
    )
    parser.add_argument(
        "--path",
        default="e2e/hello.txt",
        help="Relative workspace path to write during the test.",
    )
    parser.add_argument(
        "--content",
        default="Hello, world! (from coder)",
        help="File content to write during the test.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="Seconds to wait for service health.",
    )
    return parser.parse_args()


def fail(message):
    print(f"FAIL: {message}")
    return 1


def wait_for_health(label, base_url, timeout):
    deadline = time.time() + timeout
    health_url = f"{base_url.rstrip('/')}/health"
    last_error = None
    while time.time() < deadline:
        try:
            response = requests.get(health_url, timeout=5)
            if response.ok:
                return response.json()
            last_error = f"status={response.status_code}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(1)
    raise RuntimeError(f"{label} not healthy at {health_url}: {last_error}")


def coordinator_request(base_url, payload):
    last_response = None
    for path in TASK_PATHS:
        url = f"{base_url.rstrip('/')}{path}"
        try:
            response = requests.post(url, json=payload, timeout=15)
            last_response = response
            if response.status_code == 404:
                continue
            response.raise_for_status()
            return response.json(), url
        except requests.HTTPError:
            if response.status_code == 404:
                continue
            raise
    if last_response is None:
        raise RuntimeError("unable to reach coordinator task endpoint")
    raise RuntimeError(
        f"coordinator task endpoint failed: {last_response.status_code} {last_response.text}"
    )


def run_delegate(base_url, role, task, payload):
    request_payload = {
        "task": "run_action",
        "action": "delegate",
        "params": {
            "role": role,
            "task": task,
            "payload": payload,
            "delegation_depth": 0,
        },
    }
    response_body, url = coordinator_request(base_url, request_payload)
    result = response_body.get("result")
    if isinstance(result, dict) and result.get("error"):
        raise RuntimeError(result["error"])
    return response_body, result, url


def fetch_audit_entries(base_url, n=20):
    response = requests.get(
        f"{base_url.rstrip('/')}/audit/delegation",
        params={"n": n, "tail": "true"},
        timeout=10,
    )
    response.raise_for_status()
    body = response.json()
    return body.get("entries", [])


def matching_audit_entry(entries, role, task, path, started_at):
    workspace_path = f"/workspace/{path.lstrip('/')}"
    for entry in reversed(entries):
        if entry.get("to") != role or entry.get("task") != task:
            continue
        if entry.get("decision") != "delivered":
            continue
        if float(entry.get("time") or 0) < started_at:
            continue
        payload_summary = entry.get("payload_summary") or {}
        if payload_summary.get("path") == path:
            return entry
        result_summary = str(entry.get("result_summary") or "")
        if path in result_summary or workspace_path in result_summary:
            return entry
        payload_keys = entry.get("payload_keys") or []
        if "path" in payload_keys:
            return entry
    return None


def main():
    args = parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    expected_file = repo_root / "workspace" / args.path
    started_at = time.time()

    print("Checking service health...")
    for label, url in (
        ("coordinator", args.coordinator_url),
        ("coder", args.coder_url),
        ("tester", args.tester_url),
    ):
        health = wait_for_health(label, url, args.timeout)
        print(f"PASS: {label} healthy -> {json.dumps(health, sort_keys=True)}")

    print("Delegating write_file to coder...")
    _, write_result, write_url = run_delegate(
        args.coordinator_url,
        "coder",
        "write_file",
        {"path": args.path, "content": args.content},
    )
    if not isinstance(write_result, dict) or "written" not in write_result:
        return fail(f"unexpected coder result via {write_url}: {write_result}")
    print(f"PASS: coder wrote file via {write_url} -> {write_result['written']}")

    print("Delegating file_check to tester...")
    _, check_result, check_url = run_delegate(
        args.coordinator_url,
        "tester",
        "file_check",
        {"path": args.path},
    )
    if not isinstance(check_result, dict) or check_result.get("status") != "exists":
        return fail(f"unexpected tester result via {check_url}: {check_result}")
    print(f"PASS: tester confirmed file via {check_url} -> {check_result['path']}")

    if not expected_file.exists():
        return fail(f"expected local file missing: {expected_file}")
    actual_content = expected_file.read_text()
    if actual_content != args.content:
        return fail(
            f"local file content mismatch at {expected_file}: expected {args.content!r}, got {actual_content!r}"
        )
    print(f"PASS: local workspace file verified -> {expected_file}")

    print("Checking audit trail...")
    entries = fetch_audit_entries(args.coordinator_url)
    write_entry = matching_audit_entry(entries, "coder", "write_file", args.path, started_at)
    check_entry = matching_audit_entry(entries, "tester", "file_check", args.path, started_at)
    if not write_entry:
        return fail("missing delivered audit entry for coder/write_file")
    if not check_entry:
        return fail("missing delivered audit entry for tester/file_check")
    print("PASS: audit trail contains delivered entries for both delegations")

    print("")
    print("Hello-World E2E workflow completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
