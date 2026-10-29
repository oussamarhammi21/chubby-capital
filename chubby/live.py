"""L'arène en direct pendant la séance : la valeur de chaque Chubby minute après minute, et ses réactions.

Les Chubbies tradent pendant la séance (voir intraday.py) et décident aussi le soir, avec les clôtures. Ici, on
suit leurs positions sur les vrais cours (barres de 5 minutes de Yahoo Finance) : qui gagne, qui perd, quel titre
les fait bouger, ce qu'ils viennent d'acheter ou de vendre, et ce qu'ils en disent selon leur personnalité.
"""

from __future__ import annotations

import json
import logging
import zlib
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from . import options as opt
from .broker import Account, Position
from .config import currency_of
from .fmt import french
from .store import Store

STOP_WATCH = 0.03  # on montre un stop quand le prix en est à moins de 3 %

log = logging.getLogger(__name__)
TORONTO = ZoneInfo("America/Toronto")
INDEXES = {"XIC.TO": "TSX", "SPY": "S&P 500", "QQQ": "Nasdaq"}
FX = "CAD=X"


def market_open(now: datetime) -> bool:
    """Heures de la Bourse de Toronto et de New York (9 h 30 à 16 h, heure de Toronto), en semaine."""
    now = now.astimezone(TORONTO)
    minutes = now.hour * 60 + now.minute
    return now.weekday() < 5 and 9 * 60 + 30 <= minutes < 16 * 60


def yahoo_bars(tickers: list[str]) -> pd.DataFrame:
    """Clôtures des barres de 5 minutes des derniers jours : une colonne par ticker, heure de Toronto."""
    import warnings

    import yfinance as yf

    warnings.filterwarnings("ignore", module=r"yfinance(\..*)?")
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    df = yf.download(tickers, period="5d", interval="5m", progress=False, auto_adjust=False, threads=True)
    if df is None or df.empty:
        return pd.DataFrame()
    close = df["Close"]
    if isinstance(close, pd.Series):
        close = close.to_frame(tickers[0])
    close.index = pd.DatetimeIndex(close.index).tz_convert(TORONTO)
    return close.sort_index()


