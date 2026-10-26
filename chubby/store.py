"""Mémoire de l'arène (SQLite) : comptes, trades, valeurs quotidiennes, journal, événements."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd

from .broker import Account, Fill, Position

SCHEMA = """
CREATE TABLE IF NOT EXISTS bots (
    id TEXT PRIMARY KEY, name TEXT, personality TEXT,
    aggressiveness REAL DEFAULT 1.0, xp INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS accounts (bot_id TEXT PRIMARY KEY, cash REAL);
CREATE TABLE IF NOT EXISTS positions (
    bot_id TEXT, ticker TEXT, qty INTEGER, avg_cost_cad REAL, opened_on TEXT,
    PRIMARY KEY (bot_id, ticker)
);
CREATE TABLE IF NOT EXISTS pending (bot_id TEXT PRIMARY KEY, targets TEXT, decided_on TEXT);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY, bot_id TEXT, day TEXT, ticker TEXT, side TEXT, qty INTEGER,
    price REAL, currency TEXT, fx REAL, fees_cad REAL, value_cad REAL
);
CREATE TABLE IF NOT EXISTS snapshots (
    bot_id TEXT, day TEXT, equity REAL, cash REAL, score INTEGER, rank INTEGER,
    PRIMARY KEY (bot_id, day)
);
CREATE TABLE IF NOT EXISTS journal (id INTEGER PRIMARY KEY, bot_id TEXT, day TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, day TEXT, bot_id TEXT, kind TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def _d(day) -> str:
    return str(pd.Timestamp(day).date())


class Store:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        columns = {r["name"] for r in self.db.execute("PRAGMA table_info(snapshots)")}
        if "holdings" not in columns:  # bases créées avant l'ajout de la colonne
            self.db.execute("ALTER TABLE snapshots ADD COLUMN holdings TEXT")

    def commit(self):
        self.db.commit()

    # --- Bots et comptes --------------------------------------------------

    def ensure_bot(self, bot, starting_cash: float):
        self.db.execute("INSERT OR IGNORE INTO bots (id, name, personality) VALUES (?, ?, ?)",
                        (bot.id, bot.name, bot.personality))
        self.db.execute("INSERT OR IGNORE INTO accounts VALUES (?, ?)", (bot.id, starting_cash))

    def bot_row(self, bot_id: str) -> sqlite3.Row:
        return self.db.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()

    def set_aggressiveness(self, bot_id: str, value: float):
        self.db.execute("UPDATE bots SET aggressiveness = ? WHERE id = ?", (value, bot_id))

    def add_xp(self, bot_id: str, xp: int):
        self.db.execute("UPDATE bots SET xp = xp + ? WHERE id = ?", (xp, bot_id))

    def load_account(self, bot_id: str) -> Account:
        cash = self.db.execute("SELECT cash FROM accounts WHERE bot_id = ?", (bot_id,)).fetchone()["cash"]
        rows = self.db.execute("SELECT * FROM positions WHERE bot_id = ?", (bot_id,)).fetchall()
        return Account(bot_id, cash, {r["ticker"]: Position(r["ticker"], r["qty"], r["avg_cost_cad"], r["opened_on"])
                                      for r in rows})

    def save_account(self, acct: Account):
        self.db.execute("UPDATE accounts SET cash = ? WHERE bot_id = ?", (acct.cash, acct.bot_id))
        self.db.execute("DELETE FROM positions WHERE bot_id = ?", (acct.bot_id,))
        self.db.executemany("INSERT INTO positions VALUES (?, ?, ?, ?, ?)",
                            [(acct.bot_id, p.ticker, p.qty, p.avg_cost_cad, p.opened_on)
                             for p in acct.positions.values()])

    # --- Ordres en attente (décidés le soir, exécutés à l'ouverture suivante) ---

    def set_pending(self, bot_id: str, targets: dict[str, float], day):
        self.db.execute("INSERT OR REPLACE INTO pending VALUES (?, ?, ?)", (bot_id, json.dumps(targets), _d(day)))

    def pop_pending(self, bot_id: str) -> dict[str, float] | None:
        row = self.db.execute("SELECT targets FROM pending WHERE bot_id = ?", (bot_id,)).fetchone()
        if row is None:
            return None
        self.db.execute("DELETE FROM pending WHERE bot_id = ?", (bot_id,))
        return json.loads(row["targets"])

    # --- Historique -------------------------------------------------------

    def add_trade(self, bot_id: str, day, f: Fill):
        self.db.execute(
            "INSERT INTO trades (bot_id, day, ticker, side, qty, price, currency, fx, fees_cad, value_cad)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (bot_id, _d(day), f.ticker, f.side, f.qty, f.price, f.currency, f.fx, f.fees_cad, f.value_cad))

    def add_snapshot(self, bot_id: str, day, equity: float, cash: float, holdings: dict[str, float] | None = None):
        self.db.execute("INSERT OR REPLACE INTO snapshots (bot_id, day, equity, cash, holdings) VALUES (?, ?, ?, ?, ?)",
                        (bot_id, _d(day), equity, cash,
                         json.dumps({t: round(v, 2) for t, v in (holdings or {}).items()})))

    def set_rank(self, bot_id: str, day, score: int, rank: int):
        self.db.execute("UPDATE snapshots SET score = ?, rank = ? WHERE bot_id = ? AND day = ?",
                        (score, rank, bot_id, _d(day)))

    def equity_series(self, bot_id: str) -> pd.Series:
        rows = self.db.execute("SELECT day, equity FROM snapshots WHERE bot_id = ? ORDER BY day", (bot_id,)).fetchall()
        return pd.Series([r["equity"] for r in rows], index=pd.DatetimeIndex([r["day"] for r in rows]), dtype=float)

    def previous_rank(self, bot_id: str, day) -> int | None:
        row = self.db.execute("SELECT rank FROM snapshots WHERE bot_id = ? AND day < ? AND rank IS NOT NULL"
                              " ORDER BY day DESC LIMIT 1", (bot_id, _d(day))).fetchone()
        return row["rank"] if row else None

    def journal(self, bot_id: str, day, text: str):
        self.db.execute("INSERT INTO journal (bot_id, day, text) VALUES (?, ?, ?)", (bot_id, _d(day), text))

    def event(self, day, bot_id: str | None, kind: str, **payload):
        self.db.execute("INSERT INTO events (day, bot_id, kind, payload) VALUES (?, ?, ?, ?)",
                        (_d(day), bot_id, kind, json.dumps(payload, ensure_ascii=False)))

    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))
