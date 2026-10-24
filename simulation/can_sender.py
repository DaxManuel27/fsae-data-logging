import struct
import time

import can

FRAME_TABLE = [
    (0x100, 100),  # throttle
    (0x101, 100),  # brake pressure
    (0x110, 100),  # steering
    (0x200, 100),  # motor RPM
    (0x210, 50),   # motor temp
    (0x300, 100),  # pack voltage / current
    (0x310, 50),   # cell temps
    (0x400, 50),   # wheel speeds
    (0x500, 50),   # IMU
]

CHANNEL = "vcan0"
SECONDS = 60
SCALE = 2.0 #2.0 = 2x load

def main():
    
    bus = can.Bus(interface="socketcan", channel=CHANNEL)
    start = time.perf_counter()
    end = start + SECONDS
    
    periods = {i: 1.0 / (hz * SCALE) for i, hz in FRAME_TABLE}
    next_send = {i: start for i, _ in FRAME_TABLE}
    seq = {i: 0 for i, _ in FRAME_TABLE}
    sent = failed = 0

    while (now := time.perf_counter()) < end:
        for arb_id, due in next_send.items():
            if now < due:
                continue
            data = struct.pack("<I", seq[arb_id]) + bytes(4)
            msg = can.Message(arbitration_id=arb_id, data=data, is_extended_id=False)
            try:
                bus.send(msg)
                sent += 1
            except can.CanError:
                failed += 1
            seq[arb_id] += 1
            next_send[arb_id] = due + periods[arb_id]

        sleep_for = min(next_send.values()) - time.perf_counter()
        if sleep_for > 0:
            time.sleep(sleep_for)

    elapsed = time.perf_counter() - start
    print(f"sent={sent} failed={failed} rate={sent / elapsed:.0f}/s")
    for arb_id, n in seq.items():
        print(f"  0x{arb_id:03X}: {n}")
    bus.shutdown()


if __name__ == "__main__":
    main()