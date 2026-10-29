"""La fiche complète d'un Chubby : sa façon de penser, ses chiffres, chaque aller-retour sur un titre,
son bilan mois par mois et tout ce qu'il a fait, jour par jour, dans l'ordre de la séance."""

from __future__ import annotations

import inspect
import json
import re
from collections import defaultdict
from dataclasses import asdict

import pandas as pd

from . import options as opt
from .bots import bot_class
from .config import ArenaConfig, currency_of
from .fmt import french
from .scoring import metrics, score
from .store import Store


def round_trips(trades: list[dict]) -> tuple[list[dict], dict[str, dict]]:
    """Regroupe les trades en allers-retours : de l'ouverture d'une position jusqu'à ce qu'elle revienne à zéro.

    Le gain d'un aller-retour est tout l'argent rentré moins tout l'argent sorti, frais inclus (dividendes exclus).
    Une option exercée ferme l'aller-retour de l'option et ouvre (put) ou ferme (call) celui des actions.
    Renvoie les allers-retours terminés et ceux encore ouverts.
    """
    state: dict[str, dict] = {}
    done: list[dict] = []

    def move(ticker, day, qty, cash):
        st = state.setdefault(ticker, {"ticker": ticker, "start": day, "qty": 0, "cash": 0.0, "invested": 0.0, "trades": 0})
        st["qty"] += qty
        st["cash"] += cash
        st["invested"] += max(-cash, 0.0)
        st["trades"] += 1
        if st["qty"] == 0:
            pnl = st["cash"]
            base = st["invested"] or abs(pnl) or 1.0
            done.append({"ticker": ticker, "start": st["start"], "end": day, "pnl_cad": round(pnl, 2),
                         "return": round(pnl / base, 4), "trades": st["trades"],
                         "days": (pd.Timestamp(day) - pd.Timestamp(st["start"])).days})
            del state[ticker]

    for t in trades:
        tk, day, q, v, side = t["ticker"], t["day"], t["qty"], t["value_cad"], t["side"]
        if side == "buy":
            move(tk, day, q, -v)
        elif side == "sell":
            move(tk, day, -q, v)
        elif side == "expire":
            held = state.get(tk, {}).get("qty", 0)
            move(tk, day, -q if held > 0 else q, v if held > 0 else 0.0)
        elif side == "assign":
            underlying, kind, _, _ = opt.parse(tk)
            move(tk, day, q, 0.0)  # l'option vendue disparaît
            shares = q * opt.MULTIPLIER
            if kind == "P":
                move(underlying, day, shares, -v)  # on achète les actions au strike
            else:
                move(underlying, day, -shares, v)  # nos actions partent au strike
    return done, state


def _monthly(store: Store, bot_id: str, starting_cash: float) -> list[dict]:
    db = store.db
    eq = store.equity_series(bot_id)
    if eq.empty:
        return []
    month_end = eq.groupby(eq.index.to_period("M")).last()
    trades = defaultdict(int)
    fees = defaultdict(float)
    for r in db.execute("SELECT substr(day, 1, 7) AS m, COUNT(*) AS n, SUM(fees_cad) AS f FROM trades WHERE bot_id = ? GROUP BY m",
                        (bot_id,)):
        trades[r["m"]], fees[r["m"]] = r["n"], r["f"] or 0.0
    income = defaultdict(float)
    for r in db.execute("SELECT substr(day, 1, 7) AS m, kind, SUM(json_extract(payload, '$.amount_cad')) AS a FROM events"
                        " WHERE bot_id = ? AND kind IN ('dividend', 'fee') GROUP BY m, kind", (bot_id,)):
        if r["kind"] == "dividend":
            income[r["m"]] += r["a"] or 0.0
        else:
            fees[r["m"]] += r["a"] or 0.0
    out, prev = [], starting_cash
    for period, value in month_end.items():
        m = str(period)
        out.append({"month": m, "equity": round(float(value), 2), "return": round(float(value) / prev - 1, 4),
                    "trades": trades[m], "dividends_cad": round(income[m], 2), "fees_cad": round(fees[m], 2)})
        prev = float(value)
    return out[::-1]


