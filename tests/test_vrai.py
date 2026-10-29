from datetime import datetime

import pytest

from chubby import vrai
from chubby.ibkr import from_ib, to_ib
from chubby.live import TORONTO
from chubby.vrai import Holding, Snapshot
from tests.test_arena import run_arena

OPEN = datetime(2026, 11, 3, 10, 0, tzinfo=TORONTO)  # un mardi, bourse ouverte


class FakeBroker:
    """Courtier de test : exécute chaque ordre tout de suite au cours limité."""

    def __init__(self, cash=100.0, paper=True, account="DU123"):
        self.cash, self.paper, self.account = cash, paper, account
        self.holdings: dict[str, Holding] = {}
        self.placed, self.cancelled = [], 0

    def snapshot(self):
        return Snapshot(self.account, self.cash, {t: Holding(t, h.qty, h.value_cad) for t, h in self.holdings.items()},
                        self.paper)

    def place(self, order):
        self.placed.append(order)
        sign = 1 if order.side == "buy" else -1
        self.cash -= sign * order.value_cad
        h = self.holdings.setdefault(order.ticker, Holding(order.ticker, 0.0, 0.0))
        unit = order.value_cad / order.qty
        h.qty += sign * order.qty
        h.value_cad = h.qty * unit
        if h.qty <= 0:
            del self.holdings[order.ticker]
        return "Filled", str(len(self.placed)), ""

    def cancel_all(self):
        self.cancelled += 1
        return 0


@pytest.fixture(scope="module")
def arena_store():
    arena, _ = run_arena()
    return arena.store


@pytest.fixture
def store(arena_store):
    arena_store.set_meta("vrai_config", "")
    arena_store.set_meta("vrai_dernier", "")
    arena_store.set_meta("vrai_annuler", "")
    arena_store.db.execute("DELETE FROM real_orders")
    arena_store.db.execute("DELETE FROM real_snapshots")
    return arena_store


def _start(store, guide="tortue", **settings):
    vrai.update_config(store, {"guide": guide, **settings})
    vrai.start(store)


def test_copies_the_guide_at_the_scale_of_the_amount(store):
    _start(store, plafond=1000, ordre_max=1000, ordre_min=1)
    broker = FakeBroker(cash=1000)
    result = vrai.run(store, broker, OPEN)
    weights, units, _ = vrai.guide_weights(store, "tortue")
    assert result["ran"] and broker.placed
    assert {o.ticker for o in broker.placed} <= set(weights)
    for o in broker.placed:
        assert o.side == "buy" and o.qty == int(o.qty)  # actions entières sans les fractions
        assert o.value_cad <= weights[o.ticker] * 1000 + 1e-6
    rows = store.db.execute("SELECT * FROM real_orders").fetchall()
    assert len(rows) == len(broker.placed) and all(r["status"] == "Filled" and r["why"] for r in rows)


def test_never_invests_more_than_the_cap_or_the_max_order(store):
    _start(store, "mochi", plafond=500, ordre_max=60, ordre_min=1, fractions=True)
    broker = FakeBroker(cash=5000)
    vrai.run(store, broker, OPEN)
    spent = sum(o.value_cad for o in broker.placed)
    assert broker.placed and spent <= 500
    assert all(o.value_cad <= 60 + 1e-6 for o in broker.placed)
    assert any(o.qty != int(o.qty) for o in broker.placed if o.currency == "USD")  # des fractions d'actions américaines


def test_sells_what_the_guide_no_longer_holds(store):
    _start(store, plafond=1000, ordre_min=1)
    broker = FakeBroker(cash=900)
    broker.holdings["ZZZ.TO"] = Holding("ZZZ.TO", 4, 100.0)
    vrai.run(store, broker, OPEN)
    sell = [o for o in broker.placed if o.side == "sell"]
    assert [(o.ticker, o.qty) for o in sell] == [("ZZZ.TO", 4)]
    assert broker.placed[0].side == "sell"  # les ventes d'abord


