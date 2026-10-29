"""L'arène : une séance = exécuter les ordres de la veille, verser les dividendes,
compter l'argent, classer les Chubbies, puis les laisser décider pour demain."""

from __future__ import annotations

import dataclasses
import logging

import pandas as pd

from .bots import Bot, BotContext
from .broker import PaperBroker
from .config import ArenaConfig
from .fmt import french
from . import options as opt
from .options import OptionData
from .scoring import metrics, score
from .store import Store

log = logging.getLogger(__name__)

PODIUM_XP = {0: 30, 1: 20, 2: 10}


class Arena:
    def __init__(self, store: Store, data, bots: list[Bot], cfg: ArenaConfig | None = None, chains=None):
        """`chains` : vraies chaînes d'options (arène en direct) ; sans elles, les options suivent le modèle."""
        self.store, self.bots = store, bots
        self.data = OptionData(data, chains) if data is not None and not isinstance(data, OptionData) else data
        self.cfg = cfg or ArenaConfig()
        self.broker = PaperBroker(self.cfg, self.data)
        # Chaque personnalité a son type de compte : USD avec forfait, ou CAD sans forfait.
        self._cad_broker = PaperBroker(dataclasses.replace(self.cfg, usd_account=False), self.data)
        for bot in bots:
            store.ensure_bot(bot, self.cfg.starting_cash, bot.defaults())
        store.commit()

    @property
    def last_day(self) -> pd.Timestamp | None:
        v = self.store.get_meta("last_day")
        return pd.Timestamp(v) if v else None

    def run(self, start=None, end=None, progress=None) -> int:
        """Joue toutes les séances pas encore jouées entre start et end.

        progress(fait, total, jour) est appelé après chaque séance, pour afficher l'avancement.
        """
        days = self.data.trading_days(start, end)
        if self.last_day is not None:
            days = [d for d in days if d > self.last_day]
        for i, day in enumerate(days, 1):
            self.tick(day, watch=i == len(days))  # la watchlist ne sert qu'au dernier soir : pas de calcul inutile
            if progress:
                progress(i, len(days), day)
        return len(days)

    def tick(self, day: pd.Timestamp, watch: bool = True):
        day_index = int(self.store.get_meta("day_index") or 0)
        for bot in self.bots:
            self._play(bot, day, day_index, watch)
        self._rank(day)
        chains = getattr(self.data, "chains", None)
        if chains is not None and chains.is_live(day):
            self.store.save_option_quotes(day, chains.used)
        self.store.set_meta("usdcad", str(self.data.usdcad(day)))  # sert au mode conseiller
        self.store.set_meta("last_day", str(day.date()))
        self.store.set_meta("day_index", str(day_index + 1))
        self.store.commit()

    def broker_for(self, bot: Bot) -> PaperBroker:
        return self.broker if self.cfg.usd_account and bot.usd_account else self._cad_broker

    def _record(self, bot: Bot, day, fills):
        for f in fills:
            if f is None:
                continue
            self.store.add_trade(bot.id, day, f)
            self.store.event(day, bot.id, "trade", side=f.side, ticker=f.ticker, qty=f.qty,
                             value_cad=round(f.value_cad, 2))

    def _play(self, bot: Bot, day: pd.Timestamp, day_index: int, watch: bool = False):
        broker = self.broker_for(bot)
        acct = self.store.load_account(bot.id)
        targets, trades = self.store.pop_pending(bot.id)
        if targets is not None:
            self._record(bot, day, broker.rebalance(acct, targets, day))
        self._record(bot, day, [broker.trade(acct, t, int(q), day) for t, q in trades])
        self._stops(bot, broker, acct, day)
        self._record(bot, day, broker.settle_expired(acct, day))
        for ticker, amount in broker.credit_dividends(acct, day):
            self.store.event(day, bot.id, "dividend", ticker=ticker, amount_cad=round(amount, 2))
        if acct.cash > 0:  # le cash rapporte des intérêts, versés chaque séance
            acct.cash += acct.cash * self.cfg.cash_rate / 252
        fee = broker.cfg.monthly_fee
        if fee and self._first_day_of_month(day):
            acct.cash -= fee
            self.store.event(day, bot.id, "fee", reason="forfait compte USD", amount_cad=fee)
        self.store.add_snapshot(bot.id, day, broker.equity(acct, day), acct.cash, broker.value_of(acct, day))

        row = self.store.bot_row(bot.id)
        ctx = BotContext(day, self.data, broker, acct, row["aggressiveness"], day_index,
                         self.store.get_params(bot.id))
        try:
            decision = bot.decide(ctx)
        except Exception:  # un bot qui plante ne doit pas arrêter l'arène
            log.exception("%s a planté le %s", bot.name, day.date())
            self.store.event(day, bot.id, "error", message="décision impossible")
            decision = None
        if decision is not None:
            if decision.targets is not None or decision.trades:
                self.store.set_pending(bot.id, decision.targets, day, decision.trades)
            for note in decision.notes:
                self.store.journal(bot.id, day, note)
        if watch:
            self._watch(bot, ctx, day)
        self.store.save_account(acct)

    def refresh_watchlists(self) -> int:
        """Recalcule la watchlist et les seuils de chaque Chubby pour la dernière séance, sans rien rejouer."""
        day = self.last_day
        if day is None:
            return 0
        day_index = int(self.store.get_meta("day_index") or 1) - 1
        for bot in self.bots:
            row = self.store.bot_row(bot.id)
            ctx = BotContext(day, self.data, self.broker_for(bot), self.store.load_account(bot.id), row["aggressiveness"],
                             day_index, self.store.get_params(bot.id))
            self._watch(bot, ctx, day)
        self.store.commit()
        return len(self.bots)

    def _watch(self, bot: Bot, ctx: BotContext, day: pd.Timestamp):
        try:
            items = bot.watchlist(ctx)
        except Exception:
            log.exception("Watchlist de %s impossible le %s", bot.name, day.date())
            return
        out, by_ticker = [], {}
        for ticker, why, *rest in items:
            trigs = rest[0] if rest and isinstance(rest[0], list) else [rest[0]] if rest else []
            trigs = [dict(t, label=french(t["label"]), then=french(t["then"])) for t in trigs if t]
            item = by_ticker.get(ticker)
            if item is not None:  # un titre, une seule ligne : ses raisons et ses seuils mis ensemble
                if french(why) not in item["why"]:
                    item["why"] += " · " + french(why)
                item["triggers"] += trigs
                item["held"] = item["held"] or any(t.get("held") for t in trigs) or why.startswith("détenu")
                item["trigger"] = item["triggers"][0] if item["triggers"] else None
                continue
            closes = self.data.closes(ticker, day, 2)
            price = self.data.price(ticker, day)
            change = float(closes.iloc[-1] / closes.iloc[-2] - 1) if len(closes) == 2 and closes.iloc[-2] else None
            item = by_ticker[ticker] = {
                "ticker": ticker, "why": french(why), "price": round(price, 2) if price else None,
                "change": round(change, 4) if change is not None else None,
                "triggers": trigs, "trigger": trigs[0] if trigs else None,
                "held": any(t.get("held") for t in trigs) or why.startswith("détenu")}
            out.append(item)
        self.store.set_watchlist(bot.id, day, out)

    def _stops(self, bot: Bot, broker: PaperBroker, acct, day: pd.Timestamp):
        """Ordres stop : déclenchés pendant la séance si le plus bas (ou le plus haut) du jour franchit le seuil."""
        levels = bot.stops(acct.positions, self.store.get_params(bot.id), lambda t: broker.fx(t, day))
        # Un titre acheté en direct après l'ouverture : le plus bas du jour a pu venir avant l'achat. Ses stops ont
        # déjà été surveillés en direct (intraday.py), on ne les rejoue pas sur les extrêmes de toute la journée.
        fresh = self.store.bought_during_session(bot.id, day)
        for ticker, (level, direction) in levels.items():
            if ticker in fresh:
                continue
            hit = broker.stop_out(acct, ticker, level, direction, day)
            if hit is None:
                continue
            fill, px = hit
            self._record(bot, day, [fill])
            name = opt.describe(ticker) if opt.is_option(ticker) else ticker
            gap = " (le titre a ouvert au-delà du seuil)" if abs(px - level) > 1e-9 else ""
            self.store.event(day, bot.id, "stop", ticker=ticker, level=level, price=round(px, 2),
                             direction=direction, value_cad=round(fill.value_cad, 2))
            self.store.journal(bot.id, day, f"Mon ordre stop sur {name} s'est déclenché à {px:.2f}{gap}. "
                                            + ("Je limite la perte." if direction == "below" else "Je rachète pour couper le risque."))

    def _first_day_of_month(self, day: pd.Timestamp) -> bool:
        previous = self.store.get_meta("last_day")
        return previous is None or pd.Timestamp(previous).month != day.month

    def _rank(self, day: pd.Timestamp):
        table = self.leaderboard()
        for rank, entry in enumerate(table):
            bot_id = entry["id"]
            prev = self.store.previous_rank(bot_id, day)
            self.store.set_rank(bot_id, day, entry["score"], rank)
            self.store.add_xp(bot_id, 10 + PODIUM_XP.get(rank, 0))
            if prev is not None and prev != rank:
                self.store.event(day, bot_id, "rank_change", old=prev + 1, new=rank + 1)
        if table:
            self.store.event(day, table[0]["id"], "leader", score=table[0]["score"])

    def leaderboard(self) -> list[dict]:
        out = []
        for bot in self.bots:
            eq = self.store.equity_series(bot.id)
            if eq.empty:
                continue
            m = metrics(eq, self.cfg.starting_cash, self.cfg.cash_rate)
            row = self.store.bot_row(bot.id)
            out.append({"id": bot.id, "name": bot.name, "personality": bot.personality,
                        "equity": float(eq.iloc[-1]), "score": score(m), "metrics": m,
                        "xp": row["xp"], "aggressiveness": row["aggressiveness"]})
        return sorted(out, key=lambda e: (-e["score"], -e["equity"]))
