from fastapi import FastAPI, Request
import os
import json
import time
import requests
from typing import List, Optional, Tuple, Union
import re
import hashlib
import socket
import threading
import uuid
import shutil
import subprocess
import shlex
from datetime import datetime
try:
    import yaml
except Exception:
    yaml = None

app = FastAPI()

ROLE = os.environ.get("ROLE", "agent")
WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", "/workspace")
MEMORY_FILE = os.path.join(WORKSPACE_DIR, "memory", "agent_state.json")
WORKFLOW_RUNS_FILE = os.path.join(WORKSPACE_DIR, "memory", "workflow_runs.json")
MODEL_ASSIGNMENTS_FILE = os.path.join(WORKSPACE_DIR, "memory", "model_assignments.json")
ZEROWEB = os.environ.get("ZEROCLAW_URL", "http://zeroclaw:42617")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama-brain:11434")
ZEROCLAW_API_ENDPOINT = os.environ.get("ZEROCLAW_API_ENDPOINT", "").strip()
REQUEST_TIMEOUT_SECONDS = int(os.environ.get("ZEROCLAW_TIMEOUT_SECONDS", "300"))
WORKFLOW_LOCK = threading.Lock()
ROLES_PATH = os.path.join(os.path.dirname(__file__), "roles.yml")


def get_role_specific_model(role: str) -> str:
    assignments = load_model_assignments()
    assigned = assignments.get("assignments", {}).get(role)
    if assigned:
        return str(assigned).strip()
    role_key = f"ZEROCLAW_MODEL_{role.upper()}"
    return (os.environ.get(role_key) or os.environ.get("ZEROCLAW_MODEL") or "auto").strip()


def load_roles_config():
    if not yaml:
        return {}
    try:
        with open(ROLES_PATH, "r") as handle:
            parsed = yaml.safe_load(handle) or {}
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def perform_action(action: str, params: dict):
    try:
        action = (action or "").strip()
        if action == "http_check":
            url = params.get("url")
            timeout = int(params.get("timeout", 10))
            r = requests.get(url, timeout=timeout)
            return {"status": r.status_code, "text": r.text[:1024]}

        if action == "write_file":
            path = params.get("path")
            content = params.get("content", "")
            if not path:
                return {"error": "missing path"}
            full = os.path.join(WORKSPACE_DIR, path.lstrip("/"))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as f:
                f.write(content)
            return {"written": full}

        if action == "start_workflow":
            wtype = params.get("workflow")
            if wtype == "intake":
                run = create_pipeline_run(
                    params.get("project_name") or "New Project",
                    params.get("goal") or "Define a working first slice.",
                    project_path=params.get("project_path"),
                    prompt=params.get("prompt"),
                    template=params.get("template", "generic"),
                    autonomous=params.get("autonomous", True)
                )
                start_background_workflow(run_pipeline_intake_stage, run["run_id"])
                return {"run_id": run["run_id"], "status": "queued", "workflow": "project_pipeline", "stage": "intake"}
            if wtype == "resource_check":
                run = create_pipeline_run(
                    params.get("project_name") or "Resource Check",
                    params.get("goal") or "Capture environment details.",
                    project_path=params.get("project_path"),
                    template="resource-check",
                    autonomous=True
                )
                start_background_workflow(run_resource_check_pipeline, run["run_id"])
                return {"run_id": run["run_id"], "status": "queued", "workflow": "project_pipeline", "template": "resource-check"}
            return {"error": f"unsupported workflow type: {wtype}"}

        if action == "delegate":
            # Delegate a task to another agent container in the compose network.
            # params: {"role":"coder","task":"write_file","payload":{...}}
            target_role = params.get("role") or params.get("target_role") or params.get("targetRole")
            task = params.get("task")
            payload = params.get("payload", {}) or {}
            # guard delegation depth
            depth = int(params.get("delegation_depth", 0) or 0)
            MAX_DELEGATION_DEPTH = int(os.environ.get("MAX_DELEGATION_DEPTH", "3"))

            if depth >= MAX_DELEGATION_DEPTH:
                audit_delegation({
                    "time": time.time(),
                    "from": ROLE,
                    "to": target_role,
                    "task": task,
                    "payload_summary": {k: ("<redacted>" if k.lower() in ("content","compose","template") else str(v)[:200]) for k,v in (payload.items() if isinstance(payload, dict) else [])},
                    "decision": "rejected",
                    "reason": f"max delegation depth {MAX_DELEGATION_DEPTH} reached",
                })
                return {"error": "delegation rejected: max delegation depth reached"}

            ok, reason = validate_delegation(target_role, task, payload)
            # prepare audit skeleton
            request_id = int(time.time() * 1000)
            host = os.environ.get("HOSTNAME") or "unknown"
            audit_base = {
                "time": time.time(),
                "request_id": request_id,
                "from": ROLE,
                "to": target_role,
                "task": task,
                "delegation_depth": depth,
                "payload_keys": list(payload.keys()) if isinstance(payload, dict) else None,
                "host": host,
            }
            # redact and hash sensitive payload fields for audit
            try:
                redacted_summary, sensitive_hashes = redact_and_hash_payload(payload)
                audit_base["payload_summary"] = redacted_summary
                audit_base["sensitive_hashes"] = sensitive_hashes
            except Exception:
                audit_base["payload_summary"] = None
                audit_base["sensitive_hashes"] = None

            if not ok:
                audit_base.update({"decision": "rejected", "reason": reason})
                audit_delegation(audit_base)
                return {"error": f"delegation rejected: {reason}"}

            # Try a few likely intra-compose endpoints
            candidates = [
                f"http://{target_role}:8000/run_task",
                f"http://zeroclaw-{target_role}:8000/run_task",
            ]

            tried = []
            result_obj = None
            reached = None

            start_t = time.time()
            for url in candidates:
                tried.append(url)
                try:
                    body = {"task": task}
                    # include payload fields under top-level when delegating
                    if isinstance(payload, dict):
                        body.update(payload)
                    # propagate delegation depth
                    body["delegation_depth"] = depth + 1
                    resp = requests.post(url, json=body, timeout=30)
                    if resp.ok:
                        reached = url
                        try:
                            result_obj = resp.json()
                        except Exception:
                            result_obj = {"status": "ok", "text": resp.text}
                        break
                    else:
                        # try next candidate
                        continue
                except Exception as e:
                    # record exception and try next
                    tried.append({"failed_endpoint": url, "error": str(e)})
                    continue

            duration_ms = int((time.time() - start_t) * 1000)
            audit_base.update({
                "decision": "delivered" if result_obj is not None else "failed",
                "reason": None if result_obj is not None else "no reachable target endpoints",
                "tried_endpoints": tried,
                "reached": reached,
                "result_summary": (str(result_obj)[:1000] if result_obj is not None else None),
                "duration_ms": duration_ms,
            })
            audit_delegation(audit_base)

            if result_obj is not None:
                return result_obj

            return {"error": "delegation failed: no reachable target endpoints"}

    except Exception as e:
        return {"error": str(e)}


def execute_parsed_actions(parsed):
    """Execute a parsed JSON action payload.

    Supported shapes:
    - {"action": "name", "params": {...}}
    - [{"action":"...","params":{...}}, ...]
    - {"actions": [{...}, ...]}
    Returns a dict with executed results.
    """
    results = []
    try:
        # Normalize to a list of action dicts
        actions = []
        if isinstance(parsed, list):
            actions = parsed
        elif isinstance(parsed, dict):
            if parsed.get("action"):
                actions = [parsed]
            elif parsed.get("actions") and isinstance(parsed.get("actions"), list):
                actions = parsed.get("actions")

        for idx, act in enumerate(actions):
            if not isinstance(act, dict):
                results.append({"index": idx, "error": "invalid action object"})
                continue
            name = act.get("action")
            params = act.get("params", {}) or {}
            res = perform_action(name, params)
            results.append({"index": idx, "action": name, "result": res})

        return {"executed": True, "count": len(results), "results": results}
    except Exception as e:
        return {"executed": False, "error": str(e)}


def parse_json_from_text(text: str) -> Optional[Union[dict, list]]:
    if not isinstance(text, str):
        return None
    # Try direct load
    try:
        data = json.loads(text)
        if isinstance(data, (dict, list)):
            return data
    except Exception:
        pass
    # Try to find a JSON array or object substring
    try:
        # Largest array match first
        m = re.search(r"(\[[\s\S]*\])", text)
        if m:
            try:
                data = json.loads(m.group(1))
                if isinstance(data, list):
                    return data
            except Exception:
                pass

        # Largest object match
        m = re.search(r"(\{[\s\S]*\})", text)
        if m:
            try:
                data = json.loads(m.group(1))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
    except Exception:
        pass
    return None


def validate_delegation(target_role: str, task: str, payload: dict) -> Tuple[bool, Optional[str]]:
    # Allowed actions per role
    allowed = {
        "coder": ["write_file"],
        "deployer": ["deploy_compose"],
        "tester": ["http_check", "file_check"],
        "coordinator": [],
    }

    if not target_role or not task:
        return False, "missing target role or task"

    role = target_role.strip()
    if role not in allowed:
        return False, f"unknown target role: {role}"

    if task not in allowed[role]:
        return False, f"task '{task}' not permitted for role '{role}'"

    # Additional param validations per task
    if task == "write_file":
        path = (payload.get("path") or "").strip()
        if not path:
            return False, "write_file requires non-empty 'path'"
        if ".." in path or path.startswith("/"):
            return False, "invalid path; must be relative and must not contain '..'"
        if len(path) > 200:
            return False, "path too long"
        # Restrict file extensions to a safe subset
        safe_exts = (".txt", ".md", ".json", ".yaml", ".yml", ".cfg", ".conf", ".ini", ".log", ".py")
        _, ext = os.path.splitext(path)
        if ext and ext.lower() not in safe_exts:
            return False, f"file extension '{ext}' not permitted"
        content = payload.get("content", "")
        if not isinstance(content, str):
            return False, "content must be a string"
        if content.startswith("#!"):
            return False, "shebangs are not allowed in delegated file content"
        if len(content) > 100000:
            return False, "content too large"
        # Prevent binary or executable content: reject NUL bytes or ELF header
        if "\x00" in content:
            return False, "binary content not allowed"
        if content.startswith("\x7fELF"):
            return False, "binary executable content not allowed"
        # Optionally restrict top-level directories if configured
        allowed_roots = os.environ.get("ALLOWED_WRITE_ROOTS")
        if allowed_roots:
            allowed = [p.strip() for p in allowed_roots.split(",") if p.strip()]
            first_segment = path.split("/")[0]
            if allowed and first_segment not in allowed:
                return False, f"write_file path must start with one of: {allowed}"

    if task == "http_check":
        url = (payload.get("url") or "").strip()
        if not url.startswith("http://") and not url.startswith("https://"):
            return False, "url must start with http:// or https://"
        if len(url) > 2000:
            return False, "url too long"
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
            # disallow common internal hostnames
            host_blacklist = ("host.docker.internal", "docker.for.mac.localhost", "docker.for.win.localhost")
            if any(b in host for b in host_blacklist):
                return False, "host not allowed for http_check"
            # reject obvious docker/internal names
            if "docker" in host or host.endswith(".local"):
                return False, "local / docker hostnames are not allowed"
            # try to resolve and reject private IP ranges
            import ipaddress
            try:
                infos = socket.getaddrinfo(host, None)
                for fam, _, _, _, sockaddr in infos:
                    addr = sockaddr[0]
                    try:
                        ip = ipaddress.ip_address(addr)
                        if ip.is_private and not ip.is_loopback:
                            return False, "resolved host resolves to a private IP; blocked"
                    except Exception:
                        continue
            except Exception:
                # resolution failed; continue with best-effort
                pass
        except Exception:
            return False, "invalid url"

    if task == "deploy_compose":
        # disallow arbitrary shell commands in payload; expect safe params only
        # Require payload to provide either a 'compose' key with YAML or a 'template' key.
        if not isinstance(payload, dict):
            return False, "payload must be an object"
        if not (payload.get("compose") or payload.get("template")):
            return False, "deploy_compose requires 'compose' or 'template' in payload"
        # Basic safety checks: disallow docker socket references and privileged flags
        compose_text = payload.get("compose") or payload.get("template") or ""
        if not isinstance(compose_text, str):
            return False, "compose/template must be a string"
        banned = ("/var/run/docker.sock", "privileged:", "cap_add", "NET_ADMIN", "devices:")
        lower = compose_text.lower()
        for b in banned:
            if b.lower() in lower:
                return False, f"deploy_compose payload contains banned pattern: {b}"
        if len(compose_text) > 20000:
            return False, "compose/template too large"
        # further checks: parse YAML and enforce safe service fields when PyYAML is available
        if yaml:
            try:
                parsed = yaml.safe_load(compose_text)
                services = parsed.get("services") if isinstance(parsed, dict) else None
                if not services:
                    return False, "compose payload missing 'services' section"
                for svc_name, svc in services.items():
                    if not isinstance(svc, dict):
                        continue
                    # banned service-level keys
                    if svc.get("privileged"):
                        return False, f"service {svc_name} requests privileged=true"
                    if svc.get("runtime") == "host":
                        return False, f"service {svc_name} requests host runtime"
                    if svc.get("network_mode") == "host":
                        return False, f"service {svc_name} requests host network_mode"
                    if svc.get("devices"):
                        return False, f"service {svc_name} requests devices"
                    if svc.get("cap_add"):
                        return False, f"service {svc_name} requests cap_add"
                    # inspect volumes for docker socket
                    vols = svc.get("volumes") or []
                    for v in vols:
                        if isinstance(v, str) and "/var/run/docker.sock" in v:
                            return False, f"service {svc_name} mounts docker socket"
            except Exception as e:
                return False, f"invalid YAML in compose/template: {e}"
        return True, None

    return True, None