# Ce que chacun dit selon sa journée. {t} est le titre qui le fait le plus bouger, {m} sa variation.
REACTIONS = {
    "tortue": {
        "big_up": ["Belle journée pour l'indice ! La patience paie.", "Tout monte, et je n'ai rien eu à faire."],
        "up": ["Ça monte doucement. Je ne touche à rien.", "Un petit pas de plus. Lentement mais sûrement."],
        "flat": ["Journée tranquille. Je reste dans ma carapace.", "Rien à signaler, je garde mes deux FNB."],
        "down": ["Le marché recule, mais je ne vends jamais sur un coup de tête.", "Un peu de rouge. Ça passera."],
        "big_down": ["Grosse journée rouge. Je rentre dans ma carapace et j'attends.", "Ça tangue ! Je ne bouge pas."],
    },
    "flan": {
        "big_up": ["{t} {m} : mes rentiers sont en forme !", "Belle hausse, et mes dividendes continuent de tomber."],
        "up": ["{t} {m}, mes dividendes valent un peu plus aujourd'hui.", "Doucement vers le haut, comme j'aime."],
        "flat": ["Tranquille. Je compte mes prochains dividendes.", "Une journée sans histoire, parfaite pour un rentier."],
        "down": ["{t} {m}, mais le dividende tombe quand même.", "Le prix baisse, le rendement monte. Je reste."],
        "big_down": ["{t} {m}… tant qu'ils ne coupent pas le dividende, je garde.", "Journée difficile, mais je vis de mes revenus."],
    },
    "dumpling": {
        "big_up": ["Tout mon mélange monte, {t} en tête ({m}) !", "Belle journée pour la diversification."],
        "up": ["Mon mélange tient bien : {t} {m}.", "Un peu de tout, un peu de hausse."],
        "flat": ["Équilibre parfait aujourd'hui.", "Mes FNB se compensent : rien ne bouge."],
        "down": ["{t} {m}, mais mes obligations et mon or amortissent.", "Petit recul. C'est pour ça qu'on diversifie."],
        "big_down": ["Ouch, {t} {m}. Si un poids dérive trop, je rebalance.", "Grosse baisse, mais pas tout au même endroit."],
    },
    "mochi": {
        "big_up": ["{t} {m} ! Le trend est mon ami !", "Mes champions s'envolent, {t} {m} !"],
        "up": ["{t} {m}, le trend continue.", "Mes titres forts restent forts."],
        "flat": ["Le marché hésite. J'attends que le trend se décide.", "Pas de trend aujourd'hui."],
        "down": ["{t} {m}… s'il casse sa SMA 50, je coupe ce soir.", "Petite pause dans le trend."],
        "big_down": ["{t} {m} ! Je surveille mes stops pour ce soir.", "Aïe, le trend se retourne ?"],
    },
    "muffin": {
        "big_up": ["Le rebond est là : {t} {m} !", "Mes aubaines remontent, {t} {m} !"],
        "up": ["{t} {m}. Acheté en solde, revendu au rebond.", "Ça rebondit gentiment."],
        "flat": ["Je cherche des titres oversold…", "Pas d'aubaine en vue pour l'instant."],
        "down": ["{t} {m} : ça devient peut-être une aubaine…", "Ça baisse ? Je prépare ma liste de courses."],
        "big_down": ["{t} {m}… mon stop à -8 % n'est pas loin.", "Les soldes sont là, mais attention aux couteaux qui tombent."],
    },
    "jalapeno": {
        "big_up": ["WOUHOU ! {t} {m}, mes options s'envolent !", "Effet de levier activé : {t} {m} !"],
        "up": ["{t} {m}, mes calls aiment ça.", "Ça chauffe gentiment !"],
        "flat": ["Le marché dort, et mes options perdent un peu de valeur chaque jour.", "Allez, bouge ! Le temps joue contre moi."],
        "down": ["Aïe, {t} {m}… mes options fondent vite.", "Ça pique un peu aujourd'hui."],
        "big_down": ["{t} {m} ! Mon stop à -50 % approche…", "C'est le grand huit, et je suis devant !"],
    },
    "jalapeno-v2": {
        "big_up": ["Le marché monte : mes puts vendus perdent de la valeur, tant mieux pour moi !", "Belle journée pour un vendeur de premium."],
        "up": ["Calme ou hausse : mes premiums fondent dans ma poche.", "Chaque jour qui passe me rapporte un peu de premium."],
        "flat": ["Rien ne bouge, et le temps travaille pour moi.", "Journée parfaite pour un vendeur d'options."],
        "down": ["{t} {m}… tant que ça reste au-dessus de mon strike, ça va.", "Un peu de baisse, rien de grave pour la roue."],
        "big_down": ["{t} {m} ! Si on m'exerce, j'achèterai les actions et je vendrai des calls.", "Gros recul : je surveille mon stop."],
    },
    "jalapeno-v3": {
        "big_up": ["{t} {m} ! Mes calls longs adorent ça, et je ne bouge pas.", "Effet de levier, mais avec patience !"],
        "up": ["{t} {m}. Mon option suit l'indice tranquillement.", "Doucement vers le haut, comme prévu."],
        "flat": ["Journée calme : mon option longue ne perd presque rien à attendre.", "Je suis patient, moi, maintenant."],
        "down": ["{t} {m}… tant que le Nasdaq tient sa SMA 200, je garde.", "Petit recul, mon expiry est loin."],
        "big_down": ["{t} {m} ! Si le Nasdaq casse sa SMA 200, je range tout au cash.", "Ça secoue, mais mon plan est clair."],
    },
    "matcha": {
        "big_up": ["Mon modèle avait vu juste : {t} {m} !", "Les données ne mentent pas, {t} {m}."],
        "up": ["{t} {m}, conforme à mes prédictions.", "Signal confirmé, on monte."],
        "flat": ["Peu de signal aujourd'hui. Je collecte des données.", "Bruit de marché, rien à apprendre."],
        "down": ["{t} {m}. Je note ça pour mon prochain entraînement.", "Erreur de prédiction, j'ajuste mes poids le mois prochain."],
        "big_down": ["{t} {m} : un cas que mon modèle n'avait pas vu venir.", "Grosse erreur, belle leçon pour mon prochain entraînement."],
    },
}
GENERIC = {"big_up": ["Superbe journée !"], "up": ["Ça monte."], "flat": ["Journée calme."],
           "down": ["Ça baisse un peu."], "big_down": ["Journée difficile."]}
