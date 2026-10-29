"""Les Chubbies tradent pendant la séance : ouverture, confirmation, stops, et ce que le direct en montre."""

import json
from datetime import datetime

import pandas as pd
import pytest

from chubby.arena import Arena
from chubby.bots import Bot, BotContext, Decision, all_bots
from chubby.bots.chubbies import Muffin, Tortue
from chubby.intraday import SessionTrader, overlay, trades_today
from chubby.live import TORONTO, pulse
from chubby.store import Store
from tests.fakes import fake_market


class Gourmand(Bot):
    """Achète NVDA dès qu'il perd 3 % dans la journée ; stop à 5 % sous son prix d'achat."""

    id, name, personality, universe = "gourmand", "Gourmand", "Teste la séance", ["NVDA"]
    usd_account = False

    def decide(self, ctx: BotContext) -> Decision:
        closes = ctx.closes("NVDA", 2)
        if "NVDA" not in ctx.account.positions and closes.iloc[-1] < 0.97 * closes.iloc[-2]:
            return Decision({"NVDA": 0.5}, ["NVDA est en solde aujourd'hui."])
        if "NVDA" in ctx.account.positions and closes.iloc[-1] > 1.0 * closes.iloc[-2]:
            return Decision({}, ["Je revends NVDA."])
        return Decision(None)

    def stops(self, positions, params, fx):
        return {t: (p.avg_cost_cad * 0.95 / fx(t), "below") for t, p in positions.items()}


def session(data, day, path: dict[str, list[float]], until: str) -> pd.DataFrame:
    """Barres de 5 minutes du jour jusqu'à `until` : chaque titre à sa clôture de la veille, sauf ceux de `path`
    (variation par rapport à la veille, une valeur par barre, la dernière répétée)."""
    stamps = [pd.Timestamp(f"{day.date()} {h:02d}:{m:02d}", tz=TORONTO) for h in range(9, 16) for m in range(0, 60, 5)
              if (h, m) >= (9, 30) and f"{h:02d}:{m:02d}" <= until]
    prev = data.trading_days(end=day - pd.Timedelta(days=1))[-1]
    cols = {}
    for t in data.frames:
        base = data.price(t, prev)
        moves = path.get(t, [0.0])
        cols[t] = [base * (1 + moves[min(i, len(moves) - 1)]) for i in range(len(stamps))]
    cols["CAD=X"] = [1.35] * len(stamps)
    return pd.DataFrame(cols, index=pd.DatetimeIndex(stamps))


def at(day, hhmm: str) -> datetime:
    return datetime(day.year, day.month, day.day, int(hhmm[:2]), int(hhmm[3:]), tzinfo=TORONTO)


@pytest.fixture()
def setup():
    data = fake_market(300)
    days = data.trading_days()
    store = Store(":memory:")
    bots = [Tortue(), Gourmand()]
    Arena(store, data, bots).run(days[-3], days[-2])  # l'arène a joué jusqu'à hier soir
    trader = SessionTrader(":memory:", Arena(store, data, bots).cfg, bots, load_daily=lambda today: data)
    return data, days[-1], store, trader


def test_last_nights_orders_fill_at_the_open_right_away(setup):
    data, today, store, trader = setup
    store.set_pending("tortue", {"XIC.TO": 0.49, "VFV.TO": 0.49}, today - pd.Timedelta(days=1))
    store.save_account(store.load_account("tortue").__class__("tortue", 10_000.0))
    bars = session(data, today, {"VFV.TO": [0.0, 0.02]}, until="10:00")
    trader.step(store, at(today, "10:00"), bars=bars)
    rows = store.db.execute("SELECT ticker, price, time FROM trades WHERE bot_id = 'tortue' AND day = ?",
                            (str(today.date()),)).fetchall()
    assert {r["ticker"] for r in rows} == {"XIC.TO", "VFV.TO"} and {r["time"] for r in rows} == {"09:30"}
    vfv = next(r for r in rows if r["ticker"] == "VFV.TO")
    assert vfv["price"] == pytest.approx(bars["VFV.TO"].iloc[0] * 1.0005)  # le premier cours, pas celui de 10 h
    assert store.pop_pending("tortue") == (None, [])  # plus rien en attente pour ce soir


