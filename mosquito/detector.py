"""Stateful, timestamp-based pulse detection; all voltages are ADC-input volts."""

import math
import statistics
from dataclasses import dataclass


@dataclass
class Settings:
    calibration_seconds: float = 3.0
    minimum_threshold: float = 0.02
    noise_multiplier: float = 6.0
    baseline_seconds: float = 2.0
    envelope_seconds: float = 0.01
    minimum_pulse_seconds: float = 0.02
    release_ratio: float = 0.5
    refractory_seconds: float = 0.6
    maximum_gap_seconds: float = 0.15
    maximum_pulse_seconds: float = 1.0

    def __post_init__(self):
        for name, value in vars(self).items():
            if not isinstance(value, (float, int)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.release_ratio >= 1:
            raise ValueError("release_ratio must be below 1")


class Detector:
    def __init__(self, settings=None):
        self.settings = settings or Settings()
        self.reset()

    def reset(self):
        self.started = self.previous = None
        self.samples = []
        self.ready = False
        self.baseline = self.envelope = 0.0
        self.threshold = self.settings.minimum_threshold
        self.above_since = None
        self.latched = False
        self.last_pulse = -math.inf
        self.peak = 0.0
        self.rejected = False

    def feed(self, timestamp, voltage):
        if not math.isfinite(timestamp) or not math.isfinite(voltage):
            self.reset()
            raise ValueError("Non-finite sample")
        if self.previous is not None:
            dt = timestamp - self.previous
            if dt <= 0:
                self.reset()
                raise ValueError("Sample timestamps must strictly increase")
            if dt > self.settings.maximum_gap_seconds:
                self.reset()
        if self.started is None:
            self.started = timestamp
        dt = timestamp - self.previous if self.previous is not None else 0.0
        self.previous = timestamp
        if not self.ready:
            self.samples.append(voltage)
            if len(self.samples) > 200000:
                raise ValueError("Calibration exceeds 200000 samples; shorten calibration")
            if timestamp - self.started >= self.settings.calibration_seconds:
                if len(self.samples) < 20:
                    raise ValueError("Calibration needs at least 20 samples")
                self.baseline = statistics.median(self.samples)
                mad = statistics.median(abs(v - self.baseline) for v in self.samples)
                self.threshold = max(self.settings.minimum_threshold,
                                     1.4826 * mad * self.settings.noise_multiplier)
                self.samples.clear()
                self.ready = True
            return None
        residual = abs(voltage - self.baseline)
        # Freeze baseline tracking during activity, so long pulses do not retrigger.
        if not self.latched and residual < self.threshold:
            self.baseline += (1 - math.exp(-dt / self.settings.baseline_seconds)) * (voltage - self.baseline)
        self.envelope += (1 - math.exp(-dt / self.settings.envelope_seconds)) * (residual - self.envelope)
        if self.above_since is not None:
            self.peak = max(self.peak, residual)
            if timestamp - self.above_since > self.settings.maximum_pulse_seconds:
                self.rejected = True
        if self.envelope <= self.threshold * self.settings.release_ratio:
            peak = None
            if (self.above_since is not None and self.latched and not self.rejected
                    and timestamp - self.last_pulse >= self.settings.refractory_seconds):
                peak = self.peak
                self.last_pulse = timestamp
            self.latched = False
            self.above_since = None
            self.peak = 0.0
            self.rejected = False
            return peak
        if self.envelope < self.threshold:
            return None
        if self.above_since is None:
            self.above_since = timestamp
            self.peak = residual
        if timestamp - self.above_since >= self.settings.minimum_pulse_seconds:
            self.latched = True
        return None

    def status(self):
        return dict(ready=self.ready, baseline=self.baseline,
                    envelope=self.envelope, threshold=self.threshold)
