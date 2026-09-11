import argparse
import json
import logging
import math
import os
import secrets
import threading
import sys

from .acquisition import HttpSink, acquire
from .control import Controller
from .detector import Settings
from .server import make_server
from . import sources


def parser():
    p = argparse.ArgumentParser(description="Mosquito signals -> amplitude choices / clicks")
    p.add_argument("command", choices=["receive", "acquire", "demo", "serve"])
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--desktop", action="store_true", help="Enable actual Windows mouse/keyboard actions")
    p.add_argument("--mode", choices=["choose", "click"], default="choose")
    p.add_argument("--options", help="JSON file: 1-3 option objects")
    p.add_argument("--medium", type=float, default=0.06, help="Medium peak threshold in ADC volts")
    p.add_argument("--strong", type=float, default=0.10, help="Strong peak threshold in ADC volts")
    p.add_argument("--arm-seconds", type=float, default=60)
    p.add_argument("--source", choices=["simulate", "csv", "serial", "ads1115"], default="simulate")
    p.add_argument("--receiver", default="http://127.0.0.1:8765")
    p.add_argument("--serial-port", default="/dev/ttyACM0")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--csv", help="CSV input with timestamp,voltage columns")
    p.add_argument("--record", help="New CSV file to record raw samples (never overwrites)")
    p.add_argument("--history", help="Receiver history CSV (default: new timestamped CSV in history/)")
    p.add_argument("--rate", type=float, default=200)
    p.add_argument("--address", type=lambda s: int(s, 0), default=0x48)
    p.add_argument("--gain", type=float, choices=[2/3, 1, 2, 4, 8, 16], default=1)
    p.add_argument("--differential", action="store_true", help="ADS1115 A0 minus A1")
    p.add_argument("--low", type=float, default=0.02, help="Minimum pulse threshold in ADC volts")
    p.add_argument("--calibration", type=float, default=3.0)
    p.add_argument("--min-pulse-ms", type=float, default=20)
    p.add_argument("--max-pulse-ms", type=float, default=1000)
    p.add_argument("--refractory-ms", type=float, default=600)
    p.add_argument("--input-min", type=float)
    p.add_argument("--input-max", type=float)
    p.add_argument("--arm-on-ready", action="store_true", help="Headless receiver: arm once after calibration")
    p.add_argument("--no-hotkeys", action="store_true", help="Headless receiver: use API control only")
    p.add_argument("--duration", type=float, help="Exit after this many seconds (receive/acquire)")
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    stop = threading.Event()
    server = None
    try:
        if not (math.isfinite(args.rate) and 20 <= args.rate <= 5000):
            raise ValueError("--rate must be between 20 and 5000")
        if args.source == "ads1115" and args.rate > 400:
            raise ValueError("ADS1115 adapter supports requested polling rates up to 400 Hz; measure actual rate")
        if not (0 < args.low < args.medium < args.strong < math.inf):
            raise ValueError("Require 0 < --low < --medium < --strong")
        if args.min_pulse_ms >= args.max_pulse_ms:
            raise ValueError("Minimum pulse duration must be below maximum")
        if args.command == "demo" and args.desktop:
            raise ValueError("Demo cannot enable desktop actions")
        if args.duration is not None and (not math.isfinite(args.duration) or args.duration <= 0):
            raise ValueError("--duration must be finite and positive")
        limits = None
        if args.input_min is not None or args.input_max is not None:
            if args.input_min is None or args.input_max is None or not (-math.inf < args.input_min < args.input_max < math.inf):
                raise ValueError("Supply finite --input-min and --input-max together, in increasing order")
            limits = (args.input_min, args.input_max)
        token = os.environ.get("MOSQUITO_TOKEN")
        if args.command == "acquire" and not token:
            raise ValueError("Set MOSQUITO_TOKEN to the token printed by the controller")
        token = token or secrets.token_urlsafe(32)
        if len(token) < 24 or not token.isascii():
            raise ValueError("MOSQUITO_TOKEN must have at least 24 ASCII characters")
        settings = Settings(calibration_seconds=args.calibration, minimum_threshold=args.low,
                            minimum_pulse_seconds=args.min_pulse_ms / 1000,
                            maximum_pulse_seconds=args.max_pulse_ms / 1000,
                            refractory_seconds=args.refractory_ms / 1000)
        if args.command in ("serve", "demo", "receive"):
            options = None
            if args.options:
                with open(args.options, encoding="utf-8") as stream:
                    options = json.load(stream)
            if args.command == "receive":
                from .headless import HeadlessController, run_receiver
                controller = HeadlessController(options=options, mode=args.mode, desktop=args.desktop,
                    medium=args.medium, strong=args.strong, arm_seconds=args.arm_seconds,
                    arm_on_ready=args.arm_on_ready)
                run_receiver(controller, token, args.host, args.port, stop, args.no_hotkeys,
                             args.duration, args.history)
                return 0
            controller = Controller(options=options, mode=args.mode, desktop=args.desktop,
                                    medium=args.medium, strong=args.strong, arm_seconds=args.arm_seconds)
            server = make_server(controller, token, args.host, args.port)
            print(f"Dashboard: http://127.0.0.1:{server.server_port}\nController token: {token}", flush=True)
            print("Output: " + ("REAL DESKTOP (starts disarmed)" if args.desktop else "DEMO (no computer input)"), flush=True)
            if args.command == "demo":
                def demo_worker():
                    try:
                        acquire(sources.simulate(args.rate), "simulate", controller.ingest, settings, stop, args.record, limits)
                    except Exception:
                        logging.exception("Demo acquisition stopped")
                threading.Thread(target=demo_worker, daemon=True).start()
            server.serve_forever(poll_interval=0.2)
        else:
            if args.source == "csv" and not args.csv:
                raise ValueError("CSV source requires --csv PATH")
            sample_source = {"simulate": lambda: sources.simulate(args.rate),
                             "csv": lambda: sources.replay(args.csv),
                             "serial": lambda: sources.serial_samples(args.serial_port, args.baud),
                             "ads1115": lambda: sources.ads1115(args.rate, args.address, args.gain, args.differential)}[args.source]()
            print("Calibrating: keep acquisition input at its quiet baseline.", flush=True)
            sink = HttpSink(args.receiver, token)
            if args.duration is not None:
                timer = threading.Timer(args.duration, stop.set)
                timer.daemon = True
                timer.start()
            acquire(sample_source, args.source, sink, settings, stop, args.record, limits)
            sink.queue.join()
    except KeyboardInterrupt:
        pass
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    finally:
        stop.set()
        if server:
            server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
