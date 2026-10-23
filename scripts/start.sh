#!/usr/bin/env bash
# One command to bring up the local stack:
#   1. starts InfluxDB (Homebrew service) if it isn't answering
#   2. runs setup_influx.sh (no-op when everything already exists)
#   3. stops the Homebrew Grafana service if it's running (it holds port 3000)
#   4. runs Grafana in the foreground with this repo's provisioning (Ctrl-C to stop)
#
# Usage (from the repo root): scripts/start.sh
# To go back to the plain Homebrew Grafana afterwards: brew services start grafana
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"
GRAFANA_PORT="${GRAFANA_PORT:-3000}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "error: $ENV_FILE not found. Copy .env.example to .env and fill it in." >&2
  exit 1
fi
set -a; source "$ENV_FILE"; set +a
: "${INFLUXDB_V2_URL:?is empty in $ENV_FILE}"

# Prints "started", "stopped", "none", ... for a Homebrew service.
brew_service_status() {
  brew services list | awk -v name="$1" '$1 == name {print $2}'
}

# 1. InfluxDB
if curl -fsS "$INFLUXDB_V2_URL/health" >/dev/null 2>&1; then
  echo "[1/4] InfluxDB is up at $INFLUXDB_V2_URL."
else
  echo "[1/4] InfluxDB not answering; starting it."
  echo "+ brew services start influxdb@2"
  brew services start influxdb@2
  for _ in $(seq 1 30); do
    curl -fsS "$INFLUXDB_V2_URL/health" >/dev/null 2>&1 && break
    sleep 1
  done
  curl -fsS "$INFLUXDB_V2_URL/health" >/dev/null 2>&1 || { echo "error: InfluxDB still not answering at $INFLUXDB_V2_URL" >&2; exit 1; }
fi

# 2. Bucket and DBRP mapping
echo "[2/4] Checking InfluxDB setup."
"$SCRIPT_DIR/setup_influx.sh"

# 3. Free port 3000
if [[ "$(brew_service_status grafana)" == "started" ]]; then
  echo "[3/4] Stopping the Homebrew Grafana service so this one can use port $GRAFANA_PORT."
  echo "+ brew services stop grafana"
  brew services stop grafana
  for _ in $(seq 1 15); do
    lsof -nP -iTCP:"$GRAFANA_PORT" -sTCP:LISTEN >/dev/null 2>&1 || break
    sleep 1
  done
else
  echo "[3/4] Homebrew Grafana service not running."
fi

# 4. Grafana (run_grafana.sh errors out if something else still holds the port)
echo "[4/4] Starting Grafana."
exec "$SCRIPT_DIR/run_grafana.sh"