ALL_CASH = "Je suis au cash, je regarde le marché bouger."


def mood_of(change: float) -> str:
    return "content" if change > 0.005 else "inquiet" if change < -0.005 else "calme"


def react(bot_id: str, change: float, mover: dict | None, now: datetime) -> str:
    """Une phrase selon sa personnalité. Elle change toutes les 30 minutes, pas à chaque rafraîchissement."""
    if mover is None:
        return ALL_CASH
    level = ("big_up" if change > 0.01 else "up" if change > 0.002 else "big_down" if change < -0.01
             else "down" if change < -0.002 else "flat")
    lines = REACTIONS.get(bot_id.removesuffix("-ia"), GENERIC)[level]
    pick = zlib.crc32(f"{bot_id}{now.date()}{(now.hour * 60 + now.minute) // 30}".encode()) % len(lines)
    move = f"{mover['change'] * 100:+.1f} %".replace(".", ",").replace("-", "−")
    text = lines[pick].format(t=mover["name"], m=move)
    if bot_id.endswith("-ia") and abs(change) > 0.01:
        text += " J'en parlerai à Claude vendredi."
    return text


def _last_two_days(bars: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Barres du dernier jour de bourse, et dernière clôture connue de la veille pour chaque ticker."""
    days = pd.Index(bars.index.date)
    today = days[-1]
    prev = bars[days < today].ffill().iloc[-1] if (days < today).any() else pd.Series(dtype=float)
    session = bars[days == today]
    session = session[[t.hour * 60 + t.minute >= 9 * 60 + 30 and t.hour < 16 for t in session.index]]
    return session.ffill(), prev


def pulse(store: Store, fetch=yahoo_bars, chains=None, now: datetime | None = None) -> dict:
    """Calcule la séance en direct de chaque Chubby et la garde dans la base (clé « live »)."""
    now = (now or datetime.now(TORONTO)).astimezone(TORONTO)
    bots = store.db.execute("SELECT id, name, personality FROM bots ORDER BY id").fetchall()
    trade_id = store.db.execute("SELECT MAX(id) AS n FROM trades").fetchone()["n"] or 0  # ordres déjà comptés ici
    accounts = {b["id"]: store.load_account(b["id"]) for b in bots}
    stocks = {t for a in accounts.values() for t in a.positions if not opt.is_option(t)}
    stocks |= {r["ticker"] for r in store.db.execute("SELECT DISTINCT ticker FROM trades WHERE day = ? AND time IS NOT NULL",
                                                      (str(now.date()),)) if not opt.is_option(r["ticker"])}
    from .report import pending_orders
    pending = {b["id"]: pending_orders(store, b["id"]) for b in bots}
    watched = {b["id"]: [i["ticker"] for i in (store.get_watchlist(b["id"]) or {}).get("items", [])
                         if not opt.is_option(i["ticker"])] for b in bots}
    for bot_id, p in pending.items():  # les titres de ses ordres de demain : il les regarde aussi
        watched[bot_id] += [o["ticker"] for o in (p or {}).get("orders", []) if not opt.is_option(o["ticker"])]
    bars = fetch(sorted(stocks | {t for w in watched.values() for t in w} | set(INDEXES) | {FX}))
    if bars is None or bars.empty:
        payload = {"time": now.isoformat(timespec="minutes"), "open": market_open(now), "ok": False,
                   "error": "Pas de cours en direct pour l'instant (réseau ou Yahoo indisponible)."}
        store.set_meta("live", json.dumps(payload, ensure_ascii=False))
        store.commit()
        return payload
    session, prev = _last_two_days(bars)
    if session.empty:
        session = bars.ffill().iloc[[-1]]
    day = session.index[-1].date()
    fx_now = float(store.get_meta("usdcad") or 1.37)
    fx = session[FX].fillna(prev.get(FX, fx_now)) if FX in session else pd.Series(fx_now, index=session.index)
    fx = fx.fillna(fx_now)

    from .intraday import status as trading_status, trades_today
    trading = trading_status(store)
    intents = (trading or {}).get("intents", {}) if (trading or {}).get("day") == str(day) else {}
    out_bots = []
    for b in bots:
        base_row = store.db.execute("SELECT day, equity, holdings FROM snapshots WHERE bot_id = ? AND day < ?"
                                    " ORDER BY day DESC LIMIT 1", (b["id"], str(day))).fetchone()
        today_row = store.db.execute("SELECT equity FROM snapshots WHERE bot_id = ? AND day = ?",
                                     (b["id"], str(day))).fetchone()
        if market_open(now) and day == now.date():
            today_row = None  # bourse ouverte : une valeur de clôture du jour ne peut pas encore exister (séance jouée trop tôt)
        acct = accounts[b["id"]]
        # Les ordres d'hier soir sont partis à l'ouverture de ce matin, mais l'arène ne les inscrit qu'au soir :
        # en attendant, on les compte au premier cours de la séance.
        morning = {}
        if today_row is None and pending.get(b["id"]) and pending[b["id"]]["decided_on"] < str(day):
            held_acct, morning = _morning_fills(acct, pending[b["id"]], session, fx, str(day))
        else:
            held_acct = acct
        # Premier jour de l'arène : pas de veille, il part de son capital de départ (tout en cash).
        held = json.loads(base_row["holdings"] or "{}") if base_row else {}
        # Les ordres passés en direct changent le cash et les quantités en cours de journée : la courbe en tient compte.
        curve, qty_paths, traded_unit = _intraday_paths(store, b["id"], str(day), session, held_acct)
        movers, held_now = [], []
        for t, qty in qty_paths.items():
            usd = currency_of(t) == "USD"
            now_qty = float(qty.iloc[-1])
            if not opt.is_option(t) and t in session and session[t].notna().any():
                px = session[t].ffill().bfill()
                value = qty * px * (fx if usd else 1.0)
                start = prev.get(t)
                change = float(px.iloc[-1] / start - 1) if start and not pd.isna(start) else None
                impact = float(now_qty * (px.iloc[-1] - start) * (fx.iloc[-1] if usd else 1.0)) if change is not None else 0.0
            else:
                quote = chains.quote(t) if chains is not None and opt.is_option(t) else None
                last = held.get(t, 0.0)
                if quote:
                    unit = quote * opt.MULTIPLIER * (fx.iloc[-1] if usd else 1.0)
                elif last and t in held_acct.positions:
                    unit = last / held_acct.positions[t].qty
                else:
                    unit = traded_unit.get(t, 0.0)
                value = qty * unit
                now_value = now_qty * unit
                change = (now_value / last - 1) if quote and last else None
                impact = now_value - last if quote else 0.0
            curve = curve + value
            if now_qty:
                held_now.append({"ticker": t, "qty": now_qty, "value_cad": round(float(value.iloc[-1]), 2)})
            if change is not None and now_qty:
                movers.append({"ticker": t, "name": opt.describe(t) if opt.is_option(t) else t,
                               "change": round(change, 4), "impact_cad": round(impact, 2)})
        holdings_live = {m["ticker"]: m["change"] for m in movers}
        movers.sort(key=lambda m: -abs(m["impact_cad"]))
        stops = _watch_stops(b["id"], acct, store.get_params(b["id"]), session, float(fx.iloc[-1]), chains)
        if base_row is None and today_row is None:
            base = float(curve.iloc[0])  # premier jour de l'arène, avant sa première séance du soir
        else:
            base = base_row["equity"] if base_row else (float(today_row["equity"]) if not acct.positions else acct.cash)
        equity = float(today_row["equity"]) if today_row else float(curve.iloc[-1])
        watch = store.get_watchlist(b["id"])
        radar = _radar(watch, session)
        quotes = _quotes(watched.get(b["id"], []), session, prev)
        plans = _plan_lines(pending.get(b["id"]), quotes, morning)
        musings = _watch_lines(watch, quotes)
        change = equity / base - 1 if base else 0.0
        top = movers[0] if movers else ({"name": "Mon portefeuille", "change": 0.0} if held_acct.positions else None)
        done = trades_today(store, b["id"], str(day))
        intent = intents.get(b["id"])
        recent = [_trade_line(x) for x in done[::-1] if x.get("time") and _minutes(session.index[-1]) - _minutes(x["time"]) <= 30]
        action = (_intent_line(intent) if intent else None)
        out_bots.append({
            "id": b["id"], "name": b["name"], "personality": b["personality"],
            "equity": round(equity, 2), "base": round(base, 2), "change": round(change, 5),
            "change_cad": round(equity - base, 2), "mood": mood_of(change),
            "reaction": (_stop_reaction(stops) or (recent[0] if recent else None) or action or _radar_reaction(radar)
                         or (None if held_acct.positions else (plans + musings)[0] if plans + musings else None)
                         or react(b["id"], change, top, now)),
            "movers": movers[:3],
            "stops": stops, "radar": radar,
            "thoughts": ([_trade_line(x) for x in done[::-1][:2]] + ([action] if action else [])
                         + _thoughts(stops, radar) + plans[:2] + musings[:2])[:6],
            "trades_today": done, "intent": intent,
            "holdings": holdings_live, "watch": quotes,
            "curve": [round(float(v) / base - 1, 5) if base else 0.0 for v in curve],
            "settled": today_row is not None,
            "cash": round(float(held_acct.cash), 2), "positions": held_now,
        })
    out_bots.sort(key=lambda e: -e["change"])
    indexes = []
    for t, label in INDEXES.items():
        if t in session and session[t].notna().any() and prev.get(t):
            indexes.append({"ticker": t, "name": label, "change": round(float(session[t].ffill().iloc[-1] / prev[t] - 1), 5)})
    market = []
    for t in session.columns:
        if t == FX or not session[t].notna().any() or not prev.get(t) or pd.isna(prev.get(t)):
            continue
        last = float(session[t].dropna().iloc[-1])
        market.append({"ticker": t, "last": round(last, 2), "change": round(last / prev[t] - 1, 4)})
    market.sort(key=lambda m: -abs(m["change"]))
    payload = {
        "ok": True, "time": now.isoformat(timespec="minutes"), "day": str(day), "market": market[:60], "trade_id": trade_id,
        "as_of": session.index[-1].strftime("%H:%M"), "open": market_open(now) and day == now.date(),
        "usdcad": round(float(fx.iloc[-1]), 4), "times": [t.strftime("%H:%M") for t in session.index],
        "indexes": indexes, "bots": out_bots,
        "trading": trading if (trading or {}).get("day") == str(day) else None,
    }
    store.set_meta("live", json.dumps(payload, ensure_ascii=False))
    store.commit()
    return payload


def _minutes(t) -> int:
    if not isinstance(t, str):
        t = t.strftime("%H:%M")
    return int(t[:2]) * 60 + int(t[3:5])


def _intraday_paths(store: Store, bot_id: str, day: str, session: pd.DataFrame, acct: Account):
    """Le cash et la quantité de chaque titre, barre par barre : on défait les ordres passés en direct pour les barres
    d'avant leur heure. Rend aussi, pour les titres échangés, leur valeur unitaire en CAD au moment de l'ordre."""
    stamps = [i.strftime("%H:%M") for i in session.index]
    cash = pd.Series(float(acct.cash), index=session.index)
    qty = {t: pd.Series(float(p.qty), index=session.index) for t, p in acct.positions.items()}
    unit = {}
    for r in store.db.execute("SELECT ticker, side, qty, value_cad, time FROM trades WHERE bot_id = ? AND day = ?"
                              " AND time > '09:30' AND side IN ('buy', 'sell') ORDER BY id", (bot_id, day)):
        before = [s < r["time"] for s in stamps]
        sign = 1 if r["side"] == "buy" else -1
        cash[before] += sign * r["value_cad"]
        path = qty.setdefault(r["ticker"], pd.Series(0.0, index=session.index))
        path[before] -= sign * r["qty"]
        unit.setdefault(r["ticker"], r["value_cad"] / r["qty"] if r["qty"] else 0.0)
    return cash, qty, unit


def _trade_line(x: dict) -> str:
    verb = "acheté" if x["side"] == "buy" else "vendu"
    name = opt.describe(x["ticker"]) if opt.is_option(x["ticker"]) else x["ticker"]
    when = "À l'ouverture" if x.get("time") == "09:30" else f"À {x['time'].replace(':', ' h ')}"
    qty = f"{x['qty']} " if x.get("qty") else ""
    price = f" à {x['price']:.2f} $" if x.get("price") else ""
    return french(f"{when}, j'ai {verb} {qty}{name}{price}. {x.get('why') or ''}").strip()


def _intent_line(intent: dict) -> str:
    verb = "acheter" if intent["side"] == "buy" else "vendre"
    t = intent["ticker"]
    name = opt.describe(t) if opt.is_option(t) else t
    return french(f"J'ai repéré une occasion : je veux {verb} {name}. {intent.get('why') or ''} "
                  f"Je confirme à la prochaine vérification.").replace("  ", " ")


def _radar(watch: dict | None, session: pd.DataFrame) -> list[dict]:
    """Ses seuils de décision contre le vrai cours : à quelle distance il est d'agir.

    Ceux qui le feraient agir passent devant ; ceux qui ne changeraient rien pour l'instant (« none ») restent
    visibles, à la fin, sans alerte."""
    out = []
    for item in (watch or {}).get("items", []):
        t = item["ticker"]
        trigs = item.get("triggers") or ([item["trigger"]] if item.get("trigger") else [])
        if not trigs or t not in session or not session[t].notna().any():
            continue
        prices = session[t].dropna()
        last = float(prices.iloc[-1])
        for trig in trigs:
            below = trig["direction"] == "below"
            crossed = last <= trig["level"] if below else last >= trig["level"]
            touched = float(prices.min()) <= trig["level"] if below else float(prices.max()) >= trig["level"]
            out.append({"ticker": t, "last": round(last, 2), "distance": round(trig["level"] / last - 1, 4),
                        "crossed": bool(crossed), "touched": bool(touched), "action": "trade", "at_close": False, **trig})
    return sorted(out, key=lambda x: (x["action"] == "none", not x["crossed"], abs(x["distance"])))


def _signed(v: float) -> str:
    return french(f"{v * 100:+.1f} %").replace("-", "−")


def _morning_fills(acct: Account, pending: dict, session: pd.DataFrame, fx: pd.Series, day: str):
    """Une copie du compte avec les ordres d'hier soir passés au premier cours de la séance (frais non comptés).

    Rend aussi, par titre, l'ordre passé : {"ticker": ("buy" ou "sell", quantité, prix)}. Les options attendent le soir.
    """
    positions, cash, done = dict(acct.positions), acct.cash, {}
    for o in pending.get("orders", []):
        t = o["ticker"]
        if opt.is_option(t) or t not in session or not session[t].notna().any():
            continue
        px = float(session[t].dropna().iloc[0])
        rate = float(fx.iloc[0]) if currency_of(t) == "USD" else 1.0
        have = positions[t].qty if t in positions else 0
        buy = o["action"].startswith("acheter")
        if "qty" in o:
            qty = o["qty"]
        elif not buy and not o.get("weight_target"):
            qty = have  # sortir complètement
        else:
            qty = int(o["amount_cad"] // (px * rate))
        qty = qty if buy else min(qty, have)
        if buy:
            qty = min(qty, int(max(cash, 0) // (px * rate)))
        if qty <= 0:
            continue
        cash += (-qty if buy else qty) * px * rate
        left = have + (qty if buy else -qty)
        if left:
            positions[t] = replace(positions[t], qty=left) if t in positions else Position(t, left, px * rate, day)
        else:
            positions.pop(t, None)
        done[t] = ("buy" if buy else "sell", qty, px)
    return Account(acct.bot_id, cash, positions), done


def _plan_lines(pending: dict | None, quotes: dict, morning: dict | None = None) -> list[str]:
    """Ce qu'il pense de ses ordres en voyant le marché d'aujourd'hui (les plus gros mouvements d'abord).

    Les ordres déjà passés à l'ouverture de ce matin (`morning`) sont racontés au passé.
    """
    out = []
    for o in (pending or {}).get("orders", []):
        q = quotes.get(o["ticker"])
        if not q or q["change"] is None:
            continue
        ch, t = q["change"], o["ticker"]
        if t in (morning or {}):
            side, qty, px = morning[t]
            since = q["last"] / px - 1 if q.get("last") and px else 0.0
            verb = "acheté" if side == "buy" else "vendu"
            good = since > 0.002 if side == "buy" else since < -0.002
            bad = since < -0.002 if side == "buy" else since > 0.002
            mood = "bien joué" if good else "un peu trop tôt" if bad else "pas de surprise"
            out.append((abs(since), f"Ce matin à l'ouverture, j'ai {verb} {qty} {t} à {french(f'{px:.2f}')} $. "
                                    f"Depuis : {_signed(since)}, {mood}."))
            continue
        if o["action"].startswith("acheter"):
            mood = ("ça me coûtera un peu plus cher" if ch > 0.005 else "il sera un peu moins cher, tant mieux" if ch < -0.005
                    else "rien ne bouge, parfait")
            line = f"Demain à l'ouverture, j'achète {t}. Il fait {_signed(ch)} aujourd'hui : {mood}."
        else:
            mood = "je vendrai un peu plus haut" if ch > 0.005 else "je vendrai un peu plus bas" if ch < -0.005 else "pas de surprise"
            line = f"Demain à l'ouverture, je vends {t}. Il fait {_signed(ch)} aujourd'hui : {mood}."
        out.append((abs(ch), line))
    return [line for _, line in sorted(out, reverse=True)]


def _watch_lines(watch: dict | None, quotes: dict) -> list[str]:
    """Les titres de sa watchlist qui bougent le plus aujourd'hui."""
    moves = []
    for item in (watch or {}).get("items", []):
        q = quotes.get(item["ticker"])
        if q and q["change"] is not None and abs(q["change"]) >= 0.01 and not item.get("held"):
            why = item["why"].split(" : ")[-1]
            moves.append((abs(q["change"]), f"{item['ticker']} fait {_signed(q['change'])} aujourd'hui. Je le garde à l'œil ({why})."))
    seen, out = set(), []
    for _, line in sorted(moves, reverse=True):
        if line not in seen:
            seen.add(line)
            out.append(line)
    return out


