"""Amplitude-based choices and explicitly armed desktop actions."""

import math
import threading
import time
from collections import deque

DEFAULT_OPTIONS = [
    {"label": "Option 1", "action": "1"},
    {"label": "Option 2", "action": "2"},
    {"label": "Option 3", "action": "3"},
]
ACTIONS = {"click", "double_click", "right_click", "left", "right", "up", "down",
           "enter", "tab", "space", "scroll_up", "scroll_down", "1", "2", "3", "click_at"}


class Controller:
    def __init__(self, options=None, mode="choose", desktop=False,
                 arm_seconds=60, move_pixels=30, medium=0.06, strong=0.10,
                 clock=time.monotonic, wall=time.time):
        self.options = DEFAULT_OPTIONS if options is None else options
        if not self.options or len(self.options) > 3:
            raise ValueError("Provide between 1 and 3 options")
        for option in self.options:
            if not isinstance(option, dict) or option.get("action") not in ACTIONS or not isinstance(option.get("label"), str) or not 1 <= len(option["label"]) <= 80:
                raise ValueError("Each option needs a short label and a supported action")
            if option["action"] == "click_at" and any(type(option.get(k)) is not int or option[k] < 0 for k in ("x", "y")):
                raise ValueError("click_at needs nonnegative integer x and y coordinates")
        if not (math.isfinite(medium) and math.isfinite(strong) and 0 < medium < strong):
            raise ValueError("Require 0 < medium < strong thresholds")
        if mode not in ("choose", "click"):
            raise ValueError("Mode must be choose or click")
        if not math.isfinite(arm_seconds) or not 1 <= arm_seconds <= 3600:
            raise ValueError("Arm duration must be between 1 and 3600 seconds")
        self.mode, self.desktop = mode, desktop
        self.medium, self.strong = medium, strong
        self.arm_seconds, self.move_pixels = arm_seconds, move_pixels
        self.clock, self.wall = clock, wall
        self.lock = threading.RLock()
        self.started = clock()
        self.armed_until = 0
        self.armed_at_wall = math.inf
        self.last_received = -math.inf
        self.last_action = -math.inf
        self.last_sample = {}
        self.seen = deque(maxlen=2048)
        self.history = deque(maxlen=30)
        self.trace = deque(maxlen=240)
        self.mouse = None
        if desktop:
            import pyautogui
            pyautogui.FAILSAFE = True
            self.mouse = pyautogui

    def _expire(self):
        if self.clock() - self.last_received > 2 or not self.last_sample.get("ready"):
            self.armed_until = 0

    def arm(self):
        with self.lock:
            self._expire()
            if self.clock() - self.last_received > 2 or not self.last_sample.get("ready"):
                raise ValueError("Wait for a connected, calibrated signal source")
            if self.desktop and self.last_sample.get("source") not in ("serial", "ads1115"):
                raise ValueError("Desktop mode requires a hardware source")
            self.started = self.clock()
            self.armed_at_wall = self.wall()
            self.armed_until = self.clock() + self.arm_seconds

    def set_mode(self, mode):
        if mode not in ("choose", "click"):
            raise ValueError("Mode must be choose or click")
        with self.lock:
            self.stop()
            self.mode = mode

    def stop(self):
        with self.lock:
            self.armed_until = 0

    def ingest(self, data):
        with self.lock:
            # Expire before a reconnect can refresh the last-received timestamp.
            self._expire()
            if not isinstance(data, dict):
                raise ValueError("Expected an object")
            for key in ("sent_at", "voltage", "baseline", "envelope", "threshold"):
                value = data.get(key)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f"Invalid {key}")
            if abs(self.wall() - data["sent_at"]) > 2:
                raise ValueError("Stale packet or clock mismatch")
            if type(data.get("ready")) is not bool or type(data.get("pulse")) is not bool:
                raise ValueError("ready and pulse must be boolean")
            if "sample_valid" in data and type(data["sample_valid"]) is not bool:
                raise ValueError("sample_valid must be boolean")
            if not data.get("sample_valid", True) and (data["ready"] or data["pulse"]):
                raise ValueError("Invalid samples cannot be ready or trigger pulses")
            if data.get("window_peak") is not None and (type(data["window_peak"]) not in (int, float)
                    or not math.isfinite(data["window_peak"]) or data["window_peak"] < 0):
                raise ValueError("Invalid window peak")
            if data["pulse"] and (type(data.get("peak")) not in (int, float) or not math.isfinite(data["peak"]) or data["peak"] < data["threshold"]):
                raise ValueError("Invalid pulse peak")
            if data.get("source") not in ("simulate", "csv", "serial", "ads1115"):
                raise ValueError("Unknown source")
            packet_id = data.get("id")
            if not isinstance(packet_id, str) or not 1 <= len(packet_id) <= 80:
                raise ValueError("Invalid packet id")
            if packet_id in self.seen:
                return False
            self.seen.append(packet_id)
            if self.last_sample.get("source") != data["source"]:
                self.armed_until = 0
            self.last_received = self.clock()
            self.last_sample = data.copy()
            self.trace.append({k: data[k] for k in ("voltage", "envelope", "threshold")})
            self._expire()
            if data["pulse"] and data["sent_at"] >= self.armed_at_wall:
                self._select(data["peak"])
            return True

    def _select(self, peak):
        now = self.clock()
        if now >= self.armed_until or now - self.last_action < 0.6:
            return
        if self.desktop and self.last_sample.get("source") not in ("serial", "ads1115"):
            self.stop()
            return
        index = 0 if peak >= self.strong else 1 if peak >= self.medium else 2
        if self.mode == "click":
            if index != 0:
                return
            selected = {"label": "Click", "action": "click"}
        else:
            if index >= len(self.options):
                return
            selected = self.options[index]
        self.last_action = now
        entry = dict(label=selected["label"], action=selected["action"], time=self.wall(), desktop=self.desktop,
                     peak=peak, strength=("strong", "medium", "low")[index], option=index + 1,
                     mode=self.mode)
        try:
            if self.desktop:
                self._execute(selected)
            entry["result"] = "executed" if self.desktop else "demo selection"
        except Exception as error:
            self.stop()
            entry["result"] = f"Stopped: {type(error).__name__}: {error}"
        self.history.appendleft(entry)

    def _execute(self, selected):
        action = selected["action"]
        mouse = self.mouse
        if action == "click_at":
            mouse.click(x=selected["x"], y=selected["y"])
        elif action in ("click", "right_click", "double_click"):
            mouse.click(button="right" if action == "right_click" else "left",
                        clicks=2 if action == "double_click" else 1, interval=0.1)
        elif action in ("left", "right", "up", "down"):
            dx, dy = {"left": (-1, 0), "right": (1, 0), "up": (0, -1), "down": (0, 1)}[action]
            mouse.moveRel(dx * self.move_pixels, dy * self.move_pixels, duration=0.1)
        elif action.startswith("scroll_"):
            mouse.scroll(3 if action == "scroll_up" else -3)
        else:
            mouse.press(action)

    def state(self):
        with self.lock:
            self._expire()
            now = self.clock()
            return dict(options=self.options, mode=self.mode, medium=self.medium, strong=self.strong,
                        desktop=self.desktop, armed=now < self.armed_until,
                        seconds_left=max(0, self.armed_until - now),
                        connected=now - self.last_received <= 2,
                        sample=self.last_sample, history=list(self.history), trace=list(self.trace))
