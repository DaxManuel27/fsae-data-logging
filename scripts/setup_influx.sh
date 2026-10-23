#!/usr/bin/env bash
# Idempotent InfluxDB 2.x setup for the FSAE data logger. Safe to re-run:
#   1. runs `influx setup` only if the instance has never been set up
#   2. looks up the bucket ID by name
#   3. creates the v1 DBRP mapping (needed for InfluxQL in Grafana) only if missing
#
# Usage (from the repo root): scripts/setup_influx.sh [--dry-run]
#   --dry-run  print the commands that would change InfluxDB instead of running them
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"
RP="autogen"

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

if [[ ! -f "$ENV_FILE" ]]; then
  echo "error: $ENV_FILE not found. Copy .env.example to .env and fill it in." >&2
  exit 1
fi
set -a; source "$ENV_FILE"; set +a

for var in INFLUXDB_V2_URL INFLUXDB_V2_ORG INFLUXDB_V2_TOKEN INFLUXDB_BUCKET; do
  if [[ -z "${!var:-}" ]]; then
    echo "error: $var is empty in $ENV_FILE" >&2
    exit 1
  fi
done

# The influx CLI reads these instead of its saved config profile. Passing the
# token through the environment keeps it out of the command line (and `ps`).
export INFLUX_HOST="$INFLUXDB_V2_URL"
export INFLUX_ORG="$INFLUXDB_V2_ORG"
export INFLUX_TOKEN="$INFLUXDB_V2_TOKEN"

# Prints a state-changing command, and runs it unless --dry-run.
run() {
  echo "+ $*"
  $DRY_RUN || "$@"
}

influx ping >/dev/null || { echo "error: InfluxDB not reachable at $INFLUXDB_V2_URL" >&2; exit 1; }

# 1. Initial setup. /api/v2/setup reports "allowed": true only on a fresh instance.
if curl -fsS "$INFLUXDB_V2_URL/api/v2/setup" | grep -q '"allowed": *true'; then
  echo "InfluxDB is not set up yet; running influx setup (you'll be asked for an admin username and password)."
  run influx setup \
    --org "$INFLUXDB_V2_ORG" \
    --bucket "$INFLUXDB_BUCKET" \
    --retention 0 \
    --name "${INFLUX_CONFIG_NAME:-fsae-local}"
  if $DRY_RUN; then
    echo "(dry run: stopping here, the bucket won't exist until setup has run)"
    exit 0
  fi
else
  echo "InfluxDB already set up; skipping influx setup."
fi

# 2. Bucket ID by name.
BUCKET_ID="$(influx bucket list --name "$INFLUXDB_BUCKET" --hide-headers 2>/dev/null | awk 'NR==1 {print $1}')" || true
if [[ -z "$BUCKET_ID" ]]; then
  echo "error: bucket '$INFLUXDB_BUCKET' not found in org '$INFLUXDB_V2_ORG'." >&2
  echo "Create it with: influx bucket create --name \"$INFLUXDB_BUCKET\" --retention 0" >&2
  exit 1
fi
echo "Bucket '$INFLUXDB_BUCKET' has ID $BUCKET_ID."

# 3. DBRP mapping: InfluxQL database "$INFLUXDB_BUCKET" + retention policy "autogen" -> bucket.
if [[ -n "$(influx v1 dbrp list --bucket-id "$BUCKET_ID" --db "$INFLUXDB_BUCKET" --rp "$RP" --hide-headers)" ]]; then
  echo "DBRP mapping db=$INFLUXDB_BUCKET rp=$RP already exists; nothing to do."
else
  echo "Creating DBRP mapping db=$INFLUXDB_BUCKET rp=$RP -> bucket $BUCKET_ID."
  run influx v1 dbrp create --bucket-id "$BUCKET_ID" --db "$INFLUXDB_BUCKET" --rp "$RP" --default
fi

echo "Done."