def _pct(v: float) -> str:
    return f"{abs(v) * 100:.1f} %".replace(".", ",")


def _radar_line(x: dict) -> str:
    """Un seuil en clair : où en est le cours, et ce qu'il fera (ou pas) s'il le franchit."""
    if x.get("action") == "none":
        if x["crossed"]:
            return french(f"{x['ticker']} a franchi mon {x['label']} ({x['level']:.2f}), mais ça ne change rien pour moi : {x['then']}.")
        return french(f"{x['ticker']} est à {_pct(x['distance'])} de mon {x['label']} ({x['level']:.2f}) : même là, {x['then']}.")
    if x.get("at_close"):
        when = "si ça tient à la clôture"
    else:
        when = "si c'est toujours le cas à ma prochaine vérification (dans 15 minutes au plus)"
    if x["crossed"]:
        return french(f"{x['ticker']} a franchi mon {x['label']} ({x['level']:.2f}) : {when}, {x['then']}.")
    return french(f"{x['ticker']} est à {_pct(x['distance'])} de mon {x['label']} ({x['level']:.2f}). "
                  f"S'il le franchit, {x['then']}" + (" à la clôture." if x.get("at_close") else " (après une vérification de confirmation)."))


def _radar_reaction(radar: list[dict]) -> str | None:
    hot = next((x for x in radar if x.get("action") != "none" and (x["crossed"] or abs(x["distance"]) < 0.015)), None)
    return _radar_line(hot) if hot else None


