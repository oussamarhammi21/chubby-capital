"""Courtier fictif : exécute les ordres avec frais, glissement et conversion de devises."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from .config import ArenaConfig, currency_of


@dataclass
class Position:
    ticker: str
    qty: int
    avg_cost_cad: float  # coût moyen par action, frais inclus
    opened_on: str


@dataclass
class Account:
    bot_id: str
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)


@dataclass
class Fill:
    ticker: str
    side: str  # "buy" ou "sell"
    qty: int
    price: float  # dans la devise du titre, glissement inclus
    currency: str
    fx: float
    fees_cad: float
    value_cad: float  # argent sorti (achat) ou entré (vente)


class PaperBroker:
    def __init__(self, cfg: ArenaConfig, data):
        self.cfg = cfg
        self.data = data

    def fx(self, ticker: str, day) -> float:
        return 1.0 if currency_of(ticker) == "CAD" else self.data.usdcad(day)

    def value_of(self, acct: Account, day, field: str = "Close") -> dict[str, float]:
        out = {}
        for t, pos in acct.positions.items():
            px = self.data.price(t, day, field)
            out[t] = pos.qty * (px or 0.0) * self.fx(t, day)
        return out

    def equity(self, acct: Account, day, field: str = "Close") -> float:
        return acct.cash + sum(self.value_of(acct, day, field).values())

    def weights(self, acct: Account, day) -> dict[str, float]:
        eq = self.equity(acct, day)
        return {t: v / eq for t, v in self.value_of(acct, day).items()} if eq > 0 else {}

    # --- Exécution -------------------------------------------------------

    def _unit_cost(self, ticker: str, px: float, day) -> tuple[float, float]:
        """Coût CAD d'une action à l'achat, et part de frais incluse."""
        fx = self.fx(ticker, day)
        gross = px * (1 + self.cfg.slippage) * fx
        fee = gross * self.cfg.trade_fx_fee if currency_of(ticker) == "USD" else 0.0
        return gross + fee, fee

    def buy(self, acct: Account, ticker: str, qty: int, day) -> Fill | None:
        px = self.data.price(ticker, day, "Open")
        if not px or qty <= 0:
            return None
        unit, unit_fee = self._unit_cost(ticker, px, day)
        cost = unit * qty + self.cfg.commission
        if cost > acct.cash + 1e-6:
            return None
        acct.cash -= cost
        pos = acct.positions.get(ticker)
        if pos:
            pos.avg_cost_cad = (pos.avg_cost_cad * pos.qty + cost) / (pos.qty + qty)
            pos.qty += qty
        else:
            acct.positions[ticker] = Position(ticker, qty, cost / qty, str(pd.Timestamp(day).date()))
        return Fill(ticker, "buy", qty, px * (1 + self.cfg.slippage), currency_of(ticker), self.fx(ticker, day),
                    unit_fee * qty + self.cfg.commission, cost)

    def sell(self, acct: Account, ticker: str, qty: int, day) -> Fill | None:
        pos = acct.positions.get(ticker)
        px = self.data.price(ticker, day, "Open")
        if not pos or not px or qty <= 0:
            return None
        qty = min(qty, pos.qty)
        fx = self.fx(ticker, day)
        gross = qty * px * (1 - self.cfg.slippage) * fx
        fee = gross * self.cfg.trade_fx_fee if currency_of(ticker) == "USD" else 0.0
        proceeds = gross - fee - self.cfg.commission
        acct.cash += proceeds
        pos.qty -= qty
        if pos.qty == 0:
            del acct.positions[ticker]
        return Fill(ticker, "sell", qty, px * (1 - self.cfg.slippage), currency_of(ticker), fx,
                    fee + self.cfg.commission, proceeds)

    def rebalance(self, acct: Account, targets: dict[str, float], day) -> list[Fill]:
        """Rapproche le portefeuille des poids visés, au prix d'ouverture du jour.

        Les ventes passent en premier pour libérer le cash des achats.
        """
        eq = self.equity(acct, day, "Open")
        values = self.value_of(acct, day, "Open")
        fills: list[Fill] = []
        for t in list(acct.positions):
            excess = values.get(t, 0.0) - targets.get(t, 0.0) * eq
            if targets.get(t, 0.0) <= 0:
                qty = acct.positions[t].qty
            elif excess > self.cfg.min_trade_cad:
                px = self.data.price(t, day, "Open") or 0.0
                qty = math.floor(excess / (px * self.fx(t, day))) if px else 0
            else:
                continue
            f = self.sell(acct, t, qty, day)
            if f:
                fills.append(f)
        for t, w in sorted(targets.items(), key=lambda kv: -kv[1]):
            if w <= 0 or not self.data.price(t, day, "Open"):
                continue
            current = self.value_of(acct, day, "Open").get(t, 0.0)
            gap = min(w * eq - current, acct.cash - self.cfg.commission)
            if gap <= self.cfg.min_trade_cad:
                continue
            unit, _ = self._unit_cost(t, self.data.price(t, day, "Open"), day)
            f = self.buy(acct, t, math.floor(gap / unit), day)
            if f:
                fills.append(f)
        return fills

    def credit_dividends(self, acct: Account, day) -> list[tuple[str, float]]:
        """Verse les dividendes des titres détenus à leur date ex-dividende."""
        paid = []
        for t, pos in acct.positions.items():
            per_share = self.data.dividend(t, day)
            if per_share <= 0:
                continue
            amount = pos.qty * per_share * self.fx(t, day)
            if currency_of(t) == "USD":
                amount *= (1 - self.cfg.withholding) * (1 - self.cfg.trade_fx_fee)
            acct.cash += amount
            paid.append((t, amount))
        return paid