def audit_delegation(entry: dict):
    """Write a structured delegation audit entry to workspace/logs/delegation.log"""
    try:
        # Normalize and validate entry before writing
        normalized, schema_ok, schema_errors = normalize_and_validate_audit(entry)
        log_dir = os.path.join(WORKSPACE_DIR, "logs")
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, "delegation.log")
        # persist normalized entry with schema result metadata
        out = dict(normalized)
        out["_schema_valid"] = bool(schema_ok)
        if schema_errors:
            out["_schema_errors"] = schema_errors
        with open(path, "a") as f:
            f.write(json.dumps(out) + "\n")
    except Exception:
        pass


def load_audit_schema():
    try:
        schema_path = os.path.join(os.path.dirname(__file__), "delegation_audit.schema.json")
        with open(schema_path, "r") as f:
            return json.load(f)
    except Exception:
        return None


def normalize_and_validate_audit(entry: dict):
    """Return (normalized_entry, schema_ok, schema_errors).

    Normalizes common fields (adds time_iso) and validates against the on-disk schema
    when available. Always returns a dict suitable for logging.
    """
    normalized = dict(entry or {})
    # ensure numeric time and add ISO representation
    try:
        t = float(normalized.get("time") or time.time())
    except Exception:
        t = time.time()
    normalized["time"] = t
    try:
        normalized["time_iso"] = datetime.utcfromtimestamp(t).isoformat() + "Z"
    except Exception:
        normalized["time_iso"] = None

    # ensure integer request_id when possible
    try:
        if "request_id" in normalized:
            normalized["request_id"] = int(normalized["request_id"])
    except Exception:
        normalized["request_id"] = int(time.time() * 1000)

    # ensure duration_ms exists
    if "duration_ms" not in normalized:
        normalized["duration_ms"] = 0

    schema = load_audit_schema()
    if not schema:
        return normalized, True, None

    try:
        from jsonschema import validate, ValidationError

        validate(instance=normalized, schema=schema)
        return normalized, True, None
    except Exception as e:
        # return human-friendly error string
        msg = str(e)
        return normalized, False, msg


def redact_and_hash_payload(payload):
    """Return (redacted_summary, sensitive_hashes) for a payload dict.

    - redacted_summary: dict with same keys where sensitive values are replaced by '<redacted:N>' or truncated.
    - sensitive_hashes: dict of key -> sha256hex (truncated) for redacted values.
    """
    sensitive_keywords = (
        "content",
        "compose",
        "template",
        "password",
        "secret",
        "token",
        "key",
        "private_key",
        "api_key",
    )
    if not isinstance(payload, dict):
        s = str(payload)
        return ("<non-dict>", {"__payload_hash": hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]})

    redacted = {}
    hashes = {}
    for k, v in payload.items():
        kl = k.lower()
        is_sensitive = any(sk in kl for sk in sensitive_keywords) or kl in sensitive_keywords
        if is_sensitive:
            sval = str(v)
            h = hashlib.sha256(sval.encode("utf-8")).hexdigest()[:16]
            hashes[k] = h
            redacted[k] = f"<redacted:{len(sval)}>"
        else:
            if isinstance(v, str):
                if len(v) > 200:
                    redacted[k] = v[:200] + "..."
                else:
                    redacted[k] = v
            else:
                try:
                    redacted[k] = v
                except Exception:
                    redacted[k] = str(v)

    return redacted, hashes


def append_log(entry: dict):
    try:
        log_dir = os.path.join(WORKSPACE_DIR, "logs")
        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, f"{ROLE}.log")
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def ollama_endpoints() -> List[str]:
    endpoints = []
    for candidate in [
        OLLAMA_URL,
        ZEROCLAW_API_ENDPOINT,
        "http://host.docker.internal:11434",
        "http://localhost:11434",
    ]:
        candidate = (candidate or "").strip().rstrip("/")
        if candidate and candidate not in endpoints:
            endpoints.append(candidate)
    return endpoints


def fetch_ollama_models() -> Tuple[Optional[str], List[str], List[str]]:
    errors = []
    for base_url in ollama_endpoints():
        try:
            tags_resp = requests.get(f"{base_url}/api/tags", timeout=5)
            if not tags_resp.ok:
                errors.append(f"{base_url} -> {tags_resp.status_code}")
                continue

            tags_json = tags_resp.json()
            models_list = []
            if isinstance(tags_json, dict) and isinstance(tags_json.get("models"), list):
                for item in tags_json["models"]:
                    if isinstance(item, dict) and item.get("name"):
                        models_list.append(item["name"])

            append_log(
                {
                    "time": time.time(),
                    "role": ROLE,
                    "to": "ollama",
                    "endpoint": base_url,
                    "available_models": models_list,
                }
            )
            return base_url, models_list, errors
        except Exception as exc:
            errors.append(f"{base_url} -> {exc}")

    return None, [], errors


def list_available_models() -> dict:
    endpoint, installed, errors = fetch_ollama_models()
    return {
        "endpoint": endpoint,
        "installed": installed,
        "errors": errors,
        "assignments": effective_model_assignments(),
    }


def pull_ollama_model(model_name: str) -> dict:
    model_name = (model_name or "").strip()
    if not model_name:
        raise RuntimeError("model name is required")

    endpoint, _, errors = fetch_ollama_models()
    if not endpoint:
        raise RuntimeError(
            "unable to reach any Ollama endpoint"
            + (f": {' | '.join(errors)}" if errors else "")
        )

    response = requests.post(
        f"{endpoint}/api/pull",
        json={"name": model_name, "stream": False},
        timeout=max(REQUEST_TIMEOUT_SECONDS, 600),
    )
    if not response.ok:
        raise RuntimeError(f"ollama pull failed: {response.status_code} {response.text[:300]}")

    detail = None
    try:
        parsed = response.json()
        detail = parsed if isinstance(parsed, dict) else {"response": parsed}
    except Exception:
        lines = [line.strip() for line in response.text.splitlines() if line.strip()]
        for line in reversed(lines):
            try:
                detail = json.loads(line)
                break
            except Exception:
                continue
        if detail is None:
            detail = {"response": response.text[:500]}

    refreshed = list_available_models()
    return {
        "model": model_name,
        "detail": detail,
        "installed": refreshed.get("installed", []),
        "endpoint": endpoint,
    }


def choose_model(requested_model: str, models_list: List[str]) -> Optional[str]:
    requested_model = (requested_model or "").strip()
    if requested_model and requested_model.lower() != "auto":
        if requested_model in models_list:
            return requested_model
        for model_name in models_list:
            if model_name == requested_model or model_name.startswith(requested_model + ":"):
                return model_name

    if models_list:
        return models_list[0]

    return None


def utc_now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def read_json_file(path: str, default):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception:
        return default


def write_json_file(path: str, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2, sort_keys=True)


def load_model_assignments():
    data = read_json_file(MODEL_ASSIGNMENTS_FILE, {"assignments": {}})
    if not isinstance(data, dict):
        return {"assignments": {}}
    assignments = data.get("assignments")
    if not isinstance(assignments, dict):
        data["assignments"] = {}
    return data


def save_model_assignments(data):
    write_json_file(MODEL_ASSIGNMENTS_FILE, data)


def known_agent_roles() -> List[str]:
    config = load_roles_config()
    agents_cfg = config.get("agents", {}) if isinstance(config, dict) else {}
    if isinstance(agents_cfg, dict) and agents_cfg:
        return sorted(agents_cfg.keys())
    return ["coordinator", "coder", "deployer", "tester"]


def effective_model_assignments() -> dict:
    return {role: get_role_specific_model(role) for role in known_agent_roles()}


def assign_role_model(role: str, model_name: str) -> dict:
    role = (role or "").strip().lower()
    model_name = (model_name or "").strip()
    if role not in known_agent_roles():
        raise RuntimeError(f"unknown role: {role}")
    if not model_name:
        raise RuntimeError("model name is required")

    data = load_model_assignments()
    data.setdefault("assignments", {})[role] = model_name
    data["updated_at"] = utc_now_iso() if "utc_now_iso" in globals() else datetime.utcnow().isoformat() + "Z"
    save_model_assignments(data)
    return {"role": role, "model": model_name, "assignments": data.get("assignments", {})}


def load_workflow_runs():
    data = read_json_file(WORKFLOW_RUNS_FILE, {"runs": []})
    if not isinstance(data, dict):
        return {"runs": []}
    if not isinstance(data.get("runs"), list):
        data["runs"] = []
    return data


def save_workflow_runs(data):
    write_json_file(WORKFLOW_RUNS_FILE, data)


def create_workflow_run(workflow_type: str, payload: dict):
    run = {
        "run_id": f"wf-{uuid.uuid4().hex[:12]}",
        "workflow": workflow_type,
        "status": "queued",
        "created_at": utc_now_iso(),
        "updated_at": utc_now_iso(),
        "requested_by": ROLE,
        "input": payload,
        "steps": [],
        "summary": None,
        "final_decision": None,
        "final_reasoning": None,
        "error": None,
    }
    with WORKFLOW_LOCK:
        data = load_workflow_runs()
        data["runs"].append(run)
        save_workflow_runs(data)
    return run


