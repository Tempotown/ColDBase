import requests
import time
import pytest


def wait_for(url, timeout=60):
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                return r
        except Exception:
            pass
        time.sleep(1)
    pytest.fail(f"Service at {url} not healthy")


def test_coordinator_health():
    r = wait_for("http://localhost:8001/health", timeout=60)
    j = r.json()
    assert j.get("status") == "ok"


def test_audit_endpoint_returns_json():
    # audit endpoint may be empty but should respond with JSON schema
    r = requests.get("http://localhost:8001/audit/delegation?n=5", timeout=10)
    assert r.status_code == 200
    js = r.json()
    assert isinstance(js, dict)
    assert "count" in js
