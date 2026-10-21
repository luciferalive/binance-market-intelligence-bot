"""Command-line interface for the Binance Market Intelligence Bot."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from .scanner import scan_markets


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="marketbot",
        description="Scan Binance Spot markets using public market data (no API keys required).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    scan = subparsers.add_parser("scan", help="Rank markets and generate research signals")
    scan.add_argument("--quote", default="USDT", help="Quote asset, default: USDT")
    scan.add_argument("--timeframe", default="1h", help="CCXT timeframe, e.g. 15m, 1h, 4h")
    scan.add_argument("--top", type=int, default=80, help="Number of highest-volume pairs to analyze")
    scan.add_argument("--limit", type=int, default=160, help="Candles requested per pair (max varies by exchange)")
    scan.add_argument("--output", default="reports/signals.csv", help="CSV output path")
    scan.add_argument("--json", action="store_true", help="Print JSON rather than a compact table")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.top < 1 or args.limit < 65:
        raise SystemExit("--top must be >= 1 and --limit must be >= 65")
    rows = scan_markets(quote=args.quote.upper(), timeframe=args.timeframe, top=args.top, limit=args.limit)
    if not rows:
        print("No market results. Check connectivity, quote asset, timeframe, and Binance API status.")
        return

    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print(f"{'SYMBOL':<18} {'SIGNAL':<12} {'SCORE':>5} {'CLOSE':>14} {'STOP':>14} {'TARGET':>14}")
        for row in rows:
            print(
                f"{row.get('symbol', '?'):<18} {row.get('signal', '?'):<12} "
                f"{row.get('score', 0):>5} {row.get('close', 0):>14.8g} "
                f"{row.get('stop_loss', 0):>14.8g} {row.get('target', 0):>14.8g}"
            )
            print(f"  {row.get('reasons', '')}")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({key for row in rows for key in row.keys()})
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nWrote {len(rows)} rows to {output}")


if __name__ == "__main__":
    main()
