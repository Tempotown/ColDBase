# Development Guide

This guide is for developers who want to modify, extend, or debug the ColDBase system.

## Prerequisites

### System Requirements
- **Docker & Docker Compose:** v20+ (bundled in Codespaces)
- **Git:** for version control
- **Bash:** for startup/cleanup scripts
- **Python 3.8+:** for local testing (optional, not needed in containers)
- **Free Disk:** 5GB minimum recommended, 10GB+ for experimentation
- **Free RAM:** 4GB+ available (Codespaces have 16GB)

### Check Installation
```bash
docker --version
docker compose version
git --version
bash --version
```

---

## Local Setup

### 1. Clone the Repository
```bash
git clone https://github.com/your-org/ColDBase.git
cd ColDBase
```

### 2. Review Configuration
```bash
# Copy template config
cp .env.template .env

# (Optional) Edit .env for custom models/ports
cat .env
# ZEROCLAW_MODEL=phi4-mini
# ZEROCLAW_MODEL_COORDINATOR=tinyllama
```

### 3. Bootstrap the System
```bash
chmod +x startup.sh cleanup.sh
./startup.sh
```

This will:
- Validate Docker and resources
- Build ZeroClaw from source (vendor/zeroclaw/)
- Start Ollama + ZeroClaw gateway
- Pre-pull configured models
- Initialize workspace memory files

### 4. Verify Services Are Running
```bash
docker compose ps
# Should show: ollama (healthy), zeroclaw (healthy)

curl http://localhost:11434/api/tags   # Ollama list
curl http://localhost:42617/status     # ZeroClaw status
```

### 5. (Optional) Start Agent Services
```bash
# Start all agents
docker compose up -d coordinator coder deployer tester

# Or start individually
docker compose up -d coordinator

# Health check
curl http://localhost:8001/health
curl http://localhost:8002/health
```

---

## Project Structure

```
ColDBase/
├── README.md                    # User-facing overview
├── QUICK_START.md              # Get-started guide
├── ARCHITECTURE.md             # System design (new)
├── DEVELOPMENT.md              # This file
├── roadmap.md                  # Project status
├── PRE_COMMIT_AUDIT.md         # Pre-commit checklist (new)
│
├── docker-compose.yml          # Service definitions
├── Dockerfile                  # (unused in current stack)
├── startup.sh                  # Bootstrap script
├── cleanup.sh                  # Resource cleanup
│
├── agents/                     # Agent orchestration layer
│   ├── README.md              # Agent system docs (new)
│   ├── app.py                 # FastAPI agent logic (774 lines)
│   ├── Dockerfile             # Agent container build
│   ├── requirements.txt        # Python dependencies
│   ├── roles.yml              # Role definitions
│   ├── delegation_audit.schema.json  # Audit log schema
│   └── tests/
│       ├── test_validator_audit.py
│       ├── test_audit_schema.py
│       ├── test_deploy_compose.py
│       └── test_smoke.py
│
├── scripts/
│   ├── init_memory.py         # Memory schema initialization
│   └── init_memory.sh         # Shell wrapper
│
├── vendor/
│   └── zeroclaw/              # Vendored upstream library (Rust)
│       ├── src/               # Rust source
│       ├── Cargo.toml
│       ├── Dockerfile
│       └── ...
│
└── workspace/                 # Shared state (bind-mounted in containers)
    ├── memory/
    │   ├── agent_state.json
    │   ├── agent_state.schema.json
    │   ├── task_queue.json
    │   └── shared_context.md
    ├── logs/
    │   ├── delegation.log     # Audit trail
    │   └── *.log              # Per-agent logs
    ├── projects/              # User projects
    ├── zeroclaw-data/         # ZeroClaw config
    └── delegated/             # Test outputs (generated)
```

---

## Development Workflow

### 1. Make Code Changes
```bash
# Edit agents/app.py or other source files
vim agents/app.py

# Changes are immediately visible (no rebuild needed for Python)
```

