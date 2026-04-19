# Project TODOs — synchronized with Copilot

This file is auto-synced with the interactive to-do tracker used by the assistant. Each entry includes status, short description, owner, and next action.

---

1. ✅ Validate Ollama models present
- Status: completed
- What: Verified `tinyllama` and other models are available via `ollama list` inside the `ollama` container.
- Evidence: model list returned `tinyllama:latest` and `phi4-mini:latest`.
- Owner: local (you / assistant)
- Next action: none.

2. ✅ Bring up compose stack and verify services
- Status: completed
- What: Brought up Docker Compose stack; verified `ollama`, `zeroclaw` and agents are running and healthy.
- Owner: local (you / assistant)
- Next action: monitor logs during development.

3. ✅ Switch to named ollama-data volume and document
- Status: completed
- What: Changed Ollama model cache to use named volume `ollama-data` to persist downloads across restarts.
- Owner: local (you / assistant)
- Next action: occasional volume inspection & cleanup when disk is low.

4. ✅ Implement coordinator model routing & action router
- Status: completed
- What: Coordinator reads role-specific `ZEROCLAW_MODEL_<ROLE>` env vars and routes JSON-action outputs into `perform_action` handlers.
- Owner: assistant
- Next action: add more action handlers as needed.

5. ✅ Harden delegation validator + add audit logging
- Status: completed
- What: Implement `validate_delegation(target_role, task, payload)` with whitelist rules; need structured audit log entries per delegation event.
- Why: Prevents unsafe delegated actions and provides an audit trail for review/debugging.
- Owner: assistant (implemented) / you (review policy)
- Evidence: `validate_delegation()` has whitelist per role (coder, deployer, tester), parameter constraints (path length, extensions, binary checks, URL validation, IP range checks), audit logging to `workspace/logs/delegation.log` via `write_audit_log()`, and endpoint `/audit/delegation` to retrieve entries.
- Next action: none (ready for use).

6. ✅ Finalize startup.sh (health-wait + model pre-pull)
- Status: completed
- What: `startup.sh` should validate Docker, wait for service healthchecks, and optionally pre-pull configured Ollama models to reduce boot time.
- Owner: assistant (implemented)
- Evidence: Pre-flight checks for disk/RAM, `wait_for_url()` health-check loops (Ollama 60s, ZeroClaw 120s), `OLLAMA_MODELS_PREPULL` env var (defaults to `ZEROCLAW_MODEL`), model pull loop with docker exec.
- Next action: none (ready for use).

7. ✅ Define shared memory schema and initialize files
- Status: completed
- What: Created `workspace/memory/agent_state.schema.json` (JSON Schema), `workspace/memory/agent_state.json` (seeded state), `workspace/memory/task_queue.json` (empty task queue), and `workspace/memory/shared_context.md` (human-readable context).
- Why: Provides a consistent, schema-validated store for agent status, tasks, and shared knowledge.
- Owner: assistant
- Evidence: files present at `workspace/memory/agent_state.schema.json`, `workspace/memory/agent_state.json`, `workspace/memory/task_queue.json`, `workspace/memory/shared_context.md`.
- Next action: add a small initialization script to validate and seed the memory on startup (recommended).

8. ✅ Create Hello-World E2E workflow test (Coordinator→Coder→Tester)
- Status: completed
- What: Minimal automated workflow: Coordinator plans, Coder writes a test file, Tester performs an `http_check` against it. Verifies delegation + action execution end-to-end.
- Why: Validates end-to-end delegation flow and action execution.
- Owner: assistant
- Evidence: `scripts/hello_world_e2e.py` implemented and verified against running containers. Health check issue in Dockerfile fixed to allow container healthiness.
- Next action: run full autonomous POC.

9. ✅ Tune resource profiles for Codespace operation
- Status: completed
- What: Adjust `docker-compose.yml` resource limits, memory profiles, and fallback strategies to avoid OOMs in Codespaces (4-8GB scenarios).
- Owner: you / assistant (recommendations)
- Evidence: Applied conservative limits to all services:
  - zeroclaw: 1.0 CPU, 1G memory
  - coordinator: 0.25 CPU, 256M memory
  - coder: 0.25 CPU, 256M memory
  - deployer: 0.25 CPU, 256M memory
  - tester: 0.125 CPU, 128M memory
  - Total baseline: ~1.875 CPU, 2.752GB (excludes Ollama model cache)
- Next action: run E2E test and monitor actual memory usage to validate; adjust if needed.

10. ⬜ Run full autonomous POC in Codespace and verify outputs
- Status: not-started
- What: Execute full demo (Hello-World → Nextcloud flow), gather logs, record success/failure rates, iterate on failure modes.
- Owner: you (trigger) / assistant (orchestrate & debug)
- Next action: complete item 8 (E2E harness), then run this end-to-end test and collect metrics.

---

How to interact with this todo list
- The assistant keeps the canonical in-memory tracker in sync; to update an item's status ask the assistant to mark it `in-progress` or `completed` and it will update both the tracker and this file.

File updated: auto-generated on 2026-04-14

11. ✅ Implement central delegation audit viewer endpoint
- Status: completed
- What: Added `GET /audit/delegation` to `agents/app.py`. Returns recent entries from `workspace/logs/delegation.log` as JSON (params: `n`, `tail`).
- Evidence: verified endpoint inside the coordinator container and saw recent entries including `request_id`, `host`, `tried_endpoints`, and `result_summary`.
- Owner: assistant
- Next action: optionally add access controls (API key or role check) and a simple UI.
