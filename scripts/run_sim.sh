#!/usr/bin/env bash
# One simulated drive, start to finish. Run inside the Lima VM, from anywhere:
#   scripts/run_sim.sh
#
#   1. activates ~/fsae-venv (creates it and installs packages the first time)
#   2. brings up vcan0 if it isn't up
#   3. starts the Rust logger in the background
#   4. runs simulation/can_sender.py (one drive, SECONDS long)
#   5. stops the logger cleanly (SIGTERM, handled like Ctrl-C)
#   6. loads the new .bin into InfluxDB and prints a Grafana link to just this run
#   7. regenerates the Grafana dashboard so it opens on this run by default
#
# InfluxDB and Grafana run on the Mac (scripts/start.sh); start those first.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"
VENV="${VENV:-$HOME/fsae-venv}"
DBC="$REPO_ROOT/simulation/fsae_sim.dbc"
LOGGER_DIR="$REPO_ROOT/logger"
LOGGER_BIN="$LOGGER_DIR/target/release/can_logger"
CHANNEL=vcan0

LOGGER_PID=""
# If anything fails or you press Ctrl-C mid-drive, don't leave the logger running.
cleanup() {
  if [[ -n "$LOGGER_PID" ]] && kill -0 "$LOGGER_PID" 2>/dev/null; then
    kill -TERM "$LOGGER_PID"
    wait "$LOGGER_PID" || true
  fi
}
trap cleanup EXIT

# 1. venv
echo "[1/7] Python venv at $VENV"
if [[ ! -x "$VENV/bin/python3" ]]; then
  python3 -m venv "$VENV" || { echo "error: venv failed; run: sudo apt install -y python3-venv" >&2; exit 1; }
fi
source "$VENV/bin/activate"
if ! python3 -c "import numpy, cantools, influxdb_client, can" 2>/dev/null; then
  pip install -q numpy cantools influxdb-client python-can
fi

# InfluxDB settings from .env. InfluxDB runs on the Mac, which the VM reaches as host.lima.internal.
if [[ ! -f "$ENV_FILE" ]]; then
  echo "error: $ENV_FILE not found. Copy .env.example to .env and fill it in." >&2
  exit 1
fi
set -a; source "$ENV_FILE"; set +a
INFLUX_URL="${INFLUX_URL:-$INFLUXDB_V2_URL}"
if getent hosts host.lima.internal >/dev/null; then
  INFLUX_URL="${INFLUX_URL/localhost/host.lima.internal}"
fi
curl -fsS "$INFLUX_URL/health" >/dev/null || { echo "error: InfluxDB not reachable at $INFLUX_URL (run scripts/start.sh on the Mac)" >&2; exit 1; }

# 2. vcan0
echo "[2/7] $CHANNEL"
if ! ip link show "$CHANNEL" >/dev/null 2>&1; then
  sudo modprobe vcan
  sudo ip link add dev "$CHANNEL" type vcan
fi
sudo ip link set up "$CHANNEL"

# 3. logger (rebuilds only if the source changed)
echo "[3/7] Starting logger"
if command -v cargo >/dev/null || [[ -x "$HOME/.cargo/bin/cargo" ]]; then
  (cd "$LOGGER_DIR" && "$(command -v cargo || echo "$HOME/.cargo/bin/cargo")" build --release -q)
fi
[[ -x "$LOGGER_BIN" ]] || { echo "error: $LOGGER_BIN not found; build it with cargo build --release" >&2; exit 1; }
LOGGER_OUT="$(mktemp)"
(cd "$LOGGER_DIR" && exec "$LOGGER_BIN") >"$LOGGER_OUT" 2>&1 &
LOGGER_PID=$!
# Wait for the logger to name its file, e.g. "logging vcan0 -> logs/can_20261024_155108.bin"
for _ in $(seq 1 50); do
  grep -q "^logging" "$LOGGER_OUT" && break
  kill -0 "$LOGGER_PID" 2>/dev/null || { cat "$LOGGER_OUT" >&2; echo "error: logger exited early" >&2; exit 1; }
  sleep 0.1
done
BIN="$LOGGER_DIR/$(sed -n 's/^logging .* -> //p' "$LOGGER_OUT")"
echo "      writing $BIN"

# 4. sender
echo "[4/7] Running simulated drive"
python3 "$REPO_ROOT/simulation/can_sender.py"

# 5. stop logger
echo "[5/7] Stopping logger"
sleep 0.3  # let the last frames land
kill -TERM "$LOGGER_PID"
wait "$LOGGER_PID" || true
LOGGER_PID=""
sed 's/^/      /' "$LOGGER_OUT"
rm -f "$LOGGER_OUT"

# 6. load
echo "[6/7] Loading into InfluxDB"
python3 "$REPO_ROOT/simulation/bin_to_influx.py" "$BIN" --dbc "$DBC" \
  --url "$INFLUX_URL" --org "$INFLUXDB_V2_ORG" --bucket "$INFLUXDB_BUCKET" --token "$INFLUXDB_V2_TOKEN"

# 7. dashboard, defaulting to this run
echo "[7/7] Generating Grafana dashboard"
python3 "$REPO_ROOT/visualization/make_dashboard.py" "$DBC" --run "$BIN"

echo
echo "Open the grafana: link above, or Dashboards -> FSAE -> fsae_sim signals (Grafana reloads it within 30 s)."
