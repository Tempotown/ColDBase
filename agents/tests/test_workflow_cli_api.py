import os
import sys


def reload_app_with_env(ws_path, role="coordinator"):
    os.environ["WORKSPACE_DIR"] = str(ws_path)
    os.environ["ROLE"] = role
    import importlib.util

    agents_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    app_path = os.path.join(agents_dir, "app.py")
    spec = importlib.util.spec_from_file_location("agents_app_workflow", app_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["agents_app_workflow"] = module
    spec.loader.exec_module(module)
    return module


def make_fake_writer(root):
    def fake_call_delegate(role, task, payload):
        path = payload["path"]
        full = root / path
        full.parent.mkdir(parents=True, exist_ok=True)
        if role == "coder":
            full.write_text(payload.get("content", ""))
            return {"written": f"/workspace/{path}"}
        if role == "tester":
            return {"status": "exists" if full.exists() else "missing", "path": f"/workspace/{path}"}
        raise AssertionError("unexpected delegate")

    return fake_call_delegate


def test_run_hello_workflow_records_success(tmp_path, monkeypatch):
    app = reload_app_with_env(tmp_path)
    run = app.create_workflow_run("hello_world", {"path": "e2e/hello.txt", "content": "Hello"})

    def fake_call_delegate(role, task, payload):
        if role == "coder":
            return {"written": f"/workspace/{payload['path']}"}
        if role == "tester":
            return {"status": "exists", "path": f"/workspace/{payload['path']}"}
        raise AssertionError("unexpected delegate")

    monkeypatch.setattr(app, "call_delegate", fake_call_delegate)

    app.run_hello_workflow(run["run_id"], "e2e/hello.txt", "Hello")
    updated = app.get_workflow_run(run["run_id"])

    assert updated["status"] == "completed"
    assert updated["final_decision"] == "success"
    assert len(updated["steps"]) == 2
    assert updated["steps"][0]["status"] == "completed"
    assert updated["steps"][1]["status"] == "completed"


def test_run_hello_workflow_records_failure(tmp_path, monkeypatch):
    app = reload_app_with_env(tmp_path)
    run = app.create_workflow_run("hello_world", {"path": "e2e/hello.txt", "content": "Hello"})

    def fake_call_delegate(role, task, payload):
        raise RuntimeError("tester rejected file")

    monkeypatch.setattr(app, "call_delegate", fake_call_delegate)

    app.run_hello_workflow(run["run_id"], "e2e/hello.txt", "Hello")
    updated = app.get_workflow_run(run["run_id"])

    assert updated["status"] == "failed"
    assert updated["final_decision"] == "failure"
    assert updated["error"] == "tester rejected file"


def test_run_project_bootstrap_records_success(tmp_path, monkeypatch):
    app = reload_app_with_env(tmp_path)
    run = app.create_workflow_run(
        "project_bootstrap",
        {"project_name": "Complex POC", "goal": "Build a reliable kickoff scaffold"},
    )

    def fake_call_delegate(role, task, payload):
        if role == "coder":
            return {"written": f"/workspace/{payload['path']}"}
        if role == "tester":
            return {"status": "exists", "path": f"/workspace/{payload['path']}"}
        raise AssertionError("unexpected delegate")

    monkeypatch.setattr(app, "call_delegate", fake_call_delegate)

    app.run_project_bootstrap_workflow(
        run["run_id"],
        "Complex POC",
        "Build a reliable kickoff scaffold",
    )
    updated = app.get_workflow_run(run["run_id"])

    assert updated["status"] == "completed"
    assert updated["workflow"] == "project_bootstrap"
    assert updated["final_decision"] == "success"
    assert len(updated["steps"]) == 4
    assert all(step["status"] == "completed" for step in updated["steps"])


def test_run_resource_check_records_success(tmp_path, monkeypatch):
    app = reload_app_with_env(tmp_path)
    run = app.create_workflow_run("resource_check", {"path": "reports/system-resource-report.md"})

    monkeypatch.setattr(
        app,
        "collect_resource_snapshot",
        lambda: {
            "captured_at": "2026-04-15T00:00:00Z",
            "workspace_dir": "/workspace",
            "disk": {"total_bytes": 100, "used_bytes": 40, "free_bytes": 60},
            "memory": {"mem_total_kb": 1024, "mem_available_kb": 512, "mem_used_kb": 512},
        },
    )

    def fake_call_delegate(role, task, payload):
        if role == "coder":
            return {"written": f"/workspace/{payload['path']}"}
        if role == "tester":
            return {"status": "exists", "path": f"/workspace/{payload['path']}"}
        raise AssertionError("unexpected delegate")

    monkeypatch.setattr(app, "call_delegate", fake_call_delegate)

    app.run_resource_check_workflow(run["run_id"], "reports/system-resource-report.md")
    updated = app.get_workflow_run(run["run_id"])

    assert updated["status"] == "completed"
    assert updated["workflow"] == "resource_check"
    assert updated["final_decision"] == "success"
    assert len(updated["steps"]) == 3


def test_run_build_resource_tool_retries_then_succeeds(tmp_path, monkeypatch):
    app = reload_app_with_env(tmp_path)
    run = app.create_workflow_run(
        "build_resource_tool",
        {"project_name": "Disk Check Tool", "goal": "Create a working tool"},
    )
    calls = {"execute": 0}

    def fake_call_delegate(role, task, payload):
        if role == "coder":
            return {"written": f"/workspace/{payload['path']}"}
        if role == "tester":
            return {"status": "exists", "path": f"/workspace/{payload['path']}"}
        raise AssertionError("unexpected delegate")

    def fake_execute(path):
        calls["execute"] += 1
        if calls["execute"] == 1:
            raise RuntimeError("json parse failure")
        return {
            "disk": {"total_bytes": 1, "used_bytes": 1, "free_bytes": 0},
            "memory": {"mem_total_kb": 1, "mem_available_kb": 1, "mem_used_kb": 0},
        }

    monkeypatch.setattr(app, "call_delegate", fake_call_delegate)
    monkeypatch.setattr(app, "execute_workspace_python", fake_execute)

    app.run_build_resource_tool_workflow(run["run_id"], "Disk Check Tool", "Create a working tool", max_attempts=3)
    updated = app.get_workflow_run(run["run_id"])

    assert updated["status"] == "completed"
    assert updated["final_decision"] == "success"
    assert calls["execute"] == 2
    assert any(step["status"] == "failed" for step in updated["steps"])


def test_pipeline_intake_creates_generic_project(tmp_path, monkeypatch):
    app = reload_app_with_env(tmp_path)
    run = app.create_pipeline_run("Generic App", "Build a baseline", template="generic")
    monkeypatch.setattr(app, "call_delegate", make_fake_writer(tmp_path))

    app.run_pipeline_intake_stage(run["run_id"])
    updated = app.get_workflow_run(run["run_id"])

    assert updated["family"] == "intake_inspect_verify_repair"
    assert updated["status"] == "pending"
    assert updated["pipeline"]["current_stage"] == "intake"
    assert updated["pipeline"]["next_stage"] == "inspect"
    assert "intake" in updated["pipeline"]["completed_stages"]


def test_pipeline_inspect_detects_python_project(tmp_path):
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)
    (project_root / "main.py").write_text("print('ok')\n")
    app = reload_app_with_env(tmp_path)
    run = app.create_pipeline_run("Demo", "Inspect a python project", project_path="projects/demo", template="generic")

    app.run_pipeline_inspect_stage(run["run_id"])
    updated = app.get_workflow_run(run["run_id"])

    assert updated["inspect"]["project_root"] == "projects/demo"
    assert "python" in updated["inspect"]["languages"]
    assert updated["inspect"]["verify_commands"]


