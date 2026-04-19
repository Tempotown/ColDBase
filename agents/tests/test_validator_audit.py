import os
import sys
import importlib
import json
import time


def reload_app_with_workspace(ws_path):
    os.environ["WORKSPACE_DIR"] = str(ws_path)
    # load agents/app.py directly as a module to avoid package import issues in tests
    import importlib.util
    agents_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_path = os.path.join(agents_dir, "app.py")
    spec = importlib.util.spec_from_file_location("agents_app", app_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["agents_app"] = module
    spec.loader.exec_module(module)
    return module


def test_redact_and_hash_payload(tmp_path):
    app = reload_app_with_workspace(tmp_path)
    payload = {"content": "very secret content", "note": "public note", "api_key": "abc123"}
    redacted, hashes = app.redact_and_hash_payload(payload)
    assert isinstance(redacted, dict)
    assert isinstance(hashes, dict)
    assert "content" in redacted and redacted["content"].startswith("<redacted:")
    assert "api_key" in hashes and len(hashes["api_key"]) == 16
    assert redacted["note"] == "public note"


def test_validate_write_file_ok(tmp_path):
    app = reload_app_with_workspace(tmp_path)
    payload = {"path": "e2e/hello.txt", "content": "hello world"}
    ok, reason = app.validate_delegation("coder", "write_file", payload)
    assert ok is True
    assert reason is None


def test_validate_write_file_bad_path(tmp_path):
    app = reload_app_with_workspace(tmp_path)
    payload = {"path": "/etc/passwd", "content": "no"}
    ok, reason = app.validate_delegation("coder", "write_file", payload)
    assert ok is False
    assert "invalid path" in reason


def test_validate_write_file_binary_rejected(tmp_path):
    app = reload_app_with_workspace(tmp_path)
    payload = {"path": "e2e/bin.txt", "content": "\x00\x01\x02"}
    ok, reason = app.validate_delegation("coder", "write_file", payload)
    assert ok is False
    assert "binary content" in reason


def test_validate_write_file_python_ok(tmp_path):
    app = reload_app_with_workspace(tmp_path)
    payload = {"path": "projects/tool/resource_tool.py", "content": "print('ok')\n"}
    ok, reason = app.validate_delegation("coder", "write_file", payload)
    assert ok is True
    assert reason is None


def test_validate_http_check_private_ip(tmp_path):
    app = reload_app_with_workspace(tmp_path)
    payload = {"url": "http://192.168.1.5"}
    ok, reason = app.validate_delegation("tester", "http_check", payload)
    assert ok is False
    assert "private" in reason or "not allowed" in reason


def test_validate_file_check_ok(tmp_path):
    app = reload_app_with_workspace(tmp_path)
    payload = {"path": "e2e/hello.txt"}
    ok, reason = app.validate_delegation("tester", "file_check", payload)
    assert ok is True
    assert reason is None


def test_audit_delegation_writes_log(tmp_path):
    app = reload_app_with_workspace(tmp_path)
    entry = {
        "time": time.time(),
        "request_id": 12345,
        "from": "coordinator",
        "to": "coder",
        "task": "write_file",
        "decision": "test",
    }
    app.audit_delegation(entry)
    log_file = tmp_path / "logs" / "delegation.log"
    assert log_file.exists()
    lines = log_file.read_text().strip().splitlines()
    assert len(lines) >= 1
    last = json.loads(lines[-1])
    assert last.get("request_id") == 12345
