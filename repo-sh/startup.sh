#!/bin/bash

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

RECOMMENDED_DISK_GB="${RECOMMENDED_DISK_GB:-5}"
MIN_REQUIRED_DISK_GB="${MIN_REQUIRED_DISK_GB:-3}"
REQUIRED_RAM_GB="${REQUIRED_RAM_GB:-2}"
OPTIONAL_SERVICES=(coder deployer tester)

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
echo "  Thresholds: RAM >= ${REQUIRED_RAM_GB}GB (warn), disk >= ${MIN_REQUIRED_DISK_GB}GB (required), ${RECOMMENDED_DISK_GB}GB recommended"

get_available_ram_gb() {
  free -g | awk '/^Mem:/ {print $7}'
}

get_available_disk_gb() {
  df /workspaces 2>/dev/null | awk 'NR==2 {print int($4/1024/1024)}'
}

print_resource_status() {
  local available_ram="$1"
  local available_disk="$2"

  echo -n "  Available RAM..."
  if [ "${available_ram:-0}" -lt "$REQUIRED_RAM_GB" ]; then
    echo -e " ${YELLOW}LOW${NC} (${available_ram}GB available)"
    echo "  Continuing, but the first model load may be slow or unstable."
  else
    echo -e " ${GREEN}OK${NC} (${available_ram}GB available)"
  fi

  echo -n "  Available disk..."
  if [ "${available_disk:-0}" -lt "$MIN_REQUIRED_DISK_GB" ]; then
    echo -e " ${RED}LOW${NC} (${available_disk}GB available)"
  elif [ "${available_disk:-0}" -lt "$RECOMMENDED_DISK_GB" ]; then
    echo -e " ${YELLOW}TIGHT${NC} (${available_disk}GB available)"
    echo "  Proceeding, but builds and model pulls may exhaust disk."
  else
    echo -e " ${GREEN}OK${NC} (${available_disk}GB available)"
  fi
}

cleanup_optional_resources() {
  local optional_running=()

  for service in "${OPTIONAL_SERVICES[@]}"; do
    if $DOCKER_COMPOSE ps --status running --services 2>/dev/null | grep -qx "$service"; then
      optional_running+=("$service")
    fi
  done

  echo ""
  echo -e "${YELLOW}[*] Cleanup pass${NC}"
  echo "  Reclaiming Docker build cache and releasing optional agent services from prior test runs."

  docker system df || true

  if [ "${#optional_running[@]}" -gt 0 ]; then
    echo "  Stopping optional services: ${optional_running[*]}"
    $DOCKER_COMPOSE rm -s -f "${optional_running[@]}" >/dev/null 2>&1 || true
  else
    echo "  Optional services already released."
  fi

  echo "  Reclaiming Docker build cache..."
  docker builder prune -af >/dev/null 2>&1 || true

  echo "  Docker usage after cleanup:"
  docker system df || true
}

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

AVAILABLE_RAM=$(get_available_ram_gb)
AVAILABLE_DISK=$(get_available_disk_gb)
print_resource_status "$AVAILABLE_RAM" "$AVAILABLE_DISK"

if [ "${AVAILABLE_DISK:-0}" -lt "$MIN_REQUIRED_DISK_GB" ]; then
  cleanup_optional_resources

  echo ""
  echo -e "${YELLOW}[*] Re-checking resources${NC}"
  AVAILABLE_RAM=$(get_available_ram_gb)
  AVAILABLE_DISK=$(get_available_disk_gb)
  print_resource_status "$AVAILABLE_RAM" "$AVAILABLE_DISK"

  if [ "${AVAILABLE_DISK:-0}" -lt "$MIN_REQUIRED_DISK_GB" ]; then
    echo "  Cleanup completed, but free disk is still below the ${MIN_REQUIRED_DISK_GB}GB minimum."
    echo "  Optional services can be started later after more space is available."
    exit 1
  fi
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
api_url = "http://ollama-brain:11434"
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
echo -e "${YELLOW}[*] Initializing workspace memory${NC}"
if command -v python3 >/dev/null 2>&1; then
  if python3 scripts/init_memory.py >/dev/null 2>&1; then
    echo -e "  ${GREEN}memory initialized${NC}"
  else
    echo -e "  ${YELLOW}memory init failed (continuing)${NC}"
  fi
else
  echo -e "  ${YELLOW}python3 not found, skipping memory init${NC}"
fi

echo -e "${YELLOW}[*] Building ZeroClaw image${NC}"
$DOCKER_COMPOSE build zeroclaw

echo ""
echo -e "${YELLOW}[*] Starting Ollama${NC}"
$DOCKER_COMPOSE up -d ollama

wait_for_url() {
  local url="$1"
  local retries=${2:-60}
  local sleep_sec=${3:-2}
  local i
  for i in $(seq 1 $retries); do
    if curl -sf "$url" >/dev/null 2>&1; then
      return 0
    fi
    echo -n "."
    sleep $sleep_sec
  done
  return 1
}

echo -n "  Waiting for Ollama"
if ! wait_for_url "http://localhost:11434/api/tags" 60 2; then
  echo ""
  echo -e "${RED}Ollama did not become ready in time.${NC}"
  exit 1
fi
echo -e " ${GREEN}ready${NC}"

echo ""
OLLAMA_MODELS_PREPULL="${OLLAMA_MODELS_PREPULL:-${ZEROCLAW_MODEL}}"
if [ -z "${OLLAMA_MODELS_PREPULL:-}" ] || [ "${OLLAMA_MODELS_PREPULL}" = "auto" ]; then
  echo -e "${YELLOW}[*] Skipping model pre-pull because OLLAMA_MODELS_PREPULL is empty or 'auto'${NC}"
else
  echo -e "${YELLOW}[*] Pre-pulling Ollama models: ${OLLAMA_MODELS_PREPULL}${NC}"
  IFS=',' read -ra MODELS <<<"${OLLAMA_MODELS_PREPULL}"
  for m in "${MODELS[@]}"; do
    model=$(echo "$m" | xargs)
    if [ -z "$model" ]; then
      continue
    fi
    echo -n "  Pulling ${model}"
    if docker exec ollama-brain ollama pull "${model}" >/dev/null 2>&1; then
      echo -e " ${GREEN}done${NC}"
    else
      echo -e " ${YELLOW}failed${NC} (continuing)"
    fi
  done
fi

echo ""
echo -e "${YELLOW}[*] Starting ZeroClaw gateway${NC}"
$DOCKER_COMPOSE up -d zeroclaw

echo -n "  Waiting for ZeroClaw"
if ! wait_for_url "http://localhost:${ZEROCLAW_GATEWAY_PORT}/" 120 2; then
  # fallback to in-container status check
  if docker exec zeroclaw zeroclaw status --format=exit-code >/dev/null 2>&1; then
    echo -e " ${GREEN}ready${NC}"
  else
    echo ""
    echo -e "${RED}ZeroClaw did not become ready in time.${NC}"
    echo "Inspect logs with: $DOCKER_COMPOSE logs zeroclaw"
    exit 1
  fi
else
  echo -e " ${GREEN}ready (http)${NC}"
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
