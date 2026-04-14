# ZeroClaw + Ollama Multi-Agent Setup - Implementation Session

> Historical planning log. Current repo state as of April 10, 2026: the active root `docker-compose.yml` defines `ollama` and `zeroclaw` only. Earlier references in this file to coordinator/sub-agent containers describe a prior design iteration, not the current running stack.

**Project**: Personal Cloud Service with Autonomous Multi-Agent Automation  
**Environment**: GitHub Codespaces Free Tier (4GB RAM / 32GB Disk)  
**Date Started**: April 10, 2026  
**Status**: ✅ PHASE 0 COMPLETE - PROCEED TO PHASE 1

---

## 🎬 LATEST UPDATE (April 10, 2026)

**Phase 0 Results** ✅✅✅
| Check | Result | Status |
|-------|--------|--------|
| **Docker** | v28.5.1 | ✅ Ready |
| **ZeroClaw Repo** | Active, well-maintained, cloned successfully | ✅ GOOD |
| **RAM Available** | 7.8GB total, 5.1GB available | ✅ BETTER THAN EXPECTED (not 4GB)! |
| **Disk** | 32GB total, 16GB available | ✅ Plenty |
| **CPU** | 2 cores | ✅ Adequate |
| **Ollama** | v0.20.4 installed, not running (expected) | ✅ Ready to start |

**🎉 HUGE WIN**: You actually have **7.8GB RAM**, not 4GB! This means we can run 4 agents comfortably without OOM.

**Phase 1 Assets Created** ✅
- ✅ Enhanced docker-compose.yml (resource limits, health checks, 4 agents)
- ✅ Optimized Dockerfile (multi-stage build for ZeroClaw)
- ✅ .env.template (all configurable options)
- ✅ startup.sh (automated validation & boot script)
- ✅ Directory structure (workspace, agents, skills, logs)
- ✅ Agent role definitions (roles.yml)
- ✅ Shared memory system (agent_state.json)

**Next**: Execute `./startup.sh` NOW (Phase 1 automation)

---

## 📋 Project Overview

**Goal**: Build an autonomous multi-agent Docker environment capable of deploying a complete personal cloud stack (Nextcloud + reverse proxy + database) with 80-90% success rate.

**Architecture**:
- 1× Ollama shared brain (lightweight LLM - 3-4B model)
- 1× Coordinator agent (orchestrator)
- 2-3× Specialized sub-agents (Coder, Deployer, Tester)
- Shared workspace, memory system, Docker services
- Full autonomous workflow capability

**Expected Timeline**:
- Phase 0 (Planning): 15 min
- Phase 1 (Foundation): 30 min
- Phase 2 (Agent Framework): 45 min
- Phase 3 (Cloud Stack): 60 min
- **Total**: ~2.5 hours to first working personal cloud

---

## 🚨 Critical Issues Identified

### Issue 1: Ollama Network Configuration
**Severity**: HIGH  
**Problem**: Plan shows Ollama running on host + Docker containers simultaneously; mixed execution context  
**Impact**: Service discovery failures, port conflicts, inter-service communication issues  
**Status**: ⬜ NOT ADDRESSED

- [ ] Clarify Ollama execution model (host process vs Docker container)
- [ ] Choose network strategy (host network mode vs bridge)
- [ ] Document API endpoint configuration for all agents

### Issue 2: Missing Dockerfile for ZeroClaw
**Severity**: HIGH  
**Problem**: docker-compose.yml references `build: .` but no Dockerfile exists  
**Impact**: Build will fail immediately  
**Status**: ⬜ NOT ADDRESSED

- [ ] Create Dockerfile for ZeroClaw compilation
- [ ] Test Dockerfile build
- [ ] Validate binary execution in container

### Issue 3: Ollama Model Persistence
**Severity**: MEDIUM  
**Problem**: Models re-downloaded on every container restart (3-6 min delay, bandwidth waste)  
**Impact**: Slow startup cycles, poor UX during iteration  
**Status**: ⬜ NOT ADDRESSED

- [ ] Add volume mount for Ollama model cache
- [ ] Document expected disk usage per model
- [ ] Test volume persistence

### Issue 4: Agent Memory & Context System Missing
**Severity**: MEDIUM  
**Problem**: Plan mentions "CLAUDE.md-style files" but no structure, access patterns, or format defined  
**Impact**: Agents can't reliably share context or maintain state  
**Status**: ⬜ NOT ADDRESSED

- [ ] Design memory file structure (JSON/Markdown format)
- [ ] Define agent access patterns (REST endpoint vs file system)
- [ ] Create initialization templates
- [ ] Document knowledge base format

