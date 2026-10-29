"""Petit serveur HTTP sans dépendance : la page du tableau de bord et l'API JSON."""

from __future__ import annotations

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..coaching import coach
from ..config import ArenaConfig
from ..store import Store
from . import api

STATIC = Path(__file__).parent / "static"
PAGES = {"/": "index.html", "/ile": "zones.html", "/carte": "carte.html", "/ile-3d": "ile.html", "/conseiller": "conseiller.html", "/chubby": "chubby.html",
         "/compte": "compte.html"}
TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8",
         ".png": "image/png", ".svg": "image/svg+xml",
         ".webp": "image/webp"}
log = logging.getLogger(__name__)
# Le navigateur a fermé la connexion avant la réponse (page rechargée ou quittée) : rien de grave, rien à renvoyer.
DISCONNECTED = (ConnectionAbortedError, ConnectionResetError, BrokenPipeError)


def make_handler(db_path: str, cfg: ArenaConfig, live=None):
    """`live` : fonction (store) -> séance en direct, rafraîchie au besoin (voir live.Refresher)."""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            try:
                if path in PAGES:
                    return self._send(200, (STATIC / PAGES[path]).read_bytes(), "text/html; charset=utf-8")
                if path.startswith("/static/"):
                    return self._static(path[len("/static/"):])
                store = Store(db_path)  # une connexion par requête : l'arène peut écrire en même temps
                if path == "/api/overview":
                    return self._json(api.overview(store, cfg))
                if path == "/api/version":
                    from ..capital import version
                    return self._json({"version": version(store)})
                if path == "/api/equity":
                    return self._json(api.equity_curves(store))
                if path == "/api/live":
                    from ..live import latest
                    payload = live(store) if live else latest(store)
                    return self._json(payload or {"ok": False, "error": "Pas encore de séance en direct."})
                if path == "/api/activity":
                    from urllib.parse import parse_qs, urlsplit
                    q = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
                    since, day = q.get("since", ""), q.get("day", "")
                    return self._json(api.activity(store, int(since) if since.isdigit() else None,
                                                   day=day if len(day) == 10 and day[4] == day[7] == "-" else None))
                if path == "/api/vrai":
                    from .. import vrai
                    return self._json(vrai.report(store))
                if path == "/api/replay":
                    return self._json(api.replay(store, cfg))
                if path.startswith("/api/bots/") and path.endswith("/fiche"):
                    from ..report import bot_report
                    report = bot_report(store, cfg, path.split("/")[3])
                    return self._json(report) if report else self._json({"erreur": "Chubby inconnu"}, 404)
                if path.startswith("/api/bots/"):
                    detail = api.bot_detail(store, cfg, path.rsplit("/", 1)[1])
                    return self._json(detail) if detail else self._json({"erreur": "Chubby inconnu"}, 404)
                return self._json({"erreur": "page introuvable"}, 404)
            except DISCONNECTED:
                self.close_connection = True
            except Exception:
                log.exception("Erreur sur %s", self.path)
                return self._json({"erreur": "erreur du serveur"}, 500)

        def do_POST(self):
            path = self.path.split("?", 1)[0].rstrip("/")
            try:
                parts = path.split("/")  # /api/bots/<id>/coach
                if len(parts) == 5 and parts[1:3] == ["api", "bots"] and parts[4] == "coach":
                    length = min(int(self.headers.get("Content-Length") or 0), 10_000)
                    body = json.loads(self.rfile.read(length) or b"{}")
                    result = coach(Store(db_path), parts[3], str(body.get("conseil", "")))
                    return self._json(result) if result else self._json({"erreur": "Chubby ou conseil inconnu"}, 400)
                if path.startswith("/api/vrai/"):
                    return self._vrai(path.rsplit("/", 1)[1])
                if path == "/api/conseil":
                    length = min(int(self.headers.get("Content-Length") or 0), 2_000_000)
                    body = json.loads(self.rfile.read(length) or b"{}")
                    cache = Path(db_path).parent / "cache"
                    return self._json(api.advice(Store(db_path), cfg, body.get("profil") or {}, str(body.get("csv") or ""),
                                                 cache_dir=cache))
                return self._json({"erreur": "page introuvable"}, 404)
            except DISCONNECTED:
                self.close_connection = True
            except ValueError as e:  # inclut un JSON ou un CSV illisible
                return self._json({"erreur": str(e) or "requête invalide"}, 400)
            except Exception:
                log.exception("Erreur sur %s", self.path)
                return self._json({"erreur": "erreur du serveur"}, 500)

        def _vrai(self, action: str):
            """Ton vrai compte. Stop marche toujours ; le reste demande ce PC, ou le code CHUBBY_PIN du fichier .env."""
            from .. import vrai
            length = min(int(self.headers.get("Content-Length") or 0), 10_000)
            body = json.loads(self.rfile.read(length) or b"{}")
            store = Store(db_path)
            if action == "stop":
                return self._json({"config": vrai.stop(store)})
            pin = os.environ.get("CHUBBY_PIN", "")
            local = self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")
            if not local and not (pin and str(body.get("pin", "")) == pin):
                return self._json({"erreur": "Depuis un autre appareil, il faut le code CHUBBY_PIN du fichier .env."
                                   if pin else "Ces réglages ne se changent que depuis le PC où tourne l'arène "
                                   "(ou ajoute un code CHUBBY_PIN dans le fichier .env)."}, 403)
            if action == "reglages":
                return self._json({"config": vrai.update_config(store, body)})
            if action == "demarrer":
                return self._json({"config": vrai.start(store)})
            return self._json({"erreur": "action inconnue"}, 404)

        def _static(self, name: str):
            target = (STATIC / name).resolve()
            if STATIC.resolve() not in target.parents or not target.is_file():
                return self._json({"erreur": "fichier introuvable"}, 404)
            return self._send(200, target.read_bytes(), TYPES.get(target.suffix, "application/octet-stream"))

        def _json(self, data, status: int = 200):
            self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _send(self, status: int, body: bytes, content_type: str):
            try:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            except DISCONNECTED:
                self.close_connection = True

        def log_message(self, fmt, *args):
            log.debug(fmt, *args)

    return Handler


class Server(ThreadingHTTPServer):
    # Sous Windows, « réutiliser l'adresse » laisse deux serveurs écouter le même port : un ancien tableau de bord
    # oublié répondrait alors à la place du nouveau, avec son ancien code. On refuse donc un port déjà pris.
    allow_reuse_address = os.name != "nt"


def serve(db_path: str, cfg: ArenaConfig, host: str = "0.0.0.0", port: int = 8000, live=None, block: bool = True):
    try:
        server = Server((host, port), make_handler(db_path, cfg, live))
    except OSError:
        raise SystemExit(f"Le port {port} est déjà utilisé : un autre tableau de bord tourne sans doute encore "
                         f"(une autre fenêtre ou un ancien terminal). Ferme-le, ou lance celui-ci avec --port {port + 1}.")
    if not block:  # dans un fil à côté de l'arène (chubby serve --web)
        import threading
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(f"Tableau de bord : http://localhost:{port}  (base : {db_path})")
        return server
    print(f"Tableau de bord : http://localhost:{port}  (base : {db_path})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
