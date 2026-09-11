"""Windows receiver with no browser interface; acquisition stays on the Pi."""

import ctypes
import logging
import sys
import threading
from ctypes import wintypes

from .control import Controller
from .server import make_server
from .recording import CsvHistory

LOG = logging.getLogger(__name__)


class HeadlessController(Controller):
    def __init__(self, *args, arm_on_ready=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.pending_startup_arm = arm_on_ready
        self.last_logged = None
        self.csv_history = None
        self.recording_error = None

    def arm(self):
        with self.lock:
            if self.recording_error:
                raise ValueError("History recording failed; restart the receiver")
            super().arm()
            self.pending_startup_arm = False
            LOG.info("Armed for %.0f seconds in %s mode", self.arm_seconds, self.mode)

    def stop(self):
        with self.lock:
            self.pending_startup_arm = False
            super().stop()

    def ingest(self, data):
        with self.lock:
            if self.recording_error:
                raise ValueError("History recording failed; restart the receiver")
            previous = self.history[0] if self.history else None
            if not super().ingest(data):
                return False
            action = self.history[0] if self.history and self.history[0] is not previous else None
            if self.csv_history:
                try:
                    self.csv_history.capture(data, action)
                except OSError as error:
                    self.recording_error = error
                    self.stop()
                    raise ValueError("History recording failed; control stopped") from error
            # Arm once after a quiet, calibrated sample, never on a pulse.
            # stop(), a mode change, or a successful arm consumes this request.
            if (self.pending_startup_arm and self.last_sample.get("ready")
                    and not self.last_sample.get("pulse")
                    and self.last_sample["envelope"] <= self.last_sample["threshold"] * 0.5):
                self.arm()
            if self.history and self.history[0] is not self.last_logged:
                self.last_logged = self.history[0]
                LOG.info("%s: %s, peak %.6f V (%s)", self.last_logged["result"],
                         self.last_logged["label"], self.last_logged["peak"], self.last_logged["strength"])
            return True


class WindowsHotkeys:
    """Thread-owned Win32 hotkeys. Construct, poll, and close on one thread."""
    # Ctrl+Alt+A arm; C click mode; P pick mode; S stop; Q quit.
    BINDINGS = {1: ("A", "arm"), 2: ("C", "click"), 3: ("P", "choose"),
                4: ("S", "stop"), 5: ("Q", "quit")}

    def __init__(self):
        if sys.platform != "win32":
            raise ValueError("Global hotkeys require Windows; use --no-hotkeys and the API elsewhere")
        self.api = ctypes.WinDLL("user32", use_last_error=True)
        self.api.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
        self.api.RegisterHotKey.restype = wintypes.BOOL
        self.api.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
        self.api.UnregisterHotKey.restype = wintypes.BOOL
        self.api.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                         wintypes.UINT, wintypes.UINT, wintypes.UINT]
        self.api.PeekMessageW.restype = wintypes.BOOL
        self.api.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self.api.GetAsyncKeyState.restype = ctypes.c_short
        self.registered = []
        self.pending = None
        try:
            for key_id, (key, _) in self.BINDINGS.items():
                if not self.api.RegisterHotKey(None, key_id, 0x4003, ord(key)):
                    raise OSError(ctypes.get_last_error(), f"Could not register Ctrl+Alt+{key}; another app may use it")
                self.registered.append(key_id)
        except Exception:
            self.close()
            raise

    def poll(self):
        actions = []
        message = wintypes.MSG()
        while self.api.PeekMessageW(ctypes.byref(message), None, 0x0312, 0x0312, 1):
            binding = self.BINDINGS.get(int(message.wParam))
            if binding:
                if binding[1] in ("stop", "quit"):
                    self.pending = None
                    actions.append(binding[1])
                else:
                    self.pending = binding
        # Avoid sending a click/keypress while Ctrl+Alt is still held down.
        if self.pending and not any(self.api.GetAsyncKeyState(vk) & 0x8000
                                    for vk in (0x11, 0x12, ord(self.pending[0]))):
            actions.append(self.pending[1])
            self.pending = None
        return actions

    def close(self):
        for key_id in self.registered:
            self.api.UnregisterHotKey(None, key_id)
        self.registered.clear()


def dispatch(controller, action, stop):
    if action == "arm":
        controller.arm()
    elif action in ("click", "choose"):
        controller.set_mode(action)
        LOG.info("Mode: %s. Press Ctrl+Alt+A to arm", action)
    elif action in ("stop", "quit"):
        controller.stop()
        LOG.info("Control stopped")
        if action == "quit":
            stop.set()
    else:
        raise ValueError(f"Unknown command: {action}")


def run_receiver(controller, token, host, port, stop, no_hotkeys=False, duration=None, history_path=None):
    """Run a machine-only HTTP endpoint over the Pi's USB Ethernet link."""
    hotkeys = server = worker = None
    recording = None
    finished = None
    try:
        if not no_hotkeys:
            hotkeys = WindowsHotkeys()
        server = make_server(controller, token, host, port, serve_ui=False)
        with controller.lock:
            recording = CsvHistory(controller, history_path)
            controller.csv_history = recording
        LOG.info("CSV history: %s", recording.path.resolve())
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
        worker.start()
        LOG.info("Receiver listening on %s:%s; no browser interface", host, server.server_port)
        LOG.info("Controller token: %s", token)
        LOG.info("Output: %s", "REAL DESKTOP" if controller.desktop else "DRY RUN")
        if hotkeys:
            LOG.info("Ctrl+Alt+A arm | C click mode | P choose mode | S stop | Q quit")
        started = recording.started
        previous_status = None
        while not stop.wait(0.025):
            with controller.lock:
                if controller.recording_error:
                    raise controller.recording_error
                recording.flush(min(controller.clock(), started + duration)
                                if duration is not None else None)
            if duration is not None and controller.clock() - started >= duration:
                finished = started + duration
                break
            if hotkeys:
                for action in hotkeys.poll():
                    try:
                        dispatch(controller, action, stop)
                    except ValueError as error:
                        LOG.warning("%s", error)
            state = controller.state()  # Also runs the disconnect/expiry watchdog.
            status = (state["connected"], bool(state["sample"].get("ready")), state["armed"])
            if status != previous_status:
                LOG.info("Connected=%s calibrated=%s armed=%s", *status)
                previous_status = status
    finally:
        try:
            with controller.lock:
                controller.stop()
                controller.csv_history = None
                if recording:
                    recording.close(finished)
        finally:
            stop.set()
            if worker:
                server.shutdown()
                worker.join(timeout=2)
            if server:
                server.server_close()
            if hotkeys:
                hotkeys.close()
