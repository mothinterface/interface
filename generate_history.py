"""Create a fictional 15-minute usage history; does not connect to hardware."""
import argparse
from mosquito.usage_history import generate_history


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="history/mosquito_usage_15min.csv",
                        help="New CSV file; existing history is never overwritten")
    args = parser.parse_args()
    result = generate_history(args.output)
    print(f"Synthetic history saved to {args.output}")
    print(f"{result['good_seconds']}s good, {result['missing_seconds']}s missing; "
          f"{result['option_selections']} selection, {result['registered_clicks']} simulated click")
