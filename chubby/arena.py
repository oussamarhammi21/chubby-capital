"""L'arène : une séance = exécuter les ordres de la veille, verser les dividendes,
compter l'argent, classer les Chubbies, puis les laisser décider pour demain."""

from __future__ import annotations

import logging

import pandas as pd

from .bots import Bot, BotContext
from .broker import PaperBroker
from .config import ArenaConfig
from .scoring import metrics, score
from .store import Store

log = logging.getLogger(__name__)

PODIUM_XP = {0: 30, 1: 20, 2: 10}


class Arena:
    def __init__(self, store: Store, data, bots: list[Bot], cfg: ArenaConfig | None = None):
        self.store, self.data, self.bots = store, data, bots
        self.cfg = cfg or ArenaConfig()
        self.broker = PaperBroker(self.cfg, data)
        for bot in bots:
            store.ensure_bot(bot, self.cfg.starting_cash)
        store.commit()

    @property
    def last_day(self) -> pd.Timestamp | None:
        v = self.store.get_meta("last_day")
        return pd.Timestamp(v) if v else None

    def run(self, start=None, end=None) -> int:
        """Joue toutes les séances pas encore jouées entre start et end."""
        days = self.data.trading_days(start, end)
        if self.last_day is not None:
            days = [d for d in days if d > self.last_day]
        for day in days:
            self.tick(day)
        return len(days)

    def tick(self, day: pd.Timestamp):
        day_index = int(self.store.get_meta("day_index") or 0)
        for bot in self.bots:
            self._play(bot, day, day_index)
        self._rank(day)
        self.store.set_meta("last_day", str(day.date()))
        self.store.set_meta("day_index", str(day_index + 1))
        self.store.commit()

    def _play(self, bot: Bot, day: pd.Timestamp, day_index: int):
        acct = self.store.load_account(bot.id)
        targets = self.store.pop_pending(bot.id)
        if targets is not None:
            for f in self.broker.rebalance(acct, targets, day):
                self.store.add_trade(bot.id, day, f)
                self.store.event(day, bot.id, "trade", side=f.side, ticker=f.ticker, qty=f.qty,
                                 value_cad=round(f.value_cad, 2))
        for ticker, amount in self.broker.credit_dividends(acct, day):
            self.store.event(day, bot.id, "dividend", ticker=ticker, amount_cad=round(amount, 2))
        if self.cfg.monthly_fee and self._first_day_of_month(day):
            acct.cash -= self.cfg.monthly_fee
            self.store.event(day, bot.id, "fee", reason="forfait compte USD", amount_cad=self.cfg.monthly_fee)
        self.store.add_snapshot(bot.id, day, self.broker.equity(acct, day), acct.cash,
                                self.broker.value_of(acct, day))

        row = self.store.bot_row(bot.id)
        ctx = BotContext(day, self.data, self.broker, acct, row["aggressiveness"], day_index)
        try:
            decision = bot.decide(ctx)
        except Exception:  # un bot qui plante ne doit pas arrêter l'arène
            log.exception("%s a planté le %s", bot.name, day.date())
            self.store.event(day, bot.id, "error", message="décision impossible")
            decision = None
        if decision is not None:
            if decision.targets is not None:
                self.store.set_pending(bot.id, decision.targets, day)
            for note in decision.notes:
                self.store.journal(bot.id, day, note)
        self.store.save_account(acct)

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
            m = metrics(eq, self.cfg.starting_cash)
            row = self.store.bot_row(bot.id)
            out.append({"id": bot.id, "name": bot.name, "personality": bot.personality,
                        "equity": float(eq.iloc[-1]), "score": score(m), "metrics": m,
                        "xp": row["xp"], "aggressiveness": row["aggressiveness"]})
        return sorted(out, key=lambda e: (-e["score"], -e["equity"]))
