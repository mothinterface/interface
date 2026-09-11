"""Generate one continuous synthetic CSV; never send desktop input."""
import csv
import math
from pathlib import Path
import random
from .control import Controller
from .detector import Detector
from .recording import FIELDS

RATE = 200
DURATION = 900
GOOD_SECONDS = (75, 60, 90, 70, 65, 80, 60, 75, 55)
MISSING_SECONDS = (20, 35, 15, 40, 25, 55, 35, 45)


def connection_schedule():
    intervals, start = [], 0
    for i, length in enumerate(GOOD_SECONDS):
        intervals.append(dict(start_seconds=start, end_seconds=start + length, quality="good"))
        start += length
        if i < len(MISSING_SECONDS):
            length = MISSING_SECONDS[i]
            intervals.append(dict(start_seconds=start, end_seconds=start + length, quality="missing"))
            start += length
    return intervals


def history_rows():
    """Each row covers [elapsed_seconds, elapsed_seconds + 0.2).

    signal_voltage_v is the starting sample. peak_amplitude_v is the greatest
    baseline deviation in the interval's 40 internal 200 Hz samples. Actions
    belong to the interval where the detector completes the pulse. Missing
    intervals keep their rows and leave both voltage columns blank.
    """
    rng, detector, now = random.Random(20260911), Detector(), [0.0]
    controller = Controller(desktop=False, clock=lambda: now[0], wall=lambda: now[0])
    schedule, interval_index, last_action, active_mode = connection_schedule(), 0, None, "idle"
    for row_index in range(DURATION * 5):
        start_index = row_index * 40
        now[0] = row_index / 5
        if now[0] >= schedule[interval_index]["end_seconds"]:
            interval_index += 1
        quality = schedule[interval_index]["quality"]
        row = dict(elapsed_seconds=f"{now[0]:.1f}", duration_seconds="0.2",
                   signal_voltage_v="", peak_amplitude_v="", signal_status=quality,
                   mode=active_mode, available_options=3 if active_mode == "choose" else 0,
                   selected_option="", click_registered=0, data_type="synthetic")
        if quality == "missing":
            controller.state()
        else:
            largest = 0.0
            for offset in range(40):
                index = start_index + offset
                now[0] = index / RATE
                voltage = 1.65 + 0.001 * math.sin(now[0] * 0.07) + rng.gauss(0, 0.0015)
                if 236 * RATE <= index < 236 * RATE + 24:
                    voltage += 0.08
                if 662 * RATE <= index < 662 * RATE + 24:
                    voltage += 0.12
                voltage = round(voltage, 6)
                if offset == 0:
                    row["signal_voltage_v"] = f"{voltage:.6f}"
                peak = detector.feed(now[0], voltage)
                largest = max(largest, abs(voltage - (detector.baseline if detector.ready else 1.65)))
                if index % 20 == 0 or peak is not None:
                    controller.ingest(dict(id=f"synthetic-{index}", sent_at=now[0], source="csv",
                        voltage=voltage, pulse=peak is not None, peak=peak, **detector.status()))
                if index in (225 * RATE, 650 * RATE):
                    active_mode = "choose" if index == 225 * RATE else "click"
                    controller.set_mode(active_mode)
                    controller.arm()
                    row["mode"] = active_mode
                    row["available_options"] = 3 if active_mode == "choose" else 0
                if controller.history and controller.history[0] is not last_action:
                    last_action = controller.history[0]
                    if active_mode == "choose":
                        row["selected_option"] = last_action["option"]
                    else:
                        row["click_registered"] = 1
                    controller.stop()
                    active_mode = "idle"
            row["peak_amplitude_v"] = f"{largest:.6f}"
        yield row
    controller.stop()


def generate_history(output):
    """Write only the requested CSV; never overwrite existing recordings."""
    output = Path(output)
    if output.suffix.lower() != ".csv":
        raise ValueError("Output must be a .csv file")
    output.parent.mkdir(parents=True, exist_ok=True)
    good = choices = clicks = count = 0
    with output.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        for row in history_rows():
            writer.writerow(row)
            count += 1
            good += row["signal_status"] == "good"
            choices += row["selected_option"] != ""
            clicks += row["click_registered"]
    return dict(rows=count, good_seconds=good / 5, missing_seconds=(count - good) / 5,
                option_selections=choices, registered_clicks=clicks)
