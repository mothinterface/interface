import csv
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from mosquito.acquisition import acquire
from mosquito.detector import Settings
from mosquito.headless import HeadlessController, run_receiver
from mosquito.recording import CsvHistory, FIELDS


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "history.csv"
        self.now = 100.0
        self.counter = 0
        self.c = HeadlessController(clock=lambda: self.now, wall=lambda: self.now,
                                    arm_on_ready=True, arm_seconds=3600)
        self.log = CsvHistory(self.c, self.path)
        self.c.csv_history = self.log
        self.addCleanup(self.log.close)

    def packet(self, **updates):
        self.counter += 1
        p = dict(id=str(self.counter), sent_at=self.now, source="ads1115", ready=True,
                 voltage=1.65, baseline=1.65, envelope=0.0, threshold=0.02, pulse=False,
                 peak=None, window_peak=0.004)
        p.update(updates)
        return p

    def rows(self):
        self.log.close()
        with self.path.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            self.assertEqual(tuple(reader.fieldnames), FIELDS)
            return list(reader)

    def test_fifteen_minutes_continuous_with_two_actions_and_gaps(self):
        for i in range(4500):
            self.now = 100 + i / 5 + 0.05
            if i < 3150:
                if i == 100:
                    self.c.set_mode("click")
                    self.c.arm()
                self.c.ingest(self.packet(pulse=i in (50, 100),
                    peak=0.08 if i == 50 else 0.12 if i == 100 else None))
            self.log.flush()
        self.now = 1000.0
        rows = self.rows()
        self.assertEqual(len(rows), 4500)
        self.assertEqual([r["elapsed_seconds"] for r in rows], [f"{i / 5:.1f}" for i in range(4500)])
        self.assertTrue(all(r["duration_seconds"] == "0.2" for r in rows))
        self.assertEqual(sum(r["signal_status"] == "good" for r in rows), 3150)
        self.assertEqual(sum(r["signal_status"] == "missing" for r in rows), 1350)
        self.assertEqual([r["selected_option"] for r in rows if r["selected_option"]], ["2"])
        self.assertEqual(sum(int(r["click_registered"]) for r in rows), 1)
        self.assertEqual(rows[50]["mode"], "choose")
        self.assertEqual(rows[50]["available_options"], "3")
        self.assertEqual(rows[100]["mode"], "click")
        self.assertTrue(all(r["data_type"] == "recorded_dry_run" for r in rows))
        self.assertTrue(all(r["signal_voltage_v"] == r["peak_amplitude_v"] == "" for r in rows[3150:]))

    def test_first_voltage_max_reported_peak_and_partial_final_interval(self):
        self.now += 0.01
        self.c.ingest(self.packet(voltage=1.651, window_peak=0.03))
        self.now += 0.1
        self.c.ingest(self.packet(voltage=1.655, window_peak=0.09))
        self.now = 100.35
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["signal_voltage_v"], "1.651000")
        self.assertEqual(rows[0]["peak_amplitude_v"], "0.090000")
        self.assertEqual(rows[1]["duration_seconds"], "0.15")

    def test_duplicate_and_invalid_packets_do_not_fill_missing_bins(self):
        p = self.packet()
        self.c.ingest(p)
        self.now += 0.25
        self.assertFalse(self.c.ingest(p))
        with self.assertRaises(ValueError):
            self.c.ingest(self.packet(window_peak=float("nan")))
        self.now += 0.15
        self.assertEqual([r["signal_status"] for r in self.rows()], ["good", "missing"])

    def test_calibration_and_end_notification_are_not_good_measurements(self):
        self.c.ingest(self.packet(ready=False, baseline=0, window_peak=None))
        self.now += 0.25
        self.c.ingest(self.packet(ready=False, voltage=0, sample_valid=False))
        self.now += 0.15
        rows = self.rows()
        self.assertEqual(rows[0]["signal_status"], "calibrating")
        self.assertEqual(rows[0]["peak_amplitude_v"], "")
        self.assertEqual(rows[1]["signal_status"], "missing")
        self.assertEqual(rows[1]["signal_voltage_v"], "")

    def test_real_click_success_and_failed_action(self):
        self.c.desktop = True
        self.c.mouse = Mock()
        self.c.set_mode("click")
        self.c.ingest(self.packet())
        self.c.arm()
        self.now += 0.7
        self.c.ingest(self.packet(pulse=True, peak=0.12))
        self.c.mouse.click.side_effect = RuntimeError("Driver failure")
        self.now += 0.7
        self.c.ingest(self.packet(pulse=True, peak=0.12))
        self.now += 0.2
        rows = self.rows()
        self.assertEqual(sum(int(r["click_registered"]) for r in rows), 1)
        self.assertTrue(all(r["data_type"] == "recorded" for r in rows))
        self.assertFalse(self.c.state()["armed"])

    def test_no_overwrite_and_recording_failure_stops_control(self):
        with self.assertRaises(FileExistsError):
            CsvHistory(self.c, self.path)
        self.c.ingest(self.packet())
        with patch.object(self.log, "capture", side_effect=OSError("Disk full")):
            with self.assertRaises(ValueError):
                self.c.ingest(self.packet())
        self.assertFalse(self.c.state()["armed"])
        with self.assertRaises(ValueError):
            self.c.ingest(self.packet())

    def test_acquisition_reports_peak_and_marks_end_of_stream(self):
        packets = []
        samples = [(i / 200, 1.65 + (0.08 if 800 <= i < 820 else 0)) for i in range(1000)]
        with patch("mosquito.acquisition.time.monotonic", side_effect=[i / 200 for i in range(1000)]):
            acquire(samples, "ads1115", packets.append, Settings())
        self.assertTrue(any((p["window_peak"] or 0) > 0.07 for p in packets))
        self.assertFalse(packets[-1]["sample_valid"])
        self.assertFalse(packets[-1]["ready"])
        self.assertTrue(all(p["sample_valid"] for p in packets[:-1]))

    def test_receiver_writes_even_without_a_source(self):
        path = Path(self.temp.name) / "disconnected.csv"
        c = HeadlessController()
        run_receiver(c, "test-token-12345678901234567890", "127.0.0.1", 0,
                     threading.Event(), no_hotkeys=True, duration=0.6, history_path=path)
        with path.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 3)
        self.assertEqual([r["elapsed_seconds"] for r in rows], ["0.0", "0.2", "0.4"])
        self.assertTrue(all(r["signal_status"] == "missing" and r["data_type"] == "unknown" for r in rows))


if __name__ == "__main__":
    unittest.main()
