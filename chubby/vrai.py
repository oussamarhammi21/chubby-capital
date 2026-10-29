"""Ton vrai compte : un Chubby gère un vrai montant chez un vrai courtier (Interactive Brokers).

Le Chubby que tu choisis comme guide continue de jouer dans l'arène avec sa personnalité. Ton vrai compte copie son
portefeuille à l'échelle de ton montant : s'il a 20 % de son capital en MSFT, ton compte vise 20 % de MSFT.
Les ordres partent tout seuls chez le courtier, pendant la bourse, dès que le guide change ses positions.

Garde-fous, vérifiés avant chaque ordre :
- montant maximal géré : le guide n'investit jamais plus que ce montant, même si le compte contient davantage ;
- perte maximale : si le compte tombe sous ce seuil depuis le départ, tout s'arrête et les ordres ouverts sont annulés ;
- montant maximal par achat, et montant minimal (pas de petits ordres pour rien) ;
- bouton Stop : arrête tout, et annule les ordres ouverts dans la minute ;
- en mode papier, l'app refuse de trader si le compte branché est un vrai compte.

Les options ne sont pas copiées (un contrat coûte trop cher pour un petit montant) : les Jalapeño ne peuvent pas guider.
"""

from __future__ import annotations

import json
import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from . import capital
from .config import currency_of
from .live import TORONTO, market_open
from .options import is_option
from .store import Store

log = logging.getLogger(__name__)

DEFAULTS = {
    "guide": None,  # id du Chubby qui décide
    "plafond": 100.0,  # montant maximal géré, en CAD
    "perte_max": 0.15,  # arrêt automatique sous -15 % depuis le départ
    "ordre_max": 50.0,  # un achat ne dépasse jamais ce montant, en CAD
    "ordre_min": 5.0,  # on ignore les ajustements plus petits
    "fractions": False,  # fractions d'actions américaines (à activer chez IBKR)
    "mode": "papier",  # « papier » (argent fictif chez IBKR) ou « reel »
    "actif": False,  # le bouton marche / Stop
    "pause": None,  # raison d'un arrêt automatique (perte maximale, mauvais compte…)
    "depart": None,  # valeur du compte au démarrage, en CAD
    "debut": None,  # jour du démarrage
    "guide_depart": None,  # capital du guide dans l'arène au démarrage, pour comparer
}
NOT_GUIDES = ("jalapeno",)  # stratégies d'options : pas copiables avec un petit montant
SLACK = 0.005  # ordre à cours limité 0,5 % au-delà du dernier cours : il passe tout de suite, sans surprise
RUN_EVERY = 5 * 60  # secondes, au plus souvent
CLAIM_FOR = 180  # secondes : au-delà, un programme qui ne se manifeste plus laisse la main
FIRST_RUN = (9, 35)  # après l'exécution des ordres de la veille du guide dans l'arène


@dataclass
class Holding:
    ticker: str
    qty: float
    value_cad: float
    currency: str = "CAD"


@dataclass
class Snapshot:
    """Ce que le courtier dit du compte : son numéro, son cash et ses positions."""

    account_id: str
    cash_cad: float
    holdings: dict[str, Holding] = field(default_factory=dict)
    paper: bool = True

    @property
    def equity(self) -> float:
        return self.cash_cad + sum(h.value_cad for h in self.holdings.values())


@dataclass
class Order:
    ticker: str
    side: str  # « buy » ou « sell »
    qty: float
    limit_price: float  # dans la devise du titre
    currency: str
    value_cad: float
    why: str


class LiveBroker(Protocol):
    def snapshot(self) -> Snapshot: ...
    def place(self, order: Order) -> tuple[str, str, str]:
        """Envoie l'ordre : renvoie (statut, numéro chez le courtier, message)."""
    def cancel_all(self) -> int: ...


# --- Réglages -------------------------------------------------------------

def config(store: Store) -> dict:
    saved = json.loads(store.get_meta("vrai_config") or "{}")
    return {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}


