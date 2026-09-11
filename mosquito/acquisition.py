import csv
import json
import logging
import queue
import threading
import time
import uuid
import urllib.request

from .detector import Detector

LOG = logging.getLogger(__name__)


class HttpSink:
    """Network I/O never stalls sampling. Congested/stale events are dropped."""
    def __init__(self, url, token):
        self.url = url.rstrip("/") + "/api/sample"
        self.token = token
        self.queue = queue.Queue(maxsize=8)
        threading.Thread(target=self._run, daemon=True).start()

    def __call__(self, packet):
        try:
            self.queue.put_nowait((time.monotonic(), packet))
        except queue.Full:
            LOG.warning("Network queue full: discarded sample/event")

    def _run(self):
        while True:
            queued, packet = self.queue.get()
            try:
                if time.monotonic() - queued > 0.25:
                    continue
                request = urllib.request.Request(self.url, json.dumps(packet).encode(),
                    headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
                with urllib.request.urlopen(request, timeout=0.5) as response:
                    response.read(4096)
            except Exception as error:
                LOG.warning("Receiver unavailable; event discarded: %s", error)
            finally:
                self.queue.task_done()


def acquire(samples, source, sink, settings, stop=None, record=None, limits=None):
    detector = Detector(settings)
    stop = stop or threading.Event()
    last_report = -1e9
    voltage = 0.0
    window_peak = None
    stream = open(record, "x", newline="", encoding="utf-8") if record else None
    writer = csv.writer(stream) if stream else None
    if writer:
        writer.writerow(["timestamp", "voltage"])

    def packet(pulse=False, peak=None, sample_valid=True):
        return dict(id=uuid.uuid4().hex, sent_at=time.time(), source=source,
                    voltage=voltage, pulse=pulse, peak=peak, sample_valid=sample_valid,
                    window_peak=window_peak, **detector.status())

    try:
        for timestamp, voltage in samples:
            if stop.is_set():
                break
            if writer:
                writer.writerow([timestamp, voltage])
            if limits and not limits[0] < voltage < limits[1]:
                raise ValueError("Input reached configured voltage limits; inspect amplifier/ADC clipping")
            peak = detector.feed(timestamp, voltage)
            if detector.ready:
                window_peak = max(window_peak or 0.0, abs(voltage - detector.baseline))
            now = time.monotonic()
            if peak is not None or now - last_report >= 0.10:
                sink(packet(peak is not None, peak))
                window_peak = None
                last_report = now
    finally:
        detector.reset()
        # Finite last value keeps the disarm notification valid after a bad sample.
        voltage = 0.0
        window_peak = None
        sink(packet(sample_valid=False))
        if stream:
            stream.close()
        close = getattr(samples, "close", None)
        if close:
            close()
