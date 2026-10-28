"""Le capital de chaque Chubby en ce moment : la seule source de ce chiffre pour toutes les vues.

Tableau, classement, fiche, direct et ville lisent tous ce calcul, pour afficher partout la même valeur :

- pendant (et juste après) la bourse, tant que la séance du soir n'est pas jouée : le calcul du direct (live.pulse),
  sur les vrais cours ;
- une fois la séance du soir jouée : la valeur de clôture inscrite par l'arène ;
- si un ordre a été passé depuis l'un ou l'autre, on repart du compte (cash et quantités à jour), chaque titre au
  dernier cours connu, ou à son coût s'il vient d'être acheté et n'a pas encore de cours.

Le cash et les quantités viennent toujours du compte : c'est lui qui change à chaque ordre.
"""

from __future__ import annotations

import json

from .store import Store


def current(store: Store, starting_cash: float = 10_000.0) -> dict[str, dict]:
    """{bot_id: {equity, cash, values {titre: valeur CAD}, base, day_change, change_cad, as_of, live}}."""
    from .live import latest

    live = latest(store) or {}
    ok = bool(live.get("ok") and live.get("day"))
    entries = {e["id"]: e for e in live.get("bots", [])} if ok else {}
    seen = live.get("trade_id", -1)
    out = {}
    for row in store.db.execute("SELECT id FROM bots ORDER BY id").fetchall():
        bot_id = row["id"]
        acct = store.load_account(bot_id)
        snaps = store.db.execute("SELECT day, equity, cash, holdings FROM snapshots WHERE bot_id = ? ORDER BY day DESC LIMIT 2",
                                 (bot_id,)).fetchall()
        snap = snaps[0] if snaps else None
        e = entries.get(bot_id)
        use_live = e is not None and not e.get("settled") and (snap is None or live["day"] > snap["day"])
        if use_live:
            changed = {r["ticker"] for r in store.db.execute(
                "SELECT DISTINCT ticker FROM trades WHERE bot_id = ? AND id > ?", (bot_id, seen))}
            units = {p["ticker"]: p["value_cad"] / p["qty"] for p in e.get("positions", []) if p.get("qty")}
            known, base, as_of = {}, e.get("base"), f"{live['day']} {live.get('as_of', '')}".strip()
            if not changed and "cash" in e:
                out[bot_id] = _entry(e["equity"], e["cash"], {p["ticker"]: p["value_cad"] for p in e.get("positions", [])},
                                     base, as_of, True)
                continue
        else:
            changed = {r["ticker"] for r in store.db.execute(
                "SELECT DISTINCT ticker FROM trades WHERE bot_id = ? AND day > ?", (bot_id, snap["day"] if snap else ""))}
            units = {}
            known = json.loads(snap["holdings"] or "{}") if snap else {}
            base = snaps[1]["equity"] if len(snaps) > 1 else starting_cash
            as_of = snap["day"] if snap else None
            if snap is not None and not changed:
                out[bot_id] = _entry(snap["equity"], snap["cash"], known, base, as_of, False)
                continue
        values = {}
        for t, pos in acct.positions.items():
            if t in units:
                values[t] = pos.qty * units[t]
            elif t in known and t not in changed:
                values[t] = known[t]
            else:
                values[t] = pos.qty * pos.avg_cost_cad  # tout juste acheté : à son coût, en attendant un cours
        equity = acct.cash + sum(values.values())
        out[bot_id] = _entry(equity, acct.cash, values, base if base else equity, as_of, use_live)
    return out


def _entry(equity: float, cash: float, values: dict, base: float | None, as_of, live: bool) -> dict:
    return {"equity": round(equity, 2), "cash": round(cash, 2), "values": {t: round(v, 2) for t, v in values.items()},
            "base": round(base, 2) if base else None,
            "day_change": round(equity / base - 1, 5) if base else None,
            "change_cad": round(equity - base, 2) if base else None, "as_of": as_of, "live": live}


def version(store: Store) -> str:
    """Change dès que quelque chose bouge (ordre, événement, cours du direct, séance du soir) : les pages se rafraîchissent."""
    db = store.db
    trade = db.execute("SELECT MAX(id) AS n FROM trades").fetchone()["n"] or 0
    event = db.execute("SELECT MAX(id) AS n FROM events").fetchone()["n"] or 0
    live = (json.loads(store.get_meta("live") or "{}")).get("time", "")
    return f"{trade}-{event}-{live}-{store.get_meta('last_day') or ''}"