### Issue 5: No Resource Monitoring or Auto-Scaling
**Severity**: MEDIUM  
**Problem**: 4GB RAM is hard limit; plan relies on manual intervention ("stop one sub-agent")  
**Impact**: OOM kills, poor reliability, crashes during long jobs  
**Status**: ⬜ NOT ADDRESSED

- [ ] Add Docker resource limits per container (1.2GB each)
- [ ] Create monitoring dashboard or script
- [ ] Implement auto-restart on failure
- [ ] Add memory usage tracking
- [ ] Create alert/scaling rules

### Issue 6: No Graceful Degradation
**Severity**: MEDIUM  
**Problem**: Plan doesn't handle agent failures or partial completion  
**Impact**: Incomplete deployments, orphaned processes, unclear failure modes  
**Status**: ⬜ NOT ADDRESSED

- [ ] Define fallback strategies per agent
- [ ] Create retry logic with exponential backoff
- [ ] Implement status reporting system
- [ ] Document recovery procedures

### Issue 7: Codespaces Port Forwarding Not Explicit
**Severity**: LOW  
**Problem**: Port forwarding requires explicit setup in free tier; not documented  
**Impact**: Coordinator web UI and services inaccessible without extra steps  
**Status**: ⬜ NOT ADDRESSED

- [ ] Document port forwarding setup
- [ ] Create automatic port exposure script
- [ ] Add connection validation

---

## ✅ Recommended Improvements

### Improvement 1: Enhanced docker-compose.yml
**Status**: ⬜ NOT STARTED

```yaml
Changes needed:
- Add resource limits (memory: 1.2G per container)
- Add health checks for service dependencies
- Add restart policies (on-failure)
- Add proper logging configuration
- Add environment variable templates
```

- [ ] Create improved docker-compose.yml v2

### Improvement 2: Dockerfile for ZeroClaw
**Status**: ⬜ NOT STARTED

```dockerfile
Requirements:
- Multi-stage build (optimize size)
- Cache dependencies separate from source
- Include Ollama client library
- Proper entrypoint configuration
```

- [ ] Create Dockerfile with multi-stage build

### Improvement 3: Shared Memory System
**Status**: ⬜ NOT STARTED

```
Structure needed:
workspace/memory/
  - agent_state.json (shared agent status)
  - shared_context.md (all agents' knowledge)
  - task_queue.json (pending work)
  - completed_tasks.md (audit trail)
  - error_log.json (debugging)
```

- [ ] Design memory schema (JSON format)
- [ ] Create initialization script
- [ ] Document access API

### Improvement 4: Health Checks & Monitoring
**Status**: ⬜ NOT STARTED

```
Add:
- Liveness probes for each service
- Readiness checks before agent creation
- Resource usage tracking
- Service discovery validation
```

- [ ] Create health check script
- [ ] Add prometheus metrics (optional)
- [ ] Create monitoring dashboard

### Improvement 5: Agent Communication Protocol
**Status**: ⬜ NOT STARTED

```
Design:
- REST API for inter-agent calls
- Message queue for async tasks (or gRPC for RPC)
- Request/response schema
- Error handling & timeouts
```

- [ ] Define communication schema
- [ ] Create service discovery mechanism
- [ ] Build CLI for coordinator interaction

### Improvement 6: Orchestration & Startup Script
**Status**: ⬜ NOT STARTED

```
Script should:
- Validate prerequisites (Docker, RAM available)
- Start services in dependency order
- Wait for health checks
- Initialize memory system
- Provide CLI interface
```

- [ ] Create startup script with validation
- [ ] Add shutdown cleanup
- [ ] Test error handling

### Improvement 7: Configuration Management
**Status**: ⬜ NOT STARTED

```
Add:
- .env template with all configurable options
- Model selection mechanism (not hardcoded)
- Resource allocation profiles (minimal/normal/aggressive)
- Agent role definitions
```

- [ ] Create .env.template
- [ ] Create config validation script
- [ ] Document all environment variables

---

## 🎯 Implementation Phases

### PHASE 0: Planning & Validation (15 min)
**Objective**: Verify foundation and answer key questions
**Status**: ✅ COMPLETE - ALL CHECKS PASSED

**Validation Results** ✅
```
✅ Docker v28.5.1 ready
✅ ZeroClaw repo: Active & cloned successfully
✅ RAM: 7.8GB available (UPGRADE from 4GB expectation!)
✅ Disk: 32GB total, 16GB available
✅ CPU: 2 cores
✅ Ollama: v0.20.4 installed, ready to start
✅ All directories created
✅ All config files ready
```

**Decisions Confirmed**
- [x] Ollama will run in Docker container (docker-compose)
- [x] REST API for inter-agent communication
- [x] Shared memory system via JSON files in volumes
- [x] Auto-restart + circuit breaker for failure recovery
- [x] Resource limits: 1.2-2GB per container (we have room!)

**Phase 0 Complete!** 🎉
Now proceed to Phase 1: Execute startup.sh

