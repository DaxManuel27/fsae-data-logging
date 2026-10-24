import math
import random
import struct
import time

import can

# (arb_id, base rate Hz)
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
SCALE = 2.0
SEED = None  # None = new random drive each run; set a number to repeat one

# ---- Car ----
MASS = 280.0           # kg, car + driver
WHEEL_R = 0.23         # m
GEAR = 4.0
WHEELBASE = 1.55       # m
TRACK_W = 1.2          # m
STEER_RATIO = 5.0
MAX_TORQUE = 140.0     # Nm at the motor
MAX_POWER = 80_000.0   # W (EV.3.3.1 limit)
REGEN_TORQUE = 30.0    # Nm when braking
VMAX = 29.0            # m/s, about 105 km/h (D.12.2.2 top speed)

# ---- Pack ----
CELLS = 120
PACK_R = 0.12          # ohm
PACK_WH = 7000.0

# ---- Endurance course limits, FSAE 2027 draft D.12.2.2 ----
STRAIGHT_HAIRPIN_MAX = 77.0   # m, straight with hairpins at both ends
STRAIGHT_WIDE_MAX = 61.0      # m, straight with wide turns at the ends
TURN_DIAM = (30.0, 54.0)      # m, constant turns
HAIRPIN_DIAM = (9.0, 14.0)    # m outside diameter, 9 m minimum
SLALOM_SPACING = (9.0, 15.0)  # m between cones
LAP_LENGTH = (900.0, 1100.0)  # m, typical endurance lap (approximate)


def make_track(rng):
    """Random endurance-style lap: list of (length m, curvature 1/m, +left / -right)."""
    track, total, prev = [], 0.0, "hairpin"
    while total < rng.uniform(*LAP_LENGTH):
        kind = rng.choices(["turn", "hairpin", "slalom"], weights=[5, 2, 1])[0]
        side = rng.choice([1, -1])
        # straight before it, capped by what sits at each end
        cap = STRAIGHT_HAIRPIN_MAX if prev == "hairpin" and kind == "hairpin" else STRAIGHT_WIDE_MAX
        track.append((rng.uniform(15, cap), 0.0))
        if kind == "turn":
            r = rng.uniform(*TURN_DIAM) / 2
            track.append((r * math.radians(rng.uniform(45, 180)), side / r))
        elif kind == "hairpin":
            r = rng.uniform(*HAIRPIN_DIAM) / 2 - 1.0  # driven line inside the outer edge
            track.append((r * math.pi, side / r))
        else:
            gap = rng.uniform(*SLALOM_SPACING)
            for g in range(rng.randint(4, 7)):
                track.append((gap, side * (-1) ** g / gap))
        total = sum(seg[0] for seg in track)
        prev = kind
    return track


class Driver:
    """Per-lap variation: how hard this lap pushes."""
    def __init__(self, rng):
        self.grip = rng.uniform(0.80, 0.93)     # share of the car's lateral grip used
        self.brake_g = rng.uniform(1.0, 1.3)    # braking target, g
        self.full = rng.uniform(0.85, 1.0)      # throttle on straights
        self.exit = rng.uniform(0.4, 0.7)       # throttle cap in corners
        self.corner = None

    def corner_scale(self, rng):
        # each corner gets its own small error on top of the lap's pace
        return rng.uniform(0.95, 1.03)