def _thoughts(stops: list[dict], radar: list[dict]) -> list[str]:
    """Ce qu'il a en tête en ce moment : ses stops proches et ses seuils les plus proches, du plus urgent au moins urgent."""
    lines = [_stop_reaction([x]) for x in stops if x["hit"] or abs(x["distance"] or 1) < STOP_WATCH]
    acting = [x for x in radar if x.get("action") != "none"]
    close = [x for x in acting if x["crossed"] or abs(x["distance"]) < 0.05] or acting[:1]  # les plus proches d'agir
    lines += [_radar_line(x) for x in close[:3]]
    return [line for line in lines if line][:4]


def _quotes(tickers: list[str], session: pd.DataFrame, prev: pd.Series) -> dict[str, dict]:
    """Dernier cours et variation du jour des titres de sa watchlist."""
    out = {}
    for t in tickers:
        if t in session and session[t].notna().any():
            last, start = float(session[t].dropna().iloc[-1]), prev.get(t)
            out[t] = {"last": round(last, 2),
                      "change": round(last / start - 1, 4) if start and not pd.isna(start) else None}
    return out


def _watch_stops(bot_id: str, acct, params: dict, session: pd.DataFrame, usdcad: float, chains) -> list[dict]:
    """Stop-loss du Chubby et distance au prix actuel. Un stop touché pendant la séance est exécuté au prix du
    stop par la séance du soir, exactement comme le ferait le courtier : ici, on le montre en direct."""
    from .bots import bot_class

    cls = bot_class(bot_id)
    if cls is None:
        return []
    levels = cls().stops(acct.positions, params, lambda t: usdcad if currency_of(t) == "USD" else 1.0)
    out = []
    for t, (level, direction) in levels.items():
        if t in session and session[t].notna().any():
            prices = session[t].dropna()
            last, extreme = float(prices.iloc[-1]), float(prices.min() if direction == "below" else prices.max())
        else:
            quote = chains.quote(t) if chains is not None and opt.is_option(t) else None
            if quote is None:
                continue
            last = extreme = quote
        hit = extreme <= level if direction == "below" else extreme >= level
        out.append({"ticker": t, "name": opt.describe(t) if opt.is_option(t) else t, "level": level,
                    "last": round(last, 2), "direction": direction, "hit": bool(hit),
                    "distance": round(level / last - 1, 4) if last else None})
    return sorted(out, key=lambda x: (not x["hit"], abs(x["distance"] or 1)))


