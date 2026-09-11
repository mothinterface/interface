# Moth Interface

Signal acquisition, computer input, and synchronized visualization for a moth–machine interface project. The system maps conditioned electrical activity into three-option selections and mouse clicks, records a continuous CSV history, and presents a shared timeline to an online audience.

[Website](https://mothinterface.com) · [GitHub](https://github.com/mothinterface) · [X](https://x.com/mothinterface)

## Start here

| Task | Entry point |
| --- | --- |
| Connect the amplifier, ADC, Pi, and Windows laptop | [Hardware setup](HEADLESS.md) |
| Acquire conditioned voltage readings | `pi_acquire.py` |
| Receive readings, control the desktop, and save history | `windows_receiver.py` |
| Operate the shared audience broadcast | [Website and operator guide](website/README.md) |
| Watch the current broadcast | [Live view](https://mothinterface.com) |

## Acquisition and control

```text
Moth recording electrodes
    → differential amplifier and signal conditioning
    → ADS1115 analog-to-digital converter
    → Raspberry Pi 5 / Raspberry Pi OS
    → USB Ethernet connection
    → Windows 11 receiver
    → mouse click or option selection
```

The Pi reads the amplifier's conditioned output through the ADC. The Windows receiver classifies completed pulses using their peak absolute deviation from the calibrated baseline.

| Control mode | Strong pulse | Medium pulse | Low pulse |
| --- | --- | --- | --- |
| Click | Left-click at the current pointer | No action | No action |
| Choose | Option 1 | Option 2 | Option 3 |

Activity below the detection threshold produces no action. Choices use keys `1`, `2`, and `3` by default; coordinate-based actions can be configured in `options.coordinates.example.json`. The operator selects the mode and arms control before input is accepted.

Thresholds describe voltage at the ADC after amplification. Calibrate them against measured baseline noise and pulse amplitudes. The ADS1115 path expects a conditioned activity envelope; physical signal acquisition and biological interpretation require validation with the connected equipment.

Follow [HEADLESS.md](HEADLESS.md) for installation, wiring, USB networking, acquisition commands, calibration, and receiver options. The acquisition adapter runs on Raspberry Pi OS; Windows 11 runs the laptop receiver.

### Receiver controls

| Shortcut | Action |
| --- | --- |
| Ctrl+Alt+C | Switch to click mode and disarm |
| Ctrl+Alt+P | Switch to choice mode and disarm |
| Ctrl+Alt+A | Arm after calibration |
| Ctrl+Alt+S | Stop desktop input |
| Ctrl+Alt+Q | Stop and exit |

The receiver starts disarmed. Mode changes, disconnects, expired arming, and driver errors stop control. Reconnecting does not automatically rearm it. Omit `--desktop` to inspect readings and decisions without sending desktop input.

## Continuous recording

Every receiver run creates one CSV in `history/`, with a row every **0.2 seconds**. Recording continues while control is disarmed and during missing-data intervals. The terminal prints the output path.

For a 15-minute recording with a chosen filename:

```powershell
python windows_receiver.py --host 0.0.0.0 --desktop --duration 900 --history history/moth-recording.csv
```

Configure the acquisition connection and use the mode and arming controls described above. Existing output files are never overwritten.

The history uses this exact column order:

```csv
elapsed_seconds,duration_seconds,signal_voltage_v,peak_amplitude_v,signal_status,mode,available_options,selected_option,click_registered,data_type
```

Missing intervals retain their rows with blank voltages. Action fields record the current mode, available options, selection, and click result; `data_type` preserves the source classification. A complete 900-second run produces 4,500 rows. Stopping between interval boundaries preserves a final partial row.

This history summarizes receiver activity. Optional acquisition-side recording preserves higher-rate ADC measurements separately; see the setup guide for the precise field definitions and timing behavior.

## Website and shared broadcast

The website presents the full 15-minute reference history and a separately controlled broadcast view. The supplied reference trace was authored for the production, with 70% signal availability, a three-option selection, and one click. Hardware recordings preserve the availability and actions actually observed during each run.

The broadcast uses that reference trace with a shared server clock. Viewers join the current position; the operator starts the take, pauses or resumes it, and cues clicks. Broadcast cues update the website display. Desktop input is handled by the Windows receiver.

See the [website README](website/README.md) for cue timing, access, synchronization, and deployment details.

## Development

Run the Python checks from this directory:

```powershell
python -m unittest discover -s tests -v
```

Run the website checks from `website/`:

```powershell
npm ci
npm run check
```

The existing Python package name, environment variable names, CSV filenames, and hosted domain retain their original identifiers for compatibility. Use the commands in the setup guide exactly as written.
