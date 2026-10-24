"""Load logger .bin files into InfluxDB, decoded with a DBC.

Usage:
    export INFLUX_TOKEN=...
    python3 bin_to_influx.py logs/can_20261024_185233.bin --dbc ../sender-v2/fsae_sim.dbc
    python3 bin_to_influx.py logs/*.bin --dbc car.dbc --source car --replace
    python3 bin_to_influx.py logs/can_X.bin --dbc fsae_sim.dbc --dry-run   # decode + report only

After each file it prints a Grafana link that opens the dashboard made by make_dashboard.py
on that session, zoomed to the run's first and last frame.

One measurement per DBC message, one float field per signal, tags session (file stem)
and source. Timestamps are the logger's own ns timestamps.
Install: pip install numpy cantools influxdb-client
"""
import argparse
import os
import sys
from urllib.parse import urlencode
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import cantools
import numpy as np

# ---- Record layout, must match logger-v1/src/main.rs ----
RECORD = np.dtype([
    ("ts_ns", "<u8"),
    ("can_id", "<u4"),
    ("dlc", "u1"),
    ("pad", "V3"),
    ("data", "u1", (8,)),
])
CAN_EFF_FLAG = 0x80000000
CAN_RTR_FLAG = 0x40000000
CAN_ERR_FLAG = 0x20000000

SEQ_SIGNAL = "Seq"
BATCH_SIZE = 5000


# ---- 1. Read ----

def read_records(path):
    """All complete 24-byte records in `path`; a partial tail is ignored."""
    raw = np.fromfile(path, dtype=np.uint8)
    tail = raw.size % RECORD.itemsize
    if tail:
        print(f"  warning: {tail} trailing bytes (partial record), ignored")
    return raw[: raw.size - tail].view(RECORD)


# ---- 2. Filter and normalise ----

def split_frames(recs, db):
    """Drop RTR/error frames, strip flag bits, group record indices by DBC message.

    Returns (groups {frame_id: index array}, drop counts).
    """
    word = recs["can_id"]
    rtr = (word & CAN_RTR_FLAG) != 0
    err = (word & CAN_ERR_FLAG) != 0
    eff = (word & CAN_EFF_FLAG) != 0
    ids = np.where(eff, word & 0x1FFFFFFF, word & 0x7FF)
    keep = ~(rtr | err)

    known = {m.frame_id for m in db.messages}
    groups, unknown = {}, Counter()
    for i in np.unique(ids[keep]):
        idx = np.flatnonzero(keep & (ids == i))
        if int(i) in known:
            groups[int(i)] = idx
        else:
            unknown[int(i)] = len(idx)
    drops = {"rtr": int(rtr.sum()), "error": int(err.sum()), "unknown_id": sum(unknown.values())}
    return groups, drops, unknown


# ---- 3. Check ----

def check_sequence(recs, groups, db):
    """Per-ID gap check on the Seq counter (bytes 0-3), like verify_log.py. Warns only."""
    rows = []
    for frame_id, idx in groups.items():
        msg = db.get_message_by_frame_id(frame_id)
        if SEQ_SIGNAL not in [s.name for s in msg.signals]:
            continue
        idx = idx[recs["dlc"][idx] >= msg.length]  # short frames fail decode; keep them out of the count
        seq = recs["data"][idx, 0:4].copy().view("<u4").ravel().astype(np.int64)
        missing = int((seq[-1] - seq[0] + 1) - len(seq))
        backwards = int((np.diff(seq) <= 0).sum())
        rows.append((frame_id, msg.name, len(seq), missing, backwards))
    if not rows:
        return
    print(f"  {'ID':>6} {'message':<12} {'frames':>8} {'missing':>8} {'out of order':>12}")
    for frame_id, name, n, missing, backwards in rows:
        print(f"  {hex(frame_id):>6} {name:<12} {n:>8} {missing:>8} {backwards:>12}")
    if any(r[3] or r[4] for r in rows):
        print("  warning: sequence gaps or reordering, loading anyway")


# ---- 4. Decode ----

def decode_group(msg, recs, idx, keep_seq):
    """Decode one message's frames. Returns (ts_ns array, {signal: float list}, errors, out_of_range)."""
    names = [s.name for s in msg.signals if keep_seq or s.name != SEQ_SIGNAL]
    limits = {s.name: (s.minimum, s.maximum) for s in msg.signals}
    ts, cols = [], {n: [] for n in names}
    errors, out_of_range = 0, Counter()

    data, dlc, ts_all = recs["data"][idx], recs["dlc"][idx], recs["ts_ns"][idx]
    for row, n_bytes, t in zip(data, dlc, ts_all):
        try:
            values = msg.decode(row[:n_bytes].tobytes(), decode_choices=False, scaling=True)
        except Exception:
            errors += 1
            continue
        ts.append(int(t))
        for n in names:
            v = float(values[n])
            cols[n].append(v)
            lo, hi = limits[n]
            if (lo is not None and v < lo) or (hi is not None and v > hi):
                out_of_range[n] += 1
    return ts, cols, errors, out_of_range


# ---- 5. Write ----

def escape_key(s):
    """Escape a measurement, tag key/value or field key for line protocol."""
    return s.replace("\\", "\\\\").replace(",", "\\,").replace("=", "\\=").replace(" ", "\\ ")


def line_protocol(measurement, tags, ts, cols):
    """Yield one line-protocol string per frame."""
    head = escape_key(measurement) + "".join(f",{escape_key(k)}={escape_key(v)}" for k, v in sorted(tags.items()))
    keys = [escape_key(k) for k in cols]
    columns = list(cols.values())
    for i, t in enumerate(ts):
        fields = ",".join(f"{k}={repr(c[i])}" for k, c in zip(keys, columns))
        yield f"{head} {fields} {t}"


