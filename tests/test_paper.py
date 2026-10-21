from marketbot.paper import PaperTrader


def test_paper_account_starts_with_virtual_cash_and_persists(tmp_path):
    db = tmp_path / "paper.sqlite3"
    trader = PaperTrader(db_path=str(db), starting_cash=1000, scan_interval=60)
    first = trader.status()
    assert first["mode"] == "PAPER_ONLY"
    assert first["initial_virtual_usdt"] == 1000
    assert first["cash_usdt"] == 1000
    assert first["open_positions"] == []

    # Reopening with a different requested seed must not reset existing account.
    reopened = PaperTrader(db_path=str(db), starting_cash=5000, scan_interval=60)
    second = reopened.status()
    assert second["initial_virtual_usdt"] == 1000
    assert second["cash_usdt"] == 1000
