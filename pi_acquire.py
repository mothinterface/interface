"""Run on Raspberry Pi OS: conditioned amplifier → ADS1115 → Windows receiver."""
import sys

from mosquito.__main__ import main


if __name__ == "__main__":
    raise SystemExit(main(["acquire", "--source", "ads1115", *sys.argv[1:]]))
