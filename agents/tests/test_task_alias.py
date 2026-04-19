import os
import sys

def reload_app_with_env(ws_path, role="coordinator"):
    os.environ["WORKSPACE_DIR"] = str(ws_path)
    os.environ["ROLE"] = role
    import importlib.util

    agents_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_path = os.path.join(agents_dir, "app.py")
    spec = importlib.util.spec_from_file_location("agents_app_task_alias", app_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["agents_app_task_alias"] = module
    spec.loader.exec_module(module)
    return module


def test_task_alias_matches_run_task_behavior(tmp_path, monkeypatch):
    app_module = reload_app_with_env(tmp_path, role="coordinator")

    def fake_perform_action(action, params):
        return {"ok": True, "action": action, "params": params}

    monkeypatch.setattr(app_module, "perform_action", fake_perform_action)
    payload = {
        "task": "run_action",
        "action": "delegate",
        "params": {
            "role": "coder",
            "task": "write_file",
            "payload": {"path": "e2e/hello.txt", "content": "Hello, world!"},
        },
    }

    routes = {route.path for route in app_module.app.routes}
    assert "/task" in routes
    assert "/run_task" in routes

    body = app_module.handle_task_payload(payload)
    assert body["result"]["ok"] is True
    assert body["result"]["action"] == "delegate"
    assert body["result"]["params"]["role"] == "coder"
