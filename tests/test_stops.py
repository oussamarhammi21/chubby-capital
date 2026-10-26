from datetime import datetime

import pandas as pd
import pytest

from chubby.bots.chubbies import JalapenoV2, Muffin
from chubby.broker import Account, PaperBroker, Position
from chubby.config import ArenaConfig
from chubby.data import FrameData
from chubby.live import TORONTO, pulse
from tests.test_arena import run_arena

DAY = pd.Timestamp("2024-03-01")


def market(open_, low, high, close=100.0) -> FrameData:
    idx = pd.DatetimeIndex([DAY])
    frame = lambda o, lo, hi, c: pd.DataFrame({"Open": [o], "High": [hi], "Low": [lo], "Close": [c], "Dividends": [0.0]}, index=idx)  # noqa: E731
    return FrameData({"RY.TO": frame(open_, low, high, close), "XIC.TO": frame(30, 30, 30, 30)},
                     pd.Series([1.4], index=idx), "XIC.TO")


def holding(qty=10, cost=100.0):
    return Account("muffin", 0.0, {"RY.TO": Position("RY.TO", qty, cost, "2024-02-01")})


def test_stop_sells_at_the_stop_price_during_the_session():
    broker = PaperBroker(ArenaConfig(slippage_bps=0), market(open_=99, low=90, high=101))
    acct = holding()
    fill, px = broker.stop_out(acct, "RY.TO", 92.0, "below", DAY)
    assert px == 92.0 and fill.value_cad == pytest.approx(920)
    assert "RY.TO" not in acct.positions


def test_stop_after_a_gap_sells_at_the_open():
    broker = PaperBroker(ArenaConfig(slippage_bps=0), market(open_=85, low=84, high=88))
    fill, px = broker.stop_out(holding(), "RY.TO", 92.0, "below", DAY)
    assert px == 85.0  # le titre a ouvert sous le stop : on ne peut pas vendre à 92


def test_stop_not_reached_does_nothing():
    broker = PaperBroker(ArenaConfig(), market(open_=99, low=93, high=101))
    acct = holding()
    assert broker.stop_out(acct, "RY.TO", 92.0, "below", DAY) is None
    assert acct.positions["RY.TO"].qty == 10


def test_bots_place_stops_from_their_cost_and_settings():
    acct = holding(cost=140.0)  # 100 $ US à 1,40
    levels = Muffin().stops(acct.positions, {"stop": 0.10}, lambda t: 1.4)
    assert levels == {"RY.TO": (90.0, "below")}
    short = {"OPT:XLF:P:40:2024-04-19": Position("OPT:XLF:P:40:2024-04-19", -2, 0.5 * 100 * 1.4, "2024-03-01")}
    assert JalapenoV2().stops(short, {"stop": 3.0}, lambda t: 1.4) == {"OPT:XLF:P:40:2024-04-19": (1.5, "above")}


def test_season_triggers_stops_and_writes_them_down():
    arena, _ = run_arena(400)
    stops = arena.store.db.execute("SELECT bot_id, payload FROM events WHERE kind = 'stop'").fetchall()
    assert stops and {r["bot_id"] for r in stops} <= {"muffin", "jalapeno", "jalapeno-v2"}
    assert arena.store.db.execute("SELECT COUNT(*) FROM journal WHERE text LIKE 'Mon ordre stop%'").fetchone()[0] == len(stops)


def test_live_shows_a_stop_being_hit():
    arena, _ = run_arena(400)
    acct = arena.store.load_account("muffin")
    stock = next((t for t, p in acct.positions.items() if p.qty > 0), None)
    if stock is None:
        pytest.skip("Muffin n'a rien en portefeuille à la fin de cette saison simulée")
    last = arena.last_day
    today = last + pd.offsets.BDay(1)
    close = arena.data.price(stock, last)
    stamps = [pd.Timestamp(f"{d.date()} {h}", tz=TORONTO) for d in (last, today) for h in ("10:00", "11:00", "12:00")]
    bars = pd.DataFrame({stock: [close] * 3 + [close, close * 0.5, close * 0.6], "CAD=X": [1.35] * 6}, index=pd.DatetimeIndex(stamps))
    live = pulse(arena.store, fetch=lambda tickers: bars, now=datetime(today.year, today.month, today.day, 12, 5, tzinfo=TORONTO))
    muffin = next(e for e in live["bots"] if e["id"] == "muffin")
    hit = [x for x in muffin["stops"] if x["ticker"] == stock]
    assert hit and hit[0]["hit"] and "stop" in muffin["reaction"]
