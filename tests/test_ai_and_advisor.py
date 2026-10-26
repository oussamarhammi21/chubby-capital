import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from chubby import ai_coach
from chubby.advisor import advise, match_bots, parse_holdings
from chubby.arena import Arena
from chubby.bots import AI_BOTS, all_bots
from chubby.bots.matcha import HORIZON, Matcha
from chubby.config import ArenaConfig
from chubby.store import Store
from tests.fakes import fake_market


class FakeClaude:
    """Remplace l'API : renvoie une réponse fixe et garde les requêtes reçues."""

    def __init__(self, answer):
        self.calls = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))
        self.answer = answer

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(self.answer))])


@pytest.fixture(scope="module")
def live_arena():
    data = fake_market(330)
    days = data.trading_days()
    arena = Arena(Store(":memory:"), data, all_bots(ia=True))
    arena.run(start=days[260])
    return arena


def test_ai_versions_play_alongside_originals(live_arena):
    ids = {e["id"] for e in live_arena.leaderboard()}
    assert {"mochi", "mochi-ia", "jalapeno-ia", "matcha-ia"} <= ids


def test_weekly_review_applies_bounded_advice(live_arena):
    store = live_arena.store
    answer = {"aggressiveness": 9.0, "params": {"holdings": 99, "keep_rank": 8, "lookback": 84},
              "lesson": "Je garde moins de titres mais plus longtemps.", "reasoning": "test"}
    mochi_ia = next(b for b in live_arena.bots if b.id == "mochi-ia")
    client = FakeClaude(answer)
    changes = ai_coach.apply_advice(store, mochi_ia, ai_coach.ask_claude(client, mochi_ia, "{}"), "2024-03-01")
    assert store.bot_row("mochi-ia")["aggressiveness"] == ai_coach.AGGRESSIVENESS[1]  # borné à 1,5
    params = store.get_params("mochi-ia")
    assert params == {"holdings": 8.0, "keep_rank": 8.0, "lookback": 84.0}  # 99 ramené à la borne haute
    assert "holdings" in changes
    request = client.calls[0]
    assert request["model"] == ai_coach.MODEL
    schema = request["output_config"]["format"]["schema"]
    assert set(schema["properties"]["params"]["required"]) == set(mochi_ia.params)
    assert store.db.execute("SELECT text FROM journal WHERE bot_id = 'mochi-ia' ORDER BY id DESC").fetchone()["text"] \
        == answer["lesson"]


def test_weekly_review_only_touches_ai_versions(live_arena):
    client = FakeClaude({"aggressiveness": 1.1, "params": {}, "lesson": "ok", "reasoning": "ok"})
    before = {b.id: live_arena.store.bot_row(b.id)["aggressiveness"] for b in live_arena.bots if not b.ai}
    n = ai_coach.weekly_review(live_arena.store, live_arena.data, live_arena.bots, live_arena.last_day, client=client)
    assert n == len(AI_BOTS) == len(client.calls)
    assert before == {b.id: live_arena.store.bot_row(b.id)["aggressiveness"] for b in live_arena.bots if not b.ai}
    context = client.calls[0]["messages"][0]["content"]
    assert "jumeau_sans_coach_quatre_semaines" in context and "marche" in context


def test_matcha_never_trains_on_unknown_future():
    data = fake_market(400)
    day = data.trading_days()[330]
    bot = Matcha()
    ctx = SimpleNamespace(data=data, day=day, params={})
    bot.train(ctx)
    for t, f in bot._panel.items():
        known = f.loc[:day]
        cutoff = known.index[-HORIZON - 1]
        # le résultat le plus récent utilisé dépend de la clôture d'aujourd'hui, jamais d'après
        assert data.frames[t].index.get_loc(cutoff) + HORIZON <= data.frames[t].index.get_loc(day)
    assert bot.weights is not None and np.isfinite(bot.weights).all()


