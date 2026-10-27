"""Les Chubbies tradent pendant la séance, chacun selon sa personnalité.

- À l'ouverture, les ordres décidés la veille au soir partent au premier cours de la journée, et sont inscrits tout de suite.
- Toutes les 15 minutes, chaque Chubby refait son analyse avec le cours du moment : ses règles habituelles, appliquées
  comme si la séance fermait maintenant (le RSI de Muffin, la moyenne 50 jours de Mochi, le gain de l'option de Jalapeño…).
- Il confirme avant d'agir : une occasion doit être encore là à la vérification suivante, pour ne pas réagir au bruit.
- Pas d'aller-retour sur un même titre dans la journée (des frais pour rien).
- Ses ordres stop sont surveillés en direct et exécutés dès que le cours franchit le seuil.

La séance du soir (arena.tick) garde les dividendes, les intérêts, la valeur de clôture, le classement et la décision
de fin de journée. Si elle n'a pas été jouée (tableau de bord lancé seul), on la rattrape au début de la séance suivante.

Chaque ordre exécuté est écrit dans la table events : kind « trade » (ou « stop ») avec time (HH:MM, heure de Toronto),
why (sa raison, en une phrase) et intraday=True. Une occasion repérée qui attend sa confirmation est un kind « analyse ».
"""

from __future__ import annotations

import copy
import json
import logging
from datetime import datetime

import pandas as pd

from . import options as opt
from .arena import Arena
from .bots import Bot, BotContext, Decision
from .broker import Fill, PaperBroker
from .config import ArenaConfig
from .data import FrameData
from .fmt import french
from .live import FX, INDEXES, TORONTO, _last_two_days, market_open, yahoo_bars
from .options import OptionData
from .store import Store

log = logging.getLogger(__name__)
CHECK_EVERY = 15 * 60  # secondes entre deux analyses
OPEN_TIME = "09:30"
IDLE_NOTE_EVERY = 60  # minutes entre deux « rien à faire » d'un même Chubby


def overlay(daily: FrameData, session: pd.DataFrame, at_open: bool = False) -> FrameData:
    """Les données quotidiennes plus la séance du jour comme une séance pas encore finie.

    La ligne du jour a pour clôture le dernier cours, pour plus haut et plus bas les extrêmes depuis l'ouverture.
    Son « ouverture » est le cours actuel, puisque le courtier fictif exécute ses ordres au prix d'ouverture :
    un ordre passé maintenant part au cours actuel. Avec at_open=True, c'est le vrai premier cours de la séance
    (pour les ordres de la veille). Un titre sans cours aujourd'hui garde sa dernière clôture.
    """
    today = pd.Timestamp(session.index[-1].date())
    frames = {}
    for t, f in daily.frames.items():
        f = f[f.index < today]
        if t in session and session[t].notna().any():
            px = session[t].dropna()
            row = {"Open": float(px.iloc[0] if at_open else px.iloc[-1]), "High": float(px.max()),
                   "Low": float(px.min()), "Close": float(px.iloc[-1]), "Dividends": 0.0}
            f = pd.concat([f, pd.DataFrame([row], index=pd.DatetimeIndex([today]))])
        frames[t] = f
    fx = daily.fx[daily.fx.index < today]
    if FX in session and session[FX].notna().any():
        fx = pd.concat([fx, pd.Series([float(session[FX].dropna().iloc[-1])], index=pd.DatetimeIndex([today]))])
    return FrameData(frames, fx, daily.calendar_ticker)


def apply(broker: PaperBroker, acct, targets: dict | None, trades: list, day) -> list[Fill]:
    """Passe une décision sur le compte : rééquilibrage d'abord (ventes puis achats), puis ordres directs."""
    fills = broker.rebalance(acct, targets, day) if targets is not None else []
    fills += [broker.trade(acct, t, int(q), day) for t, q in trades]
    return [f for f in fills if f is not None]


def hold_frozen(decision: Decision, frozen: set[str], weights: dict[str, float]) -> tuple[dict | None, list]:
    """La décision sans les titres qu'on ne doit plus toucher aujourd'hui : leur poids reste celui d'aujourd'hui."""
    targets = decision.targets
    if targets is not None:
        targets = {t: w for t, w in targets.items() if t not in frozen}
        targets.update({t: weights[t] for t in frozen if t in weights})
    trades = [(t, q) for t, q in decision.trades if t not in frozen]
    return targets, trades


def _minutes(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:5])


def _verb(side: str) -> str:
    return "acheter" if side == "buy" else "vendre"


