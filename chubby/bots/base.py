"""Ce que chaque Chubby doit savoir faire : regarder le marché et dire quels poids il veut."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import pandas as pd

from ..broker import Account


@dataclass
class Decision:
    targets: dict[str, float] | None  # poids visés (somme <= 1), None = ne rien changer
    notes: list[str] = field(default_factory=list)  # ce que le bot écrit dans son journal


@dataclass
class BotContext:
    day: pd.Timestamp
    data: object
    broker: object
    account: Account
    aggressiveness: float  # réglé par le coaching, 1.0 = neutre
    day_index: int  # nombre de séances depuis le début de l'arène

    @property
    def equity(self) -> float:
        return self.broker.equity(self.account, self.day)

    @property
    def weights(self) -> dict[str, float]:
        return self.broker.weights(self.account, self.day)

    @property
    def cash_ratio(self) -> float:
        eq = self.equity
        return self.account.cash / eq if eq > 0 else 1.0

    def closes(self, ticker: str, lookback: int = 260) -> pd.Series:
        return self.data.closes(ticker, self.day, lookback)

    def price(self, ticker: str) -> float | None:
        return self.data.price(ticker, self.day)

    def has_idle_cash(self, invested: float, margin: float = 0.03) -> bool:
        """Vrai si le cash dépasse ce que le bot garde volontairement (dividendes à réinvestir, etc.)."""
        return self.cash_ratio > (1 - invested) + margin

    def invest_fraction(self, base: float) -> float:
        """Part du portefeuille investie, ajustée par l'agressivité."""
        return max(0.2, min(0.99, base * self.aggressiveness))

    def first_day_of_month(self) -> bool:
        prev = self.data.trading_days(end=self.day)
        return len(prev) < 2 or prev[-2].month != self.day.month


class Bot(ABC):
    id: str
    name: str
    personality: str
    universe: list[str]

    @abstractmethod
    def decide(self, ctx: BotContext) -> Decision:
        ...

    @staticmethod
    def equal_weights(tickers: list[str], invested: float) -> dict[str, float]:
        return {t: invested / len(tickers) for t in tickers} if tickers else {}
