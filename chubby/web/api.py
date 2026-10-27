"""Ce que le tableau de bord affiche, lu directement dans la base de l'arène."""

from __future__ import annotations

import json
from dataclasses import asdict

from ..bots import bot_class
from ..config import ArenaConfig
from ..scoring import metrics, score
from ..store import Store


def _bots(store: Store) -> list:
    return store.db.execute("SELECT * FROM bots ORDER BY id").fetchall()


def overview(store: Store, cfg: ArenaConfig) -> dict:
    db = store.db
    span = db.execute("SELECT MIN(day) AS a, MAX(day) AS b, COUNT(DISTINCT day) AS n FROM snapshots").fetchone()
    costs = {r["bot_id"]: [r["n"], r["fees"] or 0.0] for r in db.execute(
        "SELECT bot_id, COUNT(*) AS n, SUM(fees_cad) AS fees FROM trades GROUP BY bot_id")}
    for r in db.execute("SELECT bot_id, SUM(json_extract(payload, '$.amount_cad')) AS fees"
                        " FROM events WHERE kind = 'fee' GROUP BY bot_id"):
        costs.setdefault(r["bot_id"], [0, 0.0])[1] += r["fees"]
    board = []
    for bot in _bots(store):
        eq = store.equity_series(bot["id"])
        if eq.empty:
            continue
        m = metrics(eq, cfg.starting_cash, cfg.cash_rate)
        last_two = db.execute("SELECT rank FROM snapshots WHERE bot_id = ? AND rank IS NOT NULL"
                              " ORDER BY day DESC LIMIT 2", (bot["id"],)).fetchall()
        n, fees = costs.get(bot["id"], [0, 0.0])
        board.append({
            "id": bot["id"], "name": bot["name"], "personality": bot["personality"],
            "equity": round(float(eq.iloc[-1]), 2),
            "day_change": float(eq.iloc[-1] / eq.iloc[-2] - 1) if len(eq) > 1 else 0.0,
            "score": score(m), "metrics": asdict(m), "trades": n, "fees": round(fees, 2),
            "xp": bot["xp"], "level": bot["xp"] // 200 + 1, "aggressiveness": bot["aggressiveness"],
            "rank_move": (last_two[1]["rank"] - last_two[0]["rank"]) if len(last_two) == 2 else 0,
        })
    board.sort(key=lambda e: (-e["score"], -e["equity"]))
    return {
        "season": {"start": span["a"], "end": span["b"], "sessions": span["n"]},
        "starting_cash": cfg.starting_cash,
        "leaderboard": board,
        "events": events(store, limit=25),
    }


def equity_curves(store: Store) -> dict:
    out = {"days": [], "series": {}}
    rows = store.db.execute("SELECT bot_id, day, equity FROM snapshots ORDER BY day").fetchall()
    days = sorted({r["day"] for r in rows})
    index = {d: i for i, d in enumerate(days)}
    for r in rows:
        series = out["series"].setdefault(r["bot_id"], [None] * len(days))
        series[index[r["day"]]] = round(r["equity"], 2)
    out["days"] = days
    return out


def bot_detail(store: Store, cfg: ArenaConfig, bot_id: str) -> dict | None:
    db = store.db
    bot = store.bot_row(bot_id)
    if bot is None:
        return None
    last = db.execute("SELECT * FROM snapshots WHERE bot_id = ? ORDER BY day DESC LIMIT 1", (bot_id,)).fetchone()
    values = json.loads(last["holdings"] or "{}") if last else {}
    real = store.real_option_quotes(last["day"]) if last else {}
    positions = [{"ticker": r["ticker"], "qty": r["qty"], "cost_cad": round(r["qty"] * r["avg_cost_cad"], 2),
                  "value_cad": values.get(r["ticker"]), "opened_on": r["opened_on"], "real_price": r["ticker"] in real}
                 for r in db.execute("SELECT * FROM positions WHERE bot_id = ? ORDER BY ticker", (bot_id,))]
    eq = store.equity_series(bot_id)
    cls = bot_class(bot_id)
    current = store.get_params(bot_id)
    params = [{"name": n, "value": current.get(n, spec.default), "help": spec.help, "low": spec.low, "high": spec.high}
              for n, spec in (cls.params.items() if cls else [])]
    return {
        "params": params, "ai": bool(cls and cls.ai),
        "id": bot_id, "name": bot["name"], "personality": bot["personality"],
        "xp": bot["xp"], "level": bot["xp"] // 200 + 1, "aggressiveness": bot["aggressiveness"],
        "cash": round(last["cash"], 2) if last else cfg.starting_cash,
        "equity": round(last["equity"], 2) if last else cfg.starting_cash,
        "metrics": asdict(metrics(eq, cfg.starting_cash, cfg.cash_rate)) if not eq.empty else None,
        "positions": sorted(positions, key=lambda p: -(p["value_cad"] or 0)),
        "trades": [dict(r) for r in db.execute(
            "SELECT day, ticker, side, qty, price, currency, fees_cad, value_cad FROM trades"
            " WHERE bot_id = ? ORDER BY id DESC LIMIT 40", (bot_id,))],
        "journal": [dict(r) for r in db.execute(
            "SELECT day, text FROM journal WHERE bot_id = ? ORDER BY id DESC LIMIT 40", (bot_id,))],
    }


