# System Architecture

## Overview

ColDBase is a **distributed agent orchestration system** that combines:
- **Ollama** (local LLM inference engine)
- **ZeroClaw** (Rust-based agent gateway and reasoning engine)
- **Multi-agent coordination** (Coordinator, Coder, Deployer, Tester roles)

The system enables autonomous task delegation, validation, and execution with a focus on safety and auditability in resource-constrained environments (Codespaces, K8s pods).

---

## Component Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    OLLAMA (LLM Inference)                       │
│                   http://localhost:11434                        │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ Models: tinyllama, phi4-mini, etc. (configurable)        │  │
│  │ Volumes: ollama-data (named volume for persistence)      │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
          ▲
          │ requests to /api/generate, /api/tags
          │
┌─────────────────────────────────────────────────────────────────┐
│                  ZEROCLAW GATEWAY (Agent Backbone)              │
│              Rust-based, http://localhost:42617                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ • Agent lifecycle management                             │  │
│  │ • Prompt templating & structured output parsing         │  │
│  │ • Delegation protocol (JSON action format)              │  │
│  │ • Built from vendor/zeroclaw/ (opensource upstream)     │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
          ▲
          │ /api/generate, status checks
          │
┌─────────────────────────────────────────────────────────────────┐
│            AGENT ORCHESTRATOR (FastAPI / Python)                │
│                   docker-compose services                        │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ COORDINATOR (Port 8001)                                  │  │
│  │ • Orchestrates task delegation                           │  │
│  │ • Monitors sub-agents & aggregates results              │  │
│  │ • Selects model: ZEROCLAW_MODEL_COORDINATOR              │  │
│  ├──────────────────────────────────────────────────────────┤  │
│  │ CODER (Port 8002)                                        │  │
│  │ • Generates/modifies code and files                      │  │
│  │ • Actions: write_file (validated & sandboxed)           │  │
│  │ • Model: ZEROCLAW_MODEL_CODER (default phi4-mini)       │  │
│  ├──────────────────────────────────────────────────────────┤  │
│  │ DEPLOYER (Port 8003)                                     │  │
│  │ • Executes container & infrastructure tasks             │  │
│  │ • Actions: deploy_compose (docker socket access)        │  │
│  │ • Model: ZEROCLAW_MODEL_DEPLOYER                         │  │
│  ├──────────────────────────────────────────────────────────┤  │
│  │ TESTER (Port 8004)                                       │  │
│  │ • Validation and health checks                           │  │
│  │ • Actions: http_check, file_check                        │  │
│  │ • Model: ZEROCLAW_MODEL_TESTER (lightweight tinyllama)  │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
          ▲
          │ POST /task, GET /health
          │
┌─────────────────────────────────────────────────────────────────┐
│                    SHARED STATE & AUDIT                         │
│                    (Volume: ./workspace)                        │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │ workspace/memory/                                        │  │
│  │ • agent_state.json          — Agent status, last seen   │  │
│  │ • agent_state.schema.json   — JSON Schema validation    │  │
│  │ • task_queue.json           — Pending tasks             │  │
│  │ • shared_context.md         — Human-readable notes      │  │
│  ├──────────────────────────────────────────────────────────┤  │
│  │ workspace/logs/                                          │  │
│  │ • delegation.log            — Audit trail (JSON)        │  │
│  │ • coordinator.log, coder.log, ... — Per-agent logs      │  │
│  ├──────────────────────────────────────────────────────────┤  │
│  │ workspace/zeroclaw-data/    — ZeroClaw config & cache   │  │
│  │ workspace/projects/         — User project files        │  │
│  └──────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Task Flow: Delegation & Execution

### 1. **Task Submission**
```
User / Coordinator
    │
    ├─ POST /task (JSON)
    │  {
    │    "task": "delegate",
    │    "payload": {
    │      "role": "coder",
    │      "task": "write_file",
    │      "payload": {
    │        "path": "workspace/test.md",
    │        "content": "# Hello"
    │      }
    │    }
    │  }
    │
    └─→ Agent Service (listens on role-specific port)
```

### 2. **Validation** (in `validate_delegation()`)
```
Agent receives delegation request
    │
    ├─ Check target role is known (coder, deployer, tester, coordinator)
    │
    ├─ Check task is in whitelist for that role
    │  Example:
    │  • coder   → [write_file]
    │  • tester  → [http_check, file_check]
    │  • deployer → [deploy_compose]
    │
    ├─ Validate payload parameters
    │  • write_file: path (no "..", max 200 chars), extension, content (no binary, <100KB)
    │  • http_check: URL (http/https, no private IP, no docker internals)
    │  • file_check: path (no "..", max 200 chars)
    │
    ├─ Check delegation depth (max 3 levels)
    │
    └─ Decision: ACCEPT or REJECT with reason
```

