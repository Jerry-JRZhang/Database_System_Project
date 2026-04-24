#!/usr/bin/env bash
# Switch EquityDB between demo (unlimited) and limited (I/O-pressure) modes.
#
#   scripts/mode.sh demo       -> full host RAM, fast warm queries
#   scripts/mode.sh limited    -> 1 GB cap per container, pages spill to disk
#   scripts/mode.sh status     -> print current mem_limit on both containers
#
# Data is preserved across switches (bind-mount volumes).
set -euo pipefail

cd "$(dirname "$0")/.."

mode="${1:-status}"
case "$mode" in
  demo)
    echo "==> Switching to DEMO mode (no memory limit)"
    docker compose -f docker-compose.yml up -d postgres timescale
    ;;
  limited)
    echo "==> Switching to LIMITED mode (mem_limit=256m, shared_buffers=64MB)"
    docker compose -f docker-compose.yml -f docker-compose.limited.yml up -d postgres timescale
    ;;
  status)
    for c in equitydb-pg equitydb-ts; do
      if docker inspect "$c" >/dev/null 2>&1; then
        mem=$(docker inspect --format '{{.HostConfig.Memory}}' "$c")
        if [ "$mem" = "0" ]; then
          echo "$c: unlimited"
        else
          mb=$((mem / 1024 / 1024))
          echo "$c: ${mb}MB"
        fi
      else
        echo "$c: not running"
      fi
    done
    exit 0
    ;;
  *)
    echo "Usage: $0 {demo|limited|status}" >&2
    exit 2
    ;;
esac

# Wait for both containers to accept connections
for c in equitydb-pg equitydb-ts; do
  echo -n "   waiting for $c ... "
  for _ in $(seq 1 60); do
    if docker exec "$c" pg_isready -U equity -d equitydb >/dev/null 2>&1; then
      echo "ready"
      break
    fi
    sleep 1
  done
done

echo "==> Done. Current mem limits:"
"$0" status