def replay(store: Store, cfg: ArenaConfig) -> dict:
    """Toute la saison, jour par jour, pour que l'île 3D la rejoue."""
    db = store.db
    playing = {r["bot_id"] for r in db.execute("SELECT DISTINCT bot_id FROM snapshots")}
    bots = [{"id": b["id"], "name": b["name"], "personality": b["personality"],
             "aggressiveness": b["aggressiveness"], "xp": b["xp"]} for b in _bots(store) if b["id"] in playing]
    days = [r["day"] for r in db.execute("SELECT DISTINCT day FROM snapshots ORDER BY day")]
    index = {d: i for i, d in enumerate(days)}
    equity = {b["id"]: [None] * len(days) for b in bots}
    rank = {b["id"]: [None] * len(days) for b in bots}
    scores = {b["id"]: [None] * len(days) for b in bots}
    for r in db.execute("SELECT bot_id, day, equity, rank, score FROM snapshots"):
        if r["bot_id"] in equity:
            i = index[r["day"]]
            equity[r["bot_id"]][i] = round(r["equity"], 2)
            rank[r["bot_id"]][i] = r["rank"]
            scores[r["bot_id"]][i] = r["score"]
    by_day: dict[str, list] = {}
    for r in db.execute("SELECT * FROM events WHERE kind IN ('trade', 'dividend', 'rank_change', 'coach', 'ai_coach', 'stop') ORDER BY id"):
        by_day.setdefault(r["day"], []).append({"bot_id": r["bot_id"], "kind": r["kind"], **json.loads(r["payload"] or "{}")})
    journal: dict[str, dict] = {}
    for r in db.execute("SELECT day, bot_id, text FROM journal ORDER BY id"):
        journal.setdefault(r["day"], {})[r["bot_id"]] = r["text"]
    return {"starting_cash": cfg.starting_cash, "bots": bots, "days": days, "equity": equity,
            "rank": rank, "score": scores, "events": by_day, "journal": journal}


def advice(store: Store, cfg: ArenaConfig, profile: dict, csv_text: str, price_of=None, cache_dir=None) -> dict:
    """Compare ton portefeuille au Chubby qui te ressemble. Le fichier n'est pas conservé, seul le profil l'est."""
    from ..advisor import advise, read_portfolio, yahoo_prices

    clean = {"risk": max(1, min(5, int(profile.get("risk", 3)))),
             "style": profile.get("style") if profile.get("style") in ("revenu", "croissance", "equilibre") else "equilibre",
             "horizon": profile.get("horizon") if profile.get("horizon") in ("court", "moyen", "long") else "long",
             "usd_account": bool(profile.get("usd_account"))}
    store.set_meta("profil", json.dumps(clean))
    store.commit()
    usdcad = float(store.get_meta("usdcad") or 1.37)
    holdings, skipped, reading_notes = (read_portfolio(csv_text, usdcad, price_of or yahoo_prices(cache_dir))
                                        if csv_text.strip() else ([], [], []))
    out = advise(store, cfg, clean, holdings)
    out["notes"] = reading_notes + out["notes"]
    out["holdings"] = holdings
    out["skipped"] = skipped
    out["usdcad"] = usdcad
    return out


def events(store: Store, limit: int = 50) -> list[dict]:
    rows = store.db.execute("SELECT * FROM events WHERE kind != 'leader' ORDER BY id DESC LIMIT ?",
                            (limit,)).fetchall()
    return [{"id": r["id"], "day": r["day"], "bot_id": r["bot_id"], "kind": r["kind"],
             **json.loads(r["payload"] or "{}")} for r in rows]


ACTIVITY_KINDS = ("trade", "stop", "analyse", "dividend")


def activity(store: Store, since: int | None = None, limit: int = 400, day: str | None = None) -> dict:
    """Ce que les Chubbies ont fait (achats, ventes, stops, analyses), pour que la ville le montre et le rejoue.

    Sans `since` : toute la journée `day` (par défaut la dernière journée connue). Avec `since` : seulement les
    nouveaux événements. `days` liste les journées qui ont des événements (les plus récentes d'abord).
    """
    marks = ",".join("?" * len(ACTIVITY_KINDS))
    if since is None:
        if day is None:
            live = store.get_meta("live")
            day = (json.loads(live).get("day") if live else None) or store.db.execute(
                f"SELECT MAX(day) AS d FROM events WHERE kind IN ({marks})", ACTIVITY_KINDS).fetchone()["d"]
        rows = store.db.execute(f"SELECT * FROM events WHERE day = ? AND kind IN ({marks}) ORDER BY id DESC LIMIT ?",
                                (day, *ACTIVITY_KINDS, limit)).fetchall()
    else:
        rows = store.db.execute(f"SELECT * FROM events WHERE id > ? AND kind IN ({marks}) ORDER BY id DESC LIMIT ?",
                                (since, *ACTIVITY_KINDS, limit)).fetchall()
        day = rows[0]["day"] if rows else None
    last = store.db.execute("SELECT MAX(id) AS m FROM events").fetchone()["m"] or 0
    days = [r["day"] for r in store.db.execute(
        f"SELECT DISTINCT day FROM events WHERE kind IN ({marks}) ORDER BY day DESC LIMIT 60", ACTIVITY_KINDS)]
    return {"day": day, "last_id": last, "days": days,
            "events": [{"id": r["id"], "day": r["day"], "bot_id": r["bot_id"], "kind": r["kind"],
                        **json.loads(r["payload"] or "{}")} for r in reversed(rows)]}