class SessionTrader:
    """Fait trader les Chubbies pendant la bourse. Appelé souvent (chaque minute) ; n'agit que toutes les `every` secondes.

    `load_daily(aujourd'hui)` rend l'historique quotidien jusqu'à la veille (FrameData) ; `fetch(tickers)` les barres de
    5 minutes ; `chains_factory(aujourd'hui)` de vraies chaînes d'options (facultatif).
    """

    def __init__(self, db_path: str, cfg: ArenaConfig, bots: list[Bot], load_daily, fetch=yahoo_bars,
                 chains_factory=None, every: int = CHECK_EVERY, clock=None):
        self.db_path, self.cfg, self.bots = db_path, cfg, bots
        self.load_daily, self.fetch, self.chains_factory = load_daily, fetch, chains_factory
        self.every = every
        self.clock = clock or (lambda: datetime.now(TORONTO))
        self.last: datetime | None = None
        self.day = None
        self.daily: FrameData | None = None
        self.intents: dict[str, dict] = {}  # occasion repérée, en attente de confirmation
        self.idle_noted: dict[str, int] = {}
        self.tickers = sorted({t for b in bots for t in b.universe + b.signals} | set(INDEXES) | {FX})

    def due(self, now: datetime) -> bool:
        return market_open(now) and (self.last is None or (now - self.last).total_seconds() >= self.every - 5)

    def __call__(self, store: Store | None = None) -> dict | None:
        now = self.clock().astimezone(TORONTO)
        if not self.due(now):
            return None
        self.last = now
        try:
            return self.step(store or Store(self.db_path), now)
        except Exception:
            log.exception("Analyse en séance impossible")
            return None

    # --- Une vérification -------------------------------------------------

    def step(self, store: Store, now: datetime, bars: pd.DataFrame | None = None) -> dict:
        today = now.date()
        if self.day != today:
            saved = status(store) or {}  # les occasions repérées survivent à un redémarrage dans la journée
            self.intents = saved.get("intents", {}) if saved.get("day") == str(today) else {}
            self.day, self.daily, self.idle_noted = today, self.load_daily(today), {}
        bars = self.fetch(self.tickers) if bars is None else bars
        if bars is None or bars.empty:
            return self._status(store, now, "Pas de cours en direct pour l'instant.")
        session, _ = _last_two_days(bars)
        if session.empty or session.index[-1].date() != today:
            return self._status(store, now, "La séance d'aujourd'hui n'a pas encore de cours.")
        day = pd.Timestamp(today)
        arena = Arena(store, self.daily, self.bots, self.cfg)
        behind = self._catch_up(arena, day)
        if behind:
            return self._status(store, now, behind)
        chains = self.chains_factory(today) if self.chains_factory else None
        at_open = OptionData(overlay(self.daily, session, at_open=True), chains)
        at_now = OptionData(overlay(self.daily, session), chains)
        time = session.index[-1].strftime("%H:%M")
        day_index = int(store.get_meta("day_index") or 0)
        for bot in self.bots:
            try:
                self._play(store, arena, bot, day, day_index, session, at_open, at_now, time)
            except Exception:
                log.exception("%s n'a pas pu analyser la séance", bot.name)
        return self._status(store, now, None, time)

    def _catch_up(self, arena: Arena, day: pd.Timestamp) -> str | None:
        """Rattrape les séances du soir pas encore jouées (jusqu'à hier). Rend un message si c'est impossible."""
        closed = [d for d in self.daily.trading_days(end=day - pd.Timedelta(days=1))]
        if not closed:
            return "Pas d'historique de prix."
        if undo_early_session(arena.store, day):
            log.warning("La séance du %s avait été jouée avant la clôture : annulée, elle sera rejouée ce soir.", day.date())
        last = arena.last_day
        if last is None:
            return None  # nouvelle arène : elle commence aujourd'hui, la séance du soir l'inscrira
        if last < closed[-1]:
            n = arena.run(last + pd.Timedelta(days=1), closed[-1])
            log.info("%s séance(s) du soir rattrapée(s) avant de trader.", n)
        return None if arena.last_day is not None and arena.last_day >= closed[-1] else \
            "L'arène doit d'abord jouer la séance d'hier."

    def _play(self, store: Store, arena: Arena, bot: Bot, day, day_index, session, at_open, at_now, time: str):
        acct = store.load_account(bot.id)
        broker_open = PaperBroker(arena.broker_for(bot).cfg, at_open)
        broker_now = PaperBroker(arena.broker_for(bot).cfg, at_now)
        # 1. Les ordres de la veille au soir partent à l'ouverture.
        row = store.db.execute("SELECT decided_on FROM pending WHERE bot_id = ?", (bot.id,)).fetchone()
        if row is not None and row["decided_on"] < str(day.date()):
            targets, trades = store.pop_pending(bot.id)
            fills = apply(broker_open, acct, targets, trades, day)
            self._record(store, bot, day, fills, OPEN_TIME, "Ordre décidé hier soir, passé à l'ouverture.")
        # 2. Ses ordres stop, surveillés en direct.
        self._stops(store, bot, acct, broker_now, day, session, time)
        # 3. Son analyse du moment.
        traded = {r["ticker"] for r in store.db.execute("SELECT DISTINCT ticker FROM trades WHERE bot_id = ? AND day = ?",
                                                         (bot.id, str(day.date())))}
        stale = {t for t in bot.universe if not opt.is_option(t) and (t not in session or not session[t].notna().any())}
        row = store.bot_row(bot.id)
        ctx = BotContext(day, at_now, broker_now, acct, row["aggressiveness"], day_index, store.get_params(bot.id),
                         intraday=True)
        try:
            decision = bot.decide(ctx)
        except Exception:
            log.exception("%s a planté pendant la séance", bot.name)
            decision = None
        frozen = traded | stale | {t for t in acct.positions if opt.is_option(t) and opt.parse(t)[0] in stale}
        if decision is not None:
            targets, trades = hold_frozen(decision, frozen, ctx.weights)
            trades = [(t, q) for t, q in trades if not (opt.is_option(t) and opt.parse(t)[0] in stale)]
            plan = apply(broker_now, copy.deepcopy(acct), targets, trades, day)
        else:
            targets, trades, plan = None, [], []
        why = french(decision.notes[0]) if decision is not None and decision.notes else "Mes règles le disent."
        signature = [list(x) for x in sorted({(f.ticker, f.side) for f in plan})]
        if not signature:
            self.intents.pop(bot.id, None)
            self._idle(store, bot, day, time)
        elif self._confirms(bot.id, signature, time):
            fills = apply(broker_now, acct, targets, trades, day)
            self._record(store, bot, day, fills, time, why)
            for note in decision.notes:
                store.journal(bot.id, day, f"{time} · {note}")
            self.intents.pop(bot.id, None)
        else:
            first = plan[0]
            self.intents[bot.id] = {"signature": signature, "ticker": first.ticker, "side": first.side, "why": why,
                                    "time": time}
            name = opt.describe(first.ticker) if opt.is_option(first.ticker) else first.ticker
            store.event(day, bot.id, "analyse", ticker=first.ticker, side=first.side, time=time, intraday=True,
                        why=f"{why} Je veux {_verb(first.side)} {name} : je confirme à la prochaine vérification.")
        store.save_account(acct)
        store.commit()

    def _confirms(self, bot_id: str, signature: list, time: str) -> bool:
        """Vrai si la même occasion était déjà là à la vérification précédente (pas une ancienne, oubliée depuis)."""
        prev = self.intents.get(bot_id)
        return bool(prev) and prev["signature"] == signature and \
            0 < _minutes(time) - _minutes(prev["time"]) <= 2 * self.every // 60 + 5

    def _idle(self, store: Store, bot: Bot, day, time: str):
        minutes = _minutes(time)
        if minutes - self.idle_noted.get(bot.id, -10_000) < IDLE_NOTE_EVERY:
            return
        self.idle_noted[bot.id] = minutes
        store.event(day, bot.id, "analyse", time=time, intraday=True,
                    why="J'ai refait mon analyse avec les cours du moment : rien à faire pour l'instant.")

    def _stops(self, store: Store, bot: Bot, acct, broker: PaperBroker, day, session: pd.DataFrame, time: str):
        """Un stop se déclenche dès que le cours le franchit depuis l'achat : exécuté au seuil (ou au cours, s'il a sauté)."""
        fx = lambda t: broker.fx(t, day)  # noqa: E731
        entered = {r["ticker"]: r["time"] for r in store.db.execute(
            "SELECT ticker, MAX(time) AS time FROM trades WHERE bot_id = ? AND day = ? AND side = 'buy' GROUP BY ticker",
            (bot.id, str(day.date())))}
        for t, (level, direction) in bot.stops(acct.positions, store.get_params(bot.id), fx).items():
            pos = acct.positions.get(t)
            if pos is None:
                continue
            if opt.is_option(t) or t not in session or not session[t].notna().any():
                last = broker.data.price(t, day)
                prices = pd.Series([last]) if last else pd.Series(dtype=float)
            else:
                prices = session[t].dropna()
                since = entered.get(t)
                if since:
                    prices = prices[[i.strftime("%H:%M") >= since for i in prices.index]]
            if prices.empty:
                continue
            first = float(prices.iloc[0])
            below = direction == "below"
            hit = float(prices.min()) <= level if below else float(prices.max()) >= level
            if not hit:
                continue
            px = first if (first <= level if below else first >= level) else level
            fill = broker.sell(acct, t, pos.qty, day, px=px) if below and pos.qty > 0 else \
                broker._buy_to_close(acct, t, -pos.qty, day, px=px) if not below and pos.qty < 0 else None
            if fill is None:
                continue
            name = opt.describe(t) if opt.is_option(t) else t
            store.add_trade(bot.id, day, fill, time=time)
            store.event(day, bot.id, "stop", ticker=t, level=level, price=round(px, 2), direction=direction,
                        value_cad=round(fill.value_cad, 2), time=time, intraday=True)
            why = (f"Mon ordre stop sur {name} s'est déclenché à {px:.2f}. "
                   + ("Je limite la perte." if below else "Je rachète pour couper le risque."))
            store.journal(bot.id, day, f"{time} · {french(why)}")

    def _record(self, store: Store, bot: Bot, day, fills: list[Fill], time: str, why: str):
        for f in fills:
            store.add_trade(bot.id, day, f, time=time)
            store.event(day, bot.id, "trade", side=f.side, ticker=f.ticker, qty=f.qty, price=round(f.price, 2),
                        value_cad=round(f.value_cad, 2), time=time, why=why, intraday=True)

    def _status(self, store: Store, now: datetime, problem: str | None, time: str | None = None) -> dict:
        """Ce que le direct montre : l'heure de la dernière analyse et les occasions en attente de confirmation."""
        status = {"day": str(now.date()), "checked_at": time or now.strftime("%H:%M"), "every_min": self.every // 60,
                  "problem": problem,
                  "intents": self.intents}
        store.set_meta("intraday", json.dumps(status, ensure_ascii=False))
        store.commit()
        return status


