# ColDBase

**Distributed multi-agent orchestration system combining Ollama (local LLM inference) and ZeroClaw (agent gateway) with built-in delegation validation and audit logging.**

A lightweight, resource-optimized framework for autonomous task coordination in constrained environments (Codespaces, Raspberry Pi, K8s pods).

---

## What It Does

ColDBase enables multiple AI agents (Coordinator, Coder, Deployer, Tester) to collaborate on complex tasks by:

- **Delegating work safely** — Validation rules prevent unsafe operations
- **Maintaining audit trails** — Every delegation decision is logged and queryable
- **Operating locally** — No cloud dependency; runs on-device with Ollama
- **Running lean** — Baseline ~2.75GB memory, tunable for 4GB Codespaces

```
┌─────────────────┐
│  Task Request   │
└────────┬────────┘
         │
    ┌────▼──────────────────┐
    │  Coordinator Agent     │ ← Orchestrates & plans
    │  (tinyllama)           │
    └────┬───────────────────┘
         │
    ┌────▼──────────────────┐
    │ Delegation Validator   │ ← Whitelist + constraints
    └────┬───────────────────┘
         │
    ┌────▼──────────────────────────────────┐
    │    Task Execution (Role-Specific)      │
    ├──────────────────────────────────────────┤
    │ • Coder (write_file)                    │
    │ • Tester (http_check, file_check)       │
    │ • Deployer (docker operations)          │
    └────┬──────────────────────────────────┘
         │
    ┌────▼──────────────────┐
    │  Audit Log Query       │ ← /audit/delegation
    │  (JSON, queryable)     │
    └────────────────────────┘
```

---

## Quick Start

### 1. Clone & Setup (5 min)
```bash
git clone https://github.com/your-org/ColDBase.git
cd ColDBase
chmod +x startup.sh cleanup.sh
./startup.sh
```

### 2. Verify Services
```bash
docker compose ps
curl http://localhost:11434/api/tags    # Ollama
curl http://localhost:42617/status      # ZeroClaw
```

### 3. Submit a Task
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

### 4. Check Audit Trail
```bash
curl http://localhost:8001/audit/delegation?n=10
```

For detailed setup, see [QUICK_START.md](QUICK_START.md).

---

## Key Features

✅ **Multi-Agent Coordination**  
Coordinator, Coder, Deployer, Tester agents with distinct capabilities and resource limits.

✅ **Safe Delegation**  
Whitelist-based validation, parameter constraints, depth limiting, and binary detection prevent unsafe operations.

✅ **Audit Logging**  
Every delegation decision recorded with request ID, decision (approved/rejected), duration, and redacted payload summary. Queryable via REST API.

✅ **Local & Private**  
Ollama handles inference (no API keys, no cloud), ZeroClaw gateway provides reasoning layer. All state stored locally.

✅ **Codespace-Ready**  
Conservative resource limits (~2.75GB baseline) with tuning guidance for 4GB environments. Startup pre-flight checks validate disk/RAM.

✅ **Extensible**  
Add new delegation tasks, agent roles, and models via configuration and code. Full test suite included.

---

## Architecture

### Components

| Service | Port | Purpose |
|---------|------|---------|
| **Ollama** | 11434 | Local LLM inference engine (models: tinyllama, phi4-mini, etc.) |
| **ZeroClaw** | 42617 | Rust-based agent gateway & reasoning engine |
| **Coordinator** | 8001 | Orchestrates & delegates tasks |
| **Coder** | 8002 | Generates/modifies code & files |
| **Deployer** | 8003 | Executes container & infra tasks |
| **Tester** | 8004 | Validates & performs health checks |

### Shared State

```
workspace/
├── memory/              # Agent state & task queue
├── logs/                # Audit trail & per-agent logs
├── projects/            # User project files
└── zeroclaw-data/       # ZeroClaw config & cache
```

