"""L'arène en direct pendant la séance : la valeur de chaque Chubby minute après minute, et ses réactions.

Les Chubbies décident toujours le soir, avec les clôtures (ce sont des stratégies quotidiennes). Pendant
la journée, on suit leurs positions sur les vrais cours (barres de 5 minutes de Yahoo Finance) : qui
gagne, qui perd, quel titre les fait bouger, et ce qu'ils en disent selon leur personnalité.
"""

from __future__ import annotations

import json
import logging
import zlib
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from . import options as opt
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
        "big_down": ["Ouch, {t} {m}. Si un poids dérive trop, je rééquilibre.", "Grosse baisse, mais pas tout au même endroit."],
    },
    "mochi": {
        "big_up": ["{t} {m} ! La tendance est mon amie !", "Mes champions s'envolent, {t} {m} !"],
        "up": ["{t} {m}, la tendance continue.", "Mes titres forts restent forts."],
        "flat": ["Le marché hésite. J'attends que la tendance se décide.", "Pas de tendance aujourd'hui."],
        "down": ["{t} {m}… s'il casse sa moyenne 50 jours, je coupe ce soir.", "Petite pause dans la tendance."],
        "big_down": ["{t} {m} ! Je surveille mes stops pour ce soir.", "Aïe, la tendance se retourne ?"],
    },
    "muffin": {
        "big_up": ["Le rebond est là : {t} {m} !", "Mes aubaines remontent, {t} {m} !"],
        "up": ["{t} {m}. Acheté en solde, revendu au rebond.", "Ça rebondit gentiment."],
        "flat": ["Je cherche des titres survendus…", "Pas d'aubaine en vue pour l'instant."],
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
        "big_up": ["Le marché monte : mes puts vendus perdent de la valeur, tant mieux pour moi !", "Belle journée pour un vendeur de primes."],
        "up": ["Calme ou hausse : mes primes fondent dans ma poche.", "Chaque jour qui passe me rapporte un peu de prime."],
        "flat": ["Rien ne bouge, et le temps travaille pour moi.", "Journée parfaite pour un vendeur d'options."],
        "down": ["{t} {m}… tant que ça reste au-dessus de mon prix d'exercice, ça va.", "Un peu de baisse, rien de grave pour la roue."],
        "big_down": ["{t} {m} ! Si on m'exerce, j'achèterai les actions et je vendrai des calls.", "Gros recul : je surveille mon stop."],
    },
    "jalapeno-v3": {
        "big_up": ["{t} {m} ! Mes calls longs adorent ça, et je ne bouge pas.", "Effet de levier, mais avec patience !"],
        "up": ["{t} {m}. Mon option suit l'indice tranquillement.", "Doucement vers le haut, comme prévu."],
        "flat": ["Journée calme : mon option longue ne perd presque rien à attendre.", "Je suis patient, moi, maintenant."],
        "down": ["{t} {m}… tant que le Nasdaq tient sa moyenne 200 jours, je garde.", "Petit recul, mon échéance est loin."],
        "big_down": ["{t} {m} ! Si le Nasdaq casse sa moyenne 200 jours, je range tout au cash.", "Ça secoue, mais mon plan est clair."],
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
    accounts = {b["id"]: store.load_account(b["id"]) for b in bots}
    stocks = {t for a in accounts.values() for t in a.positions if not opt.is_option(t)}
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

    out_bots = []
    for b in bots:
        base_row = store.db.execute("SELECT day, equity, holdings FROM snapshots WHERE bot_id = ? AND day < ?"
                                    " ORDER BY day DESC LIMIT 1", (b["id"], str(day))).fetchone()
        today_row = store.db.execute("SELECT equity FROM snapshots WHERE bot_id = ? AND day = ?",
                                     (b["id"], str(day))).fetchone()
        acct = accounts[b["id"]]
        if base_row is None and today_row is None:
            continue  # pas encore inscrit dans l'arène
        # Premier jour de l'arène : pas de veille, il part de son capital de départ (tout en cash).
        held = json.loads(base_row["holdings"] or "{}") if base_row else {}
        curve = pd.Series(acct.cash, index=session.index, dtype=float)
        movers = []
        for t, pos in acct.positions.items():
            usd = currency_of(t) == "USD"
            if not opt.is_option(t) and t in session and session[t].notna().any():
                px = session[t].ffill().bfill()
                value = pos.qty * px * (fx if usd else 1.0)
                start = prev.get(t)
                change = float(px.iloc[-1] / start - 1) if start and not pd.isna(start) else None
                impact = float(pos.qty * (px.iloc[-1] - start) * (fx.iloc[-1] if usd else 1.0)) if change is not None else 0.0
            else:
                quote = chains.quote(t) if chains is not None and opt.is_option(t) else None
                last = held.get(t, 0.0)
                now_value = pos.qty * quote * opt.MULTIPLIER * (fx.iloc[-1] if usd else 1.0) if quote else last
                value = pd.Series(now_value, index=session.index)
                change = (now_value / last - 1) if quote and last else None
                impact = now_value - last if quote else 0.0
            curve = curve + value
            if change is not None:
                movers.append({"ticker": t, "name": opt.describe(t) if opt.is_option(t) else t,
                               "change": round(change, 4), "impact_cad": round(impact, 2)})
        holdings_live = {m["ticker"]: m["change"] for m in movers}
        movers.sort(key=lambda m: -abs(m["impact_cad"]))
        stops = _watch_stops(b["id"], acct, store.get_params(b["id"]), session, float(fx.iloc[-1]), chains)
        base = base_row["equity"] if base_row else (float(today_row["equity"]) if not acct.positions else acct.cash)
        equity = float(today_row["equity"]) if today_row else float(curve.iloc[-1])
        watch = store.get_watchlist(b["id"])
        radar = _radar(watch, session)
        quotes = _quotes(watched.get(b["id"], []), session, prev)
        plans = _plan_lines(pending.get(b["id"]), quotes)
        musings = _watch_lines(watch, quotes)
        change = equity / base - 1 if base else 0.0
        top = movers[0] if movers else ({"name": "Mon portefeuille", "change": 0.0} if acct.positions else None)
        out_bots.append({
            "id": b["id"], "name": b["name"], "personality": b["personality"],
            "equity": round(equity, 2), "base": round(base, 2), "change": round(change, 5),
            "change_cad": round(equity - base, 2), "mood": mood_of(change),
            "reaction": (_stop_reaction(stops) or _radar_reaction(radar)
                         or (None if acct.positions else (plans + musings)[0] if plans + musings else None)
                         or react(b["id"], change, top, now)),
            "movers": movers[:3],
            "stops": stops, "radar": radar, "thoughts": (_thoughts(stops, radar) + plans[:2] + musings[:2])[:6],
            "holdings": holdings_live, "watch": quotes,
            "curve": [round(float(v) / base - 1, 5) if base else 0.0 for v in curve],
            "settled": today_row is not None,
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
        "ok": True, "time": now.isoformat(timespec="minutes"), "day": str(day), "market": market[:60],
        "as_of": session.index[-1].strftime("%H:%M"), "open": market_open(now) and day == now.date(),
        "usdcad": round(float(fx.iloc[-1]), 4), "times": [t.strftime("%H:%M") for t in session.index],
        "indexes": indexes, "bots": out_bots,
    }
    store.set_meta("live", json.dumps(payload, ensure_ascii=False))
    store.commit()
    return payload


def _radar(watch: dict | None, session: pd.DataFrame) -> list[dict]:
    """Ses seuils de décision contre le vrai cours : à quelle distance il est d'agir ce soir."""
    out = []
    for item in (watch or {}).get("items", []):
        trig, t = item.get("trigger"), item["ticker"]
        if not trig or t not in session or not session[t].notna().any():
            continue
        prices = session[t].dropna()
        last = float(prices.iloc[-1])
        below = trig["direction"] == "below"
        crossed = last <= trig["level"] if below else last >= trig["level"]
        touched = float(prices.min()) <= trig["level"] if below else float(prices.max()) >= trig["level"]
        out.append({"ticker": t, "last": round(last, 2), "distance": round(trig["level"] / last - 1, 4),
                    "crossed": bool(crossed), "touched": bool(touched), **trig})
    return sorted(out, key=lambda x: (not x["crossed"], abs(x["distance"])))


def _signed(v: float) -> str:
    return french(f"{v * 100:+.1f} %").replace("-", "−")


def _plan_lines(pending: dict | None, quotes: dict) -> list[str]:
    """Ce qu'il pense de ses ordres de demain en voyant le marché d'aujourd'hui (les plus gros mouvements d'abord)."""
    out = []
    for o in (pending or {}).get("orders", []):
        q = quotes.get(o["ticker"])
        if not q or q["change"] is None:
            continue
        ch, t = q["change"], o["ticker"]
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
    if x["crossed"]:
        return french(f"{x['ticker']} a franchi mon {x['label']} ({x['level']:.2f}) : si ça tient à la clôture, {x['then']}.")
    return french(f"{x['ticker']} est à {_pct(x['distance'])} de mon {x['label']} ({x['level']:.2f}). "
                  f"S'il y arrive à la clôture, {x['then']}.")


def _radar_reaction(radar: list[dict]) -> str | None:
    hot = next((x for x in radar if x["crossed"] or abs(x["distance"]) < 0.015), None)
    return _radar_line(hot) if hot else None


def _thoughts(stops: list[dict], radar: list[dict]) -> list[str]:
    """Ce qu'il a en tête en ce moment : ses stops proches et ses seuils les plus proches, du plus urgent au moins urgent."""
    lines = [_stop_reaction([x]) for x in stops if x["hit"] or abs(x["distance"] or 1) < STOP_WATCH]
    close = [x for x in radar if x["crossed"] or abs(x["distance"]) < 0.05] or radar[:1]  # les plus proches d'agir
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
    """Ordres stop du Chubby et distance au prix actuel. Un stop touché pendant la séance est exécuté au prix du
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
        return french(f"Mon ordre stop sur {hit['name']} est touché ! {verb.capitalize()} à {hit['level']:.2f}.")
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