### PHASE 1: Foundation Setup (30 min)
**Objective**: Get core services running and validated

- [ ] Create improved docker-compose.yml with resource limits
- [ ] Create Dockerfile for ZeroClaw
- [ ] Set up workspace directory structure:
  ```
  workspace/
    agents/
    skills/
    memory/
    projects/
  ```
- [ ] Create .env configuration file
- [ ] Create startup validation script
- [ ] First test: `docker compose up -d` succeeds
- [ ] Verify Ollama container running & accessible
- [ ] Verify model pulls successfully
- [ ] Document any deviations from plan

### PHASE 2: Agent Framework (45 min)
**Objective**: Build agent infrastructure and memory system

- [ ] Create shared memory system (JSON schema)
- [ ] Create agent role definitions (JSON templates)
- [ ] Create Coordinator agent template
- [ ] Create Coder sub-agent template
- [ ] Create Deployer sub-agent template
- [ ] Create health check system
- [ ] Implement agent communication protocol (HTTP/REST skeleton)
- [ ] Create CLI for coordinator interaction
- [ ] First test: Single agent responds to prompt
- [ ] Test: Coordinator delegates to one sub-agent
- [ ] Test: Two sub-agents run simultaneously

### PHASE 3: Autonomous Workflows (60 min)
**Objective**: End-to-end personal cloud deployment

- [ ] Create "Hello World" workflow test
  - [ ] Coordinator plans steps
  - [ ] Coder creates test file
  - [ ] Deployer executes & reports
- [ ] Create Nextcloud deployment workflow
  - [ ] Coordinator architecture planning
  - [ ] Coder generates docker-compose.yml, .env, backup script
  - [ ] Deployer executes full deployment
  - [ ] Tester validates connectivity
  - [ ] Agents iterate on failures
- [ ] Test full 3-agent collaboration
- [ ] Test sub-agent failure & recovery
- [ ] Document success metrics (80-90% target)
- [ ] Create reusable workflow templates

---

## ❓ Critical Questions to Answer

### Q1: ZeroClaw Repository Status
**Current**: Unknown if repo is active  
**Action**: 
- [ ] Check GitHub: https://github.com/zeroclaw-labs/zeroclaw
- [ ] Verify recent commits
- [ ] Check for open issues
- [ ] Test build locally
**Decision Point**: If repo inactive → Build minimal agent framework ourselves

### Q2: Ollama Model Selection
**Current**: Plan specifies phi4-mini, but needs validation  
**Action**:
- [ ] Run `ollama list` to check available models
- [ ] Benchmark models on 4GB RAM:
  - [ ] phi4-mini (if available)
  - [ ] qwen3:1.7b
  - [ ] orca-mini:3b
  - [ ] Fallback: llama2 (well-tested)
- [ ] Choose model with best reasoning/speed trade-off
**Decision Point**: Select model by benchmark results

### Q3: Ollama Execution Model
**Current**: Plan mixes host + container execution  
**Options**:
- [ ] A) Ollama on host, containers access via localhost:11434
- [ ] B) Ollama in Docker, all agents containerized
- [ ] C) Hybrid: Ollama container with host network mode
**Decision Point**: Choose based on Codespaces networking constraints

### Q4: Agent Communication Protocol
**Current**: Not specified  
**Options**:
- [ ] A) REST API (simple, HTTP-based)
- [ ] B) gRPC (fast, structured)
- [ ] C) Message queue (async, decoupled)
- [ ] D) Shared memory files (simple, but slow)
**Decision Point**: Impact on implementation time & scalability

### Q5: Failure Recovery Strategy
**Current**: "Manual intervention"  
**Options**:
- [ ] A) Auto-restart failed containers (simple)
- [ ] B) Circuit breaker pattern (prevents cascading)
- [ ] C) Task queue with retry logic (robust)
**Decision Point**: Affects reliability target (80-90%)

### Q6: Monitoring Stack
**Current**: Manual `docker stats` + `htop`  
**Options**:
- [ ] A) Simple bash scripts (lightweight)
- [ ] B) Prometheus + Grafana (overkill?)
- [ ] C) In-memory dashboard (custom)
**Decision Point**: Trade-off between visibility & resource usage

### Q7: Agent Persistence
**Current**: Unknown  
**Questions**:
- [ ] Save agent logs to workspace?
- [ ] Persist agent state across Codespace restarts?
- [ ] Version control generated files?
**Decision Point**: Impacts reproducibility & debugging

---
## 📊 Progress Tracking

