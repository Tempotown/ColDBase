import os
import sys
import importlib


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


def test_deploy_compose_valid(tmp_path):
    app = reload_app_with_workspace(tmp_path)
    compose = """
version: '3'
services:
  web:
    image: nginx:alpine
"""
    payload = {"compose": compose}
    ok, reason = app.validate_delegation("deployer", "deploy_compose", payload)
    assert ok is True
    assert reason is None


def test_deploy_compose_missing_services(tmp_path):
    app = reload_app_with_workspace(tmp_path)
    compose = """
version: '3'
# no services
"""
    payload = {"compose": compose}
    ok, reason = app.validate_delegation("deployer", "deploy_compose", payload)
    assert ok is False
    assert "services" in reason.lower() or "missing" in reason.lower()


def test_deploy_compose_privileged_rejected(tmp_path):
    app = reload_app_with_workspace(tmp_path)
    compose = """
version: '3'
services:
  app:
    image: busybox
    privileged: true
"""
    payload = {"compose": compose}
    ok, reason = app.validate_delegation("deployer", "deploy_compose", payload)
    assert ok is False
    assert "privileged" in reason.lower()


def test_deploy_compose_docker_socket_rejected(tmp_path):
    app = reload_app_with_workspace(tmp_path)
    compose = """
version: '3'
services:
  app:
    image: busybox
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
"""
    payload = {"compose": compose}
    ok, reason = app.validate_delegation("deployer", "deploy_compose", payload)
    assert ok is False
    rl = (reason or "").lower()
    assert "/var/run/docker.sock" in (reason or "") or "docker" in rl or "socket" in rl or "banned pattern" in rl


def test_deploy_compose_invalid_yaml(tmp_path):
    app = reload_app_with_workspace(tmp_path)
    compose = "::not yaml::"
    payload = {"compose": compose}
    ok, reason = app.validate_delegation("deployer", "deploy_compose", payload)
    assert ok is False
    rl = (reason or "").lower()
    assert "yaml" in rl or "invalid" in rl or "services" in rl or "missing" in rl