def save_config(store: Store, cfg: dict):
    store.set_meta("vrai_config", json.dumps({k: cfg.get(k, v) for k, v in DEFAULTS.items()}))
    store.commit()


def can_guide(bot_id: str) -> bool:
    return not bot_id.removesuffix("-ia").startswith(NOT_GUIDES)


def update_config(store: Store, changes: dict) -> dict:
    """Change les réglages. Lève ValueError si une valeur n'a pas de sens."""
    cfg = config(store)
    if "guide" in changes:
        guide = str(changes["guide"] or "")
        if not store.bot_row(guide):
            raise ValueError("Ce Chubby n'existe pas dans l'arène.")
        if not can_guide(guide):
            raise ValueError("Les Jalapeño tradent des options : un contrat coûte trop cher pour ce montant.")
        if guide != cfg["guide"]:
            cfg["guide"], cfg["guide_depart"] = guide, None  # nouvelle comparaison à partir d'aujourd'hui
    for key, low, high in [("plafond", 10.0, 1_000_000.0), ("perte_max", 0.01, 0.9), ("ordre_max", 1.0, 1_000_000.0),
                           ("ordre_min", 1.0, 10_000.0)]:
        if key in changes:
            v = float(changes[key])
            if not low <= v <= high:
                raise ValueError(f"« {key} » doit être entre {low:g} et {high:g}.")
            cfg[key] = v
    if "fractions" in changes:
        cfg["fractions"] = bool(changes["fractions"])
    if "mode" in changes:
        if changes["mode"] not in ("papier", "reel"):
            raise ValueError("Le mode est « papier » ou « reel ».")
        if changes["mode"] == "reel" and cfg["mode"] != "reel" and not changes.get("confirme"):
            raise ValueError("Pour passer en argent réel, coche la confirmation.")
        if changes["mode"] != cfg["mode"]:
            cfg["mode"], cfg["depart"], cfg["debut"] = changes["mode"], None, None  # nouveau compte, nouveau départ
    if cfg["ordre_min"] > cfg["ordre_max"]:
        raise ValueError("Le montant minimal d'un ordre dépasse le montant maximal.")
    save_config(store, cfg)
    return cfg


def start(store: Store) -> dict:
    cfg = config(store)
    if not cfg["guide"]:
        raise ValueError("Choisis d'abord le Chubby qui guide ton compte.")
    cfg["actif"], cfg["pause"] = True, None
    save_config(store, cfg)
    _event(store, "start", f"Démarré en mode {cfg['mode']}, guidé par {_name(store, cfg['guide'])}.")
    return cfg


def stop(store: Store, reason: str | None = None) -> dict:
    """Le bouton Stop : plus aucun ordre, et les ordres ouverts sont annulés au prochain passage (dans la minute)."""
    cfg = config(store)
    cfg["actif"], cfg["pause"] = False, reason
    save_config(store, cfg)
    store.set_meta("vrai_annuler", "1")
    _event(store, "stop", reason or "Arrêté par toi.")
    return cfg


# --- Le plan : de son portefeuille au tien ---------------------------------

def guide_weights(store: Store, guide: str) -> tuple[dict[str, float], dict[str, float], float]:
    """Poids du guide dans l'arène (sans les options), prix d'une action en CAD, et son capital."""
    now = capital.current(store).get(guide)
    if not now or now["equity"] <= 0:
        return {}, {}, 0.0
    acct = store.load_account(guide)
    weights, units = {}, {}
    for t, v in now["values"].items():
        pos = acct.positions.get(t)
        if is_option(t) or v <= 0 or not pos or pos.qty <= 0:
            continue
        weights[t] = v / now["equity"]
        units[t] = v / pos.qty
    return weights, units, now["equity"]


def _round_qty(qty: float, ticker: str, fractions: bool) -> float:
    if fractions and currency_of(ticker) == "USD":
        return math.floor(qty * 10_000) / 10_000
    return float(math.floor(qty + 1e-9))


