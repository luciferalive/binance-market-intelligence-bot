from marketbot.alerts import AlertEngine, Notifier, evaluate
from marketbot.store import Store


class Clock:
    t = 1_700_000_000.0

    def __call__(self):
        return self.t


def make(env=None, status=200):
    sent = []

    def poster(url, payload, timeout):
        sent.append((url, payload))
        return status

    clock = Clock()
    store = Store(":memory:", clock=clock)
    return AlertEngine(store, Notifier(env or {}, poster), clock), sent, clock, store


def test_dedupe_and_cooldown():
    eng, _, clock, _ = make()
    assert eng.emit("high", "k", "t", key="a") is not None
    assert eng.emit("high", "k", "t", key="a") is None
    clock.t += 3600
    assert eng.emit("high", "k", "t", key="a") is not None


def test_mute_does_not_silence_critical():
    eng, _, _, store = make()
    store.set_setting("alert_muted_kinds", ["x"])
    assert eng.emit("info", "x", "t") is None
    assert eng.emit("critical", "x", "t") is not None


def test_telegram_level_floor_and_secret_not_stored():
    env = {"TELEGRAM_BOT_TOKEN": "SECRETTOKEN", "TELEGRAM_CHAT_ID": "42"}
    eng, sent, _, store = make(env)
    eng.emit("info", "a", "quiet")
    assert sent == []
    eng.emit("high", "b", "loud")
    assert len(sent) == 1 and "SECRETTOKEN" in sent[0][0]
    dump = str(store.query("SELECT * FROM alerts")) + str(eng.channels())
    assert "SECRETTOKEN" not in dump


def test_webhook_must_be_https_and_failure_is_reported():
    assert Notifier({"ALERT_WEBHOOK_URL": "http://x"}).channels()["webhook"] is False
    eng, _sent, _, _ = make({"ALERT_WEBHOOK_URL": "https://h.example/secret"}, status=500)
    res = eng.test()
    assert res["ok"] is False and "secret" not in str(res)


def test_unconfigured_test_message():
    eng, _, _, _ = make()
    assert eng.test()["ok"] is False


def item(key="solana:p", risk="LOW", label="WATCH", conf="HIGH", liq=100_000):
    return {"key": key, "label": label, "opportunity": 70, "confidence": {"level": conf},
            "pool": {"symbol": "T", "liquidity_usd": liq},
            "risk": {"level": risk, "flags": [{"message": "mint authority"}]}}


def test_evaluate_rules():
    eng, _, _, _ = make()
    assert evaluate(eng, scored=[item(risk="CRITICAL")], watch_keys={"solana:p"}, held={}) == 1
    assert evaluate(eng, scored=[item(risk="CRITICAL")], watch_keys=set(), held={}) == 0
    held = {"solana:p": {"last_liquidity": 200_000}}
    assert evaluate(eng, scored=[item(liq=100_000)], watch_keys=set(), held=held) == 1
    assert evaluate(eng, scored=[item(key="solana:z", label="CANDIDATE")], watch_keys=set(), held={}) == 1