def test_an_opportunity_is_confirmed_then_traded_once(setup):
    data, today, store, trader = setup
    dip = {"NVDA": [0.0] * 6 + [-0.05]}  # NVDA perd 5 % à partir de 10 h
    trader.step(store, at(today, "10:00"), bars=session(data, today, dip, "10:00"))
    assert not trades_today(store, "gourmand", str(today.date()))
    seen = store.db.execute("SELECT payload FROM events WHERE kind = 'analyse' AND bot_id = 'gourmand'").fetchall()
    assert any("NVDA" in r["payload"] and "confirme" in r["payload"] for r in seen)

    trader.step(store, at(today, "10:15"), bars=session(data, today, dip, "10:15"))
    done = trades_today(store, "gourmand", str(today.date()))
    assert [(d["side"], d["ticker"], d["time"]) for d in done] == [("buy", "NVDA", "10:15")]
    assert "solde" in done[0]["why"]
    journal = store.db.execute("SELECT text FROM journal WHERE bot_id = 'gourmand'").fetchall()
    assert any(r["text"].startswith("10:15 · ") for r in journal)

    # Le rebond arrive dans l'après-midi : pas d'aller-retour sur le même titre dans la journée.
    rebound = {"NVDA": [0.0] * 6 + [-0.05] * 6 + [0.02]}
    for hhmm in ("10:30", "10:45"):
        trader.step(store, at(today, hhmm), bars=session(data, today, rebound, hhmm))
    assert len(trades_today(store, "gourmand", str(today.date()))) == 1


def test_a_passing_dip_is_not_traded(setup):
    data, today, store, trader = setup
    trader.step(store, at(today, "10:00"), bars=session(data, today, {"NVDA": [0.0] * 6 + [-0.05]}, "10:00"))
    trader.step(store, at(today, "10:15"), bars=session(data, today, {"NVDA": [0.0] * 6 + [-0.05, 0.0]}, "10:15"))
    trader.step(store, at(today, "10:30"), bars=session(data, today, {"NVDA": [0.0] * 6 + [-0.05, 0.0]}, "10:30"))
    assert not trades_today(store, "gourmand", str(today.date()))  # le creux n'a pas tenu : il n'a pas acheté


def test_stops_fire_during_the_session_after_the_purchase(setup):
    data, today, store, trader = setup
    path = {"NVDA": [0.0] * 6 + [-0.05] * 7 + [-0.12]}  # achète à 10 h 15 (-5 %), puis chute à -12 % à 10 h 35
    for hhmm in ("10:00", "10:15", "10:45"):
        trader.step(store, at(today, hhmm), bars=session(data, today, path, hhmm))
    done = trades_today(store, "gourmand", str(today.date()))
    assert [d["side"] for d in done] == ["buy", "sell"] and done[1]["time"] == "10:45"
    assert "NVDA" not in store.load_account("gourmand").positions
    # Le soir, l'arène ne rejoue pas le stop d'un titre acheté en direct sur le plus bas de toute la journée.
    assert store.bought_during_session("gourmand", today) == {"NVDA"}


def test_missed_evening_sessions_are_caught_up_first():
    data = fake_market(300)
    days = data.trading_days()
    store = Store(":memory:")
    bots = [Tortue()]
    Arena(store, data, bots).run(days[-5], days[-5])
    trader = SessionTrader(":memory:", Arena(store, data, bots).cfg, bots, load_daily=lambda today: data)
    trader.step(store, at(days[-1], "10:00"), bars=session(data, days[-1], {}, "10:00"))
    assert store.get_meta("last_day") == str(days[-2].date())
    n = store.db.execute("SELECT COUNT(*) AS n FROM snapshots WHERE bot_id = 'tortue'").fetchone()["n"]
    assert n == 4  # les trois soirées manquées ont été jouées avant de trader


