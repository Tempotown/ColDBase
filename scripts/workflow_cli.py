#!/usr/bin/env python3
"""Interactive CLI for coordinator-owned workflow operations."""

import argparse
import json
import shlex
import sys
import time
import os
import textwrap
import threading

try:
    import readline
    import signal
    
    HISTORY_FILE = os.path.expanduser("~/.coldbase_history")
    if os.path.exists(HISTORY_FILE):
        readline.read_history_file(HISTORY_FILE)
    readline.set_history_length(1000)
    
    # Readline configuration for better UX
    readline.parse_and_bind("tab: complete")
    readline.parse_and_bind("set horizontal-scroll-mode off")
    readline.parse_and_bind("set enable-bracketed-paste on")
    readline.parse_and_bind("set show-all-if-ambiguous on")
    
    def handle_resize(signum, frame):
        """Force readline to realize the terminal size has changed."""
        if hasattr(readline, "set_screen_size"):
            try:
                columns, lines = os.get_terminal_size()
                readline.set_screen_size(lines, columns)
            except (OSError, AttributeError):
                pass
    
    # Handle terminal window resizing
    if hasattr(signal, "SIGWINCH"):
        signal.signal(signal.SIGWINCH, handle_resize)

except ImportError:
    readline = None
    HISTORY_FILE = None

import requests


def parse_args():
    parser = argparse.ArgumentParser(description="ColDBase workflow CLI")
    parser.add_argument("--coordinator-url", default="http://localhost:8001", help="Coordinator base URL")
    parser.add_argument("command", nargs="*", help="Optional one-shot command")
    args, unknown = parser.parse_known_args()
    args.command.extend(unknown)
    return args


def format_request_error(exc, url):
    if isinstance(exc, requests.ConnectionError):
        return (
            f"Coordinator unavailable at {url}. "
            "Start or restart the stack, for example: "
            "`docker compose up -d ollama zeroclaw coordinator coder tester`"
        )
    return f"Request failed: {exc}"


def get_json(url, params=None):
    try:
        response = requests.get(url, params=params, timeout=120)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        raise RuntimeError(format_request_error(exc, url)) from exc


def post_json(url, payload):
    try:
        response = requests.post(url, json=payload, timeout=120)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        raise RuntimeError(format_request_error(exc, url)) from exc


