"""Run on Windows 11. No browser/UI. Pass --desktop to generate actual input."""
import sys

from mosquito.__main__ import main


if __name__ == "__main__":
    raise SystemExit(main(["receive", "--port", "8770", *sys.argv[1:]]))