def _timeline(store: Store, bot_id: str) -> list[dict]:
    """Chaque jour dans l'ordre d'une séance : l'ouverture (ordres de la veille), la séance (stops),
    la clôture (dividendes, frais, classement) et le soir (ce qu'il a décidé et écrit, le coaching)."""
    db = store.db
    days: dict[str, dict] = defaultdict(lambda: {"open": [], "session": [], "close": [], "evening": []})
    stop_keys = set()
    for r in db.execute("SELECT * FROM events WHERE bot_id = ? AND kind != 'leader' ORDER BY id", (bot_id,)):
        e = {"kind": r["kind"], **json.loads(r["payload"] or "{}")}
        if r["kind"] == "stop":
            stop_keys.add((r["day"], e.get("ticker")))
            days[r["day"]]["session"].append(e)
        elif r["kind"] in ("dividend", "fee", "rank_change"):
            days[r["day"]]["close"].append(e)
        elif r["kind"] in ("coach", "ai_coach", "error"):
            days[r["day"]]["evening"].append(e)
    for r in db.execute("SELECT * FROM trades WHERE bot_id = ? ORDER BY id", (bot_id,)):
        t = {"kind": "trade", "ticker": r["ticker"], "side": r["side"], "qty": r["qty"], "price": r["price"],
             "currency": r["currency"], "fees_cad": round(r["fees_cad"] or 0.0, 2), "value_cad": round(r["value_cad"], 2)}
        if (r["day"], r["ticker"]) in stop_keys:
            continue  # déjà raconté par l'événement « stop » de la séance
        if (r["time"] or "") > "09:30":  # passé en direct, pendant la séance
            days[r["day"]]["session"].append({**t, "time": r["time"]})
            continue
        days[r["day"]]["close" if r["side"] in ("expire", "assign") else "open"].append(t)
    for r in db.execute("SELECT day, text FROM journal WHERE bot_id = ? ORDER BY id", (bot_id,)):
        days[r["day"]]["evening"].append({"kind": "journal", "text": r["text"]})
    snaps = {r["day"]: r for r in db.execute("SELECT day, equity, cash, rank, score FROM snapshots WHERE bot_id = ?", (bot_id,))}
    ordered = sorted(set(days) | set(snaps))
    out, prev_eq, prev_day = [], None, None
    for d in ordered:
        s = snaps.get(d)
        entry = {"day": d, **days.get(d, {"open": [], "session": [], "close": [], "evening": []})}
        if s:
            entry.update(equity=round(s["equity"], 2), cash=round(s["cash"], 2), rank=s["rank"], score=s["score"],
                         change=round(s["equity"] / prev_eq - 1, 4) if prev_eq else None)
            prev_eq = s["equity"]
        if entry["open"] and prev_day:
            entry["decided_on"] = prev_day  # ces ordres ont été décidés le soir de la séance précédente
        if s:
            prev_day = d
        if entry["open"] or entry["session"] or entry["close"] or entry["evening"] or s:
            out.append(entry)
    return out[::-1]


def _activity(store: Store, bot_id: str, limit: int = 40) -> list[dict]:
    """Les derniers ordres, avec leurs détails comme sur un relevé de courtier."""
    db = store.db
    days = [r["day"] for r in db.execute("SELECT day FROM snapshots WHERE bot_id = ? ORDER BY day", (bot_id,))]
    previous = {d: days[i - 1] for i, d in enumerate(days) if i}
    stops = {(r["day"], json.loads(r["payload"])["ticker"]): json.loads(r["payload"]) for r in
             db.execute("SELECT day, payload FROM events WHERE bot_id = ? AND kind = 'stop'", (bot_id,))}
    out = []
    for r in db.execute("SELECT * FROM trades WHERE bot_id = ? ORDER BY id DESC LIMIT ?", (bot_id, limit)):
        stop = stops.get((r["day"], r["ticker"]))
        if stop:
            kind = "Stop-loss (vente)" if stop["direction"] == "below" else "Stop-loss (rachat)"
            submitted = f"posé chez le courtier, seuil {stop['level']:.2f}"
            filled = f"{r['day']}, pendant la séance"
        elif r["side"] in ("expire", "assign"):
            kind = "Expiry de l'option" if r["side"] == "expire" else "Option exercée"
            submitted, filled = "automatique", f"{r['day']}, à l'expiry"
        elif (r["time"] or "") > "09:30":
            kind = "Achat en séance" if r["side"] == "buy" else "Vente en séance"
            submitted = filled = f"{r['day']} à {r['time'].replace(':', ' h ')}, pendant la séance"
        else:
            kind = "Achat au marché" if r["side"] == "buy" else "Vente au marché"
            submitted = f"{previous.get(r['day'], r['day'])}, le soir après la clôture"
            filled = f"{r['day']}, à l'ouverture"
        out.append({"day": r["day"], "ticker": r["ticker"], "side": r["side"], "kind": kind, "qty": r["qty"],
                    "price": round(r["price"], 4), "currency": r["currency"], "fx": round(r["fx"] or 1.0, 4),
                    "fees_cad": round(r["fees_cad"] or 0.0, 2), "value_cad": round(r["value_cad"], 2),
                    "submitted": french(submitted), "filled": filled})
    return out