def test_overlay_puts_the_live_price_as_todays_close():
    data = fake_market(300)
    today = data.trading_days()[-1]
    bars = session(data, today, {"NVDA": [0.0, -0.02, 0.01]}, "09:45")
    now, opening = overlay(data, bars), overlay(data, bars, at_open=True)
    assert now.price("NVDA", today) == pytest.approx(bars["NVDA"].iloc[-1])
    assert now.price("NVDA", today, "Open") == pytest.approx(bars["NVDA"].iloc[-1])  # un ordre maintenant : cours actuel
    assert opening.price("NVDA", today, "Open") == pytest.approx(bars["NVDA"].iloc[0])
    assert now.extreme("NVDA", today, "low") == pytest.approx(bars["NVDA"].min())
    assert now.closes("NVDA", today, 3).index[-1] == today and len(now.closes("NVDA", today, 300)) <= 300


def test_muffin_waits_for_the_close_for_its_8_percent_stop():
    data = fake_market(300)
    day = data.trading_days()[-1]
    from chubby.broker import Account, PaperBroker, Position
    from chubby.config import ArenaConfig
    broker = PaperBroker(ArenaConfig(usd_account=False), data)
    px = data.price("HD", day)
    acct = Account("muffin", 1_000.0, {"HD": Position("HD", 10, px * 1.35 * 1.2, str(day.date()))})  # -17 %
    muffin = Muffin()
    close = muffin.decide(BotContext(day, data, broker, acct, 1.0, 0, {"rsi_sell": 100}))
    live = muffin.decide(BotContext(day, data, broker, acct, 1.0, 0, {"rsi_sell": 100}, intraday=True))
    assert close.targets is not None and "HD" not in close.targets
    assert live.targets is None or "HD" in live.targets


def test_live_view_shows_what_they_just_did(setup):
    data, today, store, trader = setup
    dip = {"NVDA": [0.0] * 6 + [-0.05]}
    for hhmm in ("10:00", "10:15"):
        trader.step(store, at(today, hhmm), bars=session(data, today, dip, hhmm))
    bars = session(data, today, dip, "10:20")
    live = pulse(store, fetch=lambda tickers: bars, now=at(today, "10:20"))
    g = next(e for e in live["bots"] if e["id"] == "gourmand")
    assert g["trades_today"][0]["ticker"] == "NVDA" and "j'ai acheté" in g["reaction"] and "10 h 15" in g["reaction"]
    assert g["curve"][0] == pytest.approx(0.0, abs=0.002)  # avant l'achat, la courbe ne compte pas NVDA
    assert g["change"] == pytest.approx(g["equity"] / g["base"] - 1, abs=1e-4)
    assert live["trading"]["checked_at"] == "10:15"


def test_all_bots_survive_a_live_session():
    data = fake_market(320)
    days = data.trading_days()
    store = Store(":memory:")
    bots = all_bots()
    Arena(store, data, bots).run(days[260], days[-2])
    trader = SessionTrader(":memory:", Arena(store, data, bots).cfg, bots, load_daily=lambda today: data)
    path = {t: [0.0, -0.03, -0.04] for t in data.frames}
    for hhmm in ("10:00", "10:15", "10:30"):
        trader.step(store, at(days[-1], hhmm), bars=session(data, days[-1], path, hhmm))
    assert "error" not in {r["kind"] for r in store.db.execute("SELECT DISTINCT kind FROM events")}
    assert store.db.execute("SELECT MIN(cash) AS m FROM accounts").fetchone()["m"] >= -1e-6
    Arena(store, data, bots).run(days[-1], days[-1])  # la séance du soir passe derrière sans rien casser
    assert store.get_meta("last_day") == str(days[-1].date())


