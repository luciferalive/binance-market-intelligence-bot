"""Command-line interface for Binance Spot research and paper trading."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import pandas as pd


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

    subparsers.add_parser("demo-balance", help="Read balances from Binance Spot Demo (sandbox only)")

    demo_order = subparsers.add_parser("demo-order", help="Place a manual order on Binance Spot Demo (sandbox only)")
    demo_order.add_argument("symbol", help="CCXT market symbol, for example BTC/USDT")
    demo_order.add_argument("side", choices=["buy", "sell"])
    demo_order.add_argument("amount", type=float, help="Base-asset quantity, not quote-currency amount")

    demo_auto = subparsers.add_parser(
        "demo-auto",
        help="Automatically scan and trade through the connected Binance Spot Demo account",
    )
    demo_auto.add_argument("--quote", default="USDT")
    demo_auto.add_argument("--timeframe", default="1h")
    demo_auto.add_argument("--top", type=int, default=50)
    demo_auto.add_argument("--interval", type=int, default=300)
    demo_auto.add_argument(
        "--max-allocation", type=float, default=1000.0,
        help="Strategy entry-notional cap in quote currency; not a separate wallet",
    )
    demo_auto.add_argument("--max-positions", type=int, default=5)
    demo_auto.add_argument("--db", default="data/binance_demo_trader.sqlite3")

    dash = subparsers.add_parser(
        "dashboard",
        help="Open the local web dashboard (portfolio, meme coins, pump/dump watch)",
    )
    dash.add_argument("--host", default="127.0.0.1", help="127.0.0.1 or localhost only")
    dash.add_argument("--port", type=int, default=8765)
    dash.add_argument(
        "--i-verified-key-is-read-only", action="store_true",
        help="Continue even if Binance cannot confirm the key's permissions",
    )

    memes = subparsers.add_parser("memes", help="Trending meme coins with rug pull risk checks")
    memes.add_argument("--top", type=int, default=15)
    memes.add_argument("--json", action="store_true")

    check = subparsers.add_parser("check", help="Rug pull check for one token (name, ticker or address)")
    check.add_argument("query")
    check.add_argument("--json", action="store_true")

    pumps = subparsers.add_parser("pumps", help="Pump and dump watch on Binance Spot USDT markets")
    pumps.add_argument("--all", action="store_true", help="Include quiet markets")
    pumps.add_argument("--memes-only", action="store_true")
    pumps.add_argument("--json", action="store_true")

    news_cmd = subparsers.add_parser("news", help="Recent headlines and catalysts for a coin")
    news_cmd.add_argument("query")
    news_cmd.add_argument("--json", action="store_true")

    sub_portfolio = subparsers.add_parser("portfolio", help="Print your Binance Spot portfolio (read-only key)")
    sub_portfolio.add_argument("--no-pnl", action="store_true", help="Skip trade-history PnL (faster)")
    sub_portfolio.add_argument("--json", action="store_true")
    sub_portfolio.add_argument("--i-verified-key-is-read-only", action="store_true")

    return parser


def run_dashboard(args: argparse.Namespace) -> None:
    from .dashboard import serve
    serve(args.host, args.port, allow_unverified_key=args.i_verified_key_is_read_only)


def run_memes(args: argparse.Namespace) -> None:
    from . import memecoins, rugcheck
    tokens, warnings = memecoins.fetch_dex_candidates()
    tokens = tokens[: max(1, args.top)]
    reports, more = rugcheck.check_tokens(tokens)
    rows = []
    for token in tokens:
        key = token.address if token.chain == "solana" else token.address.lower()
        report = reports[(token.chain, key)]
        row = token.to_dict()
        row["risk"] = report.to_dict()
        row["verdict"] = rugcheck.verdict(token.heat_score, report.level)
        rows.append(row)
    for message in warnings + more:
        print(f"warning: {message}")
    if args.json:
        print(json.dumps(rows, indent=2))
        return
    if not rows:
        print("No tokens passed the liquidity and volume filters. Try again in a few minutes.")
        return
    print(f"{'TOKEN':<14} {'VERDICT':<10} {'HEAT':>4} {'RISK':<11} {'LIQUIDITY':>12} {'1H':>8} {'24H':>8}  CHAIN")
    for row in rows:
        print(
            f"{row['symbol']:<14} {row['verdict']:<10} {row['heat_score']:>4} {row['risk']['level']:<11} "
            f"{row['liquidity_usd'] or 0:>12,.0f} {row['change_1h'] or 0:>7.1f}% {row['change_24h'] or 0:>7.0f}%  {row['chain']}"
        )
        for flag in row["risk"]["flags"][:3]:
            print(f"    [{flag['severity']}] {flag['message']}")
    print("\nHeat is attention only. Risk checks cannot prove a token is safe.")


def run_check(args: argparse.Namespace) -> None:
    from . import memecoins, rugcheck
    tokens = memecoins.search_tokens(args.query, limit=3)
    if not tokens:
        print("No trading pair found. Check the spelling or paste the contract address.")
        return
    reports, warnings = rugcheck.check_tokens(tokens)
    out = []
    for token in tokens:
        key = token.address if token.chain == "solana" else token.address.lower()
        report = reports[(token.chain, key)]
        out.append({"token": token.to_dict(), "risk": report.to_dict(),
                    "verdict": rugcheck.verdict(token.heat_score, report.level)})
    for message in warnings:
        print(f"warning: {message}")
    if args.json:
        print(json.dumps(out, indent=2))
        return
    for item in out:
        token, report = item["token"], item["risk"]
        print(f"\n{token['symbol']} ({token['name']}) on {token['chain']}: {report['level']} risk, "
              f"score {report['score']}/100, verdict {item['verdict']}")
        print(f"  {report['summary']}")
        for flag in report["flags"]:
            print(f"  [{flag['severity']}] {flag['message']}")


def run_pumps(args: argparse.Namespace) -> None:
    from . import pumpwatch
    rows, warnings = pumpwatch.scan_pumps()
    for message in warnings:
        print(f"warning: {message}")
    rows = [r for r in rows if (args.all or r.label != "QUIET") and (not args.memes_only or r.is_meme)]
    if args.json:
        print(json.dumps([r.to_dict() for r in rows], indent=2))
        return
    if not rows:
        print("Nothing unusual right now.")
        return
    print(f"{'MARKET':<12} {'SIGNAL':<14} {'RELVOL':>6} {'1H':>7} {'4H':>7} {'24H':>7} {'BUY%':>5} {'RSI':>4}")
    for r in rows:
        print(f"{r.base:<12} {r.label:<14} {r.rel_volume:>5.1f}x {r.ret_1h_pct:>6.1f}% {r.ret_4h_pct:>6.1f}% "
              f"{r.change_24h_pct:>6.1f}% {r.taker_buy_ratio * 100:>4.0f}% {r.rsi:>4.0f}")
        print(f"    {'; '.join(r.reasons)}")


def run_news(args: argparse.Namespace) -> None:
    from . import news
    headlines = news.fetch_headlines(args.query)
    if args.json:
        print(json.dumps({"headlines": [h.to_dict() for h in headlines],
                          "summary": news.summarize(headlines)}, indent=2))
        return
    if not headlines:
        print("No headlines in the last 7 days.")
        return
    print(f"Tone: {news.summarize(headlines)['tone']}")
    for h in headlines:
        tags = f" [{', '.join(h.tags)}]" if h.tags else ""
        print(f"- {h.title}{tags}\n    {h.source}  {h.link}")


def run_portfolio(args: argparse.Namespace) -> None:
    from . import portfolio, settings
    settings.load_dotenv(".env")
    creds = settings.binance_credentials()
    if creds is None:
        raise SystemExit(
            "Set BINANCE_API_KEY and BINANCE_API_SECRET (a read-only key) in .env or your environment."
        )
    client = portfolio.BinanceReadOnlyClient(*creds)
    try:
        snap = portfolio.build_snapshot(
            client, with_pnl=not args.no_pnl, allow_unverified_key=args.i_verified_key_is_read_only
        )
    except portfolio.UnsafeKeyError as exc:
        raise SystemExit(str(exc)) from None
    if args.json:
        print(json.dumps(snap, indent=2, default=str))
        return
    for message in snap["warnings"] + snap["flags"]:
        print(f"note: {message}")
    print(f"\nTotal ${snap['total_usd']:,.2f}   24h {snap['change_24h_usd']:+,.2f}   "
          f"stablecoins {snap['stable_pct']:.0f}%")
    print(f"{'ASSET':<10} {'VALUE':>12} {'SHARE':>6} {'24H':>8} {'UNREALIZED':>12}")
    for row in snap["holdings"]:
        change = row["change_24h_pct"]
        pnl = row.get("unrealized_pnl")
        print(f"{row['asset']:<10} {row['value_usd']:>12,.2f} {row['weight_pct']:>5.1f}% "
              f"{'n/a' if change is None else f'{change:+.1f}%':>8} "
              f"{'n/a' if pnl is None else f'{pnl:+,.2f}':>12}")


def run_scan(args: argparse.Namespace) -> None:
    from .scanner import scan_markets
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
    fields = sorted({key for row in rows for key in row})
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


def run_demo_balance() -> None:
    from .binance_demo import BinanceSpotDemo
    print(json.dumps(BinanceSpotDemo().account_balance(), indent=2))


def run_demo_order(args: argparse.Namespace) -> None:
    from .binance_demo import BinanceSpotDemo
    result = BinanceSpotDemo().market_order(args.symbol, args.side, args.amount)
    print(json.dumps(result, indent=2))


def run_demo_auto(args: argparse.Namespace) -> None:
    from .demo_trader import BinanceDemoTrader
    trader = BinanceDemoTrader(
        db_path=args.db,
        quote=args.quote,
        timeframe=args.timeframe,
        top=args.top,
        interval=args.interval,
        max_allocation=args.max_allocation,
        max_positions=args.max_positions,
    )
    trader.run_forever()


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
    elif args.command == "demo-balance":
        run_demo_balance()
    elif args.command == "demo-order":
        run_demo_order(args)
    elif args.command == "demo-auto":
        run_demo_auto(args)
    elif args.command == "dashboard":
        run_dashboard(args)
    elif args.command == "memes":
        run_memes(args)
    elif args.command == "check":
        run_check(args)
    elif args.command == "pumps":
        run_pumps(args)
    elif args.command == "news":
        run_news(args)
    elif args.command == "portfolio":
        run_portfolio(args)


if __name__ == "__main__":
    main()
