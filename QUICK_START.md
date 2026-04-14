# ZeroClaw + Ollama Quick Start

This repo now builds a real local ZeroClaw image from vendored upstream source in `vendor/zeroclaw/`.

## What It Starts

- `ollama` on `http://localhost:11434`
- `zeroclaw` gateway on `http://localhost:42617`

The root compose file currently defines only these two services. If you see older containers such as `zeroclaw-coder` or `zeroclaw-coordinator`, they are stale containers from a previous compose state and can be removed with:

```bash
docker compose up -d --remove-orphans
```

## First Run

```bash
chmod +x startup.sh
./startup.sh
```

`startup.sh` will:

- validate Docker and local resources
- build the ZeroClaw image from source
- start Ollama
- pull the configured Ollama model
- start the ZeroClaw gateway
- write the runtime config to `workspace/zeroclaw-data/.zeroclaw/config.toml`
- restrict that config file to mode `600`

## Useful Commands

```bash
docker compose logs -f zeroclaw
docker exec -it zeroclaw zeroclaw status
docker exec -it zeroclaw zeroclaw agent
docker exec -it ollama-brain ollama list
```

## Configuration

Defaults live in `.env.template`.

Common overrides:

```bash
ZEROCLAW_MODEL=phi4-mini
ZEROCLAW_GATEWAY_PORT=42617
HOST_PORT=42617
```

## Health Checks

- `ollama` is considered healthy when `ollama list` succeeds inside the container.
- `zeroclaw` is considered healthy when `zeroclaw status --format=exit-code` succeeds inside the container.

If Ollama shows as unhealthy but its logs show successful `/api/tags` responses, verify that the healthcheck is not using `curl` inside the Ollama image. The current compose file already uses the built-in `ollama` CLI for this reason.

## Build Notes

- Compose builds from `vendor/zeroclaw/Dockerfile` with the `dev` target.
- The vendored Dockerfile is patched locally to include `crates/zeroclaw-macros`, which upstream currently omits during the cached dependency build.
- The initial Rust build is heavy. In this environment the successful image build took roughly 10 minutes after dependencies were resolved.
