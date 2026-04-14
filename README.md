# ColDBase

Local ZeroClaw + Ollama workspace.

Current stack:
- `ollama` on `http://localhost:11434`
- `zeroclaw` gateway on `http://localhost:42617`

This repository's root `docker-compose.yml` currently defines only those two services. Older multi-agent containers such as `zeroclaw-coder` or `zeroclaw-coordinator` are not part of the active compose state.

Quick start:

```bash
chmod +x startup.sh
./startup.sh
```

Useful commands:

```bash
docker compose ps
docker compose logs -f ollama zeroclaw
docker exec -it ollama-brain ollama list
docker exec -it zeroclaw zeroclaw status
```

Configuration defaults live in [.env.template](/workspaces/ColDBase/.env.template).

The coordinator now resolves its Ollama model from installed tags at runtime. If `ollama list` is empty, task generation will fail until you pull a model.
