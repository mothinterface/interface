import json
import math
import random
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

from mosquito.acquisition import acquire
from mosquito.control import Controller
from mosquito.detector import Detector, Settings
from mosquito.server import make_server
from mosquito.sources import serial_samples, paced


class DetectorTests(unittest.TestCase):
    def setUp(self):
        self.detector = Detector(Settings(calibration_seconds=0.2))
        self.t = 0
        self.events = []
        self.feed(1.65, 60)
        self.assertTrue(self.detector.ready)

    def feed(self, value, count):
        for _ in range(count):
            self.t += 0.005
            peak = self.detector.feed(self.t, value)
            if peak is not None:
                self.events.append(peak)

    def test_noise_does_not_click(self):
        rng = random.Random(5)
        for _ in range(2000):
            self.feed(1.65 + rng.gauss(0, 0.002), 1)
        self.assertEqual(self.events, [])

    def test_waits_for_peak_and_release(self):
        self.feed(1.68, 15)
        self.feed(1.73, 15)
        self.feed(1.77, 15)
        self.assertEqual(self.events, [])
        self.feed(1.65, 50)
        self.assertEqual(len(self.events), 1)
        self.assertAlmostEqual(self.events[0], 0.12, places=4)

    def test_bipolar_pulses(self):
        self.feed(1.53, 20)
        self.feed(1.65, 160)
        self.feed(1.77, 20)
        self.feed(1.65, 50)
        self.assertEqual(len(self.events), 2)

    def test_short_spike_ignored(self):
        self.feed(1.70, 1)
        self.feed(1.65, 100)
        self.assertEqual(self.events, [])

    def test_stuck_signal_rejected(self):
        self.feed(1.77, 400)
        self.feed(1.65, 100)
        self.assertEqual(self.events, [])

    def test_refractory_suppresses_second_pulse(self):
        self.feed(1.77, 20)
        self.feed(1.65, 30)
        self.feed(1.77, 20)
        self.feed(1.65, 50)
        self.assertEqual(len(self.events), 1)

    def test_gap_restarts_calibration_without_event(self):
        self.feed(1.77, 20)
        self.assertIsNone(self.detector.feed(self.t + 1, 1.65))
        self.assertFalse(self.detector.ready)

    def test_invalid_sample_resets(self):
        with self.assertRaises(ValueError):
            self.detector.feed(self.t + 0.005, math.nan)
        self.assertFalse(self.detector.ready)

    def test_backwards_timestamp_resets(self):
        with self.assertRaises(ValueError):
            self.detector.feed(self.t, 1.65)
        self.assertFalse(self.detector.ready)


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.counter = 0
        self.c = Controller(clock=lambda: self.now, wall=lambda: self.now)

    def packet(self, peak=None, **changes):
        self.counter += 1
        return dict(id=str(self.counter), sent_at=self.now, voltage=1.65,
                    baseline=1.65, envelope=0.0, threshold=0.02,
                    ready=True, source="simulate", pulse=peak is not None, peak=peak, **changes)

    def arm(self):
        self.c.ingest(self.packet())
        self.c.arm()

    def test_amplitude_maps_exactly_as_requested(self):
        self.arm()
        for peak in (0.10, 0.06, 0.03):
            self.now += 0.7
            self.c.ingest(self.packet(peak))
        self.assertEqual([x["option"] for x in reversed(self.c.history)], [1, 2, 3])

    def test_only_strong_clicks(self):
        self.c.set_mode("click")
        self.arm()
        for peak in (0.03, 0.08, 0.12):
            self.now += 0.7
            self.c.ingest(self.packet(peak))
        self.assertEqual([x["action"] for x in self.c.history], ["click"])

    def test_disarmed_does_nothing(self):
        self.c.ingest(self.packet(0.12))
        self.assertEqual(len(self.c.history), 0)

    def test_duplicates_and_stale_packets(self):
        self.arm()
        packet = self.packet(0.12)
        self.c.ingest(packet)
        self.now += 0.7
        self.c.ingest(packet)
        self.assertEqual(len(self.c.history), 1)
        packet = self.packet(0.12)
        packet["sent_at"] -= 10
        with self.assertRaises(ValueError):
            self.c.ingest(packet)

    def test_disconnection_does_not_rearm_on_reconnect(self):
        self.arm()
        self.now += 3
        self.c.ingest(self.packet(0.12))
        self.assertFalse(self.c.state()["armed"])
        self.assertEqual(len(self.c.history), 0)

    def test_mode_change_requires_rearming(self):
        self.arm()
        self.c.set_mode("click")
        self.c.ingest(self.packet(0.12))
        self.assertFalse(self.c.state()["armed"])
        self.assertEqual(len(self.c.history), 0)

    def test_expired_arm_does_not_click(self):
        self.arm()
        for _ in range(61):
            self.now += 1
            self.c.ingest(self.packet())
        self.c.ingest(self.packet(0.12))
        self.assertFalse(self.c.state()["armed"])
        self.assertEqual(len(self.c.history), 0)

    def test_old_event_cannot_cross_arm_boundary(self):
        self.c.ingest(self.packet())
        old = self.packet(0.12)
        self.now += 0.5
        self.c.arm()
        self.c.ingest(old)
        self.assertEqual(len(self.c.history), 0)

    def test_missing_third_option_ignored(self):
        self.c.options = self.c.options[:2]
        self.arm()
        self.c.ingest(self.packet(0.03))
        self.assertEqual(len(self.c.history), 0)

    def test_simulation_cannot_drive_real_desktop(self):
        self.c.desktop = True
        self.c.ingest(self.packet())
        with self.assertRaises(ValueError):
            self.c.arm()

    def test_desktop_failure_disarms(self):
        self.c.desktop = True
        self.c.mouse = Mock()
        self.c.mouse.press.side_effect = RuntimeError("fail-safe")
        packet = self.packet()
        packet["source"] = "serial"
        self.c.ingest(packet)
        self.c.arm()
        packet = self.packet(0.12)
        packet["source"] = "serial"
        self.c.ingest(packet)
        self.assertFalse(self.c.state()["armed"])
        self.assertIn("Stopped", self.c.history[0]["result"])

    def test_coordinates_are_passed_to_mouse(self):
        self.c.mouse = Mock()
        self.c._execute({"action": "click_at", "x": 123, "y": 456})
        self.c.mouse.click.assert_called_once_with(x=123, y=456)


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.c = Controller()
        self.token = "test-token-12345678901234567890"
        self.server = make_server(self.c, self.token, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, data=None, token=None):
        req = urllib.request.Request(self.url + path,
            data=None if data is None else json.dumps(data).encode(),
            headers={"Authorization": "Bearer " + (token or self.token), "Content-Type": "application/json"})
        return urllib.request.urlopen(req, timeout=2)

    def test_unauthorized_control_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/arm", {}, token="wrong")
        self.assertEqual(error.exception.code, 401)
        error.exception.close()

    def test_full_signal_to_http_to_choice(self):
        import time
        detector = Detector(Settings(calibration_seconds=0.2))
        for i in range(61):
            detector.feed(i * 0.005, 1.65)
        packet = dict(id="hello", sent_at=time.time(), voltage=1.65,
                      source="serial", pulse=False, peak=None, **detector.status())
        with self.request("/api/sample", packet) as response:
            self.assertEqual(response.status, 200)
        with self.request("/api/arm", {}) as response:
            self.assertEqual(response.status, 200)
        for i in range(61, 120):
            peak = detector.feed(i * 0.005, 1.73 if i < 85 else 1.65)
            if peak is not None:
                packet.update(id=str(i), sent_at=time.time(), pulse=True, peak=peak, **detector.status())
                with self.request("/api/sample", packet):
                    pass
        with self.request("/api/state") as response:
            state = json.load(response)
        self.assertEqual(state["history"][0]["option"], 2)
        with self.request("/api/stop", {}):
            pass
        self.assertFalse(self.c.state()["armed"])

    def test_static_and_malformed_requests(self):
        with self.request("/") as response:
            self.assertIn(b"Signal control", response.read())
            self.assertIn("frame-ancestors", response.headers["Content-Security-Policy"])
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/api/sample", [])
        self.assertEqual(error.exception.code, 400)
        error.exception.close()