def plan(snap: Snapshot, weights: dict[str, float], units: dict[str, float], cfg: dict, fx: float,
         guide_name: str = "Le guide") -> tuple[list[Order], list[str]]:
    """Les ordres qui rapprochent ton compte du portefeuille du guide, et les remarques (titre trop cher, etc.).

    `units` : prix d'une action en CAD ; `fx` : le cours USD -> CAD. Les ventes passent avant les achats.
    """
    budget = min(snap.equity, cfg["plafond"])
    orders, notes = [], []

    def native(ticker: str, cad: float) -> float:
        return cad / fx if currency_of(ticker) == "USD" else cad

    cash = snap.cash_cad
    for t, h in sorted(snap.holdings.items()):
        if h.qty <= 0 or is_option(t):
            continue
        unit = h.value_cad / h.qty if h.qty else 0.0
        target = weights.get(t, 0.0) * budget
        if t not in weights:
            qty, why = h.qty, f"{guide_name} n'a plus {t} dans son portefeuille."
        elif h.value_cad - target >= cfg["ordre_min"] and unit > 0:
            qty = _round_qty((h.value_cad - target) / unit, t, cfg["fractions"])
            why = f"{guide_name} garde {weights[t]:.0%} de son capital en {t} : je réduis à la même part."
        else:
            continue
        if qty <= 0 or not unit:
            continue
        price = round(native(t, unit) * (1 - SLACK), 2)
        orders.append(Order(t, "sell", qty, price, currency_of(t), round(qty * unit, 2), why))
        cash += qty * unit
    for t, w in sorted(weights.items(), key=lambda kv: -kv[1]):
        unit = units.get(t)
        if not unit:
            continue
        held = snap.holdings.get(t)
        gap = w * budget - (held.value_cad if held else 0.0)
        if gap < cfg["ordre_min"]:
            continue
        spend = min(gap, cfg["ordre_max"], cash * (1 - SLACK))
        qty = _round_qty(spend / (unit * (1 + SLACK)), t, cfg["fractions"])
        if qty <= 0:
            if unit > spend:
                notes.append(f"{t} : une action coûte {unit:,.2f} $, plus que les {spend:,.2f} $ prévus. "
                             "Pas d'achat (un montant plus grand ou les fractions d'actions américaines le permettraient).")
            continue
        price = round(native(t, unit) * (1 + SLACK), 2)
        value = qty * unit
        orders.append(Order(t, "buy", qty, price, currency_of(t), round(value, 2),
                            f"{guide_name} a {w:.0%} de son capital en {t} : je vise la même part de ton compte."))
        cash -= value * (1 + SLACK)
    return orders, notes


# --- Un passage : vérifier, planifier, envoyer ------------------------------

