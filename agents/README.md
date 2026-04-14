# Agent System

This directory contains the FastAPI-based **multi-agent orchestration layer** that coordinates Coordinator, Coder, Deployer, and Tester agents.

## Quick Reference

### Agent Ports & Models

| Agent | Port | Model (env var) | Role |
|-------|------|-----------------|------|
| Coordinator | 8001 | `ZEROCLAW_MODEL_COORDINATOR` (tinyllama) | Orchestrate & plan |
| Coder | 8002 | `ZEROCLAW_MODEL_CODER` (phi4-mini) | Generate code/files |
| Deployer | 8003 | `ZEROCLAW_MODEL_DEPLOYER` (phi4-mini) | Deploy containers |
| Tester | 8004 | `ZEROCLAW_MODEL_TESTER` (tinyllama) | Validate & test |

### Health Check
```bash
curl http://localhost:8001/health  # Coordinator
curl http://localhost:8002/health  # Coder
curl http://localhost:8003/health  # Deployer
curl http://localhost:8004/health  # Tester
```

---

## File Structure

```
agents/
├── app.py                          # Main agent logic (774 lines)
├── Dockerfile                      # Container build for all agent roles
├── requirements.txt                # Python dependencies
├── roles.yml                       # Agent role definitions & capabilities
├── delegation_audit.schema.json    # JSON Schema for audit entries
└── tests/
    ├── test_validator_audit.py     # Validation & audit logging tests
    ├── test_audit_schema.py        # Schema conformance tests
    ├── test_deploy_compose.py      # Deployer task validation
    └── test_smoke.py               # Health checks & API smoke tests
```

---

## How It Works

### 1. Task Submission API

**POST /task** — Submit a task to an agent

```bash
curl -X POST http://localhost:8001/task \
  -H "Content-Type: application/json" \
  -d '{
    "task": "delegate",
    "payload": {
      "role": "coder",
      "task": "write_file",
      "payload": {
        "path": "workspace/hello.md",
        "content": "# Hello World"
      }
    }
  }'
```

**Request Schema:**
```json
{
  "task": "string",                     # Task name (e.g., "delegate", "run_action")
  "payload": {
    "role": "string",                   # Target agent role (if delegating)
    "task": "string",                   # Task to execute
    "payload": {}                       # Task-specific parameters
  }
}
```

**Response:**
```json
{
  "task_id": "...",
  "result": {...},                      # Task result (structure varies by task)
  "error": "string" // If failed
}
```

### 2. Delegation & Validation

When an agent receives a delegation request:

1. **Whitelist Check:** Is `(role, task)` pair allowed?
   - Returns error if role unknown or task not in whitelist
2. **Parameter Validation:** Are payload fields safe?
   - Checks path length, file extensions, URL schemes, IP addresses, etc.
3. **Depth Check:** Is delegation depth < MAX_DELEGATION_DEPTH (default 3)?
   - Prevents circular delegation and runaway chains
4. **Execution:** If all checks pass, execute the action
5. **Audit Log:** Record decision (approved/rejected) to `workspace/logs/delegation.log`

### 3. Supported Tasks

#### Coordinator → Delegate (any role)
```json
{
  "action": "delegate",
  "params": {
    "role": "coder",
    "task": "write_file",
    "payload": {"path": "...", "content": "..."},
    "delegation_depth": 0
  }
}
```

#### Coder → write_file
```json
{
  "action": "write_file",
  "params": {
    "path": "workspace/file.txt",      # Required, max 200 chars, no "..", relative only
    "content": "file content"           # Required, max 100KB, no binary
  }
}
```

**Validation:**
- File extension must be in: `.txt`, `.md`, `.json`, `.yaml`, `.yml`, `.cfg`, `.conf`, `.ini`, `.log`
- Path cannot start with `/` or contain `..`
- Content cannot start with `#!` (no shebangs)
- Content cannot contain `\x00` (no binary)

