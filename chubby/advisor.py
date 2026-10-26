"""Mode conseiller : ton profil d'investisseur + l'export de ton compte Wealthsimple.

Lecture seule. Le fichier n'est jamais enregistré : il sert à comparer ton portefeuille à celui
du Chubby qui te ressemble le plus et qui a fait ses preuves dans l'arène. C'est toi qui décides
et qui passes les ordres. Ce n'est pas un conseil financier réglementé.
"""

from __future__ import annotations

import csv
import io
import json

from .config import ArenaConfig, currency_of
from .scoring import metrics, score
from .store import Store

# Risque de 1 (très prudent) à 5 (très audacieux) et style de chaque personnalité.
TRAITS = {
    "dumpling": {"risk": 2, "style": "equilibre"},
    "flan": {"risk": 2, "style": "revenu"},
    "tortue": {"risk": 3, "style": "equilibre"},
    "muffin": {"risk": 3, "style": "croissance"},
    "mochi": {"risk": 4, "style": "croissance"},
    "matcha": {"risk": 4, "style": "croissance"},
    "jalapeno": {"risk": 5, "style": "croissance"},
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


def parse_holdings(text: str, usdcad: float) -> tuple[list[dict], list[str]]:
    """Lit un export de positions (Wealthsimple ou un CSV simple « symbole, quantité, valeur »).

    Renvoie les positions (valeur en CAD) et les lignes ignorées, pour pouvoir les signaler.
    """
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
        result["rows"].append({"symbol": t, "mine": round(mine.get(t, 0.0), 4), "bot": round(bot_weights.get(t, 0.0), 4),
                               "gap_cad": round(gap, 2),
                               "action": "ajouter" if gap > 50 else "réduire" if gap < -50 else "garder"})
    cash = 1 - sum(bot_weights.values())
    result["bot_cash"] = round(cash, 4)
    return result
