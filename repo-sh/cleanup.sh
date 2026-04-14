#!/bin/bash

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

OPTIONAL_SERVICES=(coder deployer tester)

echo -e "${BLUE}╔════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  ZeroClaw Cleanup Helper                              ║${NC}"
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

echo -e "${YELLOW}[*] Current state${NC}"
$DOCKER_COMPOSE ps || true
echo ""
docker system df || true

echo ""
echo -e "${YELLOW}[*] Releasing optional services${NC}"
if $DOCKER_COMPOSE ps --services 2>/dev/null | grep -Eq '^(coder|deployer|tester)$'; then
  $DOCKER_COMPOSE rm -s -f "${OPTIONAL_SERVICES[@]}" >/dev/null 2>&1 || true
  echo -e "  ${GREEN}Released optional services: ${OPTIONAL_SERVICES[*]}${NC}"
else
  echo "  Optional services not present."
fi

echo ""
echo -e "${YELLOW}[*] Removing compose orphans${NC}"
$DOCKER_COMPOSE up -d --remove-orphans >/dev/null 2>&1 || true
echo -e "  ${GREEN}Orphan cleanup complete${NC}"

echo ""
echo -e "${YELLOW}[*] Reclaiming Docker build cache${NC}"
docker builder prune -af >/dev/null 2>&1 || true
echo -e "  ${GREEN}Build cache pruned${NC}"

echo ""
echo -e "${YELLOW}[*] Final state${NC}"
$DOCKER_COMPOSE ps || true
echo ""
docker system df || true

echo ""
echo -e "${GREEN}Cleanup complete.${NC}"
echo "Next: ./startup.sh"