### 2. Restart Affected Service (if needed)
```bash
# For app.py changes, restart the agent
docker compose restart coordinator

# Or rebuild and restart
docker compose build --no-cache coordinator
docker compose up -d coordinator
```

### 3. View Logs
```bash
# Stream coordinator logs
docker compose logs -f coordinator

# View coder logs with tail
docker compose logs --tail 50 coder

# All agent logs
docker compose logs -f coordinator coder deployer tester

# Exit with Ctrl+C
```

### 4. Test Your Changes (locally)
```bash
# If you have Python 3.8+ installed locally:
cd agents
pip install -r requirements.txt

# Run tests
pytest tests/ -v

# Run specific test
pytest tests/test_validator_audit.py::test_write_file_valid -v
```

### 5. Test in Container
```bash
# Exec into running container
docker exec -it zeroclaw-coordinator bash

# Inside container:
python3 -m pytest /app/tests/ -v

# Or test via API
curl http://localhost:8001/task -X POST -H "Content-Type: application/json" \
  -d '{"task":"run_action","action":"http_check","params":{"url":"http://google.com"}}'
```

### 6. Commit & Push
```bash
# Check status
git status

# Stage your changes
git add agents/app.py

# Commit with clear message
git commit -m "feat: add new delegation task for agents

- Implements task_foo for coder agent
- Adds validation constraints (path length, extension)
- Adds unit test in test_validator_audit.py
- Updates agents/README.md with example usage"

# Push
git push origin feature/task-foo
```

---

## Common Development Tasks

### Add a New Delegation Task

**Example: Add `format_code` task for Coder agent**

#### Step 1: Update validation rules
**File:** `agents/app.py` (line ~240)
```python
def validate_delegation(target_role: str, task: str, payload: dict) -> Tuple[bool, Optional[str]]:
    allowed = {
        "coder": ["write_file", "format_code"],  # ← Add here
        ...
    }
    
    # Add validation for the new task
    if task == "format_code":
        path = (payload.get("path") or "").strip()
        if not path:
            return False, "format_code requires 'path'"
        # ... more validation
```

#### Step 2: Implement execution
**File:** `agents/app.py` (line ~750)
```python
def perform_action(action: str, params: dict):
    ...
    if action == "format_code":
        path = params.get("path")
        lang = params.get("language", "python")
        full = os.path.join(WORKSPACE_DIR, path.lstrip("/"))
        
        # Run formatter
        if lang == "python":
            import subprocess
            result = subprocess.run(["black", full], capture_output=True)
            return {"formatted": full, "success": result.returncode == 0}
        
        return {"error": f"format not supported for {lang}"}
```

#### Step 3: Write unit tests
**File:** `agents/tests/test_validator_audit.py`
```python
def test_format_code_valid():
    payload = {"path": "workspace/main.py", "language": "python"}
    ok, reason = app.validate_delegation("coder", "format_code", payload)
    assert ok is True, reason

def test_format_code_missing_path():
    payload = {"language": "python"}
    ok, reason = app.validate_delegation("coder", "format_code", payload)
    assert not ok
    assert "path" in reason
```

#### Step 4: Run tests
```bash
pytest agents/tests/test_validator_audit.py -v

# Verify the new tests pass
# test_format_code_valid PASSED
# test_format_code_missing_path PASSED
```

#### Step 5: Document
Update `agents/README.md` under "Supported Tasks" section:
```markdown
#### Coder → format_code
```json
{
  "action": "format_code",
  "params": {
    "path": "workspace/main.py",      # Required
    "language": "python"              # Optional, default "python"
  }
}
```

### Add a New Agent Role

**Example: Add a `researcher` agent for external lookups**

#### Step 1: Update roles.yml
**File:** `agents/roles.yml`
```yaml
researcher:
  role: "research"
  description: "Performs web searches and API queries"
  capabilities:
    - search_web
    - query_api
  max_context_tokens: 2048
  timeout_seconds: 60
