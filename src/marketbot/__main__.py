"""Command-line interface for Binance Spot research and paper trading."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import pandas as pd

from .scanner import scan_markets


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="marketbot",
        description="Binance Spot research tools and automatic virtual paper trading.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="Rank markets and generate research signals")
    scan.add_argument("--quote", default="USDT")
    scan.add_argument("--timeframe", default="1h")
    scan.add_argument("--top", type=int, default=80)
    scan.add_argument("--limit", type=int, default=160)
    scan.add_argument("--output", default="reports/signals.csv")
    scan.add_argument("--json", action="store_true")

    backtest = subparsers.add_parser("backtest", help="Backtest strategy on a candle CSV")
    backtest.add_argument("input", help="CSV with open, high, low, close, volume columns")
    backtest.add_argument("--symbol", default="TEST/USDT")
    backtest.add_argument("--timeframe", default="1h")
    backtest.add_argument("--cash", type=float, default=10_000.0)
    backtest.add_argument("--risk", type=float, default=0.01)
    backtest.add_argument("--fee", type=float, default=0.001)
    backtest.add_argument("--output", default="reports/backtest.json")

    paper = subparsers.add_parser("paper", help="Automatically trade a persistent virtual Spot account")
    paper.add_argument("--cash", type=float, default=1000.0, help="Initial virtual quote balance")
    paper.add_argument("--quote", default="USDT")
    paper.add_argument("--timeframe", default="1h")
    paper.add_argument("--top", type=int, default=50, help="Highest-volume markets analyzed per cycle")
    paper.add_argument("--interval", type=int, default=300, help="Seconds between scan cycles (minimum 30)")
    paper.add_argument("--fee", type=float, default=0.001, help="Simulated fee per side")
    paper.add_argument("--db", default="data/paper_account.sqlite3", help="SQLite account database path")

    status = subparsers.add_parser("paper-status", help="Show virtual balance, positions and recent trades")
    status.add_argument("--db", default="data/paper_account.sqlite3")

    return parser


def run_scan(args: argparse.Namespace) -> None:
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
    print(f"\\nWrote {len(rows)} rows to {output}")


def run_backtest(args: argparse.Namespace) -> None:
    from .backtest import run_backtest
    frame = pd.read_csv(args.input)
    result = run_backtest(
        frame, symbol=args.symbol, timeframe=args.timeframe,
        starting_cash=args.cash, risk_fraction=args.risk, fee_rate=args.fee,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result.to_dict(), indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({
        "ending_cash": result.ending_cash, "net_return_pct": result.net_return_pct,
        "max_drawdown_pct": result.max_drawdown_pct, "trades": len(result.trades),
        "win_rate_pct": result.win_rate_pct, "profit_factor": result.profit_factor,
        "fees_paid": result.fees_paid, "report": str(output),
    }, indent=2))


def run_paper(args: argparse.Namespace) -> None:
    from .paper import PaperTrader
    trader = PaperTrader(
        db_path=args.db, starting_cash=args.cash, quote=args.quote,
        timeframe=args.timeframe, top=args.top, scan_interval=args.interval,
        fee_rate=args.fee,
    )
    trader.run_forever()


def run_paper_status(args: argparse.Namespace) -> None:
    from .paper import PaperTrader
    # status does not reset an existing account; initialization is ignored if DB exists.
    trader = PaperTrader(db_path=args.db, starting_cash=1000.0)
    print(json.dumps(trader.status(), indent=2))


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "scan":
        run_scan(args)
    elif args.command == "backtest":
        run_backtest(args)
    elif args.command == "paper":
        run_paper(args)
    elif args.command == "paper-status":
        run_paper_status(args)


if __name__ == "__main__":
    main()