def coordinator_health(base_url):
    try:
        response = requests.get(f"{base_url}/health", timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.RequestException:
        return None


def post_task(base_url, payload):
    last_exc = None
    for endpoint in ("/task", "/run_task"):
        url = f"{base_url}{endpoint}"
        try:
            response = requests.post(url, json=payload, timeout=120)
            if response.status_code == 404:
                continue
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            last_exc = exc
            continue
    raise RuntimeError(format_request_error(last_exc, f"{base_url}/task"))


def coordinator_chat(base_url, message):
    payload = {
        "task": "generate_plan",
        "mode": "chat",
        "prompt": (
            "Respond conversationally and concisely as the coordinator. "
            "You can autonomously trigger workflows. "
            "If a request requires a workflow (intake, resource_check), "
            "use a JSON action 'start_workflow' in your response. "
            "Otherwise, respond with plain text.\n\n"
            f"User: {message}"
        ),
    }
    response = post_task(base_url, payload)
    return response.get("result") if isinstance(response, dict) else response


def coordinator_plan(base_url, message):
    payload = {
        "task": "generate_plan",
        "mode": "plan",
        "prompt": (
            "Create a concise coordinator-led plan for this request. "
            "Focus on stages, verification, and likely next CLI actions.\n\n"
            f"Request: {message}"
        ),
    }
    response = post_task(base_url, payload)
    return response.get("result") if isinstance(response, dict) else response


# ANSI Color codes with readline escape sequences for correct prompt width calculation
CLR_RESET = "\001\033[0m\002"
CLR_BOLD = "\001\033[1m\002"
CLR_DIM = "\001\033[2m\002"
CLR_RED = "\001\033[31m\002"
CLR_GREEN = "\001\033[32m\002"
CLR_YELLOW = "\001\033[33m\002"
CLR_BLUE = "\001\033[34m\002"
CLR_CYAN = "\001\033[36m\002"
CLR_WHITE = "\001\033[37m\002"

def fmt_status(value):
    mapping = {
        "completed": (CLR_GREEN, "OK"),
        "running": (CLR_BLUE, "RUN"),
        "queued": (CLR_YELLOW, "QUEUED"),
        "failed": (CLR_RED, "FAIL"),
        "pending": (CLR_DIM, "PEND"),
        "ok": (CLR_GREEN, "OK"),
        "exists": (CLR_GREEN, "OK"),
        "error": (CLR_RED, "FAIL"),
        "unreachable": (CLR_RED, "DOWN"),
        "unknown": (CLR_DIM, "?"),
    }
    color, label = mapping.get(str(value).lower(), (CLR_WHITE, str(value).upper()))
    return f"{color}{label}{CLR_RESET}"

def colorize(text, color):
    return f"{color}{text}{CLR_RESET}"

def print_header(title):
    print(f"\n{CLR_BOLD}{CLR_CYAN}=== {title} ==={CLR_RESET}")


def render_overview(base_url):
    data = get_json(f"{base_url}/system/overview")
    print_header("Agents")
    for role, item in data.get("agents", {}).items():
        status = fmt_status(item.get("status"))
        print(f"  {colorize(role, CLR_BOLD):<22} {status} \t{item.get('via') or '-'}")
    
    models = data.get("models") or {}
    if models:
        print_header("Models")
        assignments = models.get("assignments") or {}
        for role, model in assignments.items():
            print(f"  {colorize(role, CLR_BOLD):<12} {model}")
        installed = models.get("installed") or []
        if installed:
            print(f"  {colorize('installed', CLR_DIM):<12} {', '.join(installed[:6])}")
    
    print_header("Recent Runs")
    runs = data.get("recent_runs") or []
    if not runs:
        print(f"  {CLR_DIM}none{CLR_RESET}")
        return
    for run in runs[:5]:
        status = fmt_status(run.get("status"))
        summary = run.get("summary") or "-"
        print(f"  {colorize(run['run_id'], CLR_YELLOW):<18} {status} \t{run.get('workflow'):<12}  {summary}")


def print_wrapped(text, indent="  "):
    if not text:
        return
    try:
        width = os.get_terminal_size().columns
    except (AttributeError, OSError):
        width = 80
    wrapper = textwrap.TextWrapper(width=width, initial_indent=indent, subsequent_indent=indent)
    print(wrapper.fill(text))

def render_run(run):
    print_header("Run Details")
    print(f"  {colorize('ID', CLR_BOLD):<12} {colorize(run.get('run_id'), CLR_YELLOW)}")
    print(f"  {colorize('Workflow', CLR_BOLD):<12} {run.get('workflow')}")
    print(f"  {colorize('Status', CLR_BOLD):<12} {fmt_status(run.get('status'))}")
    
    summary = run.get("summary")
    if summary:
        print(f"  {colorize('Summary', CLR_BOLD):<12}")
        print_wrapped(summary, indent="    ")
        
    pipeline = run.get("pipeline") or {}
    if pipeline.get("current_stage") or pipeline.get("next_stage"):
        print(f"  {colorize('Stage', CLR_BOLD):<12} {colorize(pipeline.get('current_stage') or '-', CLR_CYAN)}")


def command_overview(base_url, args):
    render_overview(base_url)


def command_audit(base_url, args):
    limit = 10
    if args and args[0].isdigit():
        limit = int(args[0])
    data = get_json(f"{base_url}/audit/delegation", params={"n": limit, "tail": "true"})
    print_header(f"Audit Log (last {limit})")
    for entry in data.get("entries", []):
        status = fmt_status(entry.get("decision"))
        flow = f"{colorize(entry.get('from'), CLR_BLUE)} -> {colorize(entry.get('to'), CLR_GREEN)}.{colorize(entry.get('task'), CLR_CYAN)}"
        reason = entry.get('reason') or entry.get('result_summary') or '-'
        print(f"  {status} {flow}")
        print_wrapped(reason, indent="    ")


def command_models(base_url, args):
    if not args or args[0] in ("list", "show"):
        data = get_json(f"{base_url}/models")
        print_header("Role Assignments")
        assignments = data.get("assignments") or {}
        for role, model in assignments.items():
            print(f"  {colorize(role, CLR_BLUE):<12} {model}")
        print_header("Installed Models")
        installed = data.get("installed") or []
        if not installed:
            print(f"  {CLR_DIM}none{CLR_RESET}")
        else:
            for model in installed:
                print(f"  {model}")
        return

    sub = args[0]
    if sub == "pull":
        if len(args) < 2:
            raise ValueError("model pull requires a model name")
        result = post_json(f"{base_url}/models/pull", {"model": args[1]})
        if result.get("error"):
            raise RuntimeError(result["error"])
        print(f"PULLED  {result['model']}")
        return

    if sub == "assign":
        if len(args) < 3:
            raise ValueError("model assign requires a role and model")
        result = post_json(f"{base_url}/models/assign", {"role": args[1], "model": args[2]})
        if result.get("error"):
            raise RuntimeError(result["error"])
        print(f"ASSIGNED  {result['role']} -> {result['model']}")
        return

    raise ValueError("model subcommand must be one of: list, show, pull, assign")


def command_help():
    print_header("Available Commands")
    cmds = [
        ("status", "show agent health and recent runs"),
        ("kickoff <name> <path>", "resume/re-run pipeline for existing project"),
        ("last [watch]", "show or watch the most recent run"),
        ("create <name> [goal]", "create and verify a tracked project scaffold"),
        ("run <name> [path] [goal]", "create a generic project pipeline run"),
        ("log [n]", "list recent workflow runs"),
        ("show <id>", "show one workflow run"),
        ("watch <id>", "poll one workflow run until done"),
        ("diag <name> <path> [goal]", "inspect a repo and generate diagnostics"),
        ("check [path] [name] [goal]", "capture environment details and report"),
        ("build <name> [goal]", "build, execute, and repair a utility"),
        ("audit [n]", "show recent delegation decisions"),
        ("model <list|pull|assign>", "manage models and roles"),
        ("chat <text>", "send conversational input to coordinator"),
        ("plan <text>", "ask coordinator for a plan"),
        ("mode [chat|plan|command]", "switch default REPL behavior"),
        ("inspect/verify/repair <id>", "advance a pipeline stage"),
        ("hello", "submit hello_world through coordinator"),
        ("health", "check coordinator availability"),
        ("help", "show this help"),
        ("exit", "leave the shell"),
    ]
    for cmd, desc in cmds:
        print(f"  {colorize(cmd, CLR_BOLD):<28} {desc}")
    
    print_header("REPL Modes")
    modes = [
        ("chat", "plain text is treated as coordinator chat"),
        ("plan", "plain text asks for a coordinator plan"),
        ("command", "plain text must be a structured CLI command"),
    ]
    for mode, desc in modes:
        print(f"  {colorize(mode, CLR_CYAN):<28} {desc}")
    print_header("Shortcuts")
    print(f"  {colorize('/multi', CLR_BOLD):<28} Start multi-line input mode (pasting)")
    print(f"  {colorize('/cmd <command>', CLR_BOLD):<28} Run a command from any mode")
    print(f"  {colorize('/mode <mode>', CLR_BOLD):<28} Switch mode quickly")
    print(f"\n{CLR_DIM}Use arrow keys for history and navigation.{CLR_RESET}")


def dispatch(base_url, tokens):
    if not tokens:
        command_help()
        return
    cmd, rest = tokens[0], tokens[1:]
    
    # New simplified aliases
    if cmd in ("status", "overview"):
        command_overview(base_url, rest)
    elif cmd in ("run", "intake", "kickoff"):
        command_intake(base_url, rest)
    elif cmd == "last":
        command_last(base_url, rest)
    elif cmd == "inspect":
        command_stage(base_url, rest, "inspect")
    elif cmd == "verify":
        command_stage(base_url, rest, "verify")
    elif cmd == "repair":
        command_stage(base_url, rest, "repair")
    elif cmd == "hello":
        command_hello(base_url, rest)
    elif cmd in ("create", "project"):
        command_project(base_url, rest)
    elif cmd in ("check", "resource-check"):
        command_resource_check(base_url, rest)
    elif cmd in ("diag", "repo-diagnostics"):
        command_repo_diagnostics(base_url, rest)
    elif cmd in ("build", "build-resource-tool"):
        command_build_resource_tool(base_url, rest)
    elif cmd in ("log", "runs"):
        command_runs(base_url, rest)
    elif cmd == "show":
        command_show(base_url, rest)
    elif cmd == "watch":
        command_watch(base_url, rest)
    elif cmd == "audit":
        command_audit(base_url, rest)
    elif cmd in ("model", "models"):
        command_models(base_url, rest)
    elif cmd == "chat":
        print_wrapped(coordinator_chat(base_url, " ".join(rest)))
    elif cmd == "plan":
        print_wrapped(coordinator_plan(base_url, " ".join(rest)))
    elif cmd in ("health", "/health"):
        health = coordinator_health(base_url)
        if health:
            print(f"Coordinator OK  {json.dumps(health, sort_keys=True)}")
        else:
            print(f"{CLR_RED}Coordinator DOWN{CLR_RESET}")
    elif cmd == "help":
        command_help()
    else:
        raise ValueError(f"unknown command: {cmd}")


def command_mode(state, args):
    if not args:
        print(f"Mode: {colorize(state['mode'], CLR_CYAN)}")
        return
    target = args[0].lower()
    if target not in ("chat", "plan", "command"):
        raise ValueError("mode must be one of: chat, plan, command")
    state["mode"] = target
    print(f"Mode set to {colorize(target, CLR_CYAN)}")


def command_slash(base_url, state, line):
    if line in ("/help", "/?", "/h"):
        command_help()
        return True
    if line == "/health":
        health = coordinator_health(base_url)
        if health:
            print(f"Coordinator OK  {json.dumps(health, sort_keys=True)}")
        else:
            print(f"{CLR_RED}Coordinator DOWN{CLR_RESET}")
        return True
    if line.startswith("/chat "):
        print_wrapped(coordinator_chat(base_url, line[len("/chat "):].strip()))
        return True
    if line.startswith("/plan "):
        print_wrapped(coordinator_plan(base_url, line[len("/plan "):].strip()))
        return True
    if line.startswith("/cmd "):
        dispatch(base_url, shlex.split(line[len("/cmd "):].strip()))
        return True
    if line.startswith("/mode "):
        command_mode(state, shlex.split(line[len("/mode "):].strip()))
        return True
    if line == "/multi":
        print(f"{CLR_DIM}(Multi-line mode. Type '---' on a new line or Ctrl-D to finish){CLR_RESET}")
        lines = []
        while True:
            try:
                sub = input(f"{CLR_DIM}.. {CLR_RESET}").rstrip()
                if sub == "---":
                    break
                lines.append(sub)
            except EOFError:
                break
        if lines:
            state["_pending_multi"] = "\n".join(lines)
            print(f"{CLR_DIM}(Captured {len(lines)} lines. Press Enter to submit or type more.){CLR_RESET}")
        return True
    return False


def parse_stage_wait(args):
    wait = False # Default to background
    rest = []
    idx = 0
    while idx < len(args):
        token = args[idx]
        if token == "--no-wait":
            wait = False
            idx += 1
        elif token == "--wait":
            wait = True
            idx += 1
        else:
            rest.append(token)
            idx += 1
    return rest, wait


def command_stage(base_url, args, stage):
    rest, wait = parse_stage_wait(args)
    if not rest:
        raise ValueError(f"{stage} requires a run_id")
    run_id = rest[0]
    result = post_json(f"{base_url}/workflow/runs/{run_id}/{stage}", {"background": True})
    print(f"QUEUED  {colorize(result['run_id'], CLR_YELLOW)}  {stage}")
    if wait:
        # If explicit --wait, we use a blocking loop
        while True:
            run = get_json(f"{base_url}/workflow/runs/{run_id}")
            status = run.get("status")
            if status in ("completed", "failed"):
                render_run(run)
                break
            time.sleep(2)
    else:
        command_watch(base_url, [run_id], quiet=True)


def extract_pos_and_flags(args, pos_keys):
    """Extract positional arguments based on keys, then any --flags."""
    result = {k: None for k in pos_keys}
    result["flags"] = {}
    result["wait"] = False # Default to background in REPL
    
    pos_idx = 0
    idx = 0
    while idx < len(args):
        token = args[idx]
        if token.startswith("--"):
            if token == "--no-wait":
                result["wait"] = False
                idx += 1
            elif token == "--wait":
                result["wait"] = True
                idx += 1
            elif idx + 1 < len(args) and not args[idx+1].startswith("--"):
                key = token.lstrip("-").replace("-", "_")
                result["flags"][key] = args[idx+1]
                idx += 2
            else:
                key = token.lstrip("-").replace("-", "_")
                result["flags"][key] = True
                idx += 1
        else:
            if pos_idx < len(pos_keys):
                result[pos_keys[pos_idx]] = token
                pos_idx += 1
            idx += 1
    return result


def command_intake(base_url, args):
    parsed = extract_pos_and_flags(args, ["name", "path", "goal"])
    project_name = parsed["name"] or parsed["flags"].get("name")
    project_path = parsed["path"] or parsed["flags"].get("path")
    goal = parsed["goal"] or parsed["flags"].get("goal") or "Define a working first slice."
    
    if not project_name:
        raise ValueError("run/intake requires a project name")

    result = post_json(
        f"{base_url}/workflow/intake",
        {
            "project_name": project_name,
            "goal": goal,
            "project_path": project_path,
            "prompt": parsed["flags"].get("prompt"),
            "template": parsed["flags"].get("template", "generic"),
            "background": True,
            "autonomous": True,
        },
    )
    run_id = result["run_id"]
    print(f"QUEUED  {colorize(run_id, CLR_YELLOW)}  run name={project_name}")
    if parsed["wait"]:
        command_watch_blocking(base_url, run_id)
    else:
        command_watch(base_url, [run_id], quiet=True)


def command_project(base_url, args):
    parsed = extract_pos_and_flags(args, ["name", "goal"])
    project_name = parsed["name"] or parsed["flags"].get("name")
    goal = parsed["goal"] or parsed["flags"].get("goal")

    if not project_name:
        raise ValueError("create requires a project name")

    if goal and goal != "Define a working first slice.":
        result = post_json(
            f"{base_url}/workflow/intake",
            {
                "project_name": project_name,
                "goal": goal,
                "background": True,
                "autonomous": True,
            },
        )
    else:
        result = post_json(
            f"{base_url}/workflow/project",
            {"project_name": project_name, "goal": goal or "Define a working first slice.", "background": True},
        )
    
    run_id = result["run_id"]
    print(f"QUEUED  {colorize(run_id, CLR_YELLOW)}  {result.get('workflow', 'create')} name={project_name}")
    if parsed["wait"]:
        command_watch_blocking(base_url, run_id)
    else:
        command_watch(base_url, [run_id], quiet=True)


def command_resource_check(base_url, args):
    parsed = extract_pos_and_flags(args, ["path", "name", "goal"])
    project_name = parsed["name"] or parsed["flags"].get("name") or "Resource Check"
    project_path = parsed["path"] or parsed["flags"].get("path")
    goal = parsed["goal"] or parsed["flags"].get("goal") or "Capture environment details."

    result = post_json(
        f"{base_url}/workflow/resource-check",
        {
            "project_name": project_name,
            "goal": goal,
            "project_path": project_path,
            "background": True,
        },
    )
    run_id = result["run_id"]
    print(f"QUEUED  {colorize(run_id, CLR_YELLOW)}  check name={project_name}")
    if parsed["wait"]:
        command_watch_blocking(base_url, run_id)
    else:
        command_watch(base_url, [run_id], quiet=True)


def command_build_resource_tool(base_url, args):
    parsed = extract_pos_and_flags(args, ["name", "goal"])
    project_name = parsed["name"] or parsed["flags"].get("name")
    goal = parsed["goal"] or parsed["flags"].get("goal") or "Create a working inspection utility."

    if not project_name:
        raise ValueError("build requires a project name")

    result = post_json(
        f"{base_url}/workflow/build-resource-tool",
        {"project_name": project_name, "goal": goal, "background": True},
    )
    run_id = result["run_id"]
    print(f"QUEUED  {colorize(run_id, CLR_YELLOW)}  build name={project_name}")
    if parsed["wait"]:
        command_watch_blocking(base_url, run_id)
    else:
        command_watch(base_url, [run_id], quiet=True)


def command_repo_diagnostics(base_url, args):
    parsed = extract_pos_and_flags(args, ["name", "path", "goal"])
    project_name = parsed["name"] or parsed["flags"].get("name")
    project_path = parsed["path"] or parsed["flags"].get("path")
    goal = parsed["goal"] or parsed["flags"].get("goal") or "Inspect repo and generate diagnostics."

    if not project_name or not project_path:
        raise ValueError("diag requires name and path positional arguments")

    result = post_json(
        f"{base_url}/workflow/repo-diagnostics",
        {
            "project_name": project_name,
            "goal": goal,
            "project_path": project_path,
            "background": True,
        },
    )
    run_id = result["run_id"]
    print(f"QUEUED  {colorize(run_id, CLR_YELLOW)}  diag name={project_name}")
    if parsed["wait"]:
        command_watch_blocking(base_url, run_id)
    else:
        command_watch(base_url, [run_id], quiet=True)


def command_watch_blocking(base_url, run_id):
    last_stage = None
    while True:
        try:
            run = get_json(f"{base_url}/workflow/runs/{run_id}")
            status = run.get("status")
            pipeline = run.get("pipeline") or {}
            stage = pipeline.get("current_stage") or "pending"
            if stage != last_stage:
                print(f"WATCH  {colorize(run_id, CLR_YELLOW)}  {fmt_status(status)}  stage={colorize(stage, CLR_CYAN)}")
                last_stage = stage
            if status in ("completed", "failed"):
                render_run(run)
                break
        except Exception as e:
            print(f"Watch error: {e}")
            break
        time.sleep(2)


def command_last(base_url, args):
    data = get_json(f"{base_url}/system/overview")
    runs = data.get("recent_runs") or []
    if not runs:
        print(f"{CLR_DIM}No recent runs found.{CLR_RESET}")
        return
    last_run = runs[0]
    last_id = last_run["run_id"]
    if args and args[0] == "watch":
        command_watch(base_url, [last_id])
    else:
        run = get_json(f"{base_url}/workflow/runs/{last_id}")
        render_run(run)


def command_runs(base_url, args):
    limit = 10
    if args and args[0].isdigit():
        limit = int(args[0])
    data = get_json(f"{base_url}/system/overview")
    runs = data.get("recent_runs") or []
    if not runs:
        print(f"  {CLR_DIM}none{CLR_RESET}")
        return
    print_header(f"Recent Runs (limit {limit})")
    for run in runs[:limit]:
        status = fmt_status(run.get("status"))
        print(f"  {colorize(run['run_id'], CLR_YELLOW):<18} {status} \t{run.get('workflow'):<12}  {run.get('summary') or '-'}")


def command_show(base_url, args):
    if not args:
        raise ValueError("show requires a run_id")
    run_id = args[0]
    run = get_json(f"{base_url}/workflow/runs/{run_id}")
    render_run(run)


def command_watch(base_url, args, quiet=False):
    if not args:
        raise ValueError("watch requires a run_id")
    run_id = args[0]
    
    def watch_worker():
        last_stage = None
        while True:
            try:
                run = get_json(f"{base_url}/workflow/runs/{run_id}")
                status = run.get("status")
                pipeline = run.get("pipeline") or {}
                stage = pipeline.get("current_stage") or "pending"
                
                if stage != last_stage:
                    # Print without breaking the prompt line too badly if possible
                    # We use \r to overwrite if we were just printing stage, but here we just print
                    msg = f"\n{CLR_DIM}WATCH  {colorize(run_id, CLR_YELLOW)}  {fmt_status(status)}  stage={colorize(stage, CLR_CYAN)}{CLR_RESET}\n"
                    sys.stdout.write(msg)
                    sys.stdout.flush()
                    last_stage = stage
                
                if status in ("completed", "failed"):
                    msg = f"\n{CLR_BOLD}DONE   {colorize(run_id, CLR_YELLOW)}  {fmt_status(status)}: {run.get('summary') or '-'}{CLR_RESET}\n"
                    sys.stdout.write(msg)
                    sys.stdout.flush()
                    break
            except Exception:
                break
            time.sleep(3)
            
    thread = threading.Thread(target=watch_worker, daemon=True)
    thread.start()
    if not quiet:
        print(f"{CLR_DIM}(Monitoring {run_id} in background...){CLR_RESET}")


def command_hello(base_url, args):
    parsed = extract_pos_and_flags(args, ["path", "content"])
    path = parsed["path"] or parsed["flags"].get("path") or "e2e/test.txt"
    content = parsed["content"] or parsed["flags"].get("content") or "Hello from cold CLI"
    
    result = post_json(
        f"{base_url}/workflow/hello",
        {"path": path, "content": content, "background": parsed["wait"]}
    )
    run_id = result["run_id"]
    print(f"QUEUED  {colorize(run_id, CLR_YELLOW)}  hello path={path}")
    if parsed["wait"]:
        command_watch(base_url, [run_id])


def parse_explicit_command(parts):
    if not parts:
        return None
    cmd = parts[0]
    if cmd in ("status", "create", "run", "log", "show", "watch", "diag", "check", "build", "audit", "model", "chat", "plan", "mode", "inspect", "verify", "repair", "hello", "help", "exit", "overview", "runs"):
        return parts
    return None


def main():
    args = parse_args()
    base_url = args.coordinator_url.rstrip("/")
    try:
        if args.command:
            dispatch(base_url, args.command)
            return 0
        
        def save_history():
            if readline and HISTORY_FILE:
                try:
                    readline.write_history_file(HISTORY_FILE)
                except Exception:
                    pass

        print(f"{CLR_BOLD}{CLR_CYAN}ColDBase Workflow CLI{CLR_RESET}")
        print(f"{CLR_DIM}Type `help` for commands.{CLR_RESET}")
        
        state = {"mode": "chat", "_pending_multi": None}
        while True:
            try:
                mode_tag = colorize(state["mode"], CLR_CYAN)
                prompt = f"{CLR_BOLD}cold{CLR_RESET} ({mode_tag})> "
                line = input(prompt).strip()
                
                # Check for pending multi-line input
                if not line and state.get("_pending_multi"):
                    line = state["_pending_multi"]
                    state["_pending_multi"] = None
                    print(f"{CLR_DIM}(Submitting multi-line prompt...){CLR_RESET}")
                elif not line:
                    continue
                if line in ("exit", "quit"):
                    save_history()
                    return 0
                if line in ("help", "--help", "?", "-h"):
                    command_help()
                    continue

                if line.startswith("/"):
                    if command_slash(base_url, state, line):
                        if readline:
                            readline.add_history(line)
                        continue

                try:
                    parts = shlex.split(line)
                except Exception:
                    parts = [line]

                if parts and parts[0] in ("chat", "plan", "command") and len(parts) == 1:
                    command_mode(state, [parts[0]])
                    continue

                if parts and parts[0] == "mode":
                    command_mode(state, parts[1:] or [state["mode"]])
                    continue

                explicit = parse_explicit_command(parts)
                if explicit:
                    dispatch(base_url, explicit)
                    if readline:
                        readline.add_history(line)
                    continue

                if state["mode"] == "chat":
                    print_wrapped(coordinator_chat(base_url, line))
                elif state["mode"] == "plan":
                    print_wrapped(coordinator_plan(base_url, line))
                else:
                    raise ValueError("Unknown command. Use `help` for list of commands or switch mode.")
                
                if readline:
                    readline.add_history(line)

            except EOFError:
                print("")
                save_history()
                return 0
            except Exception as exc:
                print(f"{CLR_RED}FAIL: {exc}{CLR_RESET}")

    except Exception as exc:
        print(f"{CLR_RED}CRITICAL FAIL: {exc}{CLR_RESET}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
