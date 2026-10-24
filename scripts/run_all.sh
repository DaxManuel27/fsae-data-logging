#!/usr/bin/env bash
# Everything in one command. Run on the Mac, from the repo root:
#   scripts/run_all.sh
#
#   1. starts InfluxDB (Homebrew) if it isn't answering, and checks its setup
#   2. starts Grafana in the background with this repo's provisioning, if it isn't already up
#   3. starts the Lima VM "can" if it isn't running
#   4. inside the VM, runs scripts/run_sim.sh: venv, vcan0, logger, simulated drive,
#      dashboard generation, load into InfluxDB
#   5. opens the new run in your browser
#
# Grafana keeps running afterwards so you can look at the data. To stop it:
#   kill "$(cat /tmp/fsae-grafana.pid)"
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"
VM="${VM:-can}"
GRAFANA_PORT="${GRAFANA_PORT:-3000}"
GRAFANA_LOG=/tmp/fsae-grafana.log
GRAFANA_PID=/tmp/fsae-grafana.pid

if [[ ! -f "$ENV_FILE" ]]; then
  echo "error: $ENV_FILE not found. Copy .env.example to .env and fill it in." >&2
  exit 1
fi
set -a; source "$ENV_FILE"; set +a

# 1. InfluxDB
if curl -fsS "$INFLUXDB_V2_URL/health" >/dev/null 2>&1; then
  echo "[1/5] InfluxDB is up."
else
  echo "[1/5] Starting InfluxDB."
  brew services start influxdb@2
  for _ in $(seq 1 30); do
    curl -fsS "$INFLUXDB_V2_URL/health" >/dev/null 2>&1 && break
    sleep 1
  done
  curl -fsS "$INFLUXDB_V2_URL/health" >/dev/null 2>&1 || { echo "error: InfluxDB not answering at $INFLUXDB_V2_URL" >&2; exit 1; }
fi
"$SCRIPT_DIR/setup_influx.sh" >/dev/null

# 2. Grafana
if curl -fsS "http://localhost:$GRAFANA_PORT/api/health" >/dev/null 2>&1; then
  echo "[2/5] Grafana is up."
else
  echo "[2/5] Starting Grafana in the background (log: $GRAFANA_LOG)."
  if [[ "$(brew services list | awk '$1 == "grafana" {print $2}')" == "started" ]]; then
    brew services stop grafana   # it holds port 3000 without this repo's provisioning
  fi
  nohup "$SCRIPT_DIR/run_grafana.sh" >"$GRAFANA_LOG" 2>&1 &
  echo $! >"$GRAFANA_PID"
  for _ in $(seq 1 60); do
    curl -fsS "http://localhost:$GRAFANA_PORT/api/health" >/dev/null 2>&1 && break
    sleep 1
  done
  curl -fsS "http://localhost:$GRAFANA_PORT/api/health" >/dev/null 2>&1 || { tail -20 "$GRAFANA_LOG" >&2; echo "error: Grafana didn't start" >&2; exit 1; }
fi

# 3. VM
if [[ "$(limactl list --format '{{.Status}}' "$VM" 2>/dev/null)" != "Running" ]]; then
  echo "[3/5] Starting Lima VM '$VM'."
  limactl start "$VM"
else
  echo "[3/5] Lima VM '$VM' is running."
fi

# 4. Simulated drive -> logger -> InfluxDB, inside the VM
echo "[4/5] Running the simulation in the VM."
SIM_OUT="$(mktemp)"
limactl shell --workdir "$REPO_ROOT" "$VM" bash scripts/run_sim.sh | tee "$SIM_OUT"
LINK="$(sed -n 's/^ *grafana: //p' "$SIM_OUT" | tail -1)"
rm -f "$SIM_OUT"

# 5. Browser
if [[ -n "$LINK" ]]; then
  echo "[5/5] Opening $LINK"
  open "$LINK"
else
  echo "[5/5] No Grafana link found in the output above." >&2
  exit 1
fi