class Car:
    def __init__(self, rng):
        self.rng = rng
        self.track = make_track(rng)
        self.lat_g = rng.uniform(1.3, 1.5) * 9.81   # this car's grip today
        self.driver = Driver(rng)
        self.seg_scale = [self.driver.corner_scale(rng) for _ in self.track]
        self.ambient = rng.uniform(15, 32)
        self.lap = 0
        self.seg = 0
        self.pos = 0.0          # m into current segment
        self.v = 0.0            # m/s
        self.throttle = 0.0     # 0..1
        self.brake = 0.0        # 0..1
        self.curv = 0.0         # smoothed path curvature
        self.steer = 0.0        # deg at the wheel
        self.steer_rate = 0.0   # deg/s
        self.torque = 0.0       # Nm
        self.ax = 0.0           # m/s^2
        self.soc = rng.uniform(0.85, 1.0)
        self.current = 0.0      # A, + = discharge
        self.voltage = self.ocv()
        self.t_motor = self.ambient + rng.uniform(5, 20)
        self.t_inv = self.ambient + rng.uniform(3, 15)
        self.t_cell = self.ambient + rng.uniform(2, 8)
        self.cell_spread = rng.uniform(1.5, 4.0)  # max - min cell temp, degC

    def ocv(self):
        return CELLS * (3.6 + 0.6 * self.soc)

    def limit(self, i):
        curv = self.track[i][1]
        if curv == 0:
            return VMAX
        return min(VMAX, math.sqrt(self.lat_g * self.driver.grip / abs(curv)) * self.seg_scale[i])

    def step(self, dt):
        d = self.driver
        length, curv = self.track[self.seg]
        nxt = (self.seg + 1) % len(self.track)
        v_here, v_next = self.limit(self.seg), self.limit(nxt)
        remaining = length - self.pos

        # Driver: target speed = this segment's limit, or less if we must slow for the next one
        decel = d.brake_g * 9.81
        v_target = min(v_here, math.sqrt(v_next**2 + 2 * decel * remaining))
        err = v_target - self.v
        if err >= 0:
            want_t, want_b = min(d.full if curv == 0 else d.exit, 0.15 + err * 0.4), 0.0
        else:
            want_t, want_b = 0.0, min(1.0, -err * 0.4)
        rate = 4.0 * dt  # pedals move at most 400 %/s
        self.throttle += max(-rate, min(rate, want_t - self.throttle))
        self.brake += max(-rate, min(rate, want_b - self.brake))

        # Motor torque, power limited; light regen under braking, none below 5 km/h (EV.3.3.3)
        w_motor = self.v / WHEEL_R * GEAR
        t_avail = min(MAX_TORQUE, MAX_POWER / max(w_motor, 1.0))
        regen = REGEN_TORQUE * self.brake if self.v > 5 / 3.6 else 0.0
        self.torque = self.throttle * t_avail - regen

        # Longitudinal dynamics; full pedal = 1.5 g of brake force
        f_drive = self.torque * GEAR / WHEEL_R
        f_brake = self.brake * 1.5 * 9.81 * MASS
        f_drag = 0.5 * 1.2 * 1.2 * self.v**2 + 0.015 * MASS * 9.81
        self.ax = (f_drive - f_brake - (f_drag if self.v > 0 else 0)) / MASS
        self.v = max(0.0, self.v + self.ax * dt)
        self.pos += self.v * dt
        if self.pos >= length:
            self.pos -= length
            self.seg = nxt
            if self.seg == 0:  # new lap, new pace
                self.lap += 1
                self.driver = Driver(self.rng)
                self.seg_scale = [self.driver.corner_scale(self.rng) for _ in self.track]

        # Steering follows path curvature with a little lag
        self.curv += (curv - self.curv) * min(1.0, dt / 0.15)
        new_steer = math.degrees(math.atan(WHEELBASE * self.curv)) * STEER_RATIO
        self.steer_rate = (new_steer - self.steer) / dt if dt > 0 else 0.0
        self.steer = new_steer

        # Electrical
        p_mech = self.torque * w_motor
        p_elec = p_mech / 0.92 if p_mech >= 0 else p_mech * 0.85
        ocv = self.ocv()
        self.current = p_elec / ocv
        self.voltage = ocv - self.current * PACK_R
        self.soc -= p_elec * dt / 3600 / PACK_WH

        # Thermal: heat in, cooling out
        a = self.ambient
        self.t_motor += (0.06 * abs(p_mech) - 20 * (self.t_motor - a)) / 4000 * dt
        self.t_inv += (0.03 * abs(p_mech) - 15 * (self.t_inv - a)) / 2000 * dt
        self.t_cell += (self.current**2 * PACK_R - 8 * (self.t_cell - a)) / 40000 * dt