WS_CSV = """Account Name,Account Type,Symbol,Exchange,Name,Security Type,Quantity,Market Price,Market Value,Market Value Currency
CELI,TFSA,XEQT,TSX,iShares Core Equity,EXCHANGE_TRADED_FUND,100,"32,50",3250.00,CAD
CELI,TFSA,AAPL,NASDAQ,Apple,EQUITY,10,200,2000.00,USD
CELI,TFSA,SHOP,TSX,Shopify,EQUITY,40,100,4000.00,CAD
CELI,TFSA,MYSTERE,TSX,Sans valeur,EQUITY,5,,,CAD
"""


def test_parse_wealthsimple_export():
    holdings, skipped = parse_holdings(WS_CSV, usdcad=1.4)
    by = {h["symbol"]: h for h in holdings}
    assert set(by) == {"XEQT.TO", "AAPL", "SHOP.TO"}
    assert by["AAPL"]["value_cad"] == pytest.approx(2800)
    assert by["SHOP.TO"]["currency"] == "CAD"
    assert skipped == ["MYSTERE"]


def test_advisor_matches_profile_and_compares(live_arena):
    cfg = ArenaConfig()
    prudent = match_bots(live_arena.store, cfg, {"risk": 1, "style": "revenu", "horizon": "long"})
    assert prudent and all(m["id"].removesuffix("-ia") in {"flan", "dumpling"} for m in prudent)
    holdings, _ = parse_holdings(WS_CSV, 1.4)
    out = advise(live_arena.store, cfg, {"risk": 4, "style": "croissance", "horizon": "long", "usd_account": False}, holdings)
    assert out["advisor"]["id"].removesuffix("-ia") in {"mochi", "matcha", "muffin", "jalapeno"}
    assert any("SHOP.TO" in n for n in out["notes"])  # 40 % sur un seul titre
    assert {r["symbol"] for r in out["rows"]} >= {"XEQT.TO", "AAPL", "SHOP.TO"}
    assert not out["advisor"]["certified"]  # trop peu de séances pour l'instant


ACTIVITIES = """effective_date,activity_type,activity_sub_type,direction,symbol,account_type,currency,quantity,unit_price,net_cash_amount
2024-01-02,MoneyMovement,EFT,,,TFSA,CAD,5000,,5000
2024-01-03,Trade,BUY,LONG,XEQT,TFSA,CAD,100,30,-3000
2024-02-01,Trade,BUY,LONG,AAPL,TFSA,USD,10,180,-1800
2024-03-01,Trade,SELL,LONG,AAPL,TFSA,USD,-4,190,760
2024-03-05,Dividend,-,,XEQT,TFSA,CAD,12.5,,12.5
2024-04-01,CorporateAction,CONSOLIDATION,LONG,XEQT,TFSA,,-50,,
2024-05-01,Trade,BUY,LONG,BTC,Crypto,CAD,0.01,60000,-600
2024-06-01,Trade,BUY,LONG,NVDA  240628C00130000,TFSA,USD,2,1.5,-300
2024-06-28,OptionExpiry,-,LONG,NVDA  240628C00130000,TFSA,,-2,,
2024-07-01,Trade,BUY,LONG,ZZZZ,TFSA,CAD,10,2,-20
2024-07-02,Trade,BUY,LONG,GONE,TFSA,USD,10,1,-10
"""


def test_rebuild_positions_from_activity_export():
    from chubby.advisor import read_portfolio

    prices = {("XEQT", "CAD"): ("XEQT.TO", 35.0), ("AAPL", "USD"): ("AAPL", 200.0)}
    holdings, skipped, notes = read_portfolio(ACTIVITIES, 1.4, lambda s, c: prices.get((s, c)))
    by = {h["symbol"]: h for h in holdings}
    assert by["XEQT.TO"]["quantity"] == 50  # achat de 100, regroupement de 2 pour 1
    assert by["AAPL"]["quantity"] == 6 and by["AAPL"]["value_cad"] == pytest.approx(6 * 200 * 1.4)
    assert by["ZZZZ.TO"]["value_cad"] == pytest.approx(20)  # pas de cours : dernier prix payé
    assert "BTC" not in str(holdings) and not any("NVDA" in s for s in by)  # crypto et options exclues
    assert skipped == [] and any("crypto" in n for n in notes) and any("ZZZZ" in n for n in notes)
