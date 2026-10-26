import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from chubby.config import ArenaConfig
from chubby.store import Store
from chubby.web import api
from chubby.web.server import make_handler
from tests.test_arena import run_arena


def test_overview_lists_every_bot_with_its_numbers():
    arena, played = run_arena()
    ov = api.overview(arena.store, ArenaConfig())
    assert ov["season"]["sessions"] == played
    assert [e["id"] for e in ov["leaderboard"]] == [e["id"] for e in arena.leaderboard()]
    first = ov["leaderboard"][0]
    assert {"equity", "score", "metrics", "fees", "trades", "rank_move", "day_change"} <= set(first)
    assert ov["events"]


def test_equity_curves_align_on_days():
    arena, played = run_arena()
    eq = api.equity_curves(arena.store)
    assert len(eq["days"]) == played
    assert all(len(s) == played for s in eq["series"].values())


def test_bot_detail_shows_positions_with_values():
    arena, _ = run_arena()
    d = api.bot_detail(arena.store, ArenaConfig(), "tortue")
    assert d["positions"] and all(p["value_cad"] for p in d["positions"])
    assert api.bot_detail(arena.store, ArenaConfig(), "inconnu") is None


def test_server_serves_page_and_api(tmp_path):
    db = tmp_path / "arene.db"
    arena, _ = run_arena()
    arena.store.db.backup(Store(db).db)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(str(db), ArenaConfig()))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        page = urllib.request.urlopen(base + "/").read().decode()
        assert "Chubby Capital" in page
        data = json.loads(urllib.request.urlopen(base + "/api/overview").read())
        assert len(data["leaderboard"]) == 6
        detail = json.loads(urllib.request.urlopen(base + "/api/bots/flan").read())
        assert detail["name"] == "Flan"
    finally:
        server.shutdown()