def _stop_reaction(stops: list[dict]) -> str | None:
    hit = next((x for x in stops if x["hit"]), None)
    if hit:
        verb = "je vends" if hit["direction"] == "below" else "je rachète"
        return french(f"Mon stop-loss sur {hit['name']} est touché ! {verb.capitalize()} à {hit['level']:.2f}.")
    near = next((x for x in stops if x["distance"] is not None and abs(x["distance"]) < STOP_WATCH), None)
    if near:
        return french(f"{near['name']} n'est plus qu'à {abs(near['distance']) * 100:.1f} % de mon stop… je croise les doigts.")
    return None


def latest(store: Store) -> dict | None:
    raw = store.get_meta("live")
    return json.loads(raw) if raw else None


class Refresher:
    """Relance un calcul en direct quand le dernier date de plus de `every` secondes (pendant la bourse),
    ou quand il manque celui du jour. Sert au tableau de bord : `chubby web` suffit pour voir le direct."""

    def __init__(self, db_path: str, every: int = 240, fetch=yahoo_bars, chains_factory=None, clock=None):
        import threading
        self.db_path, self.every, self.fetch = db_path, every, fetch
        self.chains_factory = chains_factory
        self.clock = clock or (lambda: datetime.now(TORONTO))
        self.lock = threading.Lock()
        self.last: datetime | None = None

    def due(self, payload: dict | None, now: datetime) -> bool:
        if self.last is None or payload is None:
            return True
        if market_open(now):
            return (now - self.last).total_seconds() >= self.every
        return (now - self.last).total_seconds() >= 3600  # bourse fermée : une fois par heure suffit

    def __call__(self, store: Store) -> dict | None:
        now = self.clock()
        if not self.due(latest(store), now) or not self.lock.acquire(blocking=False):
            return latest(store)
        try:
            chains = self.chains_factory(now.date()) if self.chains_factory else None
            return pulse(store, self.fetch, chains, now)
        except Exception:
            log.exception("Calcul en direct impossible")
            return latest(store)
        finally:
            self.last = now
            self.lock.release()