def i16(x):
    return max(-32768, min(32767, round(x)))


def u16(x):
    return max(0, min(65535, round(x)))


def u8(x):
    return max(0, min(255, round(x)))


def payload(arb_id, car, noise):
    """Bytes 4-7 for each ID. Scales match fsae_sim.dbc."""
    n = noise.gauss
    if arb_id == 0x100:   # throttle %, 0.1
        apps1 = car.throttle * 100
        return struct.pack("<HH", u16(apps1 * 10 + n(0, 2)), u16(apps1 * 0.98 * 10 + n(0, 2)))
    if arb_id == 0x101:   # brake bar, 0.1
        front = car.brake * 60
        return struct.pack("<HH", u16(front * 10 + n(0, 1)), u16(front * 0.6 * 10 + n(0, 1)))
    if arb_id == 0x110:   # deg, deg/s, 0.1
        return struct.pack("<hh", i16(car.steer * 10 + n(0, 1)), i16(car.steer_rate * 10))
    if arb_id == 0x200:   # rpm 1, torque Nm 0.1
        rpm = car.v / WHEEL_R * GEAR * 60 / (2 * math.pi)
        return struct.pack("<Hh", u16(rpm + n(0, 3)), i16(car.torque * 10))
    if arb_id == 0x210:   # degC 0.1
        return struct.pack("<hh", i16(car.t_motor * 10), i16(car.t_inv * 10))
    if arb_id == 0x300:   # V 0.1, A 0.1 (+ = discharge)
        return struct.pack("<Hh", u16(car.voltage * 10 + n(0, 2)), i16(car.current * 10 + n(0, 5)))
    if arb_id == 0x310:   # degC 0.1, max / min cell
        return struct.pack("<hh", i16((car.t_cell + car.cell_spread / 2) * 10), i16((car.t_cell - car.cell_spread / 2) * 10))
    if arb_id == 0x400:   # km/h 0.5, FL FR RL RR
        kmh = car.v * 3.6
        d = car.curv * TRACK_W / 2  # inside wheels slower
        slip = 1 + 0.03 * car.throttle  # rears spin a little under power
        w = [kmh * (1 - d), kmh * (1 + d), kmh * (1 - d) * slip, kmh * (1 + d) * slip]
        return bytes(u8(x * 2) for x in w)
    if arb_id == 0x500:   # g 0.001, longitudinal / lateral
        ay = car.v**2 * car.curv
        return struct.pack("<hh", i16(car.ax / 9.81 * 1000 + n(0, 20)), i16(ay / 9.81 * 1000 + n(0, 20)))
    return bytes(4)


def main():
    bus = can.Bus(interface="socketcan", channel=CHANNEL)
    seed = SEED if SEED is not None else random.randrange(1_000_000)
    print(f"seed={seed} (set SEED = {seed} to repeat this drive)")
    rng = random.Random(seed)
    car = Car(rng)
    noise = random.Random(seed + 1)
    start = time.perf_counter()
    end = start + SECONDS
    last = start

    periods = {i: 1.0 / (hz * SCALE) for i, hz in FRAME_TABLE}
    next_send = {i: start for i, _ in FRAME_TABLE}
    seq = {i: 0 for i, _ in FRAME_TABLE}
    sent = failed = 0

    while (now := time.perf_counter()) < end:
        car.step(now - last)
        last = now
        for arb_id, due in next_send.items():
            if now < due:
                continue
            data = struct.pack("<I", seq[arb_id] & 0xFFFFFFFF) + payload(arb_id, car, noise)
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
    print(f"sent={sent} failed={failed} rate={sent / elapsed:.0f}/s laps={car.lap}")
    for arb_id, n in seq.items():
        print(f"  0x{arb_id:03X}: {n}")
    bus.shutdown()


if __name__ == "__main__":
    main()
