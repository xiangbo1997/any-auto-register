#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SERVICE="${1:-app}"

if docker compose version >/dev/null 2>&1; then
  echo "[INFO] Using docker compose v2"
  docker compose up -d --build "$SERVICE"
  exit 0
fi

if ! command -v docker-compose >/dev/null 2>&1; then
  echo "[ERROR] Neither 'docker compose' nor 'docker-compose' is available." >&2
  exit 1
fi

COMPOSE_VERSION="$(docker-compose version --short 2>/dev/null || true)"
echo "[INFO] Using docker-compose ${COMPOSE_VERSION:-unknown}"

case "$COMPOSE_VERSION" in
  1.*)
    echo "[INFO] docker-compose 1.x detected; using compatibility path: down -> up"
    docker-compose down
    docker-compose up -d --build "$SERVICE"
    ;;
  *)
    docker-compose up -d --build "$SERVICE"
    ;;
esac
