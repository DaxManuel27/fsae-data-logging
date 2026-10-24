"""Generate a Grafana dashboard with one panel per DBC signal.

Usage (from the repo root):
    python3 visualization/make_dashboard.py simulation/fsae_sim.dbc

Writes visualization/grafana/dashboards/<dbc name>-signals.json. Grafana's file provisioning
picks it up within 30 s, under Dashboards -> FSAE. Re-run it whenever the DBC changes.

Layout: one row per CAN message, one time-series panel per signal, and a "session"
dropdown at the top listing every session loaded by bin_to_influx.py.
Queries are InfluxQL, against the provisioned datasource uid "influxdb-data-logging".
"""
import argparse
import json
from pathlib import Path

import cantools

DATASOURCE = {"type": "influxdb", "uid": "influxdb-data-logging"}
SKIP_SIGNALS = {"Seq"}   # sim sequence counter, not loaded into InfluxDB
PANEL_W, PANEL_H = 12, 8  # two panels per row (Grafana grid is 24 wide)


def signal_panel(panel_id, msg, sig, x, y):
    query = (
        f'SELECT mean("{sig.name}") AS "{sig.name}" FROM "{msg.name}" '
        f"WHERE \"session\" =~ /^$session$/ AND $timeFilter "
        f"GROUP BY time($__interval), \"session\" fill(none)"
    )
    unit = f"suffix: {sig.unit}" if sig.unit else "none"
    return {
        "id": panel_id,
        "type": "timeseries",
        "title": f"{sig.name}" + (f" ({sig.unit})" if sig.unit else ""),
        "datasource": DATASOURCE,
        "gridPos": {"x": x, "y": y, "w": PANEL_W, "h": PANEL_H},
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "min": sig.minimum,
                "max": sig.maximum,
                "custom": {"lineWidth": 1, "showPoints": "never", "spanNulls": False},
            },
            "overrides": [],
        },
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}},
        "targets": [{"refId": "A", "datasource": DATASOURCE, "rawQuery": True, "query": query,
                     "resultFormat": "time_series", "alias": "$tag_session"}],
    }


def build_dashboard(db, title, uid):
    panels, panel_id, y = [], 1, 0
    for msg in sorted(db.messages, key=lambda m: m.frame_id):
        signals = [s for s in msg.signals if s.name not in SKIP_SIGNALS]
        if not signals:
            continue
        panels.append({
            "id": panel_id, "type": "row", "collapsed": False, "panels": [],
            "title": f"{msg.name} (0x{msg.frame_id:03X})",
            "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
        })
        panel_id += 1
        y += 1
        for i, sig in enumerate(signals):
            x = (i % 2) * PANEL_W
            if i and i % 2 == 0:
                y += PANEL_H
            panels.append(signal_panel(panel_id, msg, sig, x, y))
            panel_id += 1
        y += PANEL_H

    session_var = {
        "name": "session",
        "label": "Session",
        "type": "query",
        "datasource": DATASOURCE,
        "query": 'SHOW TAG VALUES WITH KEY = "session"',
        "definition": 'SHOW TAG VALUES WITH KEY = "session"',
        "refresh": 1,          # reload the list when the dashboard opens
        "sort": 2,             # alphabetical descending = newest session first
        "multi": True,
        "includeAll": False,
        "current": {},
        "options": [],
    }
    return {
        "uid": uid,
        "title": title,
        "tags": ["fsae", "generated"],
        "timezone": "browser",
        "schemaVersion": 39,
        "time": {"from": "now-24h", "to": "now"},
        "refresh": "",
        "graphTooltip": 1,     # shared crosshair across panels
        "templating": {"list": [session_var]},
        "panels": panels,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("dbc", help="DBC file the data was decoded with")
    p.add_argument("--out", help="output JSON path (default: visualization/grafana/dashboards/<dbc>-signals.json)")
    args = p.parse_args()

    db = cantools.database.load_file(args.dbc)
    stem = Path(args.dbc).stem
    out = Path(args.out) if args.out else Path(__file__).resolve().parent / "grafana" / "dashboards" / f"{stem}-signals.json"
    dash = build_dashboard(db, title=f"{stem} signals", uid=f"{stem}-signals"[:40])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dash, indent=2) + "\n")
    n = sum(1 for panel in dash["panels"] if panel["type"] == "timeseries")
    print(f"wrote {out} ({n} panels)")


if __name__ == "__main__":
    main()
