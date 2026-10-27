import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from chubby.bots import ALL_BOTS
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


def test_replay_has_one_value_per_day_and_events_by_day():
    arena, played = run_arena()
    r = api.replay(arena.store, ArenaConfig())
    assert len(r["days"]) == played
    assert {b["id"] for b in r["bots"]} == set(r["equity"])
    assert all(len(v) == played and None not in v for v in r["equity"].values())
    assert set(r["events"]) <= set(r["days"])
    kinds = {e["kind"] for evs in r["events"].values() for e in evs}
    assert "trade" in kinds


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
        assert len(data["leaderboard"]) == len(ALL_BOTS)
        detail = json.loads(urllib.request.urlopen(base + "/api/bots/flan").read())
        assert detail["name"] == "Flan"
        assert "Chubby Capital" in urllib.request.urlopen(base + "/ile").read().decode()
        assert len(urllib.request.urlopen(base + "/static/vendor/three.min.js").read()) > 500_000
        try:
            urllib.request.urlopen(base + "/static/../server.py")
            assert False, "un fichier hors du dossier static ne doit pas être servi"
        except urllib.error.HTTPError as e:
            assert e.code == 404
        req = urllib.request.Request(base + "/api/bots/jalapeno/coach", data=b'{"conseil": "moins"}',
                                     headers={"Content-Type": "application/json"}, method="POST")
        coached = json.loads(urllib.request.urlopen(req).read())
        assert coached["aggressiveness"] < coached["old"]
        assert Store(db).bot_row("jalapeno")["aggressiveness"] == coached["aggressiveness"]
        body = json.dumps({"profil": {"risk": 2, "style": "revenu"}, "csv": "Symbol,Quantity,Market Value\nXEQT.TO,10,400"}).encode()
        req = urllib.request.Request(base + "/api/conseil", data=body, headers={"Content-Type": "application/json"}, method="POST")
        advice = json.loads(urllib.request.urlopen(req).read())
        assert advice["total_cad"] == 400 and advice["matches"]
        assert json.loads(Store(db).get_meta("profil"))["style"] == "revenu"  # le profil est gardé, pas le fichier
        bad = urllib.request.Request(base + "/api/conseil", data=b'{"csv": "rien,ici"}', method="POST")
        try:
            urllib.request.urlopen(bad)
            assert False, "un CSV sans colonne Symbol doit être refusé"
        except urllib.error.HTTPError as e:
            assert e.code == 400 and "Symbol" in json.loads(e.read())["erreur"]
    finally:
        server.shutdown()


def test_closed_browser_connection_is_not_an_error(tmp_path, caplog):
    """Le navigateur ferme la connexion avant la réponse (page rechargée) : pas d'erreur ni de 500."""
    import io
    from chubby.web.server import make_handler as mk

    class Closed(io.BytesIO):
        def write(self, b):
            raise ConnectionAbortedError(10053, "connexion interrompue")

    handler = mk(str(tmp_path / "arene.db"), ArenaConfig(), live=lambda store: {"ok": True})
    h = handler.__new__(handler)
    h.path, h.request_version, h.requestline, h.command = "/api/live", "HTTP/1.1", "GET /api/live HTTP/1.1", "GET"
    h.client_address, h.wfile, h.close_connection = ("127.0.0.1", 0), Closed(), False
    with caplog.at_level("ERROR"):
        h.do_GET()
    assert h.close_connection and not caplog.records