def batched(lines, size):
    batch = []
    for line in lines:
        batch.append(line)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def delete_session(client, args, session):
    client.delete_api().delete(
        start=datetime(1970, 1, 1, tzinfo=timezone.utc),
        stop=datetime.now(timezone.utc),
        predicate=f'session="{session}"',
        bucket=args.bucket,
        org=args.org,
    )


def count_in_influx(client, args, session, measurement, field, t0, t1):
    """Points stored for one field of one measurement in this session."""
    start = datetime.fromtimestamp(t0 // 1_000_000_000 - 1, timezone.utc).isoformat()
    stop = datetime.fromtimestamp(t1 // 1_000_000_000 + 2, timezone.utc).isoformat()
    flux = f'''
from(bucket: "{args.bucket}")
  |> range(start: {start}, stop: {stop})
  |> filter(fn: (r) => r._measurement == "{measurement}" and r._field == "{field}" and r.session == "{session}")
  |> count()'''
    tables = client.query_api().query(flux, org=args.org)
    return sum(rec.get_value() for table in tables for rec in table.records)


def grafana_link(args, session, t0_ns, t1_ns):
    """Dashboard URL showing just this run (1 s margin each side)."""
    query = urlencode({
        "from": t0_ns // 1_000_000 - 1000,
        "to": t1_ns // 1_000_000 + 1000,
        "var-session": session,
    })
    return f"{args.grafana_url.rstrip('/')}/d/{args.dashboard_uid}?{query}"


# ---- Per file ----

def load_file(path, db, args, client):
    session = Path(path).stem
    print(f"{path}  (session={session}, source={args.source})")
    recs = read_records(path)
    print(f"  {len(recs)} frames")
    if len(recs) == 0:
        return True

    groups, drops, unknown = split_frames(recs, db)
    print(f"  dropped: {drops['rtr']} RTR, {drops['error']} error frames, {drops['unknown_id']} with IDs not in DBC")
    for frame_id, n in sorted(unknown.items()):
        print(f"    unknown {hex(frame_id)}: {n}")
    check_sequence(recs, groups, db)

    write_api = None
    if client is not None:
        from influxdb_client.client.write_api import SYNCHRONOUS
        if args.replace:
            delete_session(client, args, session)
            print("  deleted existing points for this session")
        write_api = client.write_api(write_options=SYNCHRONOUS)

    tags = {"session": session, "source": args.source}
    ok = True
    print(f"  {'message':<12} {'frames':>8} {'decoded':>8} {'errors':>7} {'in influx':>10}  out of range")
    for frame_id, idx in sorted(groups.items()):
        msg = db.get_message_by_frame_id(frame_id)
        ts, cols, errors, oor = decode_group(msg, recs, idx, args.keep_seq)
        stored = "-"
        if write_api is not None and ts and cols:
            for batch in batched(line_protocol(msg.name, tags, ts, cols), args.batch):
                write_api.write(bucket=args.bucket, org=args.org, record=batch, write_precision="ns")
            first_field = next(iter(cols))
            stored = count_in_influx(client, args, session, msg.name, first_field, ts[0], ts[-1])
            if stored != len(ts):
                ok = False
        oor_text = ", ".join(f"{k} {v}" for k, v in oor.items()) or "-"
        print(f"  {msg.name:<12} {len(idx):>8} {len(ts):>8} {errors:>7} {stored:>10}  {oor_text}")

    t0, t1 = int(recs["ts_ns"].min()), int(recs["ts_ns"].max())
    print(f"  run: {(t1 - t0) / 1e9:.1f} s")
    print(f"  grafana: {grafana_link(args, session, t0, t1)}")
    return ok


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("files", nargs="+", help="logger .bin files")
    p.add_argument("--dbc", required=True, help="DBC file to decode with")
    p.add_argument("--source", default="sim", help="value of the source tag (default: sim)")
    p.add_argument("--url", default=os.environ.get("INFLUX_URL", "http://localhost:8086"))
    p.add_argument("--org", default=os.environ.get("INFLUX_ORG", "fsae"))
    p.add_argument("--bucket", default=os.environ.get("INFLUX_BUCKET", "car"))
    p.add_argument("--token", default=os.environ.get("INFLUX_TOKEN"), help="default: $INFLUX_TOKEN")
    p.add_argument("--replace", action="store_true", help="delete the session's points before loading")
    p.add_argument("--keep-seq", action="store_true", help="also write the Seq counter as a field")
    p.add_argument("--batch", type=int, default=BATCH_SIZE, help="points per write request")
    p.add_argument("--grafana-url", default=os.environ.get("GRAFANA_URL", "http://localhost:3000"))
    p.add_argument("--dashboard-uid", help="dashboard to link to (default: <dbc name>-signals, as make_dashboard.py names it)")
    p.add_argument("--dry-run", action="store_true", help="decode and report, write nothing")
    args = p.parse_args()

    db = cantools.database.load_file(args.dbc)
    if not args.dashboard_uid:
        args.dashboard_uid = f"{Path(args.dbc).stem}-signals"[:40]

    client = None
    if not args.dry_run:
        if not args.token:
            sys.exit("no token: set INFLUX_TOKEN or pass --token (or use --dry-run)")
        from influxdb_client import InfluxDBClient
        client = InfluxDBClient(url=args.url, token=args.token, org=args.org, timeout=30_000)

    ok = True
    try:
        for path in args.files:
            ok &= load_file(path, db, args, client)
            print()
    finally:
        if client is not None:
            client.close()

    if not ok:
        sys.exit("FAIL: InfluxDB point counts don't match decoded frames")
    print("OK" if client is not None else "OK (dry run, nothing written)")


if __name__ == "__main__":
    main()
