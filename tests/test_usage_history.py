import csv
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from mosquito.usage_history import generate_history, connection_schedule


class UsageHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.temp.name) / "history.csv"
        cls.summary = generate_history(cls.path)
        with cls.path.open(newline="", encoding="utf-8") as stream:
            cls.rows = list(csv.DictReader(stream))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_continuous_02_second_rows_cover_15_minutes(self):
        self.assertEqual(len(self.rows), 4500)
        for i, row in enumerate(self.rows):
            self.assertEqual(Decimal(row["elapsed_seconds"]), Decimal(i) / 5)
            self.assertEqual(Decimal(row["duration_seconds"]), Decimal("0.2"))
        self.assertEqual(Decimal(self.rows[-1]["elapsed_seconds"]) + Decimal("0.2"), 900)
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_exact_availability_and_missing_values(self):
        self.assertEqual(sum(r["signal_status"] == "good" for r in self.rows), 3150)
        self.assertEqual(sum(r["signal_status"] == "missing" for r in self.rows), 1350)
        for row in self.rows:
            self.assertEqual(row["data_type"], "synthetic")
            if row["signal_status"] == "missing":
                self.assertEqual(row["signal_voltage_v"], "")
                self.assertEqual(row["peak_amplitude_v"], "")
            else:
                self.assertGreater(float(row["signal_voltage_v"]), 0)

    def test_exactly_one_three_option_choice_and_one_click(self):
        choices = [r for r in self.rows if r["selected_option"]]
        clicks = [r for r in self.rows if r["click_registered"] == "1"]
        self.assertEqual(len(choices), 1)
        self.assertEqual(len(clicks), 1)
        self.assertEqual(choices[0]["selected_option"], "2")
        self.assertEqual(choices[0]["available_options"], "3")
        self.assertEqual(choices[0]["mode"], "choose")
        self.assertEqual(clicks[0]["mode"], "click")
        self.assertEqual(choices[0]["elapsed_seconds"], "236.0")
        self.assertEqual(clicks[0]["elapsed_seconds"], "662.0")
        self.assertTrue(0.06 <= float(choices[0]["peak_amplitude_v"]) < 0.10)
        self.assertGreaterEqual(float(clicks[0]["peak_amplitude_v"]), 0.10)

    def test_eight_dropouts_and_no_actions_during_gaps(self):
        self.assertEqual(sum(i["quality"] == "missing" for i in connection_schedule()), 8)
        for row in self.rows:
            if row["signal_status"] == "missing":
                self.assertEqual(row["click_registered"], "0")
                self.assertEqual(row["selected_option"], "")

    def test_never_overwrites_existing_history(self):
        with self.assertRaises(FileExistsError):
            generate_history(self.path)


if __name__ == "__main__":
    unittest.main()
