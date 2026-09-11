# Code-only setup: amplifier + ADS1115 → Pi 5 → Windows 11

`pi_acquire.py` samples the ADS1115 on Raspberry Pi OS. `windows_receiver.py` runs on Windows and sends mouse clicks or option keypresses. It serves no HTML, opens no window or browser, and logs status to the terminal. The old dashboard files are unused by these entry points.

## Connections

The analog cable goes from the amplifier's **conditioned output** to ADS1115 A0 and the appropriate ADC reference/ground. ADS1115 SDA/SCL connect to the Pi via I²C; an analog cable is not a Pi data connection. For a 3.3 V-compatible breakout, the Pi pins are 1 → VDD, 6 → GND, 3/GPIO2 → SDA, 5/GPIO3 → SCL. `--differential` selects A0 minus A1 if the amplifier provides a compatible differential output. Respect the ADC's per-pin input limits and the amplifier's grounding requirements.

The Pi connects to Windows using USB **Ethernet gadget mode** on the Pi 5 USB-C port. Enable `rpi-usb-gadget` on Raspberry Pi OS and install the official Windows RNDIS driver as described in [the Raspberry Pi guide](https://www.raspberrypi.com/news/usb-gadget-mode-in-raspberry-pi-os-ssh-over-usb/). A bare USB cable without gadget networking does not transfer these events. No HID-mouse emulation or serial-over-USB gadget is assumed.

The ADS1115 path expects a slowly varying, conditioned pulse/activity envelope. An amplifier output containing raw fast neural spikes needs suitable envelope conditioning before this ADC. The code cannot recover bandwidth absent from the sampled signal.

## Windows receiver

Run in the project folder using Python 3.10+:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
$env:MOSQUITO_TOKEN = & .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
.\.venv\Scripts\python.exe windows_receiver.py --host 0.0.0.0 --desktop --mode choose --options options.example.json
```

The receiver prints its token. Copy that value to the Pi command below. Default port is **8770**. Run `ipconfig` and find the **Windows laptop's** IPv4 address on the Raspberry Pi USB network adapter. Permit TCP 8770 from the Pi on that adapter if Windows Firewall blocks it. This authenticated HTTP transport is for the direct USB/private connection, not the public internet. Keep Pi and Windows clocks synchronized within two seconds.

Control from anywhere on the normal Windows desktop:

| Hotkey | Action |
| --- | --- |
| Ctrl+Alt+C | Switch to strong-signal click mode; disarm |
| Ctrl+Alt+P | Switch to three-strength choice mode; disarm |
| Ctrl+Alt+A | Arm after calibration, for 60 seconds |
| Ctrl+Alt+S | Stop input immediately |
| Ctrl+Alt+Q | Stop and exit the receiver |

Select the mode, focus the target application, and arm. Mode and arm hotkeys execute after their modifier keys are released, to avoid accidentally generating Ctrl+Alt-modified input. A registered hotkey conflict stops startup with an error rather than leaving stop controls unavailable. Ctrl+C also exits from the terminal. Moving the cursor to the top-left invokes PyAutoGUI's fail-safe on the next action.

`--arm-seconds 300` changes the arm window. `--arm-on-ready` arms **once** after the first quiet, calibrated sample so no manual arm is needed at startup. Stopping or changing mode cancels pending startup arming. Disconnects, expired arming, and driver errors do not automatically rearm. `--no-hotkeys` disables registration when controlling through the API or running tests.

## Pi acquisition

Copy the project to the Pi, enable I²C in `sudo raspi-config`, then:

```bash
sudo apt install python3-venv
python3 -m venv .venv --system-site-packages
.venv/bin/python -m pip install -r requirements-pi.txt
export MOSQUITO_TOKEN='PASTE_THE_TOKEN_PRINTED_ON_WINDOWS'
.venv/bin/python pi_acquire.py --receiver http://LAPTOP_USB_IP:8770 --low 0.02 --rate 200 --address 0x48 --gain 1
```

Replace `LAPTOP_USB_IP` with the laptop USB adapter address. The 0.02 V low threshold is a placeholder measured **after amplification**, relative to the resting baseline. Use a quiet baseline for the first three seconds. The detector suppresses events during calibration. The I²C address is configurable if your board is not at 0x48. Blinka setup may require system packages/permissions; see [Adafruit's installation guide](https://learn.adafruit.com/circuitpython-on-raspberrypi-linux/installing-circuitpython-on-raspberry-pi).

The Windows receiver automatically saves the continuous usage CSV described below. Optionally, to also record raw ADC measurements on the Pi (a separate diagnostic format with `timestamp,voltage` columns):

```bash
mkdir -p recordings
.venv/bin/python pi_acquire.py --receiver http://LAPTOP_USB_IP:8770 --record recordings/session-01.csv
```

## Signal strength and actions

All thresholds are ADC-input volts above/below the baseline, not unamplified electrode volts. On the Pi, `--low` controls pulse detection (with an additional noise-based minimum). On Windows, `--medium` and `--strong` control classification. Example receiver thresholds:

```powershell
.\.venv\Scripts\python.exe windows_receiver.py --host 0.0.0.0 --desktop --mode click --medium 0.06 --strong 0.10 --arm-on-ready
```

In click mode, only peaks ≥ strong generate a left-click at the current pointer. In choose mode, strong → option 1, medium → option 2, low → option 3. The default options send keys 1/2/3; the target app must recognize those keys. To click buttons instead, edit the placeholder coordinates in `options.coordinates.example.json` and pass it with `--options`. Display scaling and target window position must match those coordinates.

There is no screen-reading logic to infer when it is time to choose versus click. Set the mode with the hotkeys, `--mode`, or code. Another program can call the authenticated endpoints:

```python
import json
import os
import urllib.request

def control(command, **data):
    request = urllib.request.Request(
        f"http://127.0.0.1:8770/api/{command}",
        data=json.dumps(data).encode(),
        headers={"Authorization": "Bearer " + os.environ["MOSQUITO_TOKEN"],
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        return json.load(response)

control("mode", mode="choose")  # or mode="click"; changing mode disarms
control("arm")                  # errors until calibrated hardware is connected
# ... application presents its options / waits for a selection ...
control("stop")
```

## Test without actual computer input

Omit `--desktop` on the receiver to log actions instead. Simulation/replay cannot arm a receiver started with `--desktop`.

```powershell
python windows_receiver.py --no-hotkeys --arm-on-ready --duration 20
# In a second terminal, with the same MOSQUITO_TOKEN:
python -m mosquito acquire --source simulate --receiver http://127.0.0.1:8770 --duration 15
```

Run `python -m unittest discover -s tests -v` for automated checks. The tests include continuous CSV recording, missing intervals, action results, duplicate rejection, and recording failures. `python tests/smoke_headless.py` checks the separate acquisition/receiver processes and their saved CSV with all three simulated strengths. Real hardware needs calibration and end-to-end testing on your connected devices; no physical Pi/ADC has been accessed during development.

## Automatic live CSV history

Every run of `windows_receiver.py` (or `python -m mosquito receive`) automatically creates **one CSV**, named `history/usage_YYYYMMDD_HHMMSS_microseconds.csv`, relative to the working directory. The name uses UTC; the terminal prints the full path. Recording starts with the receiver, continues while disarmed or disconnected, and ends when the receiver exits. No extra history files are created. Your existing synthetic example is preserved.

To choose a filename and record for exactly 15 minutes:

```powershell
python windows_receiver.py --host 0.0.0.0 --desktop --duration 900 --history history/my-recording.csv
```

Use the usual mode and arming controls. An existing output filename is rejected. Omit `--duration` to record until Ctrl+C or Ctrl+Alt+Q. Deploy the updated `mosquito` package on both the Pi and Windows to include acquisition peak summaries and explicit source-shutdown notifications.

The column names and order exactly match `history/mosquito_usage_15min.csv`:

```csv
elapsed_seconds,duration_seconds,signal_voltage_v,peak_amplitude_v,signal_status,mode,available_options,selected_option,click_registered,data_type
```

Rows start at 0.0, 0.2, 0.4 seconds, etc., measured on the receiver's monotonic clock. Normal rows cover 0.2 seconds. A manual exit between boundaries preserves the final partial interval with its actual `duration_seconds`; `--duration 900` produces 4,500 complete rows. Rows are flushed regularly and on graceful shutdown; a forced process kill or power failure may lose the unfinished interval.

`signal_voltage_v` is the first valid voltage **received** in the interval. `peak_amplitude_v` is the largest baseline deviation reported during it. Updated acquisition code summarizes high-rate samples between telemetry reports, sent approximately every 0.1 seconds and on detected pulses. These summaries are assigned to receiver arrival intervals; network delay and window boundaries mean this is not a precisely synchronized raw ADC recording. Older senders without peak summaries fall back to baseline deviation of their transmitted samples. Keep `--record` if you also need the raw high-rate measurements.

An interval with no valid measurements is `missing`, with blank voltages. Received but uncalibrated samples are `calibrating`, with a blank peak. `good` means calibrated measurements arrived, not that electrode contact or biological origin was independently verified. A loose electrode can still yield numeric ADC readings; the program cannot reliably label those as disconnected without additional contact-quality information.

`mode` is `idle` while disarmed and `choose` or `click` while armed; action rows retain the action's mode. `available_options` is the configured option count in choose mode. Successful choices populate `selected_option`; successful mouse actions set `click_registered=1`. Failed desktop actions do not count as successful results. The existing action cooldown prevents multiple actions within one 0.2-second row.

`data_type` is `recorded` for hardware with desktop output enabled, `recorded_dry_run` for hardware without desktop output, `synthetic` for the simulator, and `replay` for CSV input. Missing rows before the first source are `unknown`; later gaps retain the last source type. A row containing different source types is `mixed`. In dry runs, action columns represent simulated decisions and clicks, not actual computer input. Live recordings reflect observed availability and actions; they do not force the example's 70% availability or its two actions. File-writing errors stop control and exit with an error.

## Included synthetic usage history

`history/mosquito_usage_15min.csv` is the single result file. It contains 4,500 consecutive rows at 0.2-second intervals, covering [0.0, 900.0) seconds. There are 3,150 good rows (70%) and 1,350 missing rows across eight simulated connection gaps. Missing voltages are blank, and their timeline rows remain present.

Each row covers the interval starting at `elapsed_seconds` for `duration_seconds` (0.2 s). `signal_voltage_v` is the voltage at its start; `peak_amplitude_v` is the maximum baseline deviation within that interval, calculated from the internal 200 Hz simulation. Actions detected within the interval are included in the same row: at 236.0 s, `selected_option=2` with `available_options=3`; at 662.0 s, `click_registered=1`. Other rows have no selected option and `click_registered=0`.

Every row has `data_type=synthetic`. This is generated demonstration data, not a hardware recording; no desktop input is executed. The 5 Hz export is a reporting history, not a replacement for the detector's high-rate input.

Generate another CSV with `python generate_history.py --output history/another-synthetic-history.csv`. The file must be new. The same seed reproduces the data without waiting 15 minutes. No JSON, sidecar logs, or separate session files are generated.
