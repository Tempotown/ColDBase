from fastapi import FastAPI, Request
import os
import json
import time
import requests
from typing import List, Optional, Tuple
import re
import hashlib
import socket
from datetime import datetime
try:
    import yaml
except Exception:
    yaml = None

app = FastAPI()

ROLE = os.environ.get("ROLE", "agent")
WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", "/workspace")
MEMORY_FILE = os.path.join(WORKSPACE_DIR, "memory", "agent_state.json")
ZEROWEB = os.environ.get("ZEROCLAW_URL", "http://zeroclaw:42617")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama-brain:11434")
ZEROCLAW_API_ENDPOINT = os.environ.get("ZEROCLAW_API_ENDPOINT", "").strip()
REQUEST_TIMEOUT_SECONDS = int(os.environ.get("ZEROCLAW_TIMEOUT_SECONDS", "300"))


def get_role_specific_model(role: str) -> str:
    role_key = f"ZEROCLAW_MODEL_{role.upper()}"
    return (os.environ.get(role_key) or os.environ.get("ZEROCLAW_MODEL") or "auto").strip()


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


def parse_json_from_text(text: str) -> Optional[dict]:
    if not isinstance(text, str):
        return None
    # Try direct load
    try:
        return json.loads(text)
    except Exception:
        pass
    # Try to find a JSON object substring
    try:
        # Find all candidate JSON objects and return the first that contains an 'action' key
        for m in re.finditer(r"(\{[\s\S]*?\})", text):
            candidate = m.group(1)
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict) and parsed.get("action"):
                    return parsed
            except Exception:
                continue
        # Fallback: try largest match
        m = re.search(r"(\{[\s\S]*\})", text)
        if m:
            candidate = m.group(1)
            return json.loads(candidate)
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
        safe_exts = (".txt", ".md", ".json", ".yaml", ".yml", ".cfg", ".conf", ".ini", ".log")
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


@app.post("/run_task")
async def run_task(req: Request):
    payload = await req.json()
    task = payload.get("task")
    task_id = int(time.time())
    entry = {"time": time.time(), "role": ROLE, "task": task, "payload": payload}
    append_log(entry)

    # Simple coordinator behavior: if asked, call ZeroClaw gateway for a completion
    if ROLE == "coordinator" and task == "generate_plan":
        prompt = payload.get("prompt", "Create a short plan.")
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
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 128},
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
                    "prompt": prompt,
                    "stream": False,
                    "options": {"num_predict": 128},
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