def update_workflow_run(run_id: str, mutate_fn):
    with WORKFLOW_LOCK:
        data = load_workflow_runs()
        updated = None
        for run in data["runs"]:
            if run.get("run_id") == run_id:
                mutate_fn(run)
                run["updated_at"] = utc_now_iso()
                updated = dict(run)
                break
        save_workflow_runs(data)
    return updated


def append_completed_stage(run: dict, stage: str):
    pipeline = run.setdefault("pipeline", {})
    completed = pipeline.setdefault("completed_stages", [])
    if stage not in completed:
        completed.append(stage)


def get_workflow_run(run_id: str):
    data = load_workflow_runs()
    for run in data["runs"]:
        if run.get("run_id") == run_id:
            return run
    return None


def list_workflow_runs(limit: int = 20):
    data = load_workflow_runs()
    runs = data.get("runs", [])
    return list(reversed(runs[-limit:]))


def append_workflow_step(run_id: str, name: str, agent: str, task: str, details: Optional[dict] = None):
    details = details or {}

    def mutate(run):
        run["steps"].append(
            {
                "name": name,
                "agent": agent,
                "task": task,
                "status": "running",
                "started_at": utc_now_iso(),
                "ended_at": None,
                "details": details,
                "result": None,
                "error": None,
            }
        )
        run["status"] = "running"

    return update_workflow_run(run_id, mutate)


def complete_workflow_step(run_id: str, index: int, status: str, result=None, error: Optional[str] = None):
    def mutate(run):
        if index < 0 or index >= len(run.get("steps", [])):
            return
        step = run["steps"][index]
        step["status"] = status
        step["ended_at"] = utc_now_iso()
        step["result"] = result
        step["error"] = error

    return update_workflow_run(run_id, mutate)


def finish_workflow_run(run_id: str, status: str, summary: str, final_decision: str, final_reasoning: str, error: Optional[str] = None):
    def mutate(run):
        run["status"] = status
        run["summary"] = summary
        run["final_decision"] = final_decision
        run["final_reasoning"] = final_reasoning
        run["error"] = error

    return update_workflow_run(run_id, mutate)


def call_delegate(role: str, task: str, payload: dict):
    result = perform_action(
        "delegate",
        {
            "role": role,
            "task": task,
            "payload": payload,
            "delegation_depth": 0,
        },
    )
    if isinstance(result, dict) and result.get("error"):
        raise RuntimeError(result["error"])
    return result