def explanation(cls, params: dict[str, float]) -> list[str]:
    """La façon de penser du Chubby : ses règles en clair, avec la valeur actuelle de ses réglages."""
    if cls.rules:
        values = {n: spec.clamp(params.get(n, spec.default)) for n, spec in cls.params.items()}
        return [french(rule.format(**values)) for rule in cls.rules]
    doc = inspect.getdoc(cls) or ""
    return [" ".join(p.split()) for p in doc.split("\n\n") if p.strip()]


def pending_orders(store: Store, bot_id: str) -> dict | None:
    """Les ordres décidés au dernier soir, qui partiront à la prochaine ouverture.

    Un Chubby donne soit des poids visés (« 20 % en NVDA »), soit des ordres directs (« vendre 2 puts XLF »).
    Pour les poids, on traduit en achat ou vente approximatifs, au prix de la dernière clôture.
    """
    row = store.db.execute("SELECT targets, trades, decided_on FROM pending WHERE bot_id = ?", (bot_id,)).fetchone()
    if row is None:
        return None
    targets = json.loads(row["targets"] or "null")
    trades = json.loads(row["trades"] or "[]")
    last = store.db.execute("SELECT equity, holdings FROM snapshots WHERE bot_id = ? ORDER BY day DESC LIMIT 1",
                            (bot_id,)).fetchone()
    equity = last["equity"] if last else 0.0
    values = json.loads(last["holdings"] or "{}") if last else {}
    orders = []
    if targets is not None:
        for t in sorted(set(targets) | {t for t in values if values[t] > 0}):
            want, now = targets.get(t, 0.0) * equity, values.get(t, 0.0)
            gap = want - now
            if abs(gap) < 100:  # en dessous de 100 $, le courtier ignore l'ajustement
                continue
            orders.append({"ticker": t, "action": "acheter" if gap > 0 else "vendre", "amount_cad": round(abs(gap), 2),
                           "weight_now": round(now / equity, 4) if equity else 0.0,
                           "weight_target": round(targets.get(t, 0.0), 4)})
    for t, q in trades:
        orders.append({"ticker": t, "qty": abs(int(q)),
                       "action": ("acheter" if q > 0 else "vendre") + (" (ordre direct)" if not opt.is_option(t) else "")})
    orders.sort(key=lambda o: (o["action"].startswith("acheter"), -o.get("amount_cad", 0)))  # ventes d'abord, comme à l'exécution
    return {"decided_on": row["decided_on"], "orders": orders,
            "cash_target": round(1 - sum(targets.values()), 4) if targets is not None else None}


