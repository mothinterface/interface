"""Separate-process smoke test. Simulation only; never emits desktop input."""
import json
import csv
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
import tempfile
import urllib.request


def main():
    root = Path(__file__).resolve().parents[1]
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    token = secrets.token_urlsafe(32)
    env = dict(os.environ, MOSQUITO_TOKEN=token)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    processes = []
    temp = tempfile.TemporaryDirectory()
    history_path = Path(temp.name) / "live.csv"

    def launch(*args):
        process = subprocess.Popen([sys.executable, *args], cwd=root, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=flags)
        processes.append(process)
        return process

    def api(path, body=None):
        request = urllib.request.Request(f"http://127.0.0.1:{port}/api/{path}",
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=1) as response:
            return json.load(response)

    try:
        receiver = launch("windows_receiver.py", "--port", str(port), "--no-hotkeys",
                          "--arm-on-ready", "--duration", "16", "--history", str(history_path))
        deadline = time.monotonic() + 5
        while True:
            try:
                api("state")
                break
            except OSError:
                if receiver.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("Receiver did not start")
                time.sleep(0.05)
        # Override the wrapper's hardware default only in this simulation test.
        sender = launch("pi_acquire.py", "--source", "simulate", "--receiver",
                        f"http://127.0.0.1:{port}", "--duration", "14")
        stdout, stderr = sender.communicate(timeout=20)
        if sender.returncode:
            raise AssertionError((stdout + stderr).replace(token, "<token>"))
        state = api("state")
        assert not state["desktop"], "Smoke test must never drive the desktop"
        assert not state["armed"], "Source shutdown must disarm the receiver"
        assert {entry["option"] for entry in state["history"]} == {1, 2, 3}, state["history"]
        api("mode", {"mode": "click"})
        assert api("state")["mode"] == "click"
        api("stop", {})
        stdout, stderr = receiver.communicate(timeout=5)
        assert receiver.returncode == 0, (stdout + stderr).replace(token, "<token>")
        with history_path.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            assert reader.fieldnames == ["elapsed_seconds", "duration_seconds", "signal_voltage_v",
                "peak_amplitude_v", "signal_status", "mode", "available_options", "selected_option",
                "click_registered", "data_type"]
            rows = list(reader)
        assert len(rows) == 80, len(rows)
        assert [r["elapsed_seconds"] for r in rows] == [f"{i / 5:.1f}" for i in range(80)]
        assert {r["selected_option"] for r in rows if r["selected_option"]} == {"1", "2", "3"}
        assert rows[-1]["signal_status"] == "missing"
        assert all(r["data_type"] == "synthetic" for r in rows if r["signal_voltage_v"])
        print("PASS: Pi/Windows entry points, all 3 strengths, shutdown, mode change, stop, continuous CSV")
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=3)
        temp.cleanup()


if __name__ == "__main__":
    main()