```

#### Step 2: Add to docker-compose.yml
**File:** `docker-compose.yml`
```yaml
researcher:
  build:
    context: ./agents
  container_name: zeroclaw-researcher
  environment:
    ROLE: researcher
    ZEROCLAW_MODEL: ${ZEROCLAW_MODEL_RESEARCHER:-tinyllama}
    WORKSPACE_DIR: /workspace
  volumes:
    - ./workspace:/workspace
  ports:
    - "8005:8000"
  healthcheck:
    test: ["CMD-SHELL", "curl -f http://localhost:8000/health || exit 1"]
    interval: 15s
    timeout: 5s
    retries: 3
    start_period: 10s
  deploy:
    resources:
      limits:
        cpus: '0.25'
        memory: 256M
  restart: unless-stopped
  networks:
    - zeroclaw-network
```

#### Step 3: Update validation
**File:** `agents/app.py` (~230)
```python
allowed = {
    ...
    "researcher": ["search_web", "query_api"],
}
```

#### Step 4: Import and start
```bash
docker compose up -d researcher

# Verify
curl http://localhost:8005/health
```

### Modify Resource Limits

**File:** `docker-compose.yml`

**Example: Increase Coder memory to 512M for large projects**
```yaml
coder:
  ...
  deploy:
    resources:
      limits:
        cpus: '0.5'          # ← Increased from 0.25
        memory: 512M         # ← Increased from 256M
```

**Then restart:**
```bash
docker compose up -d --force-recreate coder
```

### Change Default Model

```bash
# Edit .env
echo "ZEROCLAW_MODEL=orca-mini" >> .env

# Rebuild and restart
docker compose up -d --force-recreate zeroclaw coordinator

# Verify
docker exec zeroclaw zeroclaw status
```

---

## Testing

### Unit Tests (Fast)
```bash
# All tests
pytest agents/tests/ -v

# Specific file
pytest agents/tests/test_validator_audit.py -v

# Coverage
pytest agents/tests/ --cov=agents --cov-report=term-missing
```

### Integration Tests (Requires Services)
```bash
# Start services first
docker compose up -d ollama zeroclaw coordinator

# Run smoke tests
pytest agents/tests/test_smoke.py -v

# Test delegation chain
curl -X POST http://localhost:8001/task \
  -H "Content-Type: application/json" \
  -d '{
    "task": "delegate",
    "payload": {
      "role": "tester",
      "task": "file_check",
      "payload": {"path": "workspace/README.md"}
    }
  }'
```

### End-to-End Tests
```bash
# Full stack up
./startup.sh

# Run e2e workflow
docker exec zeroclaw-coordinator python3 -c "
import requests
# Test delegation chain
r = requests.post('http://localhost:8000/task', json={
    'task': 'delegate',
    'payload': {
        'role': 'coder',
        'task': 'write_file',
        'payload': {
            'path': 'workspace/e2e/test.md',
            'content': '# E2E Test\nPassed!'
        }
    }
})
print(r.json())
"

# Verify file was created
cat workspace/e2e/test.md
```

---

## Debugging Tips

### 1. Enable Debug Logging
```bash
# Edit docker-compose.yml
environment:
  RUST_LOG: debug              # Or "debug,zeroclaw=trace"
  ZEROCLAW_DEBUG: 1

# Restart
docker compose up -d
docker compose logs -f zeroclaw
```

### 2. Inspect Memory State
```bash
# View agent state
cat workspace/memory/agent_state.json | jq .

# View task queue
cat workspace/memory/task_queue.json | jq .

# View audit trail
tail -20 workspace/logs/delegation.log | jq .
```

### 3. Test Validator Rules Directly
```bash
# Enter the container
docker exec -it zeroclaw-coordinator bash

# Interactive Python test
python3 << 'EOF'
import sys
sys.path.insert(0, '/app')
from app import validate_delegation

# Test write_file with invalid extension
ok, reason = validate_delegation("coder", "write_file", 
    {"path": "test.exe", "content": "..."})
print(f"Result: {ok}, Reason: {reason}")  # Should fail

# Test http_check with private IP
ok, reason = validate_delegation("tester", "http_check",
    {"url": "http://192.168.1.1"})
