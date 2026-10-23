# Visualization

Parquet -> InfluxDB 2.x -> Grafana. For now the Parquet data is simulated.

## Local Grafana + InfluxDB setup (macOS, Homebrew)

Paths are relative to the repo root (`fsae-data-logging/`).

| Path | What it is |
| --- | --- |
| `visualization/grafana/provisioning/datasources/influxdb.yaml` | InfluxDB data source (InfluxQL, token read from `.env`) |
| `visualization/grafana/provisioning/dashboards/dashboards.yaml` | Loads every dashboard JSON in `visualization/grafana/dashboards/` |
| `visualization/grafana/dashboards/` | Exported dashboard JSON goes here |
| `scripts/setup_influx.sh` | Sets up InfluxDB and the InfluxQL (DBRP) mapping; safe to re-run |
| `scripts/run_grafana.sh` | Runs Grafana with this repo's provisioning |
| `scripts/start.sh` | Does everything: starts InfluxDB, runs `setup_influx.sh`, stops the Homebrew Grafana service, runs `run_grafana.sh` |

### Day to day

```sh
scripts/start.sh    # from the repo root; Ctrl-C stops Grafana
```

To go back to the plain Homebrew Grafana afterwards: `brew services start grafana`.

### From zero (run all commands from the repo root, `fsae-data-logging/`)

1. Install and start InfluxDB, and install Grafana:

   ```sh
   brew install influxdb@2 influxdb-cli grafana
   brew services start influxdb@2
   ```

2. Create your `.env` at the repo root and fill it in:

   ```sh
   cp .env.example .env
   ```

   ```
   INFLUXDB_V2_URL=http://localhost:8086
   INFLUXDB_V2_ORG=unb-formula-racing
   INFLUXDB_V2_TOKEN=<pick a long random string on a fresh install, or paste your existing token>
   INFLUXDB_BUCKET=data-logging
   ```

   On a fresh install, `openssl rand -base64 48` gives you a good token; setup makes it the admin token.
   `.env` is git-ignored. Never commit it.

3. Set up InfluxDB. Preview the commands first with `--dry-run`, then run it for real:

   ```sh
   scripts/setup_influx.sh --dry-run
   scripts/setup_influx.sh
   ```

   On a fresh instance it asks for an admin username and password.

4. Start Grafana with the repo's provisioning:

   ```sh
   scripts/start.sh
   ```

   (Or by hand: `brew services stop grafana`, then `scripts/run_grafana.sh`.)

5. Open http://localhost:3000 (first login is `admin` / `admin`). Go to
   Connections > Data sources > **InfluxDB (data-logging)** and click **Test**.

### Saving dashboards

Dashboards provisioned from files show up in the **FSAE** folder. To add or update one,
build it in Grafana, then Share > Export > **Export as JSON** (leave "export externally"
off) and save it into `visualization/grafana/dashboards/<name>.json`. Panels should use the
**InfluxDB (data-logging)** data source (uid `influxdb-data-logging`) so the JSON works on
every machine.