def test_resource_check_pipeline_uses_generic_family(tmp_path, monkeypatch):
    app = reload_app_with_env(tmp_path)
    run = app.create_pipeline_run("Resource Check", "Report system resources", template="resource-check")
    monkeypatch.setattr(app, "call_delegate", make_fake_writer(tmp_path))
    monkeypatch.setattr(
        app,
        "execute_workspace_python",
        lambda path: {
            "disk": {"total_bytes": 1, "used_bytes": 1, "free_bytes": 0},
            "memory": {"mem_total_kb": 1, "mem_available_kb": 1, "mem_used_kb": 0},
        },
    )

    app.run_resource_check_pipeline(run["run_id"])
    updated = app.get_workflow_run(run["run_id"])

    assert updated["workflow"] == "project_pipeline"
    assert updated["template"] == "resource-check"
    assert updated["status"] == "completed"
    assert updated["final_decision"] == "success"
    assert "repair" in updated["pipeline"]["completed_stages"]
    assert "verify" in updated["pipeline"]["completed_stages"]


def test_repo_diagnostics_pipeline_uses_generic_family(tmp_path, monkeypatch):
    repo_root = tmp_path / "projects" / "polyglot"
    repo_root.mkdir(parents=True)
    (repo_root / "main.py").write_text("print('ok')\n")
    (repo_root / "package.json").write_text('{"scripts":{"test":"echo ok"}}\n')
    (repo_root / "Cargo.toml").write_text("[package]\nname = \"demo\"\nversion = \"0.1.0\"\n")

    app = reload_app_with_env(tmp_path)
    run = app.create_pipeline_run(
        "Polyglot Repo",
        "Inspect and bundle diagnostics",
        project_path="projects/polyglot",
        template="repo-diagnostics",
    )
    monkeypatch.setattr(app, "call_delegate", make_fake_writer(tmp_path))

    app.run_repo_diagnostics_pipeline(run["run_id"])
    updated = app.get_workflow_run(run["run_id"])

    assert updated["workflow"] == "project_pipeline"
    assert updated["template"] == "repo-diagnostics"
    assert updated["status"] == "completed"
    assert updated["final_decision"] == "success"
    assert "python" in updated["inspect"]["languages"]
    assert "javascript" in updated["inspect"]["languages"]
    assert "rust" in updated["inspect"]["languages"]
    assert "repair" in updated["pipeline"]["completed_stages"]
    assert "verify" in updated["pipeline"]["completed_stages"]


