"""Check a logger .bin file: frame counts per ID and sequence gaps.

Usage: python3 verify_log.py logs/can_YYYYMMDD_HHMMSS.bin
Compare the per-ID counts with can_sender.py's printout.
"""
import sys
import numpy as np

RECORD = np.dtype([
    ("ts_ns", "<u8"),
    ("can_id", "<u4"),
    ("dlc", "u1"),
    ("pad", "V3"),
    ("data", "u1", (8,)),
])
CAN_EFF_FLAG = 0x80000000

def main(path):
    raw = np.fromfile(path, dtype=np.uint8)
    if raw.size % RECORD.itemsize:
        print(f"warning: {raw.size % RECORD.itemsize} trailing bytes (partial record), ignored")
    recs = raw[: raw.size - raw.size % RECORD.itemsize].view(RECORD)
    print(f"{path}: {len(recs)} frames")
    if len(recs) == 0:
        return

    eff = (recs["can_id"] & CAN_EFF_FLAG) != 0
    ids = np.where(eff, recs["can_id"] & 0x1FFFFFFF, recs["can_id"] & 0x7FF)
    seq = recs["data"][:, 0:4].copy().view("<u4").ravel()
    span = (recs["ts_ns"][-1] - recs["ts_ns"][0]) / 1e9
    print(f"duration {span:.2f} s, {len(recs) / span if span else 0:.0f} frames/s\n")

    print(f"{'ID':>8} {'frames':>8} {'first seq':>10} {'last seq':>10} {'missing':>8} {'out of order':>12}")
    bad = False
    for i in np.unique(ids):
        s = seq[ids == i].astype(np.int64)
        d = np.diff(s)
        missing = int((s[-1] - s[0] + 1) - len(s))
        backwards = int((d <= 0).sum())
        bad |= missing != 0 or backwards != 0
        print(f"{hex(i):>8} {len(s):>8} {s[0]:>10} {s[-1]:>10} {missing:>8} {backwards:>12}")
    print("\nOK: no gaps" if not bad else "\nFAIL: gaps or reordering found")

if __name__ == "__main__":
    main(sys.argv[1])