#### Tester → http_check
```json
{
  "action": "http_check",
  "params": {
    "url": "http://example.com",       # Required, http(s), public URLs only
    "timeout": 10                       # Optional, default 10s
  }
}
```

**Validation:**
- URL must start with `http://` or `https://`
- Cannot target: docker.internal, 127.0.0.1, private IPs, .local domains
- Timeouts: 10s default, max as specified

#### Tester → file_check
```json
{
  "action": "file_check",
  "params": {
    "path": "workspace/file.txt"       # Required, same validation as write_file
  }
}
```

Returns: `{"status": "exists"|"missing", "path": "..."}`

#### Deployer → deploy_compose
```json
{
  "action": "deploy_compose",
  "params": {
    "command": "up -d service_name"    # Docker compose subcommand
  }
}
```

**Note:** Requires docker socket mounted. Currently returns placeholder message.

---

## Audit Logging

Every delegation decision is logged to **workspace/logs/delegation.log** (one JSON object per line).

### Query Recent Audit Entries

**GET /audit/delegation**

```bash
# Coordinator audit endpoint
curl "http://localhost:8001/audit/delegation?n=10&tail=true"

# Response:
{
  "count": 3,
  "entries": [
    {
      "time": 1713099340.5,
      "request_id": 1713099340500,
      "from": "coordinator",
      "to": "coder",
      "task": "write_file",
      "decision": "delivered",
      "payload_summary": {"path": "workspace/...", "content": "<redacted>"},
      "result_summary": "written: /workspace/...",
      "duration_ms": 145,
      "host": "zeroclaw-coordinator"
    },
    ...
  ]
}
```

### Audit Entry Schema

| Field | Type | Example | Notes |
|-------|------|---------|-------|
| `time` | number (unix) | 1713099340.5 | Timestamp |
| `request_id` | integer | 1713099340500 | Unique ID per request |
| `from` | string | "coordinator" | Source agent |
| `to` | string | "coder" | Target agent |
| `task` | string | "write_file" | Action name |
| `decision` | string | "delivered" \| "rejected" | Outcome |
| `reason` | string \| null | "path too long" | If rejected |
| `delegation_depth` | integer | 0, 1, 2 | Nesting level |
| `payload_summary` | object | {"path": "...", "content": "<redacted>"} | Redacted payload |
| `result_summary` | string | "written: /workspace/..." | Result details |
| `duration_ms` | integer | 145 | Execution time |
| `host` | string | "zeroclaw-coordinator" | Container hostname |

---

## Environment Variables

Set these in `.env` or `docker-compose.yml` to customize agent behavior.

### Model Selection

```bash
# All agents use phi4-mini by default
ZEROCLAW_MODEL=phi4-mini

# Override per-role (takes precedence over ZEROCLAW_MODEL)
ZEROCLAW_MODEL_COORDINATOR=tinyllama
ZEROCLAW_MODEL_CODER=phi4-mini
ZEROCLAW_MODEL_DEPLOYER=phi4-mini
ZEROCLAW_MODEL_TESTER=tinyllama
```

### Resource Control

```bash
# Max delegation nesting (prevents circular delegation)
MAX_DELEGATION_DEPTH=3

# Restrict file writes to specific directories (comma-separated)
ALLOWED_WRITE_ROOTS=e2e,workspace/projects
```

### Timeouts & Networking

```bash
# HTTP request timeout (affects http_check)
ZEROCLAW_TIMEOUT_SECONDS=300

# ZeroClaw gateway URL
ZEROCLAW_URL=http://zeroclaw:42617

# Ollama API endpoint
OLLAMA_URL=http://ollama-brain:11434
```

---

## Running Tests Locally

### Prerequisites
```bash
pip install -r agents/requirements.txt
pytest --version
```

### Run All Tests
```bash
pytest agents/tests/ -v
```