def test_max_loss_stops_everything(store):
    _start(store, perte_max=0.10)
    broker = FakeBroker(cash=100)
    vrai.run(store, broker, OPEN)
    broker.placed.clear()
    broker.cash, broker.holdings = 85.0, {}
    later = OPEN.replace(day=4)
    result = vrai.run(store, broker, later)
    cfg = vrai.config(store)
    assert not result["ran"] and not broker.placed
    assert not cfg["actif"] and "Perte maximale" in cfg["pause"]


def test_paper_mode_refuses_a_real_account(store):
    _start(store)
    broker = FakeBroker(paper=False, account="U999")
    vrai.run(store, broker, OPEN)
    assert not broker.placed and not vrai.config(store)["actif"]


def test_stop_cancels_open_orders_on_the_next_pass(store):
    _start(store)
    vrai.stop(store)
    assert vrai.due(store, OPEN.replace(hour=20))  # même bourse fermée
    broker = FakeBroker()
    assert vrai.run(store, broker, OPEN)["ran"] is False
    assert broker.cancelled == 1 and not broker.placed


def test_runs_once_after_the_open_then_only_when_the_guide_trades(store):
    _start(store)
    assert not vrai.due(store, OPEN.replace(hour=9, minute=31))  # le guide n'a pas encore exécuté ses ordres
    assert not vrai.due(store, OPEN.replace(hour=17))
    assert vrai.due(store, OPEN)
    vrai.run(store, FakeBroker(), OPEN)
    assert not vrai.due(store, OPEN.replace(minute=30))
    store.db.execute("INSERT INTO trades (bot_id, day, ticker, side, qty, price, currency, fx, fees_cad, value_cad)"
                     " VALUES ('tortue', '2026-11-03', 'XIC.TO', 'buy', 1, 30, 'CAD', 1, 0, 30)")
    assert vrai.due(store, OPEN.replace(minute=30))


def test_settings_are_checked(store):
    with pytest.raises(ValueError):
        vrai.update_config(store, {"guide": "jalapeno"})
    with pytest.raises(ValueError):
        vrai.update_config(store, {"mode": "reel"})  # sans la confirmation
    with pytest.raises(ValueError):
        vrai.start(store)  # pas de guide
    cfg = vrai.update_config(store, {"guide": "mochi", "mode": "reel", "confirme": True})
    assert cfg["mode"] == "reel" and cfg["guide"] == "mochi"


def test_ibkr_symbols_round_trip():
    assert to_ib("RY.TO") == ("RY", "TSE", "CAD")
    assert to_ib("CTC-A.TO") == ("CTC.A", "TSE", "CAD")
    assert to_ib("BRK-B") == ("BRK B", "", "USD")
    assert from_ib("CTC.A", "TSE", "CAD") == "CTC-A.TO"
    assert from_ib("BRK B", "NYSE", "USD") == "BRK-B"


def test_report_for_the_page(store):
    _start(store)
    vrai.run(store, FakeBroker(), OPEN)
    r = vrai.report(store)
    assert r["config"]["actif"] and r["state"]["account_id"] == "DU123"
    assert any(g["id"] == "jalapeno" and not g["allowed"] for g in r["guides"])
    assert r["guide_weights"]


def test_only_one_program_manages_the_account(store):
    _start(store)
    first, second = vrai.Runner(FakeBroker), vrai.Runner(FakeBroker)
    assert first(store, OPEN)["ran"]
    assert second(store, OPEN.replace(minute=1)) is None  # le premier tient la main
    assert first.claim(store, OPEN.replace(minute=2))  # il se renouvelle chaque minute
    assert not second.claim(store, OPEN.replace(minute=3))
    assert second.claim(store, OPEN.replace(minute=6))  # le premier ne se manifeste plus depuis 4 minutes