def test_a_session_played_too_early_is_undone_before_trading():
    """Un `tick` lancé pendant la bourse a inscrit la journée avec des cours pas définitifs : ses ordres partiraient
    demain en plus de ce que le Chubby fait en séance. Le trader l'annule ; la séance du soir la rejouera."""
    data = fake_market(300)
    days = data.trading_days()
    store = Store(":memory:")
    bots = [Tortue(), Gourmand()]
    arena = Arena(store, data, bots)
    arena.run(days[-3], days[-1])  # jusqu'à aujourd'hui, comme un tick à 9 h 45
    store.set_pending("gourmand", None, days[-1], [("NVDA", 5)])
    trader = SessionTrader(":memory:", arena.cfg, bots, load_daily=lambda today: data)
    trader.step(store, at(days[-1], "10:00"), bars=session(data, days[-1], {}, "10:00"))
    assert store.get_meta("last_day") == str(days[-2].date())
    assert not store.db.execute("SELECT 1 FROM snapshots WHERE day = ?", (str(days[-1].date()),)).fetchall()
    assert store.pop_pending("gourmand") == (None, [])
    Arena(store, data, bots).run(days[-1], days[-1])  # ce soir, la journée est rejouée normalement
    assert store.get_meta("last_day") == str(days[-1].date())


def test_live_value_ignores_a_close_value_written_during_the_session(setup):
    data, today, store, trader = setup
    dip = {"NVDA": [0.0] * 6 + [-0.05]}
    for hhmm in ("10:00", "10:15"):
        trader.step(store, at(today, hhmm), bars=session(data, today, dip, hhmm))
    acct = store.load_account("gourmand")
    store.add_snapshot("gourmand", today, acct.cash + 5_000, acct.cash + 5_000)  # une vieille valeur « de clôture »
    live = pulse(store, fetch=lambda tickers: session(data, today, dip, "10:20"), now=at(today, "10:20"))
    g = next(e for e in live["bots"] if e["id"] == "gourmand")
    assert abs(g["change"]) < 0.01 and not g["settled"]


def test_capital_is_the_same_in_every_view(setup):
    """Tableau, classement, fiche, direct et ville : une seule valeur, recalculée dès qu'un ordre passe."""
    from chubby.capital import current, version
    from chubby.config import ArenaConfig
    from chubby.live import latest
    from chubby.report import bot_report
    from chubby.web import api

    data, today, store, trader = setup
    dip = {"NVDA": [0.0] * 6 + [-0.05]}
    before = version(store)
    for hhmm in ("10:00", "10:15"):
        trader.step(store, at(today, hhmm), bars=session(data, today, dip, hhmm))  # achète NVDA, puis le direct suit
    assert version(store) != before  # les pages voient le changement et se rafraîchissent

    cfg = ArenaConfig()
    live = {e["id"]: e for e in latest(store)["bots"]}
    board = {e["id"]: e for e in api.overview(store, cfg)["leaderboard"]}
    for bot_id in ("gourmand", "tortue"):
        detail, report = api.bot_detail(store, cfg, bot_id), bot_report(store, cfg, bot_id)
        values = {live[bot_id]["equity"], board[bot_id]["equity"], detail["equity"], report["equity"]}
        assert len(values) == 1, (bot_id, values)
        assert detail["cash"] == report["cash"] == round(store.load_account(bot_id).cash, 2)
        # Le % « depuis le début » vient du même capital, partout : pas celui d'une autre période.
        season = report["equity"] / report["starting_cash"] - 1
        assert board[bot_id]["metrics"]["total_return"] == pytest.approx(season, abs=1e-6)
        assert report["metrics"]["total_return"] == pytest.approx(season, abs=1e-6)
        assert detail["metrics"]["total_return"] == pytest.approx(season, abs=1e-6)
        assert report["day_change"] == pytest.approx(live[bot_id]["change"], abs=1e-4)
    g = api.bot_detail(store, cfg, "gourmand")
    assert g["positions"][0]["value_cad"] and g["equity"] == pytest.approx(g["cash"] + g["positions"][0]["value_cad"], abs=0.02)

    # Un ordre passé après le dernier calcul du direct : le capital repart du compte, sans attendre.
    acct = store.load_account("tortue")
    from chubby.broker import PaperBroker
    from chubby.intraday import overlay
    fill = PaperBroker(cfg, overlay(data, session(data, today, dip, "10:15"))).buy(acct, "XIC.TO", 1, today)
    store.add_trade("tortue", today, fill, time="10:20")
    store.save_account(acct)
    now = current(store)["tortue"]
    assert now["cash"] == round(acct.cash, 2) and now["equity"] == pytest.approx(live["tortue"]["equity"], abs=1)
    assert now["equity"] != live["tortue"]["equity"] or fill.value_cad == 0  # recalculé, pas recopié