### 3. **Execution** (in `perform_action()`)
```
Validated action is executed
    │
    ├─ http_check     → requests.get(url, timeout)
    ├─ write_file     → os.mkdir() + open(path, 'w')
    ├─ file_check     → os.path.exists(path)
    ├─ delegate       → HTTP POST to target agent (recursive)
    │
    └─ Result: {"status": ..., "path": ..., "error": ...}
```

### 4. **Audit Logging** (in `write_audit_log()`)
```
Every delegation decision is logged to workspace/logs/delegation.log

Entry schema:
{
  "time": 1713099340.5,                    # Unix timestamp
  "request_id": 1713099340500,             # Unique ID
  "from": "coordinator",                   # Source agent
  "to": "coder",                           # Target agent
  "task": "write_file",                    # Action name
  "delegation_depth": 0,
  "payload_keys": ["path", "content"],     # Keys only (no values for privacy)
  "payload_summary": {...redacted...},     # Sensitive fields removed/hashed
  "decision": "delivered",                 # delivered|rejected|failed
  "reason": null,                          # Error message if rejected
  "tried_endpoints": ["http://zeroclaw-coder:8000/task"],
  "result_summary": "written: /workspace/...",
  "duration_ms": 145,
  "host": "zeroclaw-coordinator"
}

Audit is queryable via:
  GET /audit/delegation?n=10&tail=true
    → Returns last 10 entries as JSON
```

---

## Agent Roles & Capabilities

### Coordinator
- **Purpose:** Plan, delegate, and orchestrate
- **Model:** `ZEROCLAW_MODEL_COORDINATOR` (default: tinyllama)
- **Tasks:** Reads system prompts, generates JSON actions
- **Actions:** Can delegate to any other role
- **Resource Limits:** 0.25 CPU, 256M RAM

### Coder
- **Purpose:** Generate and manage code/content
- **Model:** `ZEROCLAW_MODEL_CODER` (default: phi4-mini)
- **Capabilities:** write_file, read file operations
- **Safe File Extensions:** .txt, .md, .json, .yaml, .yml, .cfg, .conf, .ini, .log
- **Constraints:**
  - Paths max 200 chars, no "..", relative only
  - Content max 100KB, no binary (NUL bytes), no shebangs
- **Resource Limits:** 0.25 CPU, 256M RAM

### Deployer
- **Purpose:** Execute infrastructure/container tasks
- **Model:** `ZEROCLAW_MODEL_DEPLOYER` (default: phi4-mini)
- **Capabilities:** Docker operations, compose management
- **Constraints:**
  - Requires docker socket binding (via volume mount)
  - Limited to schema-validated docker commands
- **Resource Limits:** 0.25 CPU, 256M RAM

### Tester
- **Purpose:** Validation, health checks, assertions
- **Model:** `ZEROCLAW_MODEL_TESTER` (lightweight: tinyllama)
- **Capabilities:** http_check (HTTP requests), file_check (file existence)
- **Constraints:**
  - URLs must be public (no private IPs, no docker internals)
  - HTTP timeouts: 10s default
- **Resource Limits:** 0.125 CPU, 128M RAM

---

## Configuration & Environment Variables

### Service Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `ZEROCLAW_MODEL` | `phi4-mini` | Default model for all agents |
| `ZEROCLAW_MODEL_COORDINATOR` | `tinyllama` | Coordinator-specific model |
| `ZEROCLAW_MODEL_CODER` | `phi4-mini` | Coder-specific model |
| `ZEROCLAW_MODEL_DEPLOYER` | `phi4-mini` | Deployer-specific model |
| `ZEROCLAW_MODEL_TESTER` | `tinyllama` | Tester-specific model (lightweight) |
| `ZEROCLAW_GATEWAY_PORT` | `42617` | ZeroClaw gateway port |
| `OLLAMA_URL` | `http://ollama-brain:11434` | Ollama API endpoint |
| `WORKSPACE_DIR` | `/workspace` | Shared workspace mount |
| `MAX_DELEGATION_DEPTH` | `3` | Max task delegation nesting |
| `ALLOWED_WRITE_ROOTS` | (empty) | Restrict file writes to paths (e.g., "e2e,workspace/projects") |
| `OLLAMA_MODELS_PREPULL` | `${ZEROCLAW_MODEL}` | Comma-separated models to pull on startup |

### Startup Behavior

