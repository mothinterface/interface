"""Optional hardware imports stay out of the dependency-free demo."""

import csv
import math
import random
import time


def paced(rate):
    deadline = time.perf_counter()
    while True:
        time.sleep(max(0, deadline - time.perf_counter()))
        yield time.perf_counter()
        deadline = max(deadline + 1 / rate, time.perf_counter() + 1 / rate)


def simulate(rate=200):
    rng = random.Random(42)
    start = time.perf_counter()
    for now in paced(rate):
        t = now - start
        burst = (0.12, 0.08, 0.035)[int(t // 3) % 3] if t > 5 and t % 3 < 0.10 else 0.0
        yield now, 1.65 + rng.gauss(0, 0.002) + burst


def replay(path):
    with open(path, newline="", encoding="utf-8") as stream:
        first = previous = None
        start = time.monotonic()
        for row in csv.DictReader(stream):
            timestamp, voltage = float(row["timestamp"]), float(row["voltage"])
            if not math.isfinite(timestamp) or not math.isfinite(voltage):
                raise ValueError("CSV contains a non-finite sample")
            if previous is not None and timestamp <= previous:
                raise ValueError("CSV timestamps must increase")
            if first is None:
                first = timestamp
            previous = timestamp
            target = start + timestamp - first
            time.sleep(max(0, target - time.monotonic()))
            yield target, voltage


def serial_samples(port, baud):
    import serial
    with serial.Serial(port, baudrate=baud, timeout=0.5) as connection:
        connection.reset_input_buffer()
        origin = device_origin = previous = None
        while True:
            # Protocol: acquisition timestamp in seconds,voltage in volts + newline.
            line = connection.read_until(b"\n", size=128)
            if not line.endswith(b"\n"):
                raise ValueError("Serial timeout or oversized line; acquisition stopped")
            parts = line.decode("ascii").strip().split(",")
            if len(parts) != 2:
                raise ValueError("Expected serial line: timestamp_seconds,voltage_volts")
            stamp, voltage = map(float, parts)
            if not math.isfinite(stamp) or not math.isfinite(voltage):
                raise ValueError("Invalid serial sample")
            now = time.monotonic()
            if origin is None:
                origin, device_origin = now, stamp
            if previous is not None and stamp <= previous:
                raise ValueError("Serial device timestamp reset or moved backward")
            previous = stamp
            mapped = origin + stamp - device_origin
            if abs(now - mapped) > 0.25:
                raise ValueError("Serial samples are delayed or device clock drifted; restart acquisition")
            yield mapped, voltage


def ads1115(rate, address, gain, differential=False):
    import board
    from adafruit_ads1x15 import ADS1115, AnalogIn, ads1x15
    i2c = board.I2C()
    try:
        adc = ADS1115(i2c, address=address, gain=gain, data_rate=860)
        channel = AnalogIn(adc, ads1x15.Pin.A0, ads1x15.Pin.A1) if differential else AnalogIn(adc, ads1x15.Pin.A0)
        for _ in paced(rate):
            voltage = channel.voltage
            yield time.perf_counter(), voltage
    finally:
        i2c.deinit()
