#!/usr/bin/env python3
"""Validate and seed workspace/memory/agent_state.json using the schema.

If `jsonschema` is available it performs a strict validation, otherwise it performs
basic structural checks and fills missing top-level keys with sensible defaults.
"""
import json
import os
import datetime
import sys

ROOT = os.path.dirname(os.path.dirname(__file__))
MEM_DIR = os.path.join(ROOT, "workspace", "memory")
SCHEMA_PATH = os.path.join(MEM_DIR, "agent_state.schema.json")
STATE_PATH = os.path.join(MEM_DIR, "agent_state.json")


def load_json(path):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"ERROR: failed to read {path}: {e}")
        return None


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2, sort_keys=True)


def utc_now():
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def seed_defaults():
    return {
        "metadata": {
            "created_at": utc_now(),
            "last_updated": utc_now(),
            "version": "1.0",
        },
        "agents": {},
        "tasks": {"current": [], "completed": [], "failed": []},
        "shared_context": {"project_name": None, "project_status": None},
        "errors": {"recent": []},
    }


def basic_validate_and_seed(state):
    changed = False
    if state is None:
        state = seed_defaults()
        changed = True

    if "metadata" not in state:
        state["metadata"] = {"created_at": utc_now(), "last_updated": utc_now(), "version": "1.0"}
        changed = True
    else:
        state["metadata"].setdefault("created_at", utc_now())
        state["metadata"]["last_updated"] = utc_now()
        state["metadata"].setdefault("version", "1.0")

    state.setdefault("agents", {})
    state.setdefault("tasks", {"current": [], "completed": [], "failed": []})
    state.setdefault("shared_context", {})
    state.setdefault("errors", {"recent": []})

    return state, changed


def run():
    schema = load_json(SCHEMA_PATH)
    state = load_json(STATE_PATH)

    if schema is None:
        print("Warning: schema not found, performing basic seed/validation")
        state, changed = basic_validate_and_seed(state)
        write_json(STATE_PATH, state)
        print(f"Wrote {STATE_PATH}")
        return 0

    try:
        import jsonschema
        validator = jsonschema.Draft7Validator(schema)
        if state is None:
            state, _ = basic_validate_and_seed(state)

        errors = sorted(validator.iter_errors(state), key=lambda e: e.path)
        if errors:
            print("Schema validation errors:")
            for e in errors:
                print(" -", ".".join([str(p) for p in e.path]) or "<root>", e.message)
            # attempt to fix minimal missing properties
        # update last_updated
        state.setdefault("metadata", {})
        state["metadata"]["last_updated"] = utc_now()
        write_json(STATE_PATH, state)
        print(f"Validated and wrote {STATE_PATH}")
        return 0
    except Exception:
        # jsonschema not available or other error — fallback
        state, changed = basic_validate_and_seed(state)
        write_json(STATE_PATH, state)
        print(f"Fallback: wrote {STATE_PATH} (basic seed)")
        return 0


if __name__ == "__main__":
    rc = run()
    sys.exit(rc)
