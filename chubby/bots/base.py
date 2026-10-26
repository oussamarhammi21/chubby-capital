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
    trades: list[tuple[str, int]] = field(default_factory=list)  # ordres directs : quantité > 0 achète, < 0 vend


@dataclass(frozen=True)
class Param:
    """Un réglage qu'on peut ajuster (coach IA ou humain) sans changer la personnalité du bot."""

    default: float
    low: float
    high: float
    help: str
    integer: bool = False

    def clamp(self, value: float) -> float:
        v = max(self.low, min(self.high, float(value)))
        return float(round(v)) if self.integer else v


@dataclass
class BotContext:
    day: pd.Timestamp
    data: object
    broker: object
    account: Account
    aggressiveness: float  # réglé par le coaching, 1.0 = neutre
    day_index: int  # nombre de séances depuis le début de l'arène
    params: dict[str, float] = field(default_factory=dict)  # réglages actuels du bot

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

    def held_sessions(self, ticker: str) -> int:
        """Nombre de séances depuis l'achat d'une position."""
        return len(self.data.trading_days(self.account.positions[ticker].opened_on, self.day)) - 1


class Bot(ABC):
    id: str
    name: str
    personality: str
    universe: list[str]
    signals: list[str] = []  # tickers seulement observés (jamais achetés)
    params: dict[str, Param] = {}
    ai: bool = False  # vrai pour les versions coachées par Claude
    usd_account: bool = True  # forfait avec compte en USD ; False = compte en CAD sans forfait

    @abstractmethod
    def decide(self, ctx: BotContext) -> Decision:
        ...

    def p(self, ctx: BotContext, name: str) -> float:
        """Valeur actuelle d'un réglage, bornée."""
        return self.param(ctx.params, name)

    def param(self, params: dict[str, float], name: str) -> float:
        spec = self.params[name]
        return spec.clamp(params.get(name, spec.default))

    def stops(self, positions: dict, params: dict[str, float], fx) -> dict[str, tuple[float, str]]:
        """Ordres stop laissés chez le courtier : {ticker: (prix de déclenchement, "below" ou "above")}.

        Le prix est dans la devise du titre ; fx(ticker) donne le cours en CAD de cette devise.
        Ils ne dépendent que du coût d'achat et des réglages : rien du futur.
        """
        return {}

    def defaults(self) -> dict[str, float]:
        return {k: v.default for k, v in self.params.items()}

    @staticmethod
    def equal_weights(tickers: list[str], invested: float) -> dict[str, float]:
        return {t: invested / len(tickers) for t in tickers} if tickers else {}


def ai_version(cls: type[Bot]) -> type[Bot]:
    """Même personnalité et mêmes règles, mais Claude ajuste ses réglages chaque semaine."""
    return type(cls.__name__ + "IA", (cls,), {
        "id": cls.id + "-ia", "name": cls.name + " IA",
        "personality": cls.personality + " · coaché par Claude", "ai": True, "__doc__": cls.__doc__,
    })