def run(store: Store, broker: LiveBroker, now: datetime | None = None) -> dict:
    """Un passage complet. Renvoie ce qui s'est passé (envoyé à la page et au journal)."""
    now = (now or datetime.now(TORONTO)).astimezone(TORONTO)
    day, hhmm = str(now.date()), now.strftime("%H:%M")
    cfg = config(store)
    if store.get_meta("vrai_annuler"):
        n = broker.cancel_all()
        store.set_meta("vrai_annuler", "")
        if n:
            _event(store, "cancel", f"{n} ordre(s) ouvert(s) annulé(s).")
    if not cfg["actif"]:
        return {"ran": False, "why": cfg["pause"] or "arrêté"}
    snap = broker.snapshot()
    _save_state(store, snap, now)
    if cfg["mode"] == "papier" and not snap.paper:
        stop(store, f"Le compte branché ({snap.account_id}) est un vrai compte, mais tu es en mode papier.")
        return {"ran": False, "why": "mauvais compte"}
    if cfg["depart"] is None:
        cfg["depart"], cfg["debut"] = round(snap.equity, 2), day
        save_config(store, cfg)
    guide = cfg["guide"]
    weights, units, guide_equity = guide_weights(store, guide)
    if cfg["guide_depart"] is None and guide_equity:
        cfg["guide_depart"] = round(guide_equity, 2)
        save_config(store, cfg)
    store.db.execute("INSERT OR REPLACE INTO real_snapshots VALUES (?, ?, ?)", (day, round(snap.equity, 2), guide_equity))
    floor = cfg["depart"] * (1 - cfg["perte_max"])
    if snap.equity < floor:
        n = broker.cancel_all()
        stop(store, f"Perte maximale atteinte : le compte vaut {snap.equity:,.2f} $, sous le seuil de {floor:,.2f} $"
                    f" (-{cfg['perte_max']:.0%}). {n} ordre(s) ouvert(s) annulé(s).")
        return {"ran": False, "why": "perte maximale"}
    broker.cancel_all()  # les ordres d'un passage précédent qui n'ont pas trouvé preneur : on repart à neuf
    fx = float(store.get_meta("usdcad") or 1.37)
    orders, notes = plan(snap, weights, units, cfg, fx, _name(store, guide))
    for o in orders:
        try:
            status, broker_id, message = broker.place(o)
        except Exception as e:  # un ordre refusé ne doit pas bloquer les autres
            log.exception("Ordre refusé : %s", o)
            status, broker_id, message = "erreur", "", str(e)
        store.db.execute(
            "INSERT INTO real_orders (day, time, ticker, side, qty, limit_price, currency, value_cad, why, status,"
            " broker_id, message) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (day, hhmm, o.ticker, o.side, o.qty, o.limit_price, o.currency, o.value_cad, o.why, status, broker_id,
             message))
    for note in notes:
        if not store.db.execute("SELECT 1 FROM real_log WHERE day = ? AND message = ?", (day, note)).fetchone():
            _event(store, "note", note, day=day)  # une seule fois par jour, pas à chaque passage
    if orders:
        _save_state(store, broker.snapshot(), now)  # le compte après les ordres
    store.set_meta("vrai_dernier", json.dumps({"time": now.isoformat(timespec="minutes"), "orders": len(orders),
                                                "trade_id": _guide_trade_id(store, guide)}))
    store.commit()
    return {"ran": True, "orders": [o.__dict__ for o in orders], "notes": notes}


def due(store: Store, now: datetime) -> bool:
    """Faut-il un passage maintenant ? Pendant la bourse, une fois par jour après l'ouverture, puis chaque fois que le
    guide change ses positions dans l'arène (au plus toutes les 5 minutes). Le Stop, lui, passe tout de suite."""
    if store.get_meta("vrai_annuler"):
        return True
    cfg = config(store)
    now = now.astimezone(TORONTO)
    if not cfg["actif"] or not market_open(now) or (now.hour, now.minute) < FIRST_RUN:
        return False
    last = json.loads(store.get_meta("vrai_dernier") or "{}")
    if not last.get("time"):
        return True
    previous = datetime.fromisoformat(last["time"]).astimezone(TORONTO)
    if previous.date() != now.date():
        return True
    return (now - previous).total_seconds() >= RUN_EVERY and _guide_trade_id(store, cfg["guide"]) != last.get("trade_id")