### Run Specific Test File
```bash
pytest agents/tests/test_validator_audit.py -v
pytest agents/tests/test_audit_schema.py -v
```

### Run Single Test
```bash
pytest agents/tests/test_validator_audit.py::test_write_file_valid -v
```

### Test Coverage (if installed)
```bash
pip install pytest-cov
pytest agents/tests/ --cov=agents --cov-report=html
# Open htmlcov/index.html in browser
```

### What Each Test File Does

| File | Purpose | Coverage |
|------|---------|----------|
| `test_validator_audit.py` | Validation rules per task | write_file, http_check, file_check, delegation depth |
| `test_audit_schema.py` | Audit log entry conformance | JSON schema validation, time fields, request IDs |
| `test_deploy_compose.py` | Deployer task validation | deploy_compose safety checks |
| `test_smoke.py` | Basic health & API checks | /health endpoint, /audit/delegation response |

---

## Extending the Agent System

### Add a New Delegation Task

Example: Add a `generate_config` task for Coder agent.

**Step 1:** Add to whitelist in `validate_delegation()` (agents/app.py ~230)
```python
allowed = {
    "coder": ["write_file", "generate_config"],  # ← Add here
    ...
}
```

**Step 2:** Add parameter validation in `validate_delegation()` (~250)
```python
if task == "generate_config":
    config_type = (payload.get("type") or "").strip()
    if config_type not in ("docker", "k8s", "systemd"):
        return False, "invalid config_type"
    # ... more validation
```

**Step 3:** Implement execution in `perform_action()` (~750)
```python
if action == "generate_config":
    config_type = params.get("type")
    # ... generate config logic
    return {"generated": path, "type": config_type}
```

**Step 4:** Add unit test in tests/
```python
def test_generate_config_valid():
    payload = {"type": "docker"}
    ok, reason = app.validate_delegation("coder", "generate_config", payload)
    assert ok is True, reason
```

### Add a New Agent Role

Example: Add a `researcher` role for web/API lookups.

**Step 1:** Update `roles.yml`
```yaml
researcher:
  role: "research"
  description: "Searches and queries external APIs"
  capabilities:
    - search_web
    - query_api
  max_context_tokens: 2048
  timeout_seconds: 60
```

**Step 2:** Add docker-compose service in root `docker-compose.yml`
```yaml
researcher:
  build:
    context: ./agents
  container_name: zeroclaw-researcher
  environment:
    ROLE: researcher
    ZEROCLAW_MODEL: ${ZEROCLAW_MODEL_RESEARCHER:-tinyllama}
  ports:
    - "8005:8000"
  deploy:
    resources:
      limits:
        cpus: '0.25'
        memory: 256M
```

**Step 3:** Add to allowed roles in `validate_delegation()`
```python
allowed = {
    ...
    "researcher": ["search_web", "query_api"],
}
```

**Step 4:** Implement tasks in `perform_action()`

---

## Debugging & Troubleshooting

### Check Agent Health
```bash
for port in 8001 8002 8003 8004; do
  echo "Port $port: $(curl -s http://localhost:$port/health | jq .status)"
done
```

### View Agent Logs
```bash
# All agent logs
docker compose logs -f coordinator coder deployer tester

# Specific agent
docker compose logs -f coordinator --tail 50
```

### Inspect Audit Trail
```bash
# View last 20 audit entries
curl "http://localhost:8001/audit/delegation?n=20"

# Raw log file
tail -20 workspace/logs/delegation.log
```

### Test a Delegation
```bash
curl -X POST http://localhost:8001/task \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": "test-1",
    "task": "delegate",
    "payload": {
      "role": "tester",
      "task": "file_check",
      "payload": {"path": "workspace/README.md"}
    }
  }' | jq .
```

### Memory/CPU Usage
```bash
docker stats
```

---

## Contributing

See [DEVELOPMENT.md](../DEVELOPMENT.md) for guidelines on extending and testing the agent system.

