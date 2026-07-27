#!/usr/bin/env bash
# Polls each LedgerLens service's health endpoint from the HOST machine
# (not from inside Docker) and prints clickable links once everything
# is ready. Run after `docker compose up -d --build`, or just use
# `make up`, which calls this automatically.

set -uo pipefail

API_URL="http://localhost:8090"
STREAMLIT_URL="http://localhost:8511"
PROM_URL="http://localhost:9091"
GRAFANA_URL="http://localhost:3001"

MAX_WAIT=120
INTERVAL=3

check() {
  curl -sf -o /dev/null --max-time 3 "$1"
}

wait_for() {
  local name="$1" url="$2"
  local waited=0
  printf "  waiting for %-11s" "$name..."
  until check "$url"; do
    if [ "$waited" -ge "$MAX_WAIT" ]; then
      echo " TIMEOUT (checked: $url)"
      echo "    -> run 'docker compose logs ${3:-}' to see why it isn't healthy yet"
      return 1
    fi
    sleep "$INTERVAL"
    waited=$((waited + INTERVAL))
  done
  echo " ready"
  return 0
}

echo "LedgerLens: waiting for all services to become healthy..."
echo ""

wait_for "API"        "$API_URL/health"              "api"
wait_for "Streamlit"  "$STREAMLIT_URL/_stcore/health" "streamlit"
wait_for "Prometheus" "$PROM_URL/-/healthy"           "prometheus"
wait_for "Grafana"    "$GRAFANA_URL/api/health"       "grafana"

cat <<EOF

============================================================
 LedgerLens is up
============================================================
  Streamlit UI (upload/review):  $STREAMLIT_URL
  API docs (Swagger):            $API_URL/docs
  API health check:              $API_URL/health
  API raw metrics:                $API_URL/metrics
  Prometheus targets:             $PROM_URL/targets
  Grafana dashboard:              $GRAFANA_URL
    login: admin / admin

  Stop everything:   make down       (or: docker compose down)
  Restart one thing: make restart-api / restart-streamlit /
                      restart-prometheus / restart-grafana
  Tail logs:         make logs       (or: docker compose logs -f)
============================================================
EOF