def slugify_name(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return slug or "project"


def normalize_workspace_path(path: Optional[str], fallback_name: Optional[str] = None) -> str:
    candidate = (path or "").strip()
    if not candidate:
        candidate = f"projects/{slugify_name(fallback_name or 'project')}"
    if candidate.startswith(WORKSPACE_DIR):
        candidate = os.path.relpath(candidate, WORKSPACE_DIR)
    candidate = candidate.lstrip("/")
    return candidate or f"projects/{slugify_name(fallback_name or 'project')}"


def workspace_abspath(relative_path: str) -> str:
    return os.path.join(WORKSPACE_DIR, normalize_workspace_path(relative_path))


def project_bootstrap_documents(project_name: str, goal: str, base: Optional[str] = None):
    slug = slugify_name(project_name)
    base = normalize_workspace_path(base, project_name)
    now = utc_now_iso()
    readme = (
        f"# {project_name}\n\n"
        f"## Goal\n{goal}\n\n"
        "## Initial Scope\n"
        "- Define a thin end-to-end slice first\n"
        "- Capture assumptions before implementation expands\n"
        "- Prefer coordinator-led delegation with auditable steps\n\n"
        "## First Deliverables\n"
        "- Working baseline workflow\n"
        "- Verification path for success/failure\n"
        "- Notes for the next operator handoff\n"
    )
    brief = (
        f"# Project Session\n\n"
        f"- Created: {now}\n"
        f"- Project: {project_name}\n"
        f"- Goal: {goal}\n"
        "- Coordinator Decision: start with a minimal validated slice before broader automation.\n"
        "- Next Step: use the workflow CLI to trigger follow-up workflows from the coordinator.\n"
    )
    return {
        "slug": slug,
        "base": base,
        "files": [
            {"path": f"{base}/README.md", "content": readme},
            {"path": f"{base}/SESSION.md", "content": brief},
        ],
    }


def collect_resource_snapshot():
    total_bytes, used_bytes, free_bytes = shutil.disk_usage(WORKSPACE_DIR)
    memory = {
        "mem_total_kb": None,
        "mem_available_kb": None,
        "mem_used_kb": None,
    }
    try:
        with open("/proc/meminfo", "r") as f:
            rows = f.readlines()
        parsed = {}
        for row in rows:
            if ":" not in row:
                continue
            key, value = row.split(":", 1)
            parts = value.strip().split()
            if parts:
                parsed[key] = int(parts[0])
        memory["mem_total_kb"] = parsed.get("MemTotal")
        memory["mem_available_kb"] = parsed.get("MemAvailable")
        if memory["mem_total_kb"] is not None and memory["mem_available_kb"] is not None:
            memory["mem_used_kb"] = memory["mem_total_kb"] - memory["mem_available_kb"]
    except Exception:
        pass

    return {
        "captured_at": utc_now_iso(),
        "workspace_dir": WORKSPACE_DIR,
        "disk": {
            "total_bytes": total_bytes,
            "used_bytes": used_bytes,
            "free_bytes": free_bytes,
        },
        "memory": memory,
    }


def infer_frameworks_and_verify_commands(project_root: str, file_inventory: List[str]) -> Tuple[List[str], List[str], List[dict]]:
    languages = set()
    frameworks = set()
    verify_commands = []
    root_abs = workspace_abspath(project_root)
    file_set = set(file_inventory)

    if any(name.endswith(".py") for name in file_inventory) or "pyproject.toml" in file_set or "requirements.txt" in file_set:
        languages.add("python")
        frameworks.add("python")
        if any("/test_" in name or name.startswith("tests/") or name.endswith("_test.py") for name in file_inventory):
            verify_commands.append({"label": "pytest", "command": "pytest -q", "cwd": project_root})
        else:
            py_files = [name for name in file_inventory if name.endswith(".py")][:10]
            if py_files:
                targets = " ".join(shlex.quote(path) for path in py_files)
                verify_commands.append(
                    {"label": "python compile", "command": f"python3 -m py_compile {targets}", "cwd": project_root}
                )

    if "package.json" in file_set:
        languages.add("javascript")
        frameworks.add("node")
        try:
            package_json = read_json_file(os.path.join(root_abs, "package.json"), {})
            scripts = package_json.get("scripts", {}) if isinstance(package_json, dict) else {}
            if isinstance(scripts, dict) and scripts.get("test"):
                verify_commands.append({"label": "npm test", "command": "npm test", "cwd": project_root})
            if isinstance(scripts, dict) and scripts.get("build"):
                verify_commands.append({"label": "npm build", "command": "npm run build", "cwd": project_root})
        except Exception:
            pass

    if "Cargo.toml" in file_set:
        languages.add("rust")
        frameworks.add("cargo")
        verify_commands.append({"label": "cargo test", "command": "cargo test", "cwd": project_root})

    if "go.mod" in file_set:
        languages.add("go")
        frameworks.add("go")
        verify_commands.append({"label": "go test", "command": "go test ./...", "cwd": project_root})

    if "pom.xml" in file_set:
        languages.add("java")
        frameworks.add("maven")
        verify_commands.append({"label": "maven test", "command": "mvn test", "cwd": project_root})

    if "build.gradle" in file_set or "build.gradle.kts" in file_set:
        languages.add("java")
        frameworks.add("gradle")
        if os.path.exists(os.path.join(root_abs, "gradlew")):
            verify_commands.append({"label": "gradle test", "command": "./gradlew test", "cwd": project_root})

    if "docker-compose.yml" in file_set or "docker-compose.yaml" in file_set or "Dockerfile" in file_set:
        frameworks.add("docker")

    return sorted(languages), sorted(frameworks), verify_commands


def inspect_project_root(project_root: str) -> dict:
    root_rel = normalize_workspace_path(project_root)
    root_abs = workspace_abspath(root_rel)
    if not os.path.exists(root_abs):
        raise RuntimeError(f"project root not found: {root_rel}")

    info = {
        "project_root": root_rel,
        "exists": True,
        "source_type": "directory" if os.path.isdir(root_abs) else "file",
        "files": [],
        "languages": [],
        "frameworks": [],
        "verify_commands": [],
    }

    if os.path.isfile(root_abs):
        rel_name = os.path.basename(root_abs)
        info["files"] = [rel_name]
        ext = os.path.splitext(rel_name)[1].lower()
        if ext == ".py":
            info["languages"] = ["python"]
            info["frameworks"] = ["python"]
            info["verify_commands"] = [
                {"label": "python compile", "command": f"python3 -m py_compile {shlex.quote(rel_name)}", "cwd": os.path.dirname(root_rel) or "."}
            ]
        return info

    collected = []
    for current_root, dirs, files in os.walk(root_abs):
        rel_dir = os.path.relpath(current_root, root_abs)
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv")]
        for name in files:
            rel_path = name if rel_dir == "." else os.path.join(rel_dir, name)
            collected.append(rel_path.replace("\\", "/"))
            if len(collected) >= 250:
                break
        if len(collected) >= 250:
            break

    info["files"] = sorted(collected)
    languages, frameworks, verify_commands = infer_frameworks_and_verify_commands(root_rel, info["files"])
    info["languages"] = languages
    info["frameworks"] = frameworks
    info["verify_commands"] = verify_commands
    info["file_count"] = len(collected)
    return info


def execute_verify_command(command_spec: dict) -> dict:
    command = command_spec.get("command") or ""
    cwd_rel = normalize_workspace_path(command_spec.get("cwd") or ".")
    cwd_abs = workspace_abspath(cwd_rel) if cwd_rel != "." else WORKSPACE_DIR
    result = subprocess.run(
        shlex.split(command),
        cwd=cwd_abs,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return {
        "label": command_spec.get("label") or command,
        "command": command,
        "cwd": cwd_rel,
        "returncode": result.returncode,
        "stdout": result.stdout[-2000:],
        "stderr": result.stderr[-2000:],
    }


def create_pipeline_run(project_name: str, goal: str, project_path: Optional[str] = None, prompt: Optional[str] = None, template: Optional[str] = None, autonomous: bool = True):
    root_path = normalize_workspace_path(project_path, project_name)
    run = create_workflow_run(
        "project_pipeline",
        {
            "project_name": project_name,
            "goal": goal,
            "project_root": root_path,
            "prompt": prompt,
            "template": template or "generic",
            "autonomous": autonomous,
        },
    )

    def mutate(current):
        current["family"] = "intake_inspect_verify_repair"
        current["template"] = template or "generic"
        current["project"] = {
            "name": project_name,
            "goal": goal,
            "project_root": root_path,
            "prompt": prompt,
        }
        current["inspect"] = {}
        current["verification"] = {}
        current["artifacts"] = {}
        current["pipeline"] = {
            "current_stage": None,
            "next_stage": "intake",
            "completed_stages": [],
            "last_failed_stage": None,
            "repair_attempts": 0,
            "autonomous": autonomous,
        }
        current["status"] = "queued"

    return update_workflow_run(run["run_id"], mutate)


def summarize_recent_runs(limit: int = 5) -> List[str]:
    lines = []
    for run in list_workflow_runs(limit=limit):
        lines.append(
            f"- {run.get('run_id')}: workflow={run.get('workflow')} template={run.get('template') or '-'} "
            f"status={run.get('status')} summary={run.get('summary') or '-'}"
        )
    return lines or ["- none"]


def prompt_requests_pre_answer_inspection(prompt: str) -> bool:
    low = (prompt or "").lower()
    triggers = (
        "dig in",
        "look into",
        "inspect",
        "analyze",
        "analyse",
        "check the repo",
        "check the project",
        "what's in",
        "what is in",
        "what frameworks",
        "what languages",
        "repo metadata",
        "project metadata",
        "current project",
        "current repo",
        "uploaded project",
        "uploaded repo",
        "before making responses",
    )
    return any(trigger in low for trigger in triggers)


def extract_candidate_project_roots(prompt: str) -> List[str]:
    text = (prompt or "").strip()
    low = text.lower()
    candidates = []

    for match in re.findall(r"(workspace/projects/[A-Za-z0-9._/\-]+|projects/[A-Za-z0-9._/\-]+)", text):
        normalized = normalize_workspace_path(match)
        if normalized not in candidates:
            candidates.append(normalized)

    projects_root = os.path.join(WORKSPACE_DIR, "projects")
    if os.path.isdir(projects_root):
        for name in sorted(os.listdir(projects_root)):
            rel = f"projects/{name}"
            if name.lower() in low or name.replace("-", " ").lower() in low:
                if rel not in candidates:
                    candidates.append(rel)

    if any(phrase in low for phrase in ("current project", "current repo", "this project", "this repo", "uploaded project", "uploaded repo")):
        for run in list_workflow_runs(limit=10):
            project_root = ((run.get("project") or {}).get("project_root") or (run.get("input") or {}).get("project_root"))
            if project_root:
                normalized = normalize_workspace_path(project_root)
                if normalized not in candidates:
                    candidates.append(normalized)
                break

    return candidates[:3]


def collect_pre_answer_inspection_context(prompt: str) -> dict:
    context = {
        "triggered": False,
        "recent_runs": list_workflow_runs(limit=5),
        "project_inspections": [],
        "candidate_project_roots": [],
    }
    if not prompt_requests_pre_answer_inspection(prompt):
        return context

    context["triggered"] = True
    candidates = extract_candidate_project_roots(prompt)
    context["candidate_project_roots"] = candidates
    for project_root in candidates:
        try:
            inspection = inspect_project_root(project_root)
            context["project_inspections"].append(inspection)
        except Exception as exc:
            context["project_inspections"].append({"project_root": project_root, "error": str(exc)})
    return context


def summarize_roles_config() -> dict:
    config = load_roles_config()
    agents_cfg = config.get("agents", {}) if isinstance(config, dict) else {}
    communication = config.get("communication", {}) if isinstance(config, dict) else {}
    summary = {"agents": {}, "communication": communication if isinstance(communication, dict) else {}}
    for name, item in agents_cfg.items():
        if not isinstance(item, dict):
            continue
        summary["agents"][name] = {
            "role": item.get("role"),
            "description": item.get("description"),
            "capabilities": item.get("capabilities") or [],
            "tools": item.get("tools") or [],
        }
    return summary


def local_capabilities_summary() -> str:
    return (
        "Coordinator-owned pipeline family: intake, inspect, verify, repair. "
        "Specialized adapters: resource-check and repo-diagnostics. "
        "Project inspection currently detects Python, JavaScript/Node, Rust, Go, Java/Maven, Java/Gradle, and Docker-shaped repos. "
        "Coder can write validated text/json/config/python files. Tester can verify files and HTTP endpoints. "
        "Deployer is present but deploy_compose remains placeholder-oriented."
    )


def maybe_answer_from_local_knowledge(prompt: str) -> Optional[str]:
    text = (prompt or "").strip()
    low = text.lower()
    roles = summarize_roles_config()
    agents = roles.get("agents", {})
    communication = roles.get("communication", {})

    if any(phrase in low for phrase in ("how many agents", "specific roles", "roles of these agents", "capabilities", "talk to each other", "communicate", "what agents are available")):
        lines = [f"I have access to the following agents in this system:"]
        for name, info in agents.items():
            capabilities = ", ".join(info.get("capabilities")[:5]) if info.get("capabilities") else "none listed"
            lines.append(f"- {colorize(name.capitalize(), CLR_BOLD) if 'colorize' in globals() else name.capitalize()}: {info.get('description') or info.get('role')}.")
            lines.append(f"  Capabilities: {capabilities}.")
        
        lines.append("\nCommunication flow:")
        lines.append("- Coordinator delegates to Coder for writing files.")
        lines.append("- Coordinator delegates to Tester for verifying outcomes (HTTP/Files).")
        lines.append("- Coordinator delegates to Deployer for infrastructure tasks.")
        
        lines.append("\nResuming work:")
        lines.append("- Use 'kickoff <name> <path>' to re-trigger inspection and verification after pushing changes.")
        lines.append("- Use 'last' or 'last watch' to quickly re-attach to your most recent activity.")
        
        lines.append("\nYou should mainly interact with me (the Coordinator). I will handle the delegation to specialized agents based on your requests.")
        return "\n".join(lines)

    if any(phrase in low for phrase in ("what software projects can we build", "what can we build", "what projects can we build and execute", "what are your capabilities")):
        return (
            "The current system is project-oriented, not limited to one demo tool. "
            + local_capabilities_summary()
            + " In practice, it is strongest today at: "
            "single-file utilities, small Python services, repo diagnostics bundles, environment/resource tooling, and project scaffolds. "
            "For larger multi-language repos, the coordinator can intake and inspect them generically, then use repo-diagnostics as a concrete verified specialization."
        )

    return None


def build_grounded_coordinator_prompt(user_prompt: str, mode: str = "plan") -> str:
    roles = summarize_roles_config()
    project_runs = summarize_recent_runs(limit=5)
    inspection_context = collect_pre_answer_inspection_context(user_prompt)
    
    if mode == "chat":
        mode_instruction = (
            "Answer conversationally and briefly. If a request requires starting a workflow "
            "(intake for new projects, resource_check for environment details), you MUST "
            "trigger it autonomously using a JSON action array with 'start_workflow'. "
            "Example: [{\"action\": \"start_workflow\", \"params\": {\"workflow\": \"intake\", \"project_name\": \"...\", \"goal\": \"...\"}}]"
        )
    else:
        mode_instruction = "Produce a concise, grounded plan using only the provided local context."

    return (
        f"{mode_instruction}\n"
        "If the answer is uncertain, say so explicitly. Do not invent agents, teams, tools, or integrations not present in context.\n\n"
        "Local agent roles:\n"
        f"{json.dumps(roles, indent=2, sort_keys=True)}\n\n"
        "Current local workflow capabilities:\n"
        f"{local_capabilities_summary()}\n\n"
        "Recent workflow runs:\n"
        + "\n".join(project_runs)
        + "\n\nPre-answer inspection context:\n"
        + json.dumps(inspection_context, indent=2, sort_keys=True)
        + "\n\nUser request:\n"
        + user_prompt
    )


def set_pipeline_stage(run_id: str, stage: str, status: str, next_stage: Optional[str] = None, error: Optional[str] = None, summary: Optional[str] = None):
    def mutate(run):
        pipeline = run.setdefault("pipeline", {})
        pipeline["current_stage"] = stage
        pipeline["next_stage"] = next_stage
        if status == "completed":
            append_completed_stage(run, stage)
            pipeline["last_failed_stage"] = None
        elif status == "failed":
            pipeline["last_failed_stage"] = stage
        if summary:
            run["summary"] = summary
        if error:
            run["error"] = error
        run["status"] = status

    return update_workflow_run(run_id, mutate)


def start_background_pipeline_stage(run_id: str, stage: str):
    targets = {
        "intake": run_pipeline_intake_stage,
        "inspect": run_pipeline_inspect_stage,
        "implement": run_pipeline_implement_stage,
        "verify": verify_pipeline_run,
        "repair": repair_pipeline_run,
    }
    target = targets.get(stage)
    if target:
        start_background_workflow(target, run_id)


def complete_pipeline_stage(run_id: str, stage: str, next_stage: Optional[str], summary: str):
    def mutate(run):
        append_completed_stage(run, stage)
        pipeline = run.setdefault("pipeline", {})
        pipeline["current_stage"] = stage
        pipeline["next_stage"] = next_stage
        pipeline["last_failed_stage"] = None
        run["summary"] = summary
        run["status"] = "pending"

    updated = update_workflow_run(run_id, mutate)
    if updated and next_stage:
        pipeline = updated.get("pipeline", {})
        if pipeline.get("autonomous", True):
            # Advance status to running and trigger next stage outside the mutate lock
            update_workflow_run(run_id, lambda r: r.update({"status": "running"}))
            start_background_pipeline_stage(run_id, next_stage)
    return updated


def fail_pipeline_stage(run_id: str, stage: str, error: str):
    return set_pipeline_stage(run_id, stage, "failed", next_stage="repair", error=error, summary=error)


def run_pipeline_intake_stage(run_id: str):
    run = get_workflow_run(run_id)
    if not run:
        raise RuntimeError(f"workflow run not found: {run_id}")
    project = run.get("project", {})
    project_name = project.get("name") or "New Project"
    goal = project.get("goal") or "Define a working first slice."
    project_root = normalize_workspace_path(project.get("project_root"), project_name)
    set_pipeline_stage(run_id, "intake", "running", next_stage="inspect")

    try:
        root_abs = workspace_abspath(project_root)
        if os.path.exists(root_abs):
            source_type = "directory" if os.path.isdir(root_abs) else "file"
            update_workflow_run(
                run_id,
                lambda current: current["project"].update({"project_root": project_root, "source_type": source_type}),
            )
            complete_pipeline_stage(run_id, "intake", "inspect", f"intake completed for existing {source_type} {project_root}")
            return get_workflow_run(run_id)

        docs = project_bootstrap_documents(project_name, goal, base=project_root)
        created_paths = []
        for file_spec in docs["files"]:
            append_workflow_step(
                run_id,
                name=f"write {os.path.basename(file_spec['path'])}",
                agent="coder",
                task="write_file",
                details={"path": file_spec["path"], "stage": "intake"},
            )
            result = call_delegate("coder", "write_file", {"path": file_spec["path"], "content": file_spec["content"]})
            created_paths.append(file_spec["path"])
            current = get_workflow_run(run_id) or {}
            complete_workflow_step(run_id, len(current.get("steps", [])) - 1, "completed", result=result)

        update_workflow_run(
            run_id,
            lambda current: current.update(
                {
                    "artifacts": {**current.get("artifacts", {}), "intake_files": created_paths},
                }
            ),
        )
        update_workflow_run(
            run_id,
            lambda current: current["project"].update({"project_root": project_root, "source_type": "generated_project"}),
        )
        complete_pipeline_stage(run_id, "intake", "inspect", f"intake completed for generated project {project_root}")
        return get_workflow_run(run_id)
    except Exception as exc:
        fail_pipeline_stage(run_id, "intake", str(exc))
        raise


def run_pipeline_inspect_stage(run_id: str):
    run = get_workflow_run(run_id)
    if not run:
        raise RuntimeError(f"workflow run not found: {run_id}")
    project = run.get("project", {})
    project_root = project.get("project_root")
    if not project_root:
        raise RuntimeError("pipeline run missing project_root")

    set_pipeline_stage(run_id, "inspect", "running", next_stage="verify")
    try:
        inspection = inspect_project_root(project_root)
        update_workflow_run(
            run_id,
            lambda current: current.update(
                {
                    "inspect": inspection,
                    "project": {**current.get("project", {}), "languages": inspection.get("languages"), "frameworks": inspection.get("frameworks")},
                }
            ),
        )
        next_stage = "repair" if run.get("template") == "resource-check" else "implement"
        summary = (
            f"inspect completed for {project_root}; "
            f"languages={', '.join(inspection.get('languages') or ['unknown'])}; "
            f"frameworks={', '.join(inspection.get('frameworks') or ['none'])}"
        )
        complete_pipeline_stage(run_id, "inspect", next_stage, summary)
        return get_workflow_run(run_id)
    except Exception as exc:
        fail_pipeline_stage(run_id, "inspect", str(exc))
        raise


def run_pipeline_implement_stage(run_id: str):
    run = get_workflow_run(run_id)
    if not run:
        raise RuntimeError(f"workflow run not found: {run_id}")
    project = run.get("project", {})
    project_root = project.get("project_root")
    goal = project.get("goal")
    inspection = run.get("inspect", {})
    
    set_pipeline_stage(run_id, "implement", "running", next_stage="verify")
    
    try:
        # Build a prompt for the coordinator to implement the goal
        prompt = (
            f"GOAL: {goal}\n\n"
            f"PROJECT ROOT: {project_root}\n"
            f"EXISTING LANGUAGES: {', '.join(inspection.get('languages') or [])}\n"
            f"EXISTING FRAMEWORKS: {', '.join(inspection.get('frameworks') or [])}\n"
            f"FILES: {', '.join(inspection.get('files') or [])}\n\n"
            "Task: Implement the necessary code files to achieve the goal in this project root. "
            "You MUST respond ONLY with a JSON array of actions. "
            "Each action must be in the form: "
            '{"action": "write_file", "params": {"path": "...", "content": "..."}}.\n'
            "Ensure paths are relative to the project root (e.g. 'main.py' or 'src/utils.py')."
        )
        
        grounded_prompt = build_grounded_coordinator_prompt(prompt, mode="plan")
        model = get_role_specific_model(ROLE)
        endpoint, models_list, _ = fetch_ollama_models()
        chosen_model = choose_model(model, models_list)
        
        if not chosen_model:
            raise RuntimeError("no models available for implementation")

        req_payload = {
            "model": chosen_model,
            "prompt": grounded_prompt,
            "stream": False,
            "options": {"num_predict": 512},
        }
        
        ollama_base = endpoint or OLLAMA_URL.rstrip("/")
        resp = requests.post(
            f"{ollama_base}/api/generate",
            json=req_payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        
        if not resp.ok:
            raise RuntimeError(f"ollama error: {resp.status_code}")
            
        parsed = parse_json_from_text(resp.text)
        if not parsed:
            # Try to extract the whole response if it's supposed to be a list
            try:
                raw_text = resp.json().get("response", "")
                parsed = parse_json_from_text(raw_text)
            except Exception:
                pass
                
        if not isinstance(parsed, (list, dict)):
            raise RuntimeError("model did not return valid JSON actions")
            
        # Ensure it's a list for processing
        actions_list = parsed if isinstance(parsed, list) else ([parsed] if isinstance(parsed, dict) else [])
        
        executed_results = []
        for action_spec in actions_list:
            if not isinstance(action_spec, dict):
                continue
            action = action_spec.get("action")
            params = action_spec.get("params") or {}
            
            # Prefix path with project_root if it's not already
            orig_path = params.get("path")
            if orig_path and not orig_path.startswith(project_root):
                params["path"] = os.path.join(project_root, orig_path.lstrip("/"))
                
            if action == "write_file":
                append_workflow_step(
                    run_id,
                    name=f"write {os.path.basename(params['path'])}",
                    agent="coder",
                    task="write_file",
                    details=params,
                )
                res = call_delegate("coder", "write_file", params)
                executed_results.append(res)
                current = get_workflow_run(run_id) or {}
                complete_workflow_step(run_id, len(current.get("steps", [])) - 1, "completed", result=res)
        
        update_workflow_run(
            run_id,
            lambda current: current.update({"implementation": {"actions_count": len(executed_results)}})
        )
        
        complete_pipeline_stage(run_id, "implement", "verify", f"implementation stage completed with {len(executed_results)} files written")
        return get_workflow_run(run_id)
        
    except Exception as exc:
        fail_pipeline_stage(run_id, "implement", str(exc))
        raise


def verify_pipeline_run(run_id: str):
    run = get_workflow_run(run_id)
    if not run:
        raise RuntimeError(f"workflow run not found: {run_id}")
    stage = "verify"
    set_pipeline_stage(run_id, stage, "running", next_stage=None)

    try:
        if run.get("template") == "repo-diagnostics":
            artifacts = run.get("artifacts", {})
            verify_paths = [
                artifacts.get("stack_summary"),
                artifacts.get("verify_commands_bundle"),
                artifacts.get("repair_notes"),
            ]
            verify_paths = [path for path in verify_paths if path]
            if not verify_paths:
                raise RuntimeError("repo-diagnostics artifacts are missing")

            verify_results = []
            for verify_path in verify_paths:
                append_workflow_step(
                    run_id,
                    name=f"verify {os.path.basename(verify_path)}",
                    agent="tester",
                    task="file_check",
                    details={"path": verify_path, "stage": "verify"},
                )
                result = call_delegate("tester", "file_check", {"path": verify_path})
                current = get_workflow_run(run_id) or {}
                complete_workflow_step(run_id, len(current.get("steps", [])) - 1, "completed", result=result)
                verify_results.append(result)

            update_workflow_run(
                run_id,
                lambda current: (
                    current.update({"verification": {"strategy": "repo-diagnostics", "results": verify_results}}),
                    append_completed_stage(current, stage),
                ),
            )
            finish_workflow_run(
                run_id,
                status="completed",
                summary=f"verify completed for repo diagnostics at {run.get('project', {}).get('project_root')}",
                final_decision="success",
                final_reasoning=(
                    "Coordinator generated and verified a stack-aware diagnostics bundle so a mixed-language repo "
                    "has concrete repair artifacts before deeper implementation work."
                ),
            )
            return get_workflow_run(run_id)

        if run.get("template") == "resource-check":
            project_root = run.get("project", {}).get("project_root")
            plan = build_resource_tool_files(
                run.get("project", {}).get("name") or "Resource Tool",
                run.get("project", {}).get("goal") or "Create a working disk and memory inspection utility.",
                max(1, int(run.get("pipeline", {}).get("repair_attempts") or 1)),
            )
            tool_output = execute_workspace_python(plan["script_path"])
            verification = {
                "strategy": "resource-check",
                "result": tool_output,
                "verified_path": plan["script_path"],
                "report_path": plan["report_path"],
            }
            update_workflow_run(
                run_id,
                lambda current: (
                    current.update({"verification": verification, "artifacts": {**current.get("artifacts", {}), "resource_report": plan["report_path"], "resource_tool": plan["script_path"]}}),
                    append_completed_stage(current, stage),
                ),
            )
            finish_workflow_run(
                run_id,
                status="completed",
                summary=f"verify completed for {project_root}",
                final_decision="success",
                final_reasoning=(
                    "Coordinator executed the specialized resource-check tool and validated that "
                    "its output contained both disk and memory details."
                ),
            )
            return get_workflow_run(run_id)

        inspection = run.get("inspect", {})
        commands = inspection.get("verify_commands") or []
        if not commands:
            raise RuntimeError("no verification strategy detected for this project")
        command_results = []
        for command_spec in commands[:3]:
            command_result = execute_verify_command(command_spec)
            command_results.append(command_result)
            if command_result["returncode"] != 0:
                raise RuntimeError(
                    f"{command_result['label']} failed with exit code {command_result['returncode']}: "
                    f"{(command_result['stderr'] or command_result['stdout']).strip()[:300]}"
                )
        update_workflow_run(
            run_id,
            lambda current: (
                current.update({"verification": {"strategy": "detected_commands", "commands": command_results}}),
                append_completed_stage(current, stage),
            ),
        )
        finish_workflow_run(
            run_id,
            status="completed",
            summary=f"verify completed for {run.get('project', {}).get('project_root')}",
            final_decision="success",
            final_reasoning=(
                "Coordinator used the inspected project profile to choose verification commands "
                "and all selected checks completed successfully."
            ),
        )
        return get_workflow_run(run_id)
    except Exception as exc:
        fail_pipeline_stage(run_id, stage, str(exc))
        raise


def repair_pipeline_run(run_id: str):
    run = get_workflow_run(run_id)
    if not run:
        raise RuntimeError(f"workflow run not found: {run_id}")
    set_pipeline_stage(run_id, "repair", "running", next_stage="verify")

    try:
        if run.get("template") == "repo-diagnostics":
            bundle = build_repo_diagnostics_bundle(run)
            for path_key, content_key, label in (
                ("summary_path", "summary_content", "stack summary"),
                ("commands_path", "commands_content", "verify commands"),
                ("notes_path", "notes_content", "repair notes"),
            ):
                append_workflow_step(
                    run_id,
                    name=f"write {label}",
                    agent="coder",
                    task="write_file",
                    details={"path": bundle[path_key], "stage": "repair"},
                )
                result = call_delegate("coder", "write_file", {"path": bundle[path_key], "content": bundle[content_key]})
                current = get_workflow_run(run_id) or {}
                complete_workflow_step(run_id, len(current.get("steps", [])) - 1, "completed", result=result)

            update_workflow_run(
                run_id,
                lambda current: current.update(
                    {
                        "artifacts": {
                            **current.get("artifacts", {}),
                            "stack_summary": bundle["summary_path"],
                            "verify_commands_bundle": bundle["commands_path"],
                            "repair_notes": bundle["notes_path"],
                        }
                    }
                ),
            )
            complete_pipeline_stage(run_id, "repair", "verify", f"repair completed for repo diagnostics at {bundle['analysis_base']}")
            return get_workflow_run(run_id)

        if run.get("template") != "resource-check":
            raise RuntimeError("no repair adapter is available for this project template yet")

        project = run.get("project", {})
        project_name = project.get("name") or "Resource Tool"
        goal = project.get("goal") or "Create a working disk and memory inspection utility."
        last_error = run.get("error")
        repair_attempts = int(run.get("pipeline", {}).get("repair_attempts") or 0) + 1
        plan = build_resource_tool_files(project_name, goal, repair_attempts, last_error=last_error)

        update_workflow_run(run_id, lambda current: current["pipeline"].update({"repair_attempts": repair_attempts}))

        for path_key, content_key, label in (
            ("script_path", "script_content", "resource tool"),
            ("notes_path", "notes_content", "repair notes"),
        ):
            append_workflow_step(
                run_id,
                name=f"write {label}",
                agent="coder",
                task="write_file",
                details={"path": plan[path_key], "stage": "repair", "attempt": repair_attempts},
            )
            result = call_delegate("coder", "write_file", {"path": plan[path_key], "content": plan[content_key]})
            current = get_workflow_run(run_id) or {}
            complete_workflow_step(run_id, len(current.get("steps", [])) - 1, "completed", result=result)

        update_workflow_run(
            run_id,
            lambda current: current.update(
                {
                    "artifacts": {
                        **current.get("artifacts", {}),
                        "resource_tool": plan["script_path"],
                        "resource_report": plan["report_path"],
                        "repair_notes": plan["notes_path"],
                    }
                }
            ),
        )
        complete_pipeline_stage(run_id, "repair", "verify", f"repair completed for {plan['base']} on attempt {repair_attempts}")
        return get_workflow_run(run_id)
    except Exception as exc:
        fail_pipeline_stage(run_id, "repair", str(exc))
        raise


def run_resource_check_pipeline(run_id: str):
    try:
        run_pipeline_intake_stage(run_id)
        run_pipeline_inspect_stage(run_id)
        repair_pipeline_run(run_id)
        verify_pipeline_run(run_id)
    except Exception:
        pass


def run_repo_diagnostics_pipeline(run_id: str):
    try:
        run_pipeline_intake_stage(run_id)
        run_pipeline_inspect_stage(run_id)
        repair_pipeline_run(run_id)
        verify_pipeline_run(run_id)
    except Exception:
        pass


def validate_resource_tool_output(output_payload):
    if not isinstance(output_payload, dict):
        return False, "tool output is not a JSON object"
    if not isinstance(output_payload.get("disk"), dict):
        return False, "tool output missing disk details"
    if not isinstance(output_payload.get("memory"), dict):
        return False, "tool output missing memory details"
    return True, None


def bytes_to_gb(value):
    return round(value / (1024 ** 3), 2)


def kb_to_gb(value):
    if value is None:
        return None
    return round(value / (1024 ** 2), 2)


def resource_report_markdown(snapshot):
    disk = snapshot["disk"]
    memory = snapshot["memory"]
    lines = [
        "# System Resource Report",
        "",
        f"- Captured: {snapshot['captured_at']}",
        f"- Workspace: {snapshot['workspace_dir']}",
        "",
        "## Disk",
        f"- Total: {bytes_to_gb(disk['total_bytes'])} GB",
        f"- Used: {bytes_to_gb(disk['used_bytes'])} GB",
        f"- Free: {bytes_to_gb(disk['free_bytes'])} GB",
        "",
        "## Memory",
    ]
    if memory["mem_total_kb"] is None:
        lines.append("- Memory details unavailable")
    else:
        lines.extend(
            [
                f"- Total: {kb_to_gb(memory['mem_total_kb'])} GB",
                f"- Used: {kb_to_gb(memory['mem_used_kb'])} GB",
                f"- Available: {kb_to_gb(memory['mem_available_kb'])} GB",
            ]
        )
    lines.extend(
        [
            "",
            "## Coordinator Decision",
            "- Capture the current environment first before attempting heavier project work.",
        ]
    )
    return "\n".join(lines) + "\n"


def build_resource_tool_files(project_name: str, goal: str, attempt: int, last_error: Optional[str] = None):
    slug = slugify_name(project_name)
    base = f"projects/{slug}"
    script_path = f"{base}/resource_tool.py"
    report_path = f"{base}/resource-report.json"
    notes_path = f"{base}/BUILD_NOTES.md"

    if attempt <= 1:
        script = f"""import json
import shutil


def read_meminfo():
    data = {{}}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for row in handle:
                if ":" not in row:
                    continue
                key, value = row.split(":", 1)
                parts = value.strip().split()
                if parts:
                    data[key] = int(parts[0])
    except FileNotFoundError:
        return {{"available": False}}

    total = data.get("MemTotal")
    available = data.get("MemAvailable")
    used = None
    if total is not None and available is not None:
        used = total - available
    return {{
        "available": True,
        "mem_total_kb": total,
        "mem_available_kb": available,
        "mem_used_kb": used,
    }}


def main():
    total, used, free = shutil.disk_usage("/workspace")
    payload = {{
        "project": {project_name!r},
        "goal": {goal!r},
        "disk": {{
            "path": "/workspace",
            "total_bytes": total,
            "used_bytes": used,
            "free_bytes": free,
        }},
        "memory": read_meminfo(),
    }}
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
"""
    else:
        script = f"""import json
import os
import shutil


def safe_read_meminfo():
    result = {{"available": False}}
    if not os.path.exists("/proc/meminfo"):
        return result
    with open("/proc/meminfo", "r", encoding="utf-8") as handle:
        rows = handle.readlines()
    parsed = {{}}
    for row in rows:
        if ":" not in row:
            continue
        key, value = row.split(":", 1)
        fields = value.strip().split()
        if fields:
            parsed[key] = int(fields[0])
    total = parsed.get("MemTotal")
    available = parsed.get("MemAvailable")
    result.update(
        {{
            "available": True,
            "mem_total_kb": total,
            "mem_available_kb": available,
            "mem_used_kb": (total - available) if total is not None and available is not None else None,
        }}
    )
    return result


def main():
    total, used, free = shutil.disk_usage("/workspace")
    payload = {{
        "project": {project_name!r},
        "goal": {goal!r},
        "attempt": {attempt},
        "disk": {{
            "path": "/workspace",
            "total_bytes": int(total),
            "used_bytes": int(used),
            "free_bytes": int(free),
        }},
        "memory": safe_read_meminfo(),
    }}
    print(json.dumps(payload, sort_keys=True))


if __name__ == "__main__":
    main()
"""

    notes = (
        f"# Build Notes\n\n"
        f"- Project: {project_name}\n"
        f"- Goal: {goal}\n"
        f"- Current attempt: {attempt}\n"
        f"- Last execution error: {last_error or 'none'}\n"
        "- Coordinator strategy: write the tool, execute it locally, and revise on failure.\n"
    )

    return {
        "slug": slug,
        "base": base,
        "script_path": script_path,
        "report_path": report_path,
        "notes_path": notes_path,
        "script_content": script,
        "notes_content": notes,
    }


def execute_workspace_python(script_path: str):
    full_path = os.path.join(WORKSPACE_DIR, script_path.lstrip("/"))
    result = subprocess.run(
        ["python3", full_path],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"python exited with {result.returncode}")
    try:
        payload = json.loads(result.stdout)
    except Exception as exc:
        raise RuntimeError(f"tool output was not valid JSON: {exc}") from exc
    ok, reason = validate_resource_tool_output(payload)
    if not ok:
        raise RuntimeError(reason or "tool output validation failed")
    return payload


def build_repo_diagnostics_bundle(run: dict):
    project = run.get("project", {})
    inspection = run.get("inspect", {})
    project_root = normalize_workspace_path(project.get("project_root"), project.get("name"))
    source_type = inspection.get("source_type") or "directory"
    analysis_root = project_root if source_type == "directory" else os.path.dirname(project_root) or "."
    analysis_base = normalize_workspace_path(os.path.join(analysis_root, "coldbase-analysis"))
    summary_path = f"{analysis_base}/STACK_SUMMARY.md"
    commands_path = f"{analysis_base}/VERIFY_COMMANDS.json"
    notes_path = f"{analysis_base}/REPAIR_NOTES.md"

    summary = (
        f"# Stack Summary\n\n"
        f"- Project: {project.get('name')}\n"
        f"- Goal: {project.get('goal')}\n"
        f"- Root: {project_root}\n"
        f"- Source Type: {source_type}\n"
        f"- Languages: {', '.join(inspection.get('languages') or ['unknown'])}\n"
        f"- Frameworks: {', '.join(inspection.get('frameworks') or ['none'])}\n"
        f"- File Count (sample): {inspection.get('file_count', len(inspection.get('files') or []))}\n"
    )
    commands = {
        "project_root": project_root,
        "languages": inspection.get("languages") or [],
        "frameworks": inspection.get("frameworks") or [],
        "verify_commands": inspection.get("verify_commands") or [],
        "sample_files": inspection.get("files") or [],
    }
    notes = (
        "# Repair Notes\n\n"
        "- Template: repo-diagnostics\n"
        "- Coordinator Decision: generate a portable diagnostics bundle before deeper edits.\n"
        "- Operator Follow-up: review the verify commands and stack summary before selecting the next adapter.\n"
    )
    return {
        "analysis_base": analysis_base,
        "summary_path": summary_path,
        "commands_path": commands_path,
        "notes_path": notes_path,
        "summary_content": summary,
        "commands_content": json.dumps(commands, indent=2, sort_keys=True) + "\n",
        "notes_content": notes,
    }


def run_hello_workflow(run_id: str, path: str, content: str):
    try:
        append_workflow_step(
            run_id,
            name="write hello file",
            agent="coder",
            task="write_file",
            details={"path": path},
        )
        write_result = call_delegate("coder", "write_file", {"path": path, "content": content})
        current = get_workflow_run(run_id) or {}
        complete_workflow_step(run_id, len(current.get("steps", [])) - 1, "completed", result=write_result)

        append_workflow_step(
            run_id,
            name="verify hello file",
            agent="tester",
            task="file_check",
            details={"path": path},
        )
        verify_result = call_delegate("tester", "file_check", {"path": path})
        current = get_workflow_run(run_id) or {}
        complete_workflow_step(run_id, len(current.get("steps", [])) - 1, "completed", result=verify_result)

        reasoning = (
            "Coordinator delegated file creation to coder, then delegated verification "
            "to tester so the workflow confirms both execution and observable output."
        )
        finish_workflow_run(
            run_id,
            status="completed",
            summary=f"hello_world completed for {path}",
            final_decision="success",
            final_reasoning=reasoning,
        )
    except Exception as exc:
        current = get_workflow_run(run_id) or {}
        if current.get("steps"):
            complete_workflow_step(
                run_id,
                len(current["steps"]) - 1,
                "failed",
                error=str(exc),
            )
        finish_workflow_run(
            run_id,
            status="failed",
            summary="hello_world failed",
            final_decision="failure",
            final_reasoning=(
                "Coordinator stopped the workflow because one delegated step returned an error "
                "or could not be delivered safely."
            ),
            error=str(exc),
        )


def run_resource_check_workflow(run_id: str, report_path: str):
    try:
        append_workflow_step(
            run_id,
            name="capture current resources",
            agent="coordinator",
            task="resource_snapshot",
            details={"path": report_path},
        )
        snapshot = collect_resource_snapshot()
        current = get_workflow_run(run_id) or {}
        complete_workflow_step(run_id, len(current.get("steps", [])) - 1, "completed", result=snapshot)

        append_workflow_step(
            run_id,
            name="write resource report",
            agent="coder",
            task="write_file",
            details={"path": report_path},
        )
        report = resource_report_markdown(snapshot)
        write_result = call_delegate("coder", "write_file", {"path": report_path, "content": report})
        current = get_workflow_run(run_id) or {}
        complete_workflow_step(run_id, len(current.get("steps", [])) - 1, "completed", result=write_result)

        append_workflow_step(
            run_id,
            name="verify resource report",
            agent="tester",
            task="file_check",
            details={"path": report_path},
        )
        verify_result = call_delegate("tester", "file_check", {"path": report_path})
        current = get_workflow_run(run_id) or {}
        complete_workflow_step(run_id, len(current.get("steps", [])) - 1, "completed", result=verify_result)

        finish_workflow_run(
            run_id,
            status="completed",
            summary=f"resource_check completed for {report_path}",
            final_decision="success",
            final_reasoning=(
                "Coordinator captured live resource data first, then delegated report creation "
                "and verification so the operator gets a readable artifact and a validated outcome."
            ),
        )
    except Exception as exc:
        current = get_workflow_run(run_id) or {}
        if current.get("steps"):
            complete_workflow_step(
                run_id,
                len(current["steps"]) - 1,
                "failed",
                error=str(exc),
            )
        finish_workflow_run(
            run_id,
            status="failed",
            summary="resource_check failed",
            final_decision="failure",
            final_reasoning=(
                "Coordinator stopped resource_check because the environment snapshot could not be "
                "captured, written, or verified safely."
            ),
            error=str(exc),
        )


def run_build_resource_tool_workflow(run_id: str, project_name: str, goal: str, max_attempts: int = 3):
    last_error = None
    plan = None
    for attempt in range(1, max_attempts + 1):
        plan = build_resource_tool_files(project_name, goal, attempt, last_error=last_error)
        try:
            append_workflow_step(
                run_id,
                name=f"write resource tool attempt {attempt}",
                agent="coder",
                task="write_file",
                details={"path": plan["script_path"], "attempt": attempt},
            )
            write_tool_result = call_delegate(
                "coder",
                "write_file",
                {"path": plan["script_path"], "content": plan["script_content"]},
            )
            current = get_workflow_run(run_id) or {}
            complete_workflow_step(run_id, len(current.get("steps", [])) - 1, "completed", result=write_tool_result)

            append_workflow_step(
                run_id,
                name=f"write build notes attempt {attempt}",
                agent="coder",
                task="write_file",
                details={"path": plan["notes_path"], "attempt": attempt},
            )
            write_notes_result = call_delegate(
                "coder",
                "write_file",
                {"path": plan["notes_path"], "content": plan["notes_content"]},
            )
            current = get_workflow_run(run_id) or {}
            complete_workflow_step(run_id, len(current.get("steps", [])) - 1, "completed", result=write_notes_result)

            append_workflow_step(
                run_id,
                name=f"execute resource tool attempt {attempt}",
                agent="coordinator",
                task="run_python",
                details={"path": plan["script_path"], "attempt": attempt},
            )
            tool_output = execute_workspace_python(plan["script_path"])
            current = get_workflow_run(run_id) or {}
            complete_workflow_step(run_id, len(current.get("steps", [])) - 1, "completed", result=tool_output)

            append_workflow_step(
                run_id,
                name="write execution report",
                agent="coder",
                task="write_file",
                details={"path": plan["report_path"]},
            )
            write_report_result = call_delegate(
                "coder",
                "write_file",
                {"path": plan["report_path"], "content": json.dumps(tool_output, indent=2, sort_keys=True) + "\n"},
            )
            current = get_workflow_run(run_id) or {}
            complete_workflow_step(run_id, len(current.get("steps", [])) - 1, "completed", result=write_report_result)

            for verify_path in (plan["script_path"], plan["report_path"]):
                append_workflow_step(
                    run_id,
                    name=f"verify {os.path.basename(verify_path)}",
                    agent="tester",
                    task="file_check",
                    details={"path": verify_path},
                )
                verify_result = call_delegate("tester", "file_check", {"path": verify_path})
                current = get_workflow_run(run_id) or {}
                complete_workflow_step(run_id, len(current.get("steps", [])) - 1, "completed", result=verify_result)

            finish_workflow_run(
                run_id,
                status="completed",
                summary=f"build_resource_tool completed for {plan['base']}",
                final_decision="success",
                final_reasoning=(
                    f"Coordinator built and executed the resource tool successfully on attempt {attempt}, "
                    "then persisted the validated output so the operator has both the utility and its latest report."
                ),
            )
            return
        except Exception as exc:
            last_error = str(exc)
            current = get_workflow_run(run_id) or {}
            if current.get("steps"):
                complete_workflow_step(
                    run_id,
                    len(current["steps"]) - 1,
                    "failed",
                    error=last_error,
                )
            if attempt == max_attempts:
                finish_workflow_run(
                    run_id,
                    status="failed",
                    summary="build_resource_tool failed",
                    final_decision="failure",
                    final_reasoning=(
                        "Coordinator retried the resource tool workflow, but execution could not be validated "
                        "within the allowed repair attempts."
                    ),
                    error=last_error,
                )
                return


def run_project_bootstrap_workflow(run_id: str, project_name: str, goal: str):
    plan = project_bootstrap_documents(project_name, goal)
    try:
        for file_spec in plan["files"]:
            append_workflow_step(
                run_id,
                name=f"write {os.path.basename(file_spec['path'])}",
                agent="coder",
                task="write_file",
                details={"path": file_spec["path"]},
            )
            write_result = call_delegate(
                "coder",
                "write_file",
                {"path": file_spec["path"], "content": file_spec["content"]},
            )
            current = get_workflow_run(run_id) or {}
            complete_workflow_step(run_id, len(current.get("steps", [])) - 1, "completed", result=write_result)

        for file_spec in plan["files"]:
            append_workflow_step(
                run_id,
                name=f"verify {os.path.basename(file_spec['path'])}",
                agent="tester",
                task="file_check",
                details={"path": file_spec["path"]},
            )
            verify_result = call_delegate("tester", "file_check", {"path": file_spec["path"]})
            current = get_workflow_run(run_id) or {}
            complete_workflow_step(run_id, len(current.get("steps", [])) - 1, "completed", result=verify_result)

        finish_workflow_run(
            run_id,
            status="completed",
            summary=f"project_bootstrap completed for {plan['base']}",
            final_decision="success",
            final_reasoning=(
                "Coordinator created a lightweight project scaffold first so larger work can stay "
                "anchored to a documented goal, current session notes, and verified files."
            ),
        )
    except Exception as exc:
        current = get_workflow_run(run_id) or {}
        if current.get("steps"):
            complete_workflow_step(
                run_id,
                len(current["steps"]) - 1,
                "failed",
                error=str(exc),
            )
        finish_workflow_run(
            run_id,
            status="failed",
            summary="project_bootstrap failed",
            final_decision="failure",
            final_reasoning=(
                "Coordinator stopped project bootstrap because the scaffold could not be written or "
                "verified safely."
            ),
            error=str(exc),
        )


def probe_agent_health():
    targets = {
        "coder": ["http://coder:8000/health", "http://zeroclaw-coder:8000/health"],
        "deployer": ["http://deployer:8000/health", "http://zeroclaw-deployer:8000/health"],
        "tester": ["http://tester:8000/health", "http://zeroclaw-tester:8000/health"],
    }
    results = {
        "coordinator": {
            "status": "ok",
            "via": "self",
            "detail": {"role": ROLE, "status": "ok"},
        }
    }
    for role, urls in targets.items():
        results[role] = {"status": "unknown", "via": None, "detail": None}
        for url in urls:
            try:
                response = requests.get(url, timeout=3)
                if response.ok:
                    payload = response.json()
                    results[role] = {
                        "status": payload.get("status", "ok"),
                        "via": url,
                        "detail": payload,
                    }
                    break
                results[role] = {"status": "error", "via": url, "detail": response.text[:200]}
            except Exception as exc:
                results[role] = {"status": "unreachable", "via": url, "detail": str(exc)}
    return results


def extract_text_result(payload):
    if isinstance(payload, dict):
        for key in ("response", "result", "text", "message"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return json.dumps(payload)
    if isinstance(payload, str):
        return payload.strip()
    return str(payload)


@app.get("/health")
async def health():
    return {"role": ROLE, "status": "ok"}


@app.get("/audit/delegation")
async def delegation_audit(n: int = 100, tail: bool = True):
    """Return recent delegation audit entries from workspace/logs/delegation.log.

    - `n`: max number of entries to return
    - `tail`: if true return the last `n` entries, else return the first `n`
    """
    path = os.path.join(WORKSPACE_DIR, "logs", "delegation.log")
    if not os.path.exists(path):
        return {"count": 0, "entries": []}

    entries = []
    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except Exception:
                    # best-effort fallback: include raw line
                    entries.append({"raw": line})
    except Exception as e:
        return {"error": f"failed to read audit log: {e}"}

    if tail:
        entries = entries[-n:]
    else:
        entries = entries[:n]

    return {"count": len(entries), "entries": entries}


@app.get("/system/overview")
async def system_overview():
    recent_runs = list_workflow_runs(limit=10)
    models = list_available_models() if ROLE == "coordinator" else {}
    return {
        "role": ROLE,
        "agents": probe_agent_health() if ROLE == "coordinator" else {},
        "models": models,
        "recent_runs": recent_runs,
    }


@app.get("/models")
async def models_list():
    if ROLE != "coordinator":
        return {"error": "model endpoints are only available on coordinator"}
    return list_available_models()


@app.post("/models/pull")
async def models_pull(req: Request):
    if ROLE != "coordinator":
        return {"error": "model endpoints are only available on coordinator"}
    payload = await req.json()
    try:
        return pull_ollama_model(payload.get("model"))
    except Exception as exc:
        return {"error": str(exc)}


@app.post("/models/assign")
async def models_assign(req: Request):
    if ROLE != "coordinator":
        return {"error": "model endpoints are only available on coordinator"}
    payload = await req.json()
    try:
        return assign_role_model(payload.get("role"), payload.get("model"))
    except Exception as exc:
        return {"error": str(exc)}


@app.get("/workflow/runs")
async def workflow_runs(n: int = 20):
    return {"count": min(n, len(load_workflow_runs().get("runs", []))), "runs": list_workflow_runs(limit=n)}


@app.get("/workflow/runs/{run_id}")
async def workflow_run_detail(run_id: str):
    run = get_workflow_run(run_id)
    if not run:
        return {"error": f"workflow run not found: {run_id}"}
    return run


def start_background_workflow(target, *args):
    thread = threading.Thread(target=target, args=args, daemon=True)
    thread.start()


@app.post("/workflow/intake")
async def workflow_intake(req: Request):
    if ROLE != "coordinator":
        return {"error": "workflow endpoints are only available on coordinator"}

    payload = await req.json()
    project_name = (payload.get("project_name") or "New Project").strip()
    goal = (payload.get("goal") or "Define a working first slice.").strip()
    project_path = payload.get("project_path")
    prompt = payload.get("prompt")
    template = (payload.get("template") or "generic").strip()
    background = bool(payload.get("background", True))

    run = create_pipeline_run(project_name, goal, project_path=project_path, prompt=prompt, template=template, autonomous=bool(payload.get("autonomous", True)))
    if background:
        start_background_workflow(run_pipeline_intake_stage, run["run_id"])
        return {"run_id": run["run_id"], "status": "queued", "workflow": "project_pipeline", "stage": "intake"}

    run_pipeline_intake_stage(run["run_id"])
    return get_workflow_run(run["run_id"])


@app.post("/workflow/runs/{run_id}/inspect")
async def workflow_inspect(run_id: str, req: Request):
    if ROLE != "coordinator":
        return {"error": "model endpoints are only available on coordinator"}
    payload = await req.json()
    background = bool(payload.get("background", True))
    if background:
        start_background_workflow(run_pipeline_inspect_stage, run_id)
        return {"run_id": run_id, "status": "queued", "workflow": "project_pipeline", "stage": "inspect"}

    run_pipeline_inspect_stage(run_id)
    return get_workflow_run(run_id)


@app.post("/workflow/runs/{run_id}/implement")
async def workflow_implement(run_id: str, req: Request):
    if ROLE != "coordinator":
        return {"error": "workflow endpoints are only available on coordinator"}
    payload = await req.json()
    background = bool(payload.get("background", True))
    if background:
        start_background_workflow(run_pipeline_implement_stage, run_id)
        return {"run_id": run_id, "status": "queued", "workflow": "project_pipeline", "stage": "implement"}

    run_pipeline_implement_stage(run_id)
    return get_workflow_run(run_id)


@app.post("/workflow/runs/{run_id}/verify")
async def workflow_verify(run_id: str, req: Request):
    if ROLE != "coordinator":
        return {"error": "workflow endpoints are only available on coordinator"}
    payload = await req.json()
    background = bool(payload.get("background", True))
    if background:
        start_background_workflow(verify_pipeline_run, run_id)
        return {"run_id": run_id, "status": "queued", "workflow": "project_pipeline", "stage": "verify"}

    verify_pipeline_run(run_id)
    return get_workflow_run(run_id)


@app.post("/workflow/runs/{run_id}/repair")
async def workflow_repair(run_id: str, req: Request):
    if ROLE != "coordinator":
        return {"error": "workflow endpoints are only available on coordinator"}
    payload = await req.json()
    background = bool(payload.get("background", True))
    if background:
        start_background_workflow(repair_pipeline_run, run_id)
        return {"run_id": run_id, "status": "queued", "workflow": "project_pipeline", "stage": "repair"}

    repair_pipeline_run(run_id)
    return get_workflow_run(run_id)


@app.post("/workflow/hello")
async def workflow_hello(req: Request):
    if ROLE != "coordinator":
        return {"error": "workflow endpoints are only available on coordinator"}

    payload = await req.json()
    path = (payload.get("path") or "e2e/hello.txt").strip()
    content = payload.get("content") or "Hello, world! (from coder)"
    background = bool(payload.get("background", True))
    run = create_workflow_run("hello_world", {"path": path, "content": content})

    if background:
        thread = threading.Thread(target=run_hello_workflow, args=(run["run_id"], path, content), daemon=True)
        thread.start()
        return {"run_id": run["run_id"], "status": "queued", "workflow": "hello_world"}

    run_hello_workflow(run["run_id"], path, content)
    return get_workflow_run(run["run_id"])


@app.post("/workflow/project")
async def workflow_project(req: Request):
    if ROLE != "coordinator":
        return {"error": "workflow endpoints are only available on coordinator"}

    payload = await req.json()
    project_name = (payload.get("project_name") or "New Project").strip()
    goal = (payload.get("goal") or "Define a working first slice.").strip()
    background = bool(payload.get("background", True))
    run = create_workflow_run(
        "project_bootstrap",
        {"project_name": project_name, "goal": goal},
    )

    if background:
        thread = threading.Thread(
            target=run_project_bootstrap_workflow,
            args=(run["run_id"], project_name, goal),
            daemon=True,
        )
        thread.start()
        return {"run_id": run["run_id"], "status": "queued", "workflow": "project_bootstrap"}

    run_project_bootstrap_workflow(run["run_id"], project_name, goal)
    return get_workflow_run(run["run_id"])


@app.post("/workflow/resource-check")
async def workflow_resource_check(req: Request):
    if ROLE != "coordinator":
        return {"error": "workflow endpoints are only available on coordinator"}

    payload = await req.json()
    project_name = (payload.get("project_name") or "Resource Check").strip()
    goal = (payload.get("goal") or "Capture the current environment and produce a verified resource report.").strip()
    project_path = payload.get("project_path")
    background = bool(payload.get("background", True))
    run = create_pipeline_run(
        project_name,
        goal,
        project_path=project_path,
        prompt=payload.get("prompt"),
        template="resource-check",
        autonomous=background,
    )

    if background:
        start_background_workflow(run_resource_check_pipeline, run["run_id"])
        return {"run_id": run["run_id"], "status": "queued", "workflow": "project_pipeline", "template": "resource-check"}

    run_resource_check_pipeline(run["run_id"])
    return get_workflow_run(run["run_id"])


@app.post("/workflow/repo-diagnostics")
async def workflow_repo_diagnostics(req: Request):
    if ROLE != "coordinator":
        return {"error": "workflow endpoints are only available on coordinator"}

    payload = await req.json()
    project_name = (payload.get("project_name") or "Repo Diagnostics").strip()
    goal = (payload.get("goal") or "Inspect a repo and produce a verified multi-language diagnostics bundle.").strip()
    project_path = payload.get("project_path")
    background = bool(payload.get("background", True))
    run = create_pipeline_run(
        project_name,
        goal,
        project_path=project_path,
        prompt=payload.get("prompt"),
        template="repo-diagnostics",
        autonomous=background,
    )

    if background:
        start_background_workflow(run_repo_diagnostics_pipeline, run["run_id"])
        return {"run_id": run["run_id"], "status": "queued", "workflow": "project_pipeline", "template": "repo-diagnostics"}

    run_repo_diagnostics_pipeline(run["run_id"])
    return get_workflow_run(run["run_id"])


@app.post("/workflow/build-resource-tool")
async def workflow_build_resource_tool(req: Request):
    if ROLE != "coordinator":
        return {"error": "workflow endpoints are only available on coordinator"}

    payload = await req.json()
    project_name = (payload.get("project_name") or "Resource Tool").strip()
    goal = (payload.get("goal") or "Create a working disk and memory inspection utility.").strip()
    background = bool(payload.get("background", True))
    run = create_workflow_run(
        "build_resource_tool",
        {"project_name": project_name, "goal": goal},
    )

    if background:
        thread = threading.Thread(
            target=run_build_resource_tool_workflow,
            args=(run["run_id"], project_name, goal),
            daemon=True,
        )
        thread.start()
        return {"run_id": run["run_id"], "status": "queued", "workflow": "build_resource_tool"}

    run_build_resource_tool_workflow(run["run_id"], project_name, goal)
    return get_workflow_run(run["run_id"])


def handle_task_payload(payload: dict):
    task = payload.get("task")
    task_id = int(time.time())
    entry = {"time": time.time(), "role": ROLE, "task": task, "payload": payload}
    append_log(entry)

    # Simple coordinator behavior: if asked, call ZeroClaw gateway for a completion
    if ROLE == "coordinator" and task == "generate_plan":
        prompt = payload.get("prompt", "Create a short plan.")
        mode = (payload.get("mode") or "plan").strip().lower()
        direct_answer = maybe_answer_from_local_knowledge(prompt)
        if direct_answer:
            append_log({"time": time.time(), "role": ROLE, "result": direct_answer, "source": "local_knowledge"})
            return {"task_id": task_id, "result": direct_answer}

        grounded_prompt = build_grounded_coordinator_prompt(prompt, mode=mode)
        model = get_role_specific_model(ROLE)
        endpoint, models_list, lookup_errors = fetch_ollama_models()
        chosen_model = choose_model(model, models_list)

        if not chosen_model:
            if endpoint:
                result = (
                    "ollama error: no models installed. "
                    "Run `ollama pull <model>` inside the Ollama container or set "
                    "`ZEROCLAW_MODEL` to an installed tag."
                )
            else:
                result = (
                    "ollama error: unable to reach any Ollama endpoint. "
                    f"Tried: {', '.join(ollama_endpoints())}. "
                    f"Lookup errors: {' | '.join(lookup_errors) if lookup_errors else 'none'}"
                )
            append_log({"time": time.time(), "role": ROLE, "result": result})
            return {"task_id": task_id, "result": result}

        # Prefer Ollama (containerized brain); fall back to ZeroClaw gateway if Ollama fails.
        try:
            req_payload = {
                "model": chosen_model,
                "prompt": grounded_prompt,
                "stream": False,
                "options": {"num_predict": 2048},
            }
            ollama_base = endpoint or OLLAMA_URL.rstrip("/")
            append_log(
                {
                    "time": time.time(),
                    "role": ROLE,
                    "to": "ollama",
                    "endpoint": ollama_base,
                    "configured_model": model,
                    "chosen_model": chosen_model,
                    "payload": req_payload,
                }
            )
            resp = requests.post(
                f"{ollama_base}/api/generate",
                json=req_payload,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            if resp.ok:
                # try structured JSON first
                try:
                    parsed = resp.json()
                except Exception:
                    parsed = None

                # parsed may be None; try to extract JSON from raw text if needed
                if not isinstance(parsed, dict):
                    parsed = parse_json_from_text(resp.text)

                if isinstance(parsed, dict) and (parsed.get("action") or parsed.get("actions")) or isinstance(parsed, list):
                    # model returned JSON actions — execute them
                    exec_res = execute_parsed_actions(parsed)
                    append_log({
                        "time": time.time(),
                        "role": ROLE,
                        "to": "action_executor",
                        "parsed": parsed,
                        "exec_res": exec_res,
                    })
                    result = json.dumps(exec_res)
                else:
                    try:
                        result = extract_text_result(parsed or resp.text)
                    except Exception:
                        result = extract_text_result(resp.text)
            else:
                result = f"ollama error: {resp.status_code} {resp.text}"
        except requests.Timeout:
            result = (
                f"ollama timeout after {REQUEST_TIMEOUT_SECONDS}s while generating a response "
                f"with model '{chosen_model}'."
            )
        except Exception as e:
            try:
                req2 = {
                    "model": chosen_model,
                    "prompt": grounded_prompt,
                    "stream": False,
                    "options": {"num_predict": 2048},
                }
                append_log({"time": time.time(), "role": ROLE, "to": "zeroclaw", "payload": req2})
                resp2 = requests.post(
                    f"{ZEROWEB}/api/generate",
                    json=req2,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                try:
                    parsed2 = resp2.json()
                except Exception:
                    parsed2 = None

                if not isinstance(parsed2, dict):
                    parsed2 = parse_json_from_text(resp2.text)

                if isinstance(parsed2, dict) and parsed2.get("action"):
                    action = parsed2.get("action")
                    params = parsed2.get("params", {}) or {}
                    result_obj = perform_action(action, params)
                    result = json.dumps(result_obj)
                else:
                    try:
                        result = extract_text_result(parsed2 or resp2.text)
                    except Exception:
                        result = extract_text_result(resp2.text)
            except Exception as e2:
                result = f"ollama failed: {e}; zeroclaw fallback failed: {e2}"

        append_log({"time": time.time(), "role": ROLE, "result": result})
        return {"task_id": task_id, "result": result}

    # Allow direct action execution for testing/fallback: POST {task: "run_action", action: "http_check", params: {...}}
    if ROLE == "coordinator" and task == "run_action":
        action = payload.get("action")
        params = payload.get("params", {}) or {}
        result_obj = perform_action(action, params)
        append_log({"time": time.time(), "role": ROLE, "action": action, "params": params, "result": result_obj})
        return {"task_id": task_id, "result": result_obj}

    # Coder: write a file to workspace
    if ROLE == "coder" and task == "write_file":
        path = payload.get("path")
        content = payload.get("content","")
        full = os.path.join(WORKSPACE_DIR, path.lstrip("/"))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(content)
        return {"task_id": task_id, "written": full}

    # Deployer: run a simple deploy action (placeholder)
    if ROLE == "deployer" and task == "deploy_compose":
        # For safety, we don't run docker commands here automatically in this scaffold.
        return {"task_id": task_id, "status": "deployer placeholder - mount docker socket to enable"}

    # Tester: simple HTTP check
    if ROLE == "tester" and task == "http_check":
        url = payload.get("url")
        try:
            r = requests.get(url, timeout=10)
            return {"task_id": task_id, "status": r.status_code}
        except Exception as e:
            return {"task_id": task_id, "error": str(e)}

    # Tester: file existence check (used by Hello-World E2E)
    if ROLE == "tester" and task == "file_check":
        path = (payload.get("path") or "").strip()
        if not path:
            return {"task_id": task_id, "error": "missing path"}
        full = os.path.join(WORKSPACE_DIR, path.lstrip("/"))
        if os.path.exists(full):
            return {"task_id": task_id, "status": "exists", "path": full}
        else:
            return {"task_id": task_id, "status": "missing", "path": full}

    return {"task_id": task_id, "status": "noop"}


@app.post("/run_task")
async def run_task(req: Request):
    payload = await req.json()
    return handle_task_payload(payload)


@app.post("/task")
async def task(req: Request):
    payload = await req.json()
    return handle_task_payload(payload)
