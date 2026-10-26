from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from chubby import options as opt
from chubby.advisor import advise, parse_holdings
from chubby.chains import YahooChains
from chubby.config import ArenaConfig
from chubby.live import TORONTO, Refresher, market_open, pulse, react
from chubby.options import OptionData
from chubby.store import Store
from tests.fakes import fake_market
from tests.test_arena import run_arena


@pytest.fixture(scope="module")
def arena():
    return run_arena(320)[0]


def fake_bars(arena, move=0.01):
    """Deux séances de barres de 5 minutes : la veille au prix de clôture de l'arène, puis une hausse de `move`."""
    last = arena.last_day
    today = last + pd.offsets.BDay(1)
    tickers = {t for b in arena.bots for t in arena.store.load_account(b.id).positions if not opt.is_option(t)}
    tickers |= {"SPY", "XIC.TO", "QQQ"}
    stamps = [pd.Timestamp(f"{d.date()} {h:02d}:{m:02d}", tz=TORONTO)
              for d in (last, today) for h in range(9, 16) for m in range(0, 60, 5) if (h, m) >= (9, 30)]
    n = len(stamps) // 2
    cols = {}
    for t in tickers:
        close = arena.data.price(t, last) or 50.0
        cols[t] = [close] * n + list(close * (1 + np.linspace(0, move, n)))
    cols["CAD=X"] = [1.35] * (2 * n)
    return pd.DataFrame(cols, index=pd.DatetimeIndex(stamps)), today


def test_pulse_follows_every_bot_during_the_session(arena):
    bars, today = fake_bars(arena, move=0.02)
    now = datetime(today.year, today.month, today.day, 11, 0, tzinfo=TORONTO)
    live = pulse(arena.store, fetch=lambda tickers: bars, now=now)
    assert live["ok"] and live["open"] and live["day"] == str(today.date())
    assert {e["id"] for e in live["bots"]} == {b.id for b in arena.bots}
    for e in live["bots"]:
        assert len(e["curve"]) == len(live["times"]) and e["reaction"]
        acct = arena.store.load_account(e["id"])
        if e["equity"] - acct.cash > 1000 and not any(opt.is_option(t) for t in acct.positions):
            assert e["change"] > 0 and e["mood"] in ("content", "calme")
    assert {i["name"] for i in live["indexes"]} == {"TSX", "S&P 500", "Nasdaq"}
    assert arena.store.get_meta("live")  # gardé pour le tableau de bord


def test_pulse_without_network_says_so(arena):
    live = pulse(arena.store, fetch=lambda tickers: pd.DataFrame())
    assert not live["ok"] and "Yahoo" in live["error"]


def test_reactions_follow_personality_and_stay_stable():
    now = datetime(2026, 10, 26, 11, 2, tzinfo=TORONTO)
    mover = {"name": "XLF", "change": -0.031}
    text = react("jalapeno-v2", -0.02, mover, now)
    assert text == react("jalapeno-v2", -0.02, mover, now.replace(minute=20))  # même demi-heure
    assert "Claude" in react("mochi-ia", 0.03, mover, now)
    assert react("tortue", 0.0, None, now).startswith("Je suis au cash")


def test_market_hours():
    assert market_open(datetime(2026, 10, 26, 10, 0, tzinfo=TORONTO))
    assert not market_open(datetime(2026, 10, 26, 16, 30, tzinfo=TORONTO))
    assert not market_open(datetime(2026, 10, 25, 11, 0, tzinfo=TORONTO))  # dimanche


def test_refresher_waits_between_pulses(arena):
    bars, today = fake_bars(arena)
    calls = []
    clock = [datetime(today.year, today.month, today.day, 10, 0, tzinfo=TORONTO)]
    r = Refresher(":memory:", every=240, fetch=lambda t: calls.append(1) or bars, clock=lambda: clock[0])
    r(arena.store)
    r(arena.store)
    assert len(calls) == 1
    clock[0] = clock[0].replace(minute=5)
    r(arena.store)
    assert len(calls) == 2


def fake_chains(today):
    expiries = [pd.Timestamp("2026-11-20"), pd.Timestamp("2026-12-18")]

    def table(underlying, expiry):
        strikes = np.arange(40, 61, 2.5)
        rows = [{"strike": k, "bid": 1.0, "ask": 1.2, "lastPrice": 1.1, "kind": kind} for k in strikes for kind in "CP"]
        return pd.DataFrame(rows)

    return YahooChains(today, fetch_expiries=lambda u: [str(e.date()) for e in expiries], fetch_table=table)


