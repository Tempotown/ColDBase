# Shared Context

This document contains shared, human-readable context and high-level notes that agents may read and append to.

Usage guidelines:

- Keep entries brief and dated.
- When an agent appends structured knowledge, include a JSON summary in code fences.
- Prefer append-only edits; use `agent_state.json` and `task_queue.json` for programmatic state.

Example entry:

```
2026-04-14 — Coordinator: Initialized memory system. Seeded `agent_state.json`.
{
  "summary": "Initialized memory schema and task queue"
}
```