def test_two_open_programs_do_not_trade_twice(setup):
    data, today, store, _ = setup
    bars = session(data, today, {}, "10:00")
    clock = [at(today, "10:00")]
    a, b = (SessionTrader(":memory:", Arena(store, data, [Tortue()]).cfg, [Tortue()], load_daily=lambda t: data,
                          fetch=lambda tickers: bars, clock=lambda: clock[0]) for _ in range(2))
    assert a(store) is not None and b(store) is None  # le deuxième programme laisse faire le premier
    clock[0] = at(today, "10:15")
    assert b(store) is not None


def test_idle_note_says_what_would_make_it_act(setup):
    data, today, store, trader = setup
    trader.step(store, at(today, "10:00"), bars=session(data, today, {}, "10:00"))
    notes = [json.loads(r["payload"])["why"] for r in store.db.execute(
        "SELECT payload FROM events WHERE kind = 'analyse' AND bot_id = 'tortue'")]
    assert notes and "rien à faire" in notes[0] and "conviennent" in notes[0]
    from chubby.broker import Account, PaperBroker
    now = overlay(data, session(data, today, {}, "10:00"))
    ctx = BotContext(today, now, PaperBroker(trader.cfg, now), Account("muffin", 10_000.0), 1.0, 0, {}, intraday=True)
    line = SessionTrader._closest(Muffin(), ctx)  # Muffin guette des seuils de RSI : il dit lequel est le plus proche
    assert "Le plus proche" in line and "RSI" in line


def test_a_crossed_threshold_acts_at_the_next_check_not_at_the_close():
    """Jalapeño détient un call SPY : quand SPY passe sous le seuil de sa watchlist, il vend dès la vérification suivante."""
    from chubby.bots.chubbies import Jalapeno

    data = fake_market(300, seed=7)
    days, store, bots = data.trading_days(), Store(":memory:"), [Jalapeno()]
    Arena(store, data, bots).run(days[-3], days[-2])
    store.set_params("jalapeno", {"stop": 0.7})  # le stop de secours plus loin : c'est le seuil de la watchlist qu'on teste
    today, held = days[-1], next(iter(store.load_account("jalapeno").positions))
    items = store.get_watchlist("jalapeno")["items"]
    assert len({i["ticker"] for i in items}) == len(items)  # un titre, une seule ligne
    spy = next(i for i in items if i["ticker"] == "SPY")
    sell = next(t for t in spy["triggers"] if t["action"] == "sell")
    assert spy["held"] and "il vend son call SPY" in sell["then"]
    assert all(not i["triggers"] for i in items if i["ticker"] in ("IWM", "SMH"))  # une position à la fois : rien à guetter

    trader = SessionTrader(":memory:", Arena(store, data, bots).cfg, bots, load_daily=lambda d: data)
    path = {"SPY": [0.0, sell["level"] / data.price("SPY", days[-2]) - 1.003]}  # juste sous le seuil
    sold = lambda: store.db.execute("SELECT time FROM trades WHERE bot_id = 'jalapeno' AND day = ? AND ticker = ?",  # noqa: E731
                                    (str(today.date()), held)).fetchall()
    trader.step(store, at(today, "10:00"), bars=session(data, today, path, "10:00"))
    assert not sold()  # franchi une fois : il attend la confirmation
    trader.step(store, at(today, "10:15"), bars=session(data, today, path, "10:15"))
    assert [r["time"] for r in sold()] == ["10:15"]  # confirmé : vendu tout de suite, pas ce soir