def test_local_knowledge_answers_agent_roles(tmp_path):
    app = reload_app_with_env(tmp_path)
    answer = app.maybe_answer_from_local_knowledge("what are your capabilities and how many agents are there?")
    assert answer is not None
    assert "configured agent roles" in answer
    assert "coordinator" in answer.lower()
    assert "coder" in answer.lower()


def test_grounded_prompt_contains_local_context(tmp_path):
    app = reload_app_with_env(tmp_path)
    run = app.create_pipeline_run("Demo", "Inspect a repo", project_path="projects/demo", template="generic")
    prompt = app.build_grounded_coordinator_prompt("how would you help with this project?", mode="plan")
    assert "Local agent roles" in prompt
    assert "Current local workflow capabilities" in prompt
    assert "Recent workflow runs" in prompt
    assert run["run_id"] in prompt


def test_pre_answer_inspection_policy_triggers_for_dig_in_prompt(tmp_path):
    app = reload_app_with_env(tmp_path)
    assert app.prompt_requests_pre_answer_inspection("can you dig in first and inspect the current project?") is True
    assert app.prompt_requests_pre_answer_inspection("what are your capabilities?") is False


def test_grounded_prompt_includes_project_inspection_context(tmp_path):
    project_root = tmp_path / "projects" / "demo"
    project_root.mkdir(parents=True)
    (project_root / "main.py").write_text("print('ok')\n")

    app = reload_app_with_env(tmp_path)
    app.create_pipeline_run("Demo", "Inspect a repo", project_path="projects/demo", template="generic")
    prompt = app.build_grounded_coordinator_prompt(
        "please dig in first and inspect the current project before answering",
        mode="chat",
    )

    assert "Pre-answer inspection context" in prompt
    assert "projects/demo" in prompt
    assert "python" in prompt.lower()


def test_model_assignment_overrides_role_model(tmp_path):
    app = reload_app_with_env(tmp_path)
    app.assign_role_model("coordinator", "phi4-mini")
    assert app.get_role_specific_model("coordinator") == "phi4-mini"


def test_list_available_models_includes_assignments(tmp_path, monkeypatch):
    app = reload_app_with_env(tmp_path)
    app.assign_role_model("tester", "tinyllama")
    monkeypatch.setattr(app, "fetch_ollama_models", lambda: ("http://ollama:11434", ["tinyllama", "phi4-mini"], []))
    data = app.list_available_models()
    assert data["installed"] == ["tinyllama", "phi4-mini"]
    assert data["assignments"]["tester"] == "tinyllama"