**[startup.sh]** orchestrates:
1. Pre-flight checks (disk, RAM, docker)
2. Optional cleanup if resources are low
3. ZeroClaw image build from source
4. Ollama container start + health wait (60s)
5. Model pre-pull (configurable via `OLLAMA_MODELS_PREPULL`)
6. ZeroClaw gateway start + health wait (120s)
7. Memory initialization (if python3 available)

---

## Security Model

### Delegation Validation
- **Whitelist:** Only known (role, task) pairs are accepted
- **Parameter Constraints:** Paths, URLs, content size enforced
- **Depth Limiting:** Delegation chains capped at 3 levels to prevent loops
- **Binary Detection:** Write operations reject files with NUL bytes or shebangs

### External Access Restrictions
- **File Operations:** Relative paths only, no ".." or leading "/"
- **URL Validation:** http/https required, private IPs rejected (127.0.0.1 allowed for loopback testing)
- **Docker Socket:** Deployer can mount `/var/run/docker.sock` but commands are sandboxed

### Audit Trail
- **Immutable Log:** `workspace/logs/delegation.log` records all decisions
- **Queryable Endpoint:** `/audit/delegation` (optionally with API key control)
- **Fields Logged:** request_id, source agent, target agent, task, decision, duration, failure reason

---

## Memory & State

### agent_state.json
```json
{
  "agents": {
    "coordinator": {"id": "coordinator-1", "status": "idle", "role": "orchestrator", "last_activity": null},
    "coder": {"id": "coder-1", "status": "idle", "role": "code_generation"},
    "deployer": {"id": "deployer-1", "status": "idle", "role": "deployment"},
    "tester": {"id": "tester-1", "status": "idle", "role": "validation"}
  },
  "tasks": {
    "current": [],      # In-flight tasks
    "completed": [],    # Recently finished
    "failed": []        # Errors to review
  },
  "metadata": {
    "version": "1.0",
    "created_at": "2026-04-14T00:00:00Z",
    "last_updated": "2026-04-14T14:55:40Z"
  }
}
```

### task_queue.json
```json
{
  "queue": [
    {
      "id": "task-123",
      "created_at": "2026-04-14T15:00:00Z",
      "source": "coordinator",
      "target": "coder",
      "task": "write_file",
      "status": "pending",
      "priority": 1
    }
  ]
}
```

### shared_context.md
Human-readable notes appended by agents; used for decision tracking and inter-agent communication.

---

## Resource Allocation (Codespace Optimization)

Total baseline memory usage:
- **Ollama:** 512M–1GB (depends on loaded models)
- **ZeroClaw:** 1.0 CPU, 1GB memory
- **Coordinator:** 0.25 CPU, 256M
- **Coder:** 0.25 CPU, 256M
- **Deployer:** 0.25 CPU, 256M
- **Tester:** 0.125 CPU, 128M
- **Headroom:** ~500M recommended

**Total for 4GB Codespace:** ~2.75GB used, 1.25GB headroom

If OOM detected at runtime, reduce `ZEROCLAW_MODEL` to lighter model (e.g., `tinyllama` everywhere) or disable optional services (Coder, Deployer, Tester).

---

## Data Paths

| Path | Type | Persistence | Notes |
|------|------|-------------|-------|
| `workspace/memory/` | Data | ✅ Mounted | Agent state, task queue |
| `workspace/logs/` | Data | ✅ Mounted | Audit log, per-role logs |
| `workspace/zeroclaw-data/` | Data | ✅ Mounted | ZeroClaw config & cache |
| `workspace/projects/` | Data | ✅ Mounted | User project files |
| `workspace/delegated/` | Data | ✅ Mounted | Delegation test output |
| `ollama-data/` | Named Volume | ✅ Docker | Ollama model cache |

All `workspace/` paths are bind-mounted to host `./workspace/` for easy inspection.

---

## Next Steps & Extensibility

### Adding a New Agent Role
1. Define in `agents/roles.yml`
2. Add role to `allowed` dict in `validate_delegation()`
3. Create new FastAPI service in `docker-compose.yml` with role-specific env vars
4. Implement task handlers in `agents/app.py`

### Adding a New Delegation Task
1. Define validation parameters in `validate_delegation()`
2. Implement execution in `perform_action()`
3. Add unit test in `agents/tests/test_validator_audit.py`

### Integrating a Different LLM Provider
- Modify `ZEROCLAW_PROVIDER_URL` and `PROVIDER` in `docker-compose.yml`
- Currently supports: Ollama, OpenAI (via ZeroClaw)
- ZeroClaw handles provider abstraction via `src/gateway/` layer

