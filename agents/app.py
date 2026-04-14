from fastapi import FastAPI, Request
import os
import json
import time
import requests
from typing import List, Optional, Tuple

app = FastAPI()

ROLE = os.environ.get("ROLE", "agent")
WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR", "/workspace")
MEMORY_FILE = os.path.join(WORKSPACE_DIR, "memory", "agent_state.json")
ZEROWEB = os.environ.get("ZEROCLAW_URL", "http://zeroclaw:42617")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama-brain:11434")
ZEROCLAW_API_ENDPOINT = os.environ.get("ZEROCLAW_API_ENDPOINT", "").strip()


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


@app.get("/health")
async def health():
    return {"role": ROLE, "status": "ok"}


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
        model = (os.environ.get("ZEROCLAW_MODEL") or "auto").strip() or "auto"
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
            req_payload = {"model": chosen_model, "prompt": prompt, "max_tokens": 128}
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
            resp = requests.post(f"{ollama_base}/api/generate", json=req_payload, timeout=30)
            if resp.ok:
                try:
                    result = resp.json()
                except Exception:
                    result = resp.text
            else:
                result = f"ollama error: {resp.status_code} {resp.text}"
        except Exception as e:
            try:
                req2 = {"model": chosen_model, "prompt": prompt, "max_tokens": 128}
                append_log({"time": time.time(), "role": ROLE, "to": "zeroclaw", "payload": req2})
                resp2 = requests.post(f"{ZEROWEB}/api/generate", json=req2, timeout=30)
                try:
                    result = resp2.json()
                except Exception:
                    result = resp2.text
            except Exception as e2:
                result = f"ollama failed: {e}; zeroclaw fallback failed: {e2}"

        append_log({"time": time.time(), "role": ROLE, "result": result})
        return {"task_id": task_id, "result": result}

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

    return {"task_id": task_id, "status": "noop"}
