#!/usr/bin/env bash
# Reproducible deployment for the Research KB library + Syncthing.
# Non-destructive: only creates files under RESEARCHKB_HOME and starts the
# research-kb compose project. Requires RESEARCHKB_HOME/REPORTS/DISCORD.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$REPO_DIR/docker-compose.yml"

# Load local (git-ignored) .env if present.
if [ -f "$REPO_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$REPO_DIR/.env"
    set +a
fi

: "${RESEARCHKB_HOME:?set RESEARCHKB_HOME to the deployment data directory}"
: "${RESEARCHKB_REPORTS:?set RESEARCHKB_REPORTS to the reports archive root}"
: "${RESEARCHKB_DISCORD:?set RESEARCHKB_DISCORD to the discord export root}"

mkdir -p "$RESEARCHKB_HOME"/{vault,catalog,state,backups}

echo "== compose config =="
docker compose -f "$COMPOSE_FILE" config >/dev/null

echo "== build + up =="
docker compose -f "$COMPOSE_FILE" up -d --build

echo "== status =="
docker compose -f "$COMPOSE_FILE" ps
