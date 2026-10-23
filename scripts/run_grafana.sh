#!/usr/bin/env bash
# Runs Homebrew's Grafana in the foreground using this repo's provisioning
# (data source + dashboards) and the variables from .env. Stop it with Ctrl-C.
#
# It uses the same config, data, log and plugin paths as `brew services start grafana`,
# so your users and saved settings are the same; only the provisioning folder changes.
# The Homebrew service must be stopped first (both want port 3000):
#   brew services stop grafana
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VIS_DIR="$REPO_ROOT/visualization"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"
GRAFANA_PORT="${GRAFANA_PORT:-3000}"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "error: $ENV_FILE not found. Copy .env.example to .env and fill it in." >&2
  exit 1
fi
# set -a exports everything in .env so Grafana can expand ${VAR} in the provisioning YAML.
set -a; source "$ENV_FILE"; set +a

for var in INFLUXDB_V2_URL INFLUXDB_V2_ORG INFLUXDB_V2_TOKEN INFLUXDB_BUCKET; do
  if [[ -z "${!var:-}" ]]; then
    echo "error: $var is empty in $ENV_FILE" >&2
    exit 1
  fi
done

if lsof -nP -iTCP:"$GRAFANA_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  echo "error: port $GRAFANA_PORT is already in use (probably the Homebrew Grafana service)." >&2
  echo "Stop it first with: brew services stop grafana" >&2
  exit 1
fi

BREW_PREFIX="$(brew --prefix)"
export GF_PATHS_PROVISIONING="$VIS_DIR/grafana/provisioning"
export FSAE_GRAFANA_DASHBOARDS_DIR="$VIS_DIR/grafana/dashboards"

echo "Provisioning from: $GF_PATHS_PROVISIONING"
echo "Grafana will be at http://localhost:$GRAFANA_PORT (Ctrl-C to stop)"

# Same arguments as Homebrew's launchd plist for Grafana.
exec "$BREW_PREFIX/opt/grafana/bin/grafana" server \
  --config "$BREW_PREFIX/etc/grafana/grafana.ini" \
  --homepath "$BREW_PREFIX/opt/grafana/share/grafana" \
  --packaging=brew \
  cfg:default.paths.logs="$BREW_PREFIX/var/log/grafana" \
  cfg:default.paths.data="$BREW_PREFIX/var/lib/grafana" \
  cfg:default.paths.plugins="$BREW_PREFIX/var/lib/grafana/plugins"
