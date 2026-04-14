#!/bin/bash

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

RECOMMENDED_DISK_GB="${RECOMMENDED_DISK_GB:-8}"
MIN_REQUIRED_DISK_GB="${MIN_REQUIRED_DISK_GB:-5}"
REQUIRED_RAM_GB="${REQUIRED_RAM_GB:-4}"

echo -e "${BLUE}╔════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  ZeroClaw + Ollama Local Bootstrap                    ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════╝${NC}"
echo ""

if command -v docker-compose >/dev/null 2>&1; then
  DOCKER_COMPOSE="docker-compose"
elif docker compose version >/dev/null 2>&1; then
  DOCKER_COMPOSE="docker compose"
else
  echo -e "${RED}Docker Compose is not available.${NC}"
  exit 1
fi

echo -e "${YELLOW}[*] Pre-flight checks${NC}"

echo -n "  Docker CLI..."
command -v docker >/dev/null 2>&1
echo -e " ${GREEN}OK${NC}"

echo -n "  Docker daemon..."
docker ps >/dev/null 2>&1
echo -e " ${GREEN}OK${NC}"

echo -n "  Vendored ZeroClaw source..."
if [ ! -f "vendor/zeroclaw/Cargo.toml" ]; then
  echo -e " ${RED}MISSING${NC}"
  echo "  Run the repository bootstrap again to vendor the upstream source."
  exit 1
fi
echo -e " ${GREEN}OK${NC}"

echo -n "  Available RAM..."
AVAILABLE_RAM=$(free -g | awk '/^Mem:/ {print $7}')
if [ "${AVAILABLE_RAM:-0}" -lt "$REQUIRED_RAM_GB" ]; then
  echo -e " ${YELLOW}LOW${NC} (${AVAILABLE_RAM}GB available)"
else
  echo -e " ${GREEN}OK${NC} (${AVAILABLE_RAM}GB available)"
fi

echo -n "  Available disk..."
AVAILABLE_DISK=$(df /workspaces 2>/dev/null | awk 'NR==2 {print int($4/1024/1024)}')
if [ "${AVAILABLE_DISK:-0}" -lt "$MIN_REQUIRED_DISK_GB" ]; then
  echo -e " ${RED}LOW${NC} (${AVAILABLE_DISK}GB available)"
  echo "  Need at least ${MIN_REQUIRED_DISK_GB}GB free on /workspaces."
  exit 1
fi
if [ "${AVAILABLE_DISK:-0}" -lt "$RECOMMENDED_DISK_GB" ]; then
  echo -e " ${YELLOW}TIGHT${NC} (${AVAILABLE_DISK}GB available)"
  echo "  Proceeding, but builds and model pulls may exhaust disk."
else
  echo -e " ${GREEN}OK${NC} (${AVAILABLE_DISK}GB available)"
fi

if [ ! -f ".env" ] && [ -f ".env.template" ]; then
  cp .env.template .env
  echo "  Created .env from .env.template"
fi

mkdir -p workspace/zeroclaw-data/.zeroclaw workspace/zeroclaw-data/workspace logs

set -a
if [ -f ".env" ]; then
  . ./.env
fi
set +a

ZEROCLAW_MODEL="${ZEROCLAW_MODEL:-phi4-mini}"
ZEROCLAW_GATEWAY_PORT="${ZEROCLAW_GATEWAY_PORT:-42617}"

cat > workspace/zeroclaw-data/.zeroclaw/config.toml <<EOF
workspace_dir = "/zeroclaw-data/workspace"
config_path = "/zeroclaw-data/.zeroclaw/config.toml"
api_key = "http://ollama-brain:11434"
default_provider = "ollama"
default_model = "${ZEROCLAW_MODEL}"
default_temperature = 0.7

[gateway]
port = ${ZEROCLAW_GATEWAY_PORT}
host = "[::]"
allow_public_bind = true
require_pairing = false
EOF

chmod 644 workspace/zeroclaw-data/.zeroclaw/config.toml

echo ""
echo -e "${YELLOW}[*] Building ZeroClaw image${NC}"
$DOCKER_COMPOSE build zeroclaw

echo ""
echo -e "${YELLOW}[*] Starting Ollama${NC}"
$DOCKER_COMPOSE up -d ollama

echo -n "  Waiting for Ollama"
for _ in $(seq 1 60); do
  if curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo -e " ${GREEN}ready${NC}"
    break
  fi
  echo -n "."
  sleep 2
done

if ! curl -sf http://localhost:11434/api/tags >/dev/null 2>&1; then
  echo ""
  echo -e "${RED}Ollama did not become ready in time.${NC}"
  exit 1
fi

echo ""
if [ "${ZEROCLAW_MODEL}" = "auto" ]; then
  echo -e "${YELLOW}[*] Skipping model pull because ZEROCLAW_MODEL=auto${NC}"
else
  echo -e "${YELLOW}[*] Pulling Ollama model: ${ZEROCLAW_MODEL}${NC}"
  docker exec ollama-brain ollama pull "${ZEROCLAW_MODEL}"
fi

echo ""
echo -e "${YELLOW}[*] Starting ZeroClaw gateway${NC}"
$DOCKER_COMPOSE up -d zeroclaw

echo -n "  Waiting for ZeroClaw"
READY=0
# Try both the in-container status command and an HTTP root check as a fallback.
for _ in $(seq 1 120); do
  if docker exec zeroclaw zeroclaw status --format=exit-code >/dev/null 2>&1; then
    READY=1
    echo -e " ${GREEN}ready${NC}"
    break
  fi
  if curl -sf "http://localhost:${ZEROCLAW_GATEWAY_PORT}/" >/dev/null 2>&1; then
    READY=1
    echo -e " ${GREEN}ready (http)${NC}"
    break
  fi
  echo -n "."
  sleep 2
done

if [ "$READY" -ne 1 ]; then
  echo ""
  echo -e "${RED}ZeroClaw did not become ready in time.${NC}"
  echo "Inspect logs with: $DOCKER_COMPOSE logs zeroclaw"
  exit 1
fi

echo ""
echo -e "${GREEN}System ready.${NC}"
echo "  Gateway: http://localhost:${ZEROCLAW_GATEWAY_PORT}"
echo "  Ollama:  http://localhost:11434"
echo ""
echo "Useful commands:"
echo "  $DOCKER_COMPOSE logs -f zeroclaw"
echo "  docker exec -it zeroclaw zeroclaw agent"
echo "  docker exec -it zeroclaw zeroclaw status"
