"""Petit serveur HTTP sans dépendance : la page du tableau de bord et l'API JSON."""

from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ..config import ArenaConfig
from ..store import Store
from . import api

STATIC = Path(__file__).parent / "static"
log = logging.getLogger(__name__)


def make_handler(db_path: str, cfg: ArenaConfig):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            try:
                if path == "/":
                    return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
                store = Store(db_path)  # une connexion par requête : l'arène peut écrire en même temps
                if path == "/api/overview":
                    return self._json(api.overview(store, cfg))
                if path == "/api/equity":
                    return self._json(api.equity_curves(store))
                if path.startswith("/api/bots/"):
                    detail = api.bot_detail(store, cfg, path.rsplit("/", 1)[1])
                    return self._json(detail) if detail else self._json({"erreur": "Chubby inconnu"}, 404)
                return self._json({"erreur": "page introuvable"}, 404)
            except Exception:
                log.exception("Erreur sur %s", self.path)
                return self._json({"erreur": "erreur du serveur"}, 500)

        def _json(self, data, status: int = 200):
            self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

        def _send(self, status: int, body: bytes, content_type: str):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            log.debug(fmt, *args)

    return Handler


def serve(db_path: str, cfg: ArenaConfig, host: str = "0.0.0.0", port: int = 8000):
    server = ThreadingHTTPServer((host, port), make_handler(db_path, cfg))
    print(f"Tableau de bord : http://localhost:{port}  (base : {db_path})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