def bot_report(store: Store, cfg: ArenaConfig, bot_id: str) -> dict | None:
    bot = store.bot_row(bot_id)
    if bot is None:
        return None
    cls = bot_class(bot_id)
    db = store.db
    eq = store.equity_series(bot_id)
    m = metrics(eq, cfg.starting_cash, cfg.cash_rate) if not eq.empty else None
    usdcad = float(store.get_meta("usdcad") or 1.37)
    params = store.get_params(bot_id)

    snaps = db.execute("SELECT * FROM snapshots WHERE bot_id = ? ORDER BY day DESC LIMIT 2", (bot_id,)).fetchall()
    last = snaps[0] if snaps else None
    values = json.loads(last["holdings"] or "{}") if last else {}
    before = json.loads(snaps[1]["holdings"] or "{}") if len(snaps) > 1 else {}
    acct = store.load_account(bot_id)
    fx = lambda t: usdcad if currency_of(t) == "USD" else 1.0  # noqa: E731
    stops = cls().stops(acct.positions, params, fx) if cls else {}
    from .capital import current
    now = current(store, cfg.starting_cash).get(bot_id)  # la même valeur que le tableau, le classement et la ville
    equity = now["equity"] if now else cfg.starting_cash
    values = now["values"] if now else values
    positions = []
    for t, p in sorted(acct.positions.items()):
        value = values.get(t)
        cost = p.qty * p.avg_cost_cad
        positions.append({
            "ticker": t, "qty": p.qty, "cost_cad": round(cost, 2), "value_cad": value, "opened_on": p.opened_on,
            "weight": round(value / equity, 4) if value is not None and equity else None,
            # (valeur - coût) / |coût| : juste aussi pour une option vendue, dont le coût et la valeur sont négatifs
            "gain": round((value - cost) / abs(cost), 4) if value is not None and cost else None,
            # variation de la dernière séance, si la position était déjà là la veille (à quantité égale)
            "day_change": round((value - before[t]) / abs(before[t]), 4)
            if value is not None and before.get(t) and last and p.opened_on < last["day"] and not (now and now["live"]) else None,
            "unit_cost": round(p.avg_cost_cad / (fx(t) * (opt.MULTIPLIER if opt.is_option(t) else 1)), 2),
            "currency": currency_of(t),
            "stop": {"level": stops[t][0], "direction": stops[t][1]} if t in stops else None,
        })
    positions.sort(key=lambda x: -abs(x["value_cad"] or 0))

    trades = [dict(r) for r in db.execute("SELECT day, ticker, side, qty, price, currency, fees_cad, value_cad"
                                          " FROM trades WHERE bot_id = ? ORDER BY id", (bot_id,))]
    closed, still_open = round_trips(trades)
    wins = [r for r in closed if r["pnl_cad"] > 0]
    event_sum = lambda kind: db.execute(  # noqa: E731
        "SELECT COALESCE(SUM(json_extract(payload, '$.amount_cad')), 0) AS a, COUNT(*) AS n FROM events WHERE bot_id = ? AND kind = ?",
        (bot_id, kind)).fetchone()
    div, fee = event_sum("dividend"), event_sum("fee")
    stats = {
        "trades": len(trades), "round_trips": len(closed),
        "win_rate": round(len(wins) / len(closed), 4) if closed else None,
        "realized_cad": round(sum(r["pnl_cad"] for r in closed), 2),
        "best": max(closed, key=lambda r: r["pnl_cad"]) if closed else None,
        "worst": min(closed, key=lambda r: r["pnl_cad"]) if closed else None,
        "avg_days": round(sum(r["days"] for r in closed) / len(closed), 1) if closed else None,
        "trading_fees_cad": round(sum(t["fees_cad"] or 0 for t in trades), 2),
        "account_fees_cad": round(fee["a"], 2), "dividends_cad": round(div["a"], 2), "dividend_count": div["n"],
        "stops": db.execute("SELECT COUNT(*) FROM events WHERE bot_id = ? AND kind = 'stop'", (bot_id,)).fetchone()[0],
        "coachings": db.execute("SELECT COUNT(*) FROM events WHERE bot_id = ? AND kind IN ('coach', 'ai_coach')",
                                (bot_id,)).fetchone()[0],
    }
    twin_id = bot_id.removesuffix("-ia") if bot_id.endswith("-ia") else bot_id + "-ia"
    twin = store.bot_row(twin_id)
    twin_eq = store.equity_series(twin_id) if twin else pd.Series(dtype=float)
    peak = eq.cummax() if not eq.empty else eq
    return {
        "id": bot_id, "name": bot["name"], "personality": bot["personality"], "ai": bool(cls and cls.ai),
        "xp": bot["xp"], "level": bot["xp"] // 200 + 1, "aggressiveness": bot["aggressiveness"],
        "explanation": explanation(cls, params) if cls else [],
        "account": "Compte en USD (forfait de 10 $ par mois)" if cls and cls.usd_account and cfg.usd_account
                   else "Compte en CAD (1,5 % de conversion sur les titres américains)",
        "universe": len(cls.universe) if cls else 0,
        "params": [{"name": n, "value": spec.clamp(params.get(n, spec.default)), "default": spec.default,
                    "low": spec.low, "high": spec.high, "help": spec.help} for n, spec in (cls.params.items() if cls else [])],
        "starting_cash": cfg.starting_cash,
        "equity": round(equity, 2), "cash": now["cash"] if now else cfg.starting_cash,
        "live": bool(now and now["live"]), "as_of": now["as_of"] if now else None,
        "change_cad": now["change_cad"] if now else None,
        "score": score(m) if m else None,
        "metrics": {**asdict(m), "total_return": equity / cfg.starting_cash - 1} if m else None,
        "rank": last["rank"] if last else None,
        "curve": {"days": [str(d.date()) for d in eq.index], "equity": [round(float(v), 2) for v in eq],
                  "drawdown": [round(float(v), 4) for v in (eq / peak - 1)] if not eq.empty else [],
                  "twin": {"id": twin_id, "name": twin["name"],
                           "equity": [None if pd.isna(v) else round(float(v), 2) for v in twin_eq.reindex(eq.index)]}
                  if twin and not twin_eq.empty else None},
        "positions": positions, "stats": stats, "pending": pending_orders(store, bot_id),
        "watchlist": store.get_watchlist(bot_id),
        "day_change": now["day_change"] if now and (now["live"] or len(snaps) > 1) else None,
        "activity": _activity(store, bot_id),
        "round_trips": closed[::-1],
        "open_trips": [{"ticker": s["ticker"], "start": s["start"], "cash_cad": round(s["cash"], 2)} for s in still_open.values()],
        "monthly": _monthly(store, bot_id, cfg.starting_cash),
        "timeline": _timeline(store, bot_id),
    }
