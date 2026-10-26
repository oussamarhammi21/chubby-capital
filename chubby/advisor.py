"""Mode conseiller : ton profil d'investisseur + l'export de ton compte Wealthsimple.

Lecture seule. Le fichier n'est jamais enregistré : il sert à comparer ton portefeuille à celui
du Chubby qui te ressemble le plus et qui a fait ses preuves dans l'arène. C'est toi qui décides
et qui passes les ordres. Ce n'est pas un conseil financier réglementé.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from collections import defaultdict
from datetime import date
from pathlib import Path

from .config import ArenaConfig, currency_of
from .scoring import metrics, score
from .store import Store

log = logging.getLogger(__name__)

# Risque de 1 (très prudent) à 5 (très audacieux) et style de chaque personnalité.
TRAITS = {
    "dumpling": {"risk": 2, "style": "equilibre"},
    "flan": {"risk": 2, "style": "revenu"},
    "tortue": {"risk": 3, "style": "equilibre"},
    "muffin": {"risk": 3, "style": "croissance"},
    "mochi": {"risk": 4, "style": "croissance"},
    "matcha": {"risk": 4, "style": "croissance"},
    "jalapeno": {"risk": 5, "style": "croissance"},
    "jalapeno-v3": {"risk": 5, "style": "croissance"},
    "jalapeno-v2": {"risk": 4, "style": "revenu"},
}
CERTIFIED_SESSIONS = 120  # environ six mois d'arène avant de pouvoir conseiller
# FNB très diversifiés : en avoir une grosse part n'est pas une concentration.
BROAD_FUNDS = {"XEQT.TO", "VEQT.TO", "ZEQT.TO", "XGRO.TO", "VGRO.TO", "XBAL.TO", "VBAL.TO", "VCNS.TO", "XCNS.TO",
               "XIC.TO", "VCN.TO", "ZCN.TO", "VFV.TO", "XUS.TO", "ZSP.TO", "XUU.TO", "VUN.TO", "XAW.TO", "VXC.TO",
               "ZAG.TO", "XBB.TO", "VAB.TO", "VOO", "SPY", "IVV", "VTI", "QQQ", "VT"}
CANADIAN_EXCHANGES = {"TSX", "TSXV", "XTSE", "XTSX", "NEO", "NEOE", "CSE", "TSX-V", "CBOE CANADA"}

COLUMNS = {
    "symbol": ["symbol", "symbole", "ticker", "titre"],
    "quantity": ["quantity", "quantité", "quantite", "qty", "nombre"],
    "value": ["market value", "valeur marchande", "market_value", "valeur", "value"],
    "price": ["market price", "prix du marché", "prix", "price"],
    "currency": ["market value currency", "devise de la valeur marchande", "currency", "devise", "market price currency"],
    "exchange": ["exchange", "bourse", "marché", "mic"],
    "type": ["security type", "type de titre", "type"],
}


def _find(header: list[str], key: str) -> int | None:
    lowered = [h.strip().lower() for h in header]
    for name in COLUMNS[key]:
        if name in lowered:
            return lowered.index(name)
    return None


def _number(text: str) -> float | None:
    cleaned = (text or "").replace("$", "").replace(" ", "").replace(" ", "").strip()
    if cleaned.count(",") == 1 and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")  # format français : 12,50
    cleaned = cleaned.replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return None


# Export des activités Wealthsimple : ces opérations changent le nombre d'actions détenues.
SHARE_ACTIVITIES = {"Trade", "CorporateAction", "LegacyCorporateAction", "InternalSecurityTransfer", "OptionExpiry"}
OCC_OPTION = re.compile(r"^[A-Z.]+\s+\d{6}[CP]\d{8}$")  # ex. « NVDA  240628C00130000 »
CANADIAN_SUFFIXES = (".TO", ".V", ".NE", ".CN")  # TSX, TSX croissance, Cboe Canada, CSE


def yahoo_prices(cache_dir: str | Path | None = None):
    """Renvoie une fonction (symbole, devise) -> (symbole Yahoo, prix) qui cherche le dernier cours.

    Un symbole en CAD est essayé sur les bourses canadiennes, dans l'ordre. Les prix trouvés sont gardés
    pour la journée dans le cache, pour ne pas tout retélécharger à chaque analyse.
    """
    import warnings

    import yfinance as yf

    warnings.filterwarnings("ignore", module=r"yfinance(\..*)?")
    # yfinance écrit une erreur pour chaque bourse essayée sans succès (BAD.TO, BAD.V…) : on la fait taire.
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    path = Path(cache_dir) / f"prix-du-jour-{date.today()}.json" if cache_dir else None
    cache = json.loads(path.read_text()) if path and path.exists() else {}

    def lookup(symbol: str, currency: str):
        key = f"{symbol}|{currency}"
        if key not in cache:
            candidates = [symbol + s for s in CANADIAN_SUFFIXES] if currency == "CAD" else [symbol]
            cache[key] = None
            for ticker in candidates:
                try:
                    hist = yf.Ticker(ticker).history(period="10d")
                except Exception:  # symbole inconnu, réseau…
                    continue
                if not hist.empty:
                    cache[key] = [ticker, float(hist["Close"].dropna().iloc[-1])]
                    break
            if path:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(cache))
        return tuple(cache[key]) if cache[key] else None

    return lookup


def parse_activities(rows: list[dict], usdcad: float, price_of) -> tuple[list[dict], list[str], list[str]]:
    """Reconstruit les positions à partir de l'historique des activités, puis les évalue au prix du jour.

    Renvoie les positions, les symboles sans prix, et des remarques (crypto, options, prix approximatifs).
    """
    quantity, currency, last_price = defaultdict(float), {}, {}
    crypto = options = 0
    for r in rows:
        symbol = (r.get("symbol") or "").strip()
        if not symbol or r.get("activity_type") not in SHARE_ACTIVITIES:
            continue
        if r.get("account_type") == "Crypto":
            crypto += 1
            continue
        if OCC_OPTION.match(symbol):
            options += 1
            continue
        quantity[symbol] += _number(r.get("quantity", "")) or 0.0
        if r.get("currency"):
            currency[symbol] = r["currency"].upper()
        if r.get("activity_type") == "Trade" and _number(r.get("unit_price", "")):
            last_price[symbol] = _number(r["unit_price"])
    holdings, skipped, notes, stale = [], [], [], []
    for symbol, qty in quantity.items():
        if qty <= 1e-6:
            continue
        cur = currency.get(symbol, "USD")
        found = price_of(symbol, cur)
        if found:
            yahoo, price = found
        elif symbol in last_price:
            yahoo, price = symbol + (".TO" if cur == "CAD" else ""), last_price[symbol]
            stale.append(symbol)
        else:
            skipped.append(symbol)
            continue
        holdings.append({"symbol": yahoo, "quantity": qty, "currency": cur,
                         "value_cad": qty * price * (usdcad if cur == "USD" else 1.0)})
    if stale:
        notes.append(f"Prix du jour introuvable pour {', '.join(stale)} : j'ai pris ton dernier prix d'achat ou de vente.")
    if crypto:
        notes.append("Ton compte crypto n'est pas pris en compte : les Chubbies ne tradent que des actions et des FNB.")
    if options:
        notes.append("Les options de ton historique sont ignorées (seules les actions et FNB sont comparés).")
    return sorted(holdings, key=lambda h: -h["value_cad"]), skipped, notes


def parse_holdings(text: str, usdcad: float, price_of=None) -> tuple[list[dict], list[str]]:
    """Lit un export de positions (Wealthsimple ou un CSV simple « symbole, quantité, valeur »),
    ou l'export des activités Wealthsimple, dont on reconstruit les positions.

    Renvoie les positions (valeur en CAD) et les lignes ignorées, pour pouvoir les signaler.
    """
    holdings, skipped, _ = read_portfolio(text, usdcad, price_of)
    return holdings, skipped


def read_portfolio(text: str, usdcad: float, price_of=None) -> tuple[list[dict], list[str], list[str]]:
    """Comme parse_holdings, avec en plus des remarques sur la lecture."""
    head = text.lstrip("\ufeff").strip().splitlines()[0].lower() if text.strip() else ""
    if "activity_type" in head and "quantity" in head:
        rows = list(csv.DictReader(io.StringIO(text.lstrip("\ufeff").strip())))
        return parse_activities(rows, usdcad, price_of or yahoo_prices())
    holdings, skipped = _parse_positions(text, usdcad)
    return holdings, skipped, []


def _parse_positions(text: str, usdcad: float) -> tuple[list[dict], list[str]]:
    rows = list(csv.reader(io.StringIO(text.strip())))
    header_at = next((i for i, r in enumerate(rows) if _find(r, "symbol") is not None), None)
    if header_at is None:
        raise ValueError("Je ne trouve pas de colonne « Symbol » ou « Symbole » dans le fichier.")
    header = rows[header_at]
    col = {k: _find(header, k) for k in COLUMNS}
    if col["value"] is None and (col["price"] is None or col["quantity"] is None):
        raise ValueError("Il me faut la valeur marchande, ou bien la quantité et le prix, de chaque position.")
    holdings, skipped = {}, []
    for r in rows[header_at + 1:]:
        if not r or col["symbol"] >= len(r) or not r[col["symbol"]].strip():
            continue
        get = lambda k: r[col[k]].strip() if col[k] is not None and col[k] < len(r) else ""  # noqa: E731
        symbol = get("symbol").upper()
        qty = _number(get("quantity")) or 0.0
        value = _number(get("value"))
        if value is None and _number(get("price")) is not None:
            value = qty * _number(get("price"))
        if value is None:
            skipped.append(symbol)
            continue
        exchange = get("exchange").upper()
        if "." not in symbol and (exchange in CANADIAN_EXCHANGES or exchange.startswith("TSX")):
            symbol += ".TO"
        currency = (get("currency") or currency_of(symbol)).upper()
        value_cad = value * (usdcad if currency.startswith("USD") else 1.0)
        entry = holdings.setdefault(symbol, {"symbol": symbol, "quantity": 0.0, "value_cad": 0.0,
                                             "currency": "USD" if currency.startswith("USD") else "CAD"})
        entry["quantity"] += qty
        entry["value_cad"] += value_cad
    return sorted(holdings.values(), key=lambda h: -h["value_cad"]), skipped


def match_bots(store: Store, cfg: ArenaConfig, profile: dict) -> list[dict]:
    """Classe les Chubbies selon ta personnalité d'investisseur et leurs résultats dans l'arène."""
    risk = int(profile.get("risk", 3))
    style = profile.get("style", "equilibre")
    horizon = profile.get("horizon", "long")
    out = []
    for b in store.db.execute("SELECT * FROM bots"):
        base = b["id"].removesuffix("-ia")
        traits = TRAITS.get(base)
        eq = store.equity_series(b["id"])
        if traits is None or eq.empty:
            continue
        fit = 100 - 25 * abs(traits["risk"] - risk) - (0 if traits["style"] == style else 15)
        if horizon == "court" and traits["risk"] >= 4:
            fit -= 20  # pas de stratégie nerveuse pour un argent dont tu as besoin bientôt
        if fit < 40:
            continue
        m = metrics(eq, cfg.starting_cash)
        s = score(m)
        out.append({"id": b["id"], "name": b["name"], "personality": b["personality"], "fit": fit, "score": s,
                    "sessions": len(eq), "certified": len(eq) >= CERTIFIED_SESSIONS,
                    "return": m.total_return, "max_drawdown": m.max_drawdown,
                    "overall": round(0.5 * fit + 0.5 * s, 1)})
    return sorted(out, key=lambda e: -e["overall"])


def advise(store: Store, cfg: ArenaConfig, profile: dict, holdings: list[dict]) -> dict:
    matches = match_bots(store, cfg, profile)
    total = sum(h["value_cad"] for h in holdings)
    result = {"profile": profile, "total_cad": round(total, 2), "matches": matches[:4], "notes": [], "rows": []}
    if total <= 0:
        result["notes"].append("Je n'ai trouvé aucune position avec une valeur dans le fichier.")
        return result
    mine = {h["symbol"]: h["value_cad"] / total for h in holdings}

    # Remarques générales, valables quel que soit le Chubby choisi.
    for h in holdings:
        if h["value_cad"] / total > 0.20 and h["symbol"] not in BROAD_FUNDS:
            result["notes"].append(f"{h['symbol']} pèse {h['value_cad'] / total:.0%} de ton portefeuille : "
                                   "c'est beaucoup sur un seul titre.")
    us_share = sum(h["value_cad"] for h in holdings if h["currency"] == "USD") / total
    if profile.get("usd_account") and us_share < 0.10:
        result["notes"].append(f"Seulement {us_share:.0%} de titres américains : le forfait du compte en USD "
                               "te coûte probablement plus qu'il ne te rapporte.")
    if not profile.get("usd_account") and us_share > 0.25:
        result["notes"].append(f"{us_share:.0%} de titres américains sans compte en USD : chaque achat et "
                               "vente paie environ 1,5 % de conversion.")

    if not matches:
        result["notes"].append("Aucun Chubby ne correspond encore à ton profil dans cette arène.")
        return result
    best = matches[0]
    result["advisor"] = best
    if not best["certified"]:
        result["notes"].append(f"{best['name']} n'a que {best['sessions']} séances d'arène : prends ses idées "
                               f"comme des pistes, il sera « certifié » après {CERTIFIED_SESSIONS} séances.")
    snap = store.db.execute("SELECT holdings, equity FROM snapshots WHERE bot_id = ? ORDER BY day DESC LIMIT 1",
                            (best["id"],)).fetchone()
    bot_values = json.loads(snap["holdings"] or "{}") if snap else {}
    bot_weights = {t: v / snap["equity"] for t, v in bot_values.items()} if snap and snap["equity"] else {}
    for t in sorted(set(mine) | set(bot_weights), key=lambda t: -max(mine.get(t, 0), bot_weights.get(t, 0))):
        gap = (bot_weights.get(t, 0.0) - mine.get(t, 0.0)) * total
        if t not in bot_weights:
            action = "hors de son style"  # pas forcément à vendre : il ne le choisirait juste pas
        else:
            action = "ajouter" if gap > 50 else "réduire" if gap < -50 else "garder"
        result["rows"].append({"symbol": t, "mine": round(mine.get(t, 0.0), 4), "bot": round(bot_weights.get(t, 0.0), 4),
                               "gap_cad": round(gap, 2), "action": action})
    cash = 1 - sum(bot_weights.values())
    result["bot_cash"] = round(cash, 4)
    result["style"] = style_comparison(mine, bot_weights)
    outside = [r for r in result["rows"] if r["action"] == "hors de son style"]
    if outside and bot_weights:
        share = sum(r["mine"] for r in outside)
        result["notes"].append(f"{share:.0%} de ton portefeuille est hors du style de {best['name']} "
                               f"({len(outside)} titres). Ce n'est pas un signal de vente : compare surtout les "
                               "grandes lignes (concentration, part américaine, FNB larges) dans le tableau des styles.")
    return result


def _style(weights: dict[str, float]) -> dict[str, float]:
    """Grandes lignes d'un portefeuille (poids en part du total, cash compris)."""
    stocks = {t: w for t, w in weights.items() if w > 0}
    ranked = sorted(stocks.values(), reverse=True)
    underlying = lambda t: t.split(":")[1] if t.startswith("OPT:") else t  # noqa: E731
    return {
        "positions": len(stocks),
        "largest": ranked[0] if ranked else 0.0,
        "top5": sum(ranked[:5]),
        "us": sum(w for t, w in stocks.items() if currency_of(t) == "USD"),
        "broad": sum(w for t, w in stocks.items() if underlying(t) in BROAD_FUNDS),
        "options": sum(w for t, w in weights.items() if t.startswith("OPT:")),
    }


STYLE_ROWS = [("positions", "Nombre de titres", "n"), ("largest", "Plus grosse position", "%"),
              ("top5", "Les 5 plus grosses positions", "%"), ("us", "Part américaine", "%"),
              ("broad", "FNB indiciels larges", "%"), ("options", "Options", "%")]


def style_comparison(mine: dict[str, float], bot: dict[str, float]) -> list[dict]:
    a, b = _style(mine), _style(bot)
    return [{"key": k, "label": label, "kind": kind, "mine": round(a[k], 4), "bot": round(b[k], 4)}
            for k, label, kind in STYLE_ROWS if k != "options" or a[k] or b[k]]
