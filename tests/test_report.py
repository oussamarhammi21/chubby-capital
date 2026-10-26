import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from chubby.bots import ALL_BOTS
from chubby.config import ArenaConfig
from chubby.report import bot_report, explanation, round_trips
from chubby.store import Store
from chubby.web.server import make_handler
from tests.test_arena import run_arena


def t(day, ticker, side, qty, value):
    return {"day": day, "ticker": ticker, "side": side, "qty": qty, "value_cad": value}


def test_round_trips_count_all_money_in_and_out():
    trades = [t("2024-01-02", "RY.TO", "buy", 10, 1000), t("2024-01-10", "RY.TO", "buy", 5, 520),
              t("2024-02-01", "RY.TO", "sell", 15, 1650), t("2024-02-05", "KO", "buy", 3, 300)]
    done, still_open = round_trips(trades)
    assert done == [{"ticker": "RY.TO", "start": "2024-01-02", "end": "2024-02-01", "pnl_cad": 130.0,
                     "return": pytest.approx(130 / 1520, abs=1e-4), "trades": 3, "days": 30}]
    assert set(still_open) == {"KO"}


def test_an_assigned_put_closes_the_option_and_opens_the_shares():
    put = "OPT:XLF:P:40:2024-03-15"
    trades = [t("2024-02-01", put, "sell", 1, 120), t("2024-03-15", put, "assign", 1, 5600),
              t("2024-04-01", "XLF", "sell", 100, 5800)]
    done, still_open = round_trips(trades)
    assert [(d["ticker"], d["pnl_cad"]) for d in done] == [(put, 120.0), ("XLF", 200.0)]
    assert not still_open


def test_every_bot_explains_its_rules_with_current_settings():
    for cls in ALL_BOTS:
        rules = explanation(cls, {})
        assert len(rules) >= 3 and all("{" not in r for r in rules), cls.id
    muffin = next(c for c in ALL_BOTS if c.id == "muffin")
    assert any("-12 %" in r for r in explanation(muffin, {"stop": 0.12}))


def test_report_tells_the_whole_story():
    arena, played = run_arena(400)
    r = bot_report(arena.store, ArenaConfig(), "muffin")
    assert len(r["curve"]["days"]) == played and r["curve"]["twin"] is None
    assert r["stats"]["trades"] == arena.store.db.execute("SELECT COUNT(*) FROM trades WHERE bot_id = 'muffin'").fetchone()[0]
    kinds = {e["kind"] for d in r["timeline"] for m in ("open", "session", "close", "evening") for e in d[m]}
    assert {"trade", "journal"} <= kinds
    assert any(d.get("decided_on") for d in r["timeline"])  # les ordres de l'ouverture viennent de la veille
    assert all(p["stop"] for p in r["positions"])  # Muffin a un stop sur chaque position
    assert r["monthly"] and r["round_trips"]


def test_page_and_api_are_served(tmp_path):
    db = tmp_path / "arene.db"
    arena, _ = run_arena(300)
    arena.store.db.backup(Store(db).db)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(str(db), ArenaConfig()))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert "Comment il pense" in urllib.request.urlopen(base + "/chubby?id=flan").read().decode()
        assert "window.Chubby" in urllib.request.urlopen(base + "/static/chubbies.js").read().decode()
        fiche = json.loads(urllib.request.urlopen(base + "/api/bots/flan/fiche").read())
        assert fiche["name"] == "Flan" and fiche["timeline"]
    finally:
        server.shutdown()