class AcquisitionTests(unittest.TestCase):
    def test_live_pacing_timestamps_increase(self):
        samples = paced(200)
        stamps = [next(samples) for _ in range(20)]
        self.assertTrue(all(a < b for a, b in zip(stamps, stamps[1:])))

    def test_recording_and_final_disarm_packet(self):
        packets = []
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "capture.csv"
            acquire(iter([(i * 0.005, 1.65) for i in range(100)]), "csv", packets.append,
                    Settings(calibration_seconds=0.2), record=path)
            self.assertEqual(len(path.read_text().splitlines()), 101)
            self.assertFalse(packets[-1]["ready"])
            with self.assertRaises(FileExistsError):
                acquire(iter([]), "csv", packets.append, Settings(), record=path)

    def test_clipping_stops_acquisition(self):
        packets = []
        with self.assertRaises(ValueError):
            acquire(iter([(1, 3.3)]), "serial", packets.append, Settings(), limits=(0, 3.3))
        self.assertFalse(packets[-1]["ready"])

    def test_serial_protocol(self):
        serial = Mock()
        from unittest.mock import MagicMock
        serial.Serial = MagicMock()
        connection = serial.Serial.return_value.__enter__.return_value
        connection.read_until.side_effect = [b"0.000,1.650\n", b"0.005,1.770\n", b"invalid\n"]
        with patch.dict("sys.modules", {"serial": serial}):
            samples = serial_samples("COM3", 115200)
            self.assertEqual(next(samples)[1], 1.65)
            self.assertEqual(next(samples)[1], 1.77)
            with self.assertRaises(ValueError):
                next(samples)


if __name__ == "__main__":
    unittest.main()
