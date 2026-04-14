#!/usr/bin/env bash
set -euo pipefail

MEM_DIR="./workspace/memory"
mkdir -p "$MEM_DIR"

create_if_missing() {
  local path="$1"
  local content="$2"
  if [ ! -f "$path" ]; then
    printf "%s" "$content" > "$path"
    echo "Created $path"
  else
    echo "Exists: $path"
  fi
}

create_if_missing "$MEM_DIR/agent_state.json" '{"agents": []}'
create_if_missing "$MEM_DIR/task_queue.json" '{"tasks": []}'
create_if_missing "$MEM_DIR/shared_context.md" "# Shared Context\n\n(see workspace/memory/shared_context.md for template)\n"

# Validate JSON files (requires python)
python - <<'PY'
import json,sys,os
errors=0
for f in ["agent_state.json","task_queue.json"]:
    p=os.path.join(os.getcwd(),"workspace","memory",f)
    try:
        with open(p) as fh:
            json.load(fh)
    except Exception as e:
        print(f"JSON validation failed for {p}: {e}")
        errors+=1
if errors:
    sys.exit(2)
print('Memory directory initialized and JSON validated.')
PY

echo "Memory init complete: $MEM_DIR"