For detailed architecture, see [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Supported Tasks

### Coder Agent
- **write_file** — Create/update files (validated extensions, no binary)
  ```json
  {"path": "workspace/file.md", "content": "..."}
  ```

### Tester Agent
- **http_check** — HTTP request with status code validation
  ```json
  {"url": "http://example.com", "timeout": 10}
  ```
- **file_check** — Verify file existence
  ```json
  {"path": "workspace/file.txt"}
  ```

### Deployer Agent
- **deploy_compose** — Docker Compose operations (sandboxed)
  ```json
  {"command": "up -d service_name"}
  ```

### Coordinator
- **delegate** — Submit task to another agent (recursive, depth-limited)
  ```json
  {"role": "coder", "task": "write_file", "payload": {...}}
  ```

See [agents/README.md](agents/README.md) for full API reference.

---

## Configuration

### Environment Variables
```bash
ZEROCLAW_MODEL              # Default model (phi4-mini)
ZEROCLAW_MODEL_COORDINATOR # Coordinator model (tinyllama)
ZEROCLAW_MODEL_CODER       # Coder model (phi4-mini)
ZEROCLAW_MODEL_DEPLOYER    # Deployer model (phi4-mini)
ZEROCLAW_MODEL_TESTER      # Tester model (tinyllama)

MAX_DELEGATION_DEPTH       # Max nesting (default: 3)
ALLOWED_WRITE_ROOTS        # Restrict file writes (e.g., "e2e,workspace/projects")
OLLAMA_MODELS_PREPULL      # Models to pull on startup (CSV)
```

See [.env.template](.env.template) for all options.

### Resource Limits
Configured in [docker-compose.yml](docker-compose.yml):
- Ollama: 512M–1GB (varies by model)
- ZeroClaw: 1.0 CPU, 1GB RAM
- Agents: 0.25–0.125 CPU, 128–256M each
- **Total baseline:** ~2.75GB (fits in 4GB Codespace)

---

## Documentation

| File | Audience | Purpose |
|------|----------|---------|
| [QUICK_START.md](QUICK_START.md) | Everyone | Setup & common commands |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Users & Architects | System design, components, data flow |
| [agents/README.md](agents/README.md) | Developers & API users | Agent tasks, validation, API reference |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Contributors | Setup, testing, extending the system |
| [roadmap.md](roadmap.md) | Project trackers | Status, completed items, next steps |

---

## Testing

```bash
# Run unit tests (requires Python 3.8+)
pytest agents/tests/ -v

# Run in container (requires docker-compose up)
docker exec zeroclaw-coordinator pytest /app/tests/ -v

# Smoke test (health checks)
pytest agents/tests/test_smoke.py -v
```

**Test Coverage:**
- Delegation validation per role & task
- Audit log schema conformance
- Safe file operations (extensions, paths, content)
- URL validation (IP ranges, hostnames)
- Depth limiting and circular delegation prevention

---

## Project Status

**Current Phase:** Core agent framework with delegation validation and audit logging

| Item | Status | Details |
|------|--------|---------|
| Multi-agent coordination | ✅ | Coordinator, Coder, Deployer, Tester with model routing |
| Delegation validation | ✅ | Whitelist, parameter constraints, depth limits |
| Audit logging | ✅ | Queryable via `/audit/delegation` endpoint |
| Shared memory | ✅ | agent_state.json, task_queue.json, shared_context.md |
| Resource tuning | ✅ | Conservative limits for Codespaces |
| Hello-World E2E test | 🔧 | In-progress; need orchestration harness |
| Full POC | ⬜ | Next: run integration test and collect metrics |

See [roadmap.md](roadmap.md) for full project status.

---

## Contributing

We welcome contributions! To get started:

1. Read [DEVELOPMENT.md](DEVELOPMENT.md) for setup and workflow
2. Check [roadmap.md](roadmap.md) for open items
3. Open an issue or pull request
4. Follow the code style guidelines (PEP 8, JSON Schema validation)

---

## Security

### Delegation Safety
- **Whitelist:** Only known (role, task) pairs allowed
- **Content Validation:** No binary files, shebangs, or NUL bytes
- **Path Validation:** Relative paths only, max 200 chars, no ".." or traversal
- **URL Validation:** Public URLs only, private IPs rejected
- **Depth Limiting:** Max 3-level nesting prevents circular delegation

### Audit Trail
Every delegation is logged with decision, duration, source, target, and redacted payload. Available via `/audit/delegation` endpoint.

### Network
All inter-agent communication via Docker Compose service names on internal network (`zeroclaw-network`). No external APIs required (Ollama local only).

---

## Performance

### Memory Usage (Baseline)
- Ollama: 512M–1GB (model-dependent)
- ZeroClaw: 1GB
- Coordinator + Coder + Deployer + Tester: 768M total
- **Total:** ~2.75GB (headroom in 4GB Codespace)

### Startup Time
- First run: ~3–5 min (build ZeroClaw, pull model)
- Subsequent: ~30–60 sec (health checks, no rebuild)

### Model Tuning
- **Light:** tinyllama (~1GB, fast inference)
- **Balanced:** phi4-mini (~1GB, good quality)
- **Heavy:** orca-mini (~7GB, higher accuracy, not recommended for 4GB)

---

## Roadmap & Future

**Next:**
- Complete Hello-World E2E workflow test
- Run full integration test with metrics collection
- Add more delegation tasks (email, API calls, etc.)

**Future:**
- Web dashboard for audit trail visualization
- Support for additional LLM providers (OpenAI, Claude API)
- Kubernetes deployment templates
- Performance profiling & optimization
- Advanced delegation strategies (parallel tasks, retries)

See [roadmap.md](roadmap.md) for full project plans.

---

## License

[Add your license here — e.g., MIT, Apache 2.0]

## Acknowledgments

- **ZeroClaw:** Vendored from [upstream](vendor/zeroclaw/README.md)
- **Ollama:** Local LLM inference via [ollama.ai](https://ollama.ai)
- Inspired by agent orchestration frameworks like LangChain, AutoGPT, and crew-ai

---

## Contact & Support

- **Issues:** [GitHub Issues](#)
- **Discussions:** [GitHub Discussions](#)
- **Documentation:** See files above or [ARCHITECTURE.md](ARCHITECTURE.md)

---

**Happy orchestrating! 🚀**