| Phase | Component | Status | Blocker | Notes |
|-------|-----------|--------|---------|-------|
| 0 | Q1: ZeroClaw Verification | ✅ VERIFIED | No blocker | Repo active, well-maintained |
| 0 | Q2: System Resources | ✅ VERIFIED | No blocker | 7.8GB RAM (bonus!), 32GB disk |
| 0 | Q3: Docker Setup | ✅ VERIFIED | No blocker | v28.5.1 ready |
| 0 | Q4: Ollama | ✅ VERIFIED | No blocker | v0.20.4 installed, ready to start |
| 0 | Q5: Network Architecture | ✅ DECIDED | No blocker | Container-based (docker-compose) |
| 0 | Q6: Communication protocol | ✅ DECIDED | No blocker | REST API |
| 0 | Q7: Project structure | ✅ COMPLETED | No blocker | All directories created |
| **PHASE 0 SUMMARY** | **ALL CHECKS PASSED** | **✅ READY** | **None** | **Proceed to Phase 1** |
| 1 | docker-compose.yml v2 | ✅ READY | No blocker | With resource limits & health checks |
| 1 | Dockerfile | ✅ READY | No blocker | Multi-stage build, optimized |
| 1 | Directory structure | ✅ READY | No blocker | workspace/, agents/, skills/, logs/ |
| 1 | .env.template | ✅ READY | No blocker | All options documented |
| 1 | startup.sh | ✅ READY | No blocker | 8-phase automated boot |
| 1 | Docker build & test | 🟡 NEXT | None | Run: ./startup.sh |
| 2 | Agent templates | ⬜ NOT STARTED | Phase 1 | After Docker boot |
| 2 | Health check system | ⬜ NOT STARTED | Phase 1 | After Docker boot |
| 3 | Hello World workflow | ⬜ NOT STARTED | Phase 2 | End-to-end test |
| 3 | Nextcloud deployment | ⬜ NOT STARTED | Phase 2 | Full cloud setup |

### STEP 3: Startup Script & Validation
- Pre-flight checks (RAM, Docker, ports)
- Service startup in correct order
- Health verification
- CLI interface for coordinator

### STEP 4: Shared Memory System
- JSON schema for agent state
- Access patterns (file-based or REST)
- Initialization templates
- Audit trail for completed tasks

### STEP 5: Agent Templates
- Coordinator agent
- Coder sub-agent
- Deployer sub-agent
- Communication protocol implementation

### STEP 6: End-to-End Workflows
- "Hello World" test
- Nextcloud deployment
- Error handling & recovery
- Success metrics

### STEP 7: Documentation & Optimization
- Complete README
- Troubleshooting guide
- Performance tuning
- Reusable templates

---

## 📊 Progress Tracking

| Phase | Component | Status | Blocker | Notes |
|-------|-----------|--------|---------|-------|
| 0 | Q1: ZeroClaw Verification | ⬜ NOT STARTED | None | |
| 0 | Q2: Model Selection | ⬜ NOT STARTED | None | |
| 0 | Q3: Network Architecture | ⬜ NOT STARTED | None | |
| 0 | .env.template | ⬜ NOT STARTED | Q1-Q3 | |
| 1 | docker-compose.yml v2 | ⬜ NOT STARTED | Q1-Q3 | |
| 1 | Dockerfile | ⬜ NOT STARTED | Q1 | |
| 1 | Directory structure | ⬜ NOT STARTED | None | |
| 1 | Startup validation script | ⬜ NOT STARTED | 1: docker-compose + Dockerfile | |
| 2 | Memory system schema | ⬜ NOT STARTED | None | |
| 2 | Coordinator template | ⬜ NOT STARTED | Q4: Communication protocol | |
| 2 | Sub-agent templates | ⬜ NOT STARTED | Q4 | |
| 2 | Health check system | ⬜ NOT STARTED | None | |
| 3 | Hello World workflow | ⬜ NOT STARTED | 2: All templates | |
| 3 | Nextcloud deployment | ⬜ NOT STARTED | 2: All templates | |

---

## 🚀 Ready to Begin?

**Next Action**: Start PHASE 0 (Answer the 7 critical questions)

**Instructions for you**:
1. Answer or confirm the 7 questions in "Critical Questions to Answer" section
2. We'll fill in the decision points
3. Use those decisions to build the improved stack

---curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh


## 📝 Notes & Decisions

### Decision Log
*To be filled in as we answer questions and make choices*

- [ ] Ollama execution model: TBD
- [ ] Agent communication protocol: TBD
- [ ] Model selection: TBD
- [ ] ZeroClaw approach: TBD

### Known Issues
- GitHub Codespaces networking may require port forwarding setup
- 4GB RAM is tight for 3 agents running simultaneously
- CPU-only inference means slower responses (expected)

### Dependencies
- Docker pre-installed in Codespaces ✅
- Rust build tools (may need install)
- Persistent internet for model download

---

**Last Updated**: April 10, 2026  
**Session Status**: PLANNING PHASE - Awaiting answers to Phase 0 questions