def test_live_options_use_listed_contracts_and_real_prices():
    data = fake_market(300)
    today = data.trading_days()[-1]
    chains = fake_chains(today)
    od = OptionData(data, chains)
    spot = data.price("XLF", today)
    sym = od.contract("XLF", "P", today, 0.05, 35, ref=50.0)  # vise 47,5
    _, _, strike, expiry = opt.parse(sym)
    assert strike == 47.5 and expiry in (pd.Timestamp("2026-11-20"), pd.Timestamp("2026-12-18"))
    if (expiry - today).days >= 14:
        assert od.price(sym, today) == 1.1 and od.source(sym, today) == "réel"
    assert od.price(sym, data.trading_days()[-2]) != 1.1 or spot is None  # la veille : le modèle
    store = Store(":memory:")
    store.save_option_quotes(today, chains.used)
    assert store.real_option_quotes(today) == chains.used


def test_chain_quote_prefers_mid_price():
    from chubby.chains import quote_of
    assert quote_of({"bid": 1.0, "ask": 1.4, "lastPrice": 3}) == 1.2
    assert quote_of({"bid": 0, "ask": 0, "lastPrice": 0.85}) == 0.85
    assert quote_of({"bid": float("nan"), "ask": None, "lastPrice": 0}) is None


def test_advisor_compares_styles_without_calling_everything_a_sale(arena):
    csv = "Symbol,Quantity,Market Value,Market Value Currency\nGSBD,100,1300,USD\nLYB,25,1700,USD\nBKCL.TO,155,4161,CAD\n"
    holdings, _ = parse_holdings(csv, 1.4)
    out = advise(arena.store, ArenaConfig(), {"risk": 3, "style": "equilibre", "horizon": "long"}, holdings)
    labels = {r["key"] for r in out["style"]}
    assert {"positions", "largest", "us", "broad"} <= labels
    mine = {r["key"]: r["mine"] for r in out["style"]}
    assert mine["positions"] == 3 and mine["broad"] == 0
    actions = {r["symbol"]: r["action"] for r in out["rows"]}
    assert actions["GSBD"] == "hors de son style"
    assert "réduire" not in {actions[s] for s in ("GSBD", "LYB", "BKCL.TO")}


def test_thresholds_turn_into_real_prices():
    from chubby import indicators as ind
    closes = np.cumsum(np.random.default_rng(1).normal(0, 1, 60)) + 100
    level = ind.rsi_level(closes, 30)
    assert ind.rsi(np.append(closes, level)) == pytest.approx(30, abs=1e-6)
    exit_level = ind.cross_level(closes, 50, 0.97)
    assert exit_level == pytest.approx(0.97 * ind.sma(np.append(closes, exit_level), 50))


def test_live_shows_how_close_each_bot_is_to_acting(arena):
    bars, today = fake_bars(arena, move=0.0)
    w = arena.store.get_watchlist("mochi")
    target = next(i for i in w["items"] if i.get("trigger"))
    t, level = target["ticker"], target["trigger"]["level"]
    n = len(bars) // 2
    bars.loc[bars.index[n:], t] = level * 0.99  # passe sous son seuil de sortie pendant la séance
    live = pulse(arena.store, fetch=lambda tickers: bars, now=datetime(today.year, today.month, today.day, 11, 0, tzinfo=TORONTO))
    mochi = next(e for e in live["bots"] if e["id"] == "mochi")
    hit = next(x for x in mochi["radar"] if x["ticker"] == t)
    assert hit["crossed"] and t in mochi["reaction"] and "ce soir" in mochi["reaction"]
    assert mochi["thoughts"] and live["market"]


def test_first_day_of_the_arena_is_live_too():
    from chubby.arena import Arena
    from chubby.bots import all_bots
    data = fake_market(300)
    store = Store(":memory:")
    arena = Arena(store, data, all_bots())
    arena.run(start=data.trading_days()[-1])  # une seule séance : tout le monde au cash, ordres en attente
    bars, today = fake_bars(arena)
    live = pulse(store, fetch=lambda tickers: bars, now=datetime(today.year, today.month, today.day, 11, 0, tzinfo=TORONTO))
    assert len(live["bots"]) == len(arena.bots)  # avant, personne n'apparaissait le premier jour
    assert all(e["equity"] == pytest.approx(10_000, rel=0.01) for e in live["bots"])
