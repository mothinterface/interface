import ctypes
import json
import threading
import unittest
import urllib.error
import urllib.request
from ctypes import wintypes
from unittest.mock import Mock

from mosquito.headless import HeadlessController, WindowsHotkeys, dispatch
from mosquito.__main__ import parser
from mosquito.server import make_server


class HeadlessTests(unittest.TestCase):
    def test_help_prints_on_windows_legacy_console(self):
        self.assertTrue(parser().format_help().encode("cp1252"))

    def setUp(self):
        self.now = 100.0
        self.count = 0
        self.c = HeadlessController(arm_on_ready=True, clock=lambda: self.now, wall=lambda: self.now)

    def packet(self, **updates):
        self.count += 1
        p = dict(id=str(self.count), sent_at=self.now, source="ads1115", ready=True,
                 voltage=1.65, baseline=1.65, envelope=0.0, threshold=0.02, pulse=False, peak=None)
        p.update(updates)
        return p

    def test_auto_arm_waits_for_calibration_and_quiet(self):
        self.c.ingest(self.packet(ready=False))
        self.assertFalse(self.c.state()["armed"])
        self.c.ingest(self.packet(envelope=0.08))
        self.assertFalse(self.c.state()["armed"])
        self.c.ingest(self.packet())
        self.assertTrue(self.c.state()["armed"])
        self.now += 0.7
        self.c.ingest(self.packet(pulse=True, peak=0.08))
        self.assertEqual(self.c.history[0]["option"], 2)

    def test_first_pulse_does_not_auto_arm_or_click(self):
        self.c.ingest(self.packet(pulse=True, peak=0.12))
        self.assertFalse(self.c.state()["armed"])
        self.assertFalse(self.c.history)

    def test_stop_cancels_pending_auto_arm(self):
        self.c.stop()
        self.c.ingest(self.packet())
        self.assertFalse(self.c.state()["armed"])

    def test_disconnect_does_not_repeat_auto_arm(self):
        self.c.ingest(self.packet())
        self.now += 3
        self.c.ingest(self.packet())
        self.assertFalse(self.c.state()["armed"])

    def test_driver_failure_does_not_repeat_auto_arm(self):
        self.c.desktop = True
        self.c.mouse = Mock()
        self.c.mouse.press.side_effect = RuntimeError("Fail-safe")
        self.c.ingest(self.packet())
        self.now += 0.7
        self.c.ingest(self.packet(pulse=True, peak=0.12))
        self.c.ingest(self.packet())
        self.assertFalse(self.c.state()["armed"])

    def test_mode_switch_and_manual_rearm(self):
        stop = threading.Event()
        self.c.ingest(self.packet())
        dispatch(self.c, "click", stop)
        self.assertFalse(self.c.state()["armed"])
        dispatch(self.c, "arm", stop)
        self.now += 0.7
        self.c.ingest(self.packet(pulse=True, peak=0.12))
        self.assertEqual(self.c.history[0]["action"], "click")
        dispatch(self.c, "quit", stop)
        self.assertTrue(stop.is_set())
        self.assertFalse(self.c.state()["armed"])

    def test_simulation_rejected_for_desktop(self):
        self.c.desktop = True
        with self.assertRaises(ValueError):
            self.c.ingest(self.packet(source="simulate"))
        self.assertFalse(self.c.state()["armed"])

    def test_machine_only_receiver_no_web_assets(self):
        token = "test-token-12345678901234567890"
        server = make_server(self.c, token, port=0, serve_ui=False)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        url = f"http://127.0.0.1:{server.server_port}"
        try:
            for path in ("/", "/app.js", "/style.css"):
                with self.assertRaises(urllib.error.HTTPError) as e:
                    urllib.request.urlopen(url + path, timeout=2)
                self.assertEqual(e.exception.code, 404)
                e.exception.close()
            packet = self.packet()
            request = urllib.request.Request(url + "/api/sample", json.dumps(packet).encode(),
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=2) as response:
                self.assertEqual(response.status, 200)
            self.assertTrue(self.c.state()["armed"])
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


class HotkeyTests(unittest.TestCase):
    def keys(self, ids, held=False):
        keys = WindowsHotkeys.__new__(WindowsHotkeys)
        keys.api = Mock()
        keys.pending = None
        keys.registered = [1, 2, 3, 4, 5]
        queue = list(ids)

        def peek(pointer, *_):
            if not queue:
                return False
            msg = ctypes.cast(pointer, ctypes.POINTER(wintypes.MSG)).contents
            msg.message = 0x0312
            msg.wParam = queue.pop(0)
            return True

        keys.api.PeekMessageW.side_effect = peek
        keys.api.GetAsyncKeyState.return_value = 0x8000 if held else 0
        return keys

    def test_arm_waits_for_modifier_release(self):
        keys = self.keys([1], held=True)
        self.assertEqual(keys.poll(), [])
        keys.api.GetAsyncKeyState.return_value = 0
        self.assertEqual(keys.poll(), ["arm"])

    def test_stop_cancels_pending_arm_immediately(self):
        keys = self.keys([1, 4], held=True)
        self.assertEqual(keys.poll(), ["stop"])
        keys.api.GetAsyncKeyState.return_value = 0
        self.assertEqual(keys.poll(), [])

    def test_unregister_on_close(self):
        keys = self.keys([])
        keys.close()
        self.assertEqual(keys.api.UnregisterHotKey.call_count, 5)
        self.assertEqual(keys.registered, [])


if __name__ == "__main__":
    unittest.main()