class Runner:
    """Tourne dans la boucle de la séance (chubby web ou serve) : branche le courtier au besoin et fait les passages."""

    def __init__(self, connect):
        self.connect = connect  # () -> LiveBroker
        self.broker = None
        self.owner = uuid.uuid4().hex

    def claim(self, store: Store, now: datetime) -> bool:
        """Un seul programme gère le vrai compte (deux fenêtres chubby web, ou serve et web dans Docker, ne doivent pas
        envoyer deux fois les mêmes ordres) : le premier le garde tant qu'il tourne, et il se renouvelle chaque minute."""
        db = store.db
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        try:
            held = json.loads(store.get_meta("vrai_claim") or "{}")
            fresh = held.get("time") and (now - datetime.fromisoformat(held["time"])).total_seconds() < CLAIM_FOR
            if held.get("owner") not in (None, self.owner) and fresh:
                db.rollback()
                return False
            store.set_meta("vrai_claim", json.dumps({"owner": self.owner, "time": now.isoformat()}))
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise

    def __call__(self, store: Store, now: datetime | None = None):
        now = now or datetime.now(TORONTO)
        if not self.claim(store, now) or not due(store, now):
            return None
        try:
            if self.broker is None:
                self.broker = self.connect()
            result = run(store, self.broker, now)
            store.set_meta("vrai_erreur", "")
            store.commit()
            return result
        except Exception as e:
            log.exception("Vrai compte : passage impossible")
            if self.broker is not None and hasattr(self.broker, "close"):
                try:
                    self.broker.close()
                except Exception:
                    pass
            self.broker = None  # on se rebranchera au prochain passage
            store.set_meta("vrai_erreur", json.dumps({"time": now.isoformat(timespec="minutes"), "message": str(e)},
                                                     ensure_ascii=False))
            store.commit()
            return None


# --- Pour la page ----------------------------------------------------------

def report(store: Store) -> dict:
    from .scoring import metrics, score

    cfg = config(store)
    guides = []
    for b in store.db.execute("SELECT id, name, personality FROM bots ORDER BY id"):
        eq = store.equity_series(b["id"])
        m = metrics(eq, 10_000.0) if len(eq) else None
        guides.append({"id": b["id"], "name": b["name"], "personality": b["personality"], "sessions": len(eq),
                       "score": score(m) if m else None, "return": round(m.total_return, 4) if m else None,
                       "allowed": can_guide(b["id"])})
    guides.sort(key=lambda g: (not g["allowed"], -(g["score"] or 0)))
    orders = [dict(r) for r in store.db.execute("SELECT * FROM real_orders ORDER BY id DESC LIMIT 40")]
    curve = [dict(r) for r in store.db.execute("SELECT * FROM real_snapshots ORDER BY day")]
    events = [dict(r) for r in store.db.execute("SELECT day, time, kind, message FROM real_log ORDER BY id DESC LIMIT 20")]
    state = json.loads(store.get_meta("vrai_etat") or "null")
    error = json.loads(store.get_meta("vrai_erreur") or "null")
    guide_now = capital.current(store).get(cfg["guide"]) if cfg["guide"] else None
    weights = guide_weights(store, cfg["guide"])[0] if cfg["guide"] else {}
    return {"config": cfg, "guide_weights": {t: round(w, 4) for t, w in weights.items()}, "guides": guides, "orders": orders, "curve": curve, "events": events, "state": state,
            "error": error, "last_run": json.loads(store.get_meta("vrai_dernier") or "null"),
            "guide_equity": guide_now["equity"] if guide_now else None}


def _save_state(store: Store, snap: Snapshot, now: datetime):
    store.set_meta("vrai_etat", json.dumps({
        "time": now.isoformat(timespec="minutes"), "account_id": snap.account_id, "paper": snap.paper,
        "cash_cad": round(snap.cash_cad, 2), "equity": round(snap.equity, 2),
        "holdings": [{"ticker": h.ticker, "qty": h.qty, "value_cad": round(h.value_cad, 2)}
                     for h in sorted(snap.holdings.values(), key=lambda h: -h.value_cad)]}))


def _guide_trade_id(store: Store, guide: str | None) -> int:
    row = store.db.execute("SELECT MAX(id) AS n FROM trades WHERE bot_id = ?", (guide,)).fetchone()
    return row["n"] or 0


def _name(store: Store, bot_id: str | None) -> str:
    row = store.bot_row(bot_id) if bot_id else None
    return row["name"] if row else "Le guide"


def _event(store: Store, kind: str, message: str, day: str | None = None):
    """Le journal du vrai compte (à part de celui de l'arène)."""
    now = datetime.now(TORONTO)
    store.db.execute("INSERT INTO real_log (day, time, kind, message) VALUES (?, ?, ?, ?)",
                     (day or str(now.date()), now.strftime("%H:%M"), kind, message))
    store.commit()
