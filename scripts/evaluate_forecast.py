"""Reproduce the one-month rolling-origin forecast comparison from a CSV export."""
import argparse
import csv
import json
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analytics import monthly_spending_forecast


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_file", help="CSV containing date and signed amount columns")
    args = parser.parse_args()
    rows = []
    with open(args.csv_file, newline="", encoding="utf-8-sig") as source:
        for row in csv.DictReader(source):
            try:
                rows.append((date.fromisoformat(row["date"][:10]), float(row["amount"])))
            except (KeyError, TypeError, ValueError):
                continue
    print(json.dumps(monthly_spending_forecast(rows), indent=2))


if __name__ == "__main__":
    main()
