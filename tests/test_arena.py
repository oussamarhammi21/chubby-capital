from functools import lru_cache

import pandas as pd

from chubby.arena import Arena
from chubby.bots import ALL_BOTS, all_bots
from chubby.scoring import Metrics, metrics, score
from chubby.store import Store
from tests.fakes import fake_market


@lru_cache(maxsize=None)  # la saison simulée est partagée par les tests qui ne font que la lire
def run_arena(days=500):
    data = fake_market(days)
    trading = data.trading_days()
    arena = Arena(Store(":memory:"), data, all_bots())
    played = arena.run(start=trading[260])  # les 260 premiers jours servent d'historique
    return arena, played


def test_full_season_runs_for_every_bot():
    arena, played = run_arena()
    board = arena.leaderboard()
    assert played == 240
    assert {e["id"] for e in board} == {cls.id for cls in ALL_BOTS}
    for e in board:
        assert e["equity"] > 0
        assert 0 <= e["score"] <= 100
    cash = arena.store.db.execute("SELECT MIN(cash) AS m FROM accounts").fetchone()["m"]
    assert cash >= -1e-6


def test_bots_actually_trade_and_write_journal():
    arena, _ = run_arena()
    db = arena.store.db
    traders = {r["bot_id"] for r in db.execute("SELECT DISTINCT bot_id FROM trades")}
    assert {"tortue", "flan", "dumpling", "mochi", "jalapeno", "matcha"} <= traders  # Muffin peut ne trouver aucune aubaine
    assert db.execute("SELECT COUNT(*) AS n FROM journal").fetchone()["n"] > 0
    kinds = {r["kind"] for r in db.execute("SELECT DISTINCT kind FROM events")}
    assert {"trade", "leader"} <= kinds
    assert "error" not in kinds


def test_long_term_bots_do_not_churn():
    arena, _ = run_arena()
    counts = {r["bot_id"]: r["n"] for r in arena.store.db.execute(
        "SELECT bot_id, COUNT(DISTINCT day) AS n FROM trades GROUP BY bot_id")}
    assert counts["tortue"] <= 12  # quelques réinvestissements de dividendes
    assert counts["flan"] <= 40  # environ un rééquilibrage par mois
    assert counts["dumpling"] <= 20


def test_flan_collects_dividends():
    arena, _ = run_arena()
    n = arena.store.db.execute(
        "SELECT COUNT(*) AS n FROM events WHERE kind = 'dividend' AND bot_id = 'flan'").fetchone()["n"]
    assert n > 0


def test_run_is_resumable_and_never_replays_a_day():
    data = fake_market(400)
    days = data.trading_days()
    arena = Arena(Store(":memory:"), data, all_bots())
    assert arena.run(days[260], days[300]) == 41
    assert arena.run(days[260], days[320]) == 20
    rows = arena.store.db.execute("SELECT COUNT(*) AS n FROM snapshots WHERE bot_id = 'tortue'").fetchone()
    assert rows["n"] == 61


def test_bots_never_see_future_prices():
    data = fake_market(300)
    day = data.trading_days()[200]
    assert data.closes("NVDA", day, 1000).index.max() == day
    assert data.dividends_ttm("RY.TO", day) == data.frames["RY.TO"].loc[day - pd.Timedelta(days=365):day,
                                                                          "Dividends"].sum()


def test_score_uses_yearly_return_on_long_seasons():
    import numpy as np
    days = pd.bdate_range("2022-01-03", periods=1008)  # quatre ans
    fast = pd.Series(10_000 * np.linspace(1, 1.9, len(days)), index=days)  # +90 %
    slow = pd.Series(10_000 * np.linspace(1, 1.5, len(days)), index=days)  # +50 %
    m_fast, m_slow = metrics(fast, 10_000), metrics(slow, 10_000)
    assert 0.15 < m_fast.annual_return < 0.18
    assert score(m_fast) > score(m_slow)  # avant, les deux saturaient à +20 %


def test_score_rewards_return_and_punishes_drawdown():
    calm = score(Metrics(0.10, 0.10, 1.2, -0.05, 0.6))
    reckless = score(Metrics(0.10, 0.60, 0.3, -0.35, 0.45))
    assert calm > reckless
    assert 0 <= score(Metrics(-0.9, 2, -3, -0.95, 0)) <= score(Metrics(2, 0.1, 5, 0, 1)) <= 100


def test_usd_account_fee_is_charged_once_a_month():
    arena, played = run_arena()
    n = arena.store.db.execute(
        "SELECT COUNT(*) AS n FROM events WHERE kind = 'fee' AND bot_id = 'tortue'").fetchone()["n"]
    assert 11 <= n <= 13  # 240 séances, environ 11 à 12 mois


def test_mochi_and_jalapeno_do_not_churn():
    arena, _ = run_arena()
    counts = {r["bot_id"]: r["n"] for r in arena.store.db.execute(
        "SELECT bot_id, COUNT(DISTINCT day) AS n FROM trades GROUP BY bot_id")}
    assert counts["jalapeno"] <= 240 / 3  # un aller-retour sur options dure plusieurs séances
    assert counts["matcha"] <= 240 / 21 * 2 + 4  # environ un rééquilibrage par mois