print(f"Result: {ok}, Reason: {reason}")  # Should fail
EOF
```

### 4. Capture Network Traffic
```bash
# View requests between agents
docker exec -it zeroclaw-coordinator bash

# Inside container, use curl with verbose
curl -v http://localhost:8002/task

# Or inspect container network
docker network inspect zeroclaw-network
```

### 5. Check Resource Usage
```bash
# Real-time stats
docker stats

# Specific container
docker stats zeroclaw-coordinator --no-stream

# Memory pressure
free -h  # Host memory
docker exec zeroclaw ps aux | grep python  # Container processes
```

---

## Performance Optimization

### Model Selection
- **Fast/Light:** `tinyllama` (~1GB), best for Coordinator & Tester
- **Balanced:** `phi4-mini` (~1GB), good for Coder & Deployer
- **Heavy/Accurate:** `orca-mini` (~7GB), not recommended for constrained envs

### Resource Tuning (for 4GB Codespace)
```bash
# Light configuration
ZEROCLAW_MODEL=tinyllama
ZEROCLAW_MODEL_COORDINATOR=tinyllama
ZEROCLAW_MODEL_CODER=tinyllama
ZEROCLAW_MODEL_DEPLOYER=tinyllama
ZEROCLAW_MODEL_TESTER=tinyllama

# Skip optional services
docker compose up -d ollama zeroclaw coordinator

# Reduce container limits
# In docker-compose.yml, set all to 0.125 CPU, 128M RAM
```

### Caching
- Ollama models cached in named volume `ollama-data`
- ZeroClaw build cached (subsequent builds faster)
- Workspace files local (no cloud latency)

---

## Troubleshooting

### "Services not starting"
```bash
# Check docker
docker ps
docker compose logs

# Restart from scratch
./cleanup.sh
./startup.sh
```

### "Agent responds with 500 error"
```bash
# Check logs
docker compose logs coordinator

# Validate delegation manually
docker exec -it zeroclaw-coordinator python3
>>> from app import validate_delegation
>>> validate_delegation("coder", "write_file", {"path": "test.txt", "content": "..."})
```

### "Out of memory (OOM)"
```bash
# Check usage
docker stats

# Reduce model/services
docker compose down coordinator coder deployer tester
docker compose up -d coordinator  # Just coordinator

# Or use lighter model
docker compose env ZEROCLAW_MODEL=tinyllama up -d
```

### "Delegation fails with 'role not found'"
```bash
# Check service is running
docker ps | grep zeroclaw-coder

# Check network connectivity
docker exec zeroclaw-coordinator ping zeroclaw-coder

# Check port
docker exec zeroclaw-coordinator curl http://zeroclaw-coder:8000/health
```

---

## Code Style & Standards

- **Python:** Follow PEP 8 (use `black` for auto-formatting)
- **JSON:** 2-space indentation, validate against schema
- **YAML:** 2-space indentation, no tabs
- **Rust:** Standard cargo fmt (ZeroClaw upstream handles)

### Pre-commit Checks
```bash
# Before committing
pytest agents/tests/ -v          # Tests pass?
python3 -m black agents/app.py   # Code formatted?
python3 -m pylint agents/app.py  # No obvious errors?
git status                       # No large files?
```

---

## Getting Help

1. **Check logs:** `docker compose logs -f <service>`
2. **Read ARCHITECTURE.md:** System design & concepts
3. **Read agents/README.md:** Agent task reference
4. **Check roadmap.md:** Current status & known issues
5. **Review tests:** agents/tests/ have examples of valid/invalid inputs
6. **Ask on issue tracker:** If stuck

---

## Next Steps

- [ ] Complete "Hello-World E2E workflow" (item 8 on roadmap)
- [ ] Run full autonomous POC and collect metrics
- [ ] Add more delegation tasks (email, API calls, etc.)
- [ ] Integrate with external LLM providers (OpenAI, Claude)
- [ ] Build web dashboard for audit trail visualization

