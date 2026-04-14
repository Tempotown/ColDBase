import os
import sys
import importlib
import json
import time


def reload_app_with_workspace(ws_path):
    os.environ["WORKSPACE_DIR"] = str(ws_path)
    import importlib.util
    agents_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_path = os.path.join(agents_dir, "app.py")
    spec = importlib.util.spec_from_file_location("agents_app", app_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["agents_app"] = module
    spec.loader.exec_module(module)
    return module


def test_audit_schema_validation_good(tmp_path):
    app = reload_app_with_workspace(tmp_path)
    entry = {
        "time": time.time(),
        "request_id": 99999,
        "from": "coordinator",
        "to": "coder",
        "task": "write_file",
        "decision": "delivered",
    }
    app.audit_delegation(entry)
    log_file = tmp_path / "logs" / "delegation.log"
    assert log_file.exists()
    last = json.loads(log_file.read_text().strip().splitlines()[-1])
    assert last.get("_schema_valid") is True
    assert last.get("time_iso") is not None


def test_audit_schema_validation_bad(tmp_path):
    app = reload_app_with_workspace(tmp_path)
    # missing required fields: request_id
    entry = {
        "time": time.time(),
        "from": "coordinator",
        "to": "coder",
        "task": "write_file",
        "decision": "delivered",
    }
    app.audit_delegation(entry)
    log_file = tmp_path / "logs" / "delegation.log"
    last = json.loads(log_file.read_text().strip().splitlines()[-1])
    assert last.get("_schema_valid") is False
    assert last.get("_schema_errors")