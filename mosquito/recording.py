"""Continuous receiver history, using the same schema as the example CSV."""
import csv
from datetime import datetime, timezone
import math
from pathlib import Path

FIELDS = ("elapsed_seconds", "duration_seconds", "signal_voltage_v", "peak_amplitude_v",
          "signal_status", "mode", "available_options", "selected_option",
          "click_registered", "data_type")


class CsvHistory:
    """Called under the controller lock; bins use receiver monotonic time."""
    def __init__(self, controller, path=None):
        self.controller = controller
        self.path = Path(path) if path else Path("history") / (
            "usage_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f") + ".csv")
        if self.path.suffix.lower() != ".csv":
            raise ValueError("History path must end in .csv")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("x", newline="", encoding="utf-8")
        self.writer = csv.DictWriter(self.stream, fieldnames=FIELDS)
        try:
            self.writer.writeheader()
            self.stream.flush()
        except BaseException:
            self.stream.close()
            raise
        self.started = controller.clock()
        self.next_index = 0
        self.bins = {}
        self.last_type = "unknown"

    def _index(self, now):
        return max(0, math.floor((now - self.started) * 5 + 1e-8))

    def _row(self, index):
        c = self.controller
        mode = c.mode if c.clock() < c.armed_until else "idle"
        return dict(elapsed_seconds=f"{index / 5:.1f}", duration_seconds="0.2",
                    signal_voltage_v="", peak_amplitude_v="", signal_status="missing",
                    mode=mode, available_options=len(c.options) if mode == "choose" else 0,
                    selected_option="", click_registered=0, data_type=self.last_type)

    def capture(self, data, action=None):
        if self.stream.closed:
            return
        # Completed empty bins retain the source and mode known at that time.
        self.flush()
        source = data["source"]
        kind = ("synthetic" if source == "simulate" else "replay" if source == "csv"
                else "recorded" if self.controller.desktop else "recorded_dry_run")
        self.last_type = kind
        if not data.get("sample_valid", True):
            return  # End-of-stream notification is not a zero-volt measurement.
        index = self._index(self.controller.clock())
        row = self.bins.setdefault(index, self._row(index))
        row["data_type"] = kind if row["data_type"] in (kind, "unknown") else "mixed"
        if row["signal_voltage_v"] == "":
            row["signal_voltage_v"] = f"{data['voltage']:.6f}"
        row["signal_status"] = "good" if data["ready"] else "calibrating"
        if data["ready"]:
            peak = data.get("window_peak")
            if peak is None:
                peak = abs(data["voltage"] - data["baseline"])
            if row["peak_amplitude_v"]:
                peak = max(peak, float(row["peak_amplitude_v"]))
            row["peak_amplitude_v"] = f"{peak:.6f}"
        if action and action["result"] in ("executed", "demo selection"):
            row["mode"] = action["mode"]
            row["available_options"] = len(self.controller.options) if action["mode"] == "choose" else 0
            if action["mode"] == "choose":
                row["selected_option"] = action["option"]
            # In a dry run this denotes a simulated click, as identified by data_type.
            if action["action"] in ("click", "click_at", "right_click", "double_click"):
                row["click_registered"] = 1

    def flush(self, until=None):
        if self.stream.closed:
            return
        now = self.controller.clock() if until is None else until
        end = self._index(now)
        while self.next_index < end:
            row = self.bins.pop(self.next_index, None)
            self.writer.writerow(row if row is not None else self._row(self.next_index))
            self.next_index += 1
        self.stream.flush()

    def close(self, until=None):
        if self.stream.closed:
            return
        now = self.controller.clock() if until is None else until
        try:
            self.flush(now)
            # Preserve an action or measurement in the final incomplete interval.
            remaining = now - self.started - self.next_index / 5
            if remaining > 1e-8:
                row = self.bins.pop(self.next_index, None) or self._row(self.next_index)
                row["duration_seconds"] = f"{remaining:.6f}".rstrip("0").rstrip(".")
                self.writer.writerow(row)
        finally:
            self.stream.close()