def undo_early_session(store: Store, day: pd.Timestamp) -> bool:
    """Défait la séance du soir de `day` si elle a été jouée pendant la bourse (un `tick` lancé trop tôt, avec des
    cours pas encore définitifs). Rend True si elle l'a fait.

    On garde ce qui est vrai : les ordres de la veille passés à l'ouverture. On retire la valeur « de clôture », le
    classement, les dividendes et le forfait (rendus au cash, ils reviendront ce soir) et surtout les ordres décidés
    avec ces faux cours de clôture : sinon ils partiraient demain, en plus de ce que le Chubby a fait en séance.
    """
    d = str(day.date())
    if store.get_meta("last_day") != d:
        return False
    db = store.db
    for bot in db.execute("SELECT id FROM bots").fetchall():
        acct = store.load_account(bot["id"])
        for r in db.execute("SELECT kind, payload FROM events WHERE bot_id = ? AND day = ? AND kind IN ('dividend', 'fee')",
                            (bot["id"], d)):
            amount = json.loads(r["payload"] or "{}").get("amount_cad") or 0.0
            acct.cash += -amount if r["kind"] == "dividend" else amount
        store.save_account(acct)
    db.execute("DELETE FROM events WHERE day = ? AND kind IN ('dividend', 'fee', 'rank_change', 'leader')", (d,))
    db.execute("DELETE FROM snapshots WHERE day = ?", (d,))
    db.execute("DELETE FROM pending WHERE decided_on = ?", (d,))
    db.execute("DELETE FROM journal WHERE day = ? AND text NOT GLOB '[0-9][0-9]:[0-9][0-9] · *'", (d,))
    prev = db.execute("SELECT MAX(day) AS d FROM snapshots").fetchone()["d"]
    if prev:
        store.set_meta("last_day", prev)
    else:
        db.execute("DELETE FROM meta WHERE key = 'last_day'")
    store.set_meta("day_index", str(max(0, int(store.get_meta("day_index") or 1) - 1)))
    store.commit()
    return True


def status(store: Store) -> dict | None:
    raw = store.get_meta("intraday")
    return json.loads(raw) if raw else None


def trades_today(store: Store, bot_id: str, day: str) -> list[dict]:
    """Les ordres exécutés pendant la séance de ce jour-là, dans l'ordre."""
    out = []
    for r in store.db.execute("SELECT kind, payload FROM events WHERE bot_id = ? AND day = ? AND kind IN ('trade', 'stop')"
                              " ORDER BY id", (bot_id, day)):
        p = json.loads(r["payload"] or "{}")
        if not p.get("intraday"):
            continue
        side = p.get("side") or ("sell" if p.get("direction") == "below" else "buy")
        out.append({"time": p.get("time"), "side": side, "ticker": p.get("ticker"), "qty": p.get("qty"),
                    "price": p.get("price"), "value_cad": p.get("value_cad"),
                    "why": p.get("why") or "Mon ordre stop s'est déclenché."})
    return out

