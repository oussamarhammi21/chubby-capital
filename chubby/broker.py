"""Courtier fictif : exécute les ordres avec frais, glissement et conversion de devises."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from .config import ArenaConfig, currency_of
from .options import MULTIPLIER, is_option, parse


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
            out[t] = pos.qty * (px or 0.0) * self.multiplier(t) * self.fx(t, day)
        return out

    def equity(self, acct: Account, day, field: str = "Close") -> float:
        return acct.cash + sum(self.value_of(acct, day, field).values())

    def weights(self, acct: Account, day) -> dict[str, float]:
        eq = self.equity(acct, day)
        return {t: v / eq for t, v in self.value_of(acct, day).items()} if eq > 0 else {}

    # --- Exécution -------------------------------------------------------

    @staticmethod
    def multiplier(ticker: str) -> int:
        return MULTIPLIER if is_option(ticker) else 1

    def slippage(self, ticker: str) -> float:
        return self.cfg.option_slippage if is_option(ticker) else self.cfg.slippage

    def _unit_cost(self, ticker: str, px: float, day) -> tuple[float, float]:
        """Coût CAD d'une action (ou d'un contrat d'option) à l'achat, et part de frais incluse."""
        fx = self.fx(ticker, day)
        gross = px * (1 + self.slippage(ticker)) * self.multiplier(ticker) * fx
        fee = gross * self.cfg.trade_fx_fee if currency_of(ticker) == "USD" else 0.0
        if is_option(ticker):
            fee += self.cfg.option_fee_per_contract
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
        return Fill(ticker, "buy", qty, px * (1 + self.slippage(ticker)), currency_of(ticker), self.fx(ticker, day),
                    unit_fee * qty + self.cfg.commission, cost)

    def sell(self, acct: Account, ticker: str, qty: int, day, px: float | None = None) -> Fill | None:
        """Vente à l'ouverture, ou au prix `px` (stop-loss déclenché pendant la séance)."""
        pos = acct.positions.get(ticker)
        px = px or self.data.price(ticker, day, "Open")
        if not pos or not px or qty <= 0:
            return None
        qty = min(qty, pos.qty)
        fx = self.fx(ticker, day)
        gross = qty * px * (1 - self.slippage(ticker)) * self.multiplier(ticker) * fx
        fee = gross * self.cfg.trade_fx_fee if currency_of(ticker) == "USD" else 0.0
        if is_option(ticker):
            fee += self.cfg.option_fee_per_contract * qty
        proceeds = gross - fee - self.cfg.commission
        acct.cash += proceeds
        pos.qty -= qty
        if pos.qty == 0:
            del acct.positions[ticker]
        return Fill(ticker, "sell", qty, px * (1 - self.slippage(ticker)), currency_of(ticker), fx,
                    fee + self.cfg.commission, proceeds)

    def rebalance(self, acct: Account, targets: dict[str, float], day) -> list[Fill]:
        """Rapproche le portefeuille des poids visés, au prix d'ouverture du jour.

        Les ventes passent en premier pour libérer le cash des achats.
        """
        eq = self.equity(acct, day, "Open")
        values = self.value_of(acct, day, "Open")
        fills: list[Fill] = []
        for t in list(acct.positions):
            if acct.positions[t].qty < 0:
                continue  # les options vendues se gèrent avec des ordres directs
            excess = values.get(t, 0.0) - targets.get(t, 0.0) * eq
            if targets.get(t, 0.0) <= 0:
                qty = acct.positions[t].qty
            elif excess > self.cfg.min_trade_cad:
                px = self.data.price(t, day, "Open") or 0.0
                qty = math.floor(excess / (px * self.multiplier(t) * self.fx(t, day))) if px else 0
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

    def collateral(self, acct: Account, day) -> float:
        """Cash bloqué en garantie des puts vendus (strike × 100 par contrat)."""
        total = 0.0
        for t, pos in acct.positions.items():
            if is_option(t) and pos.qty < 0 and parse(t)[1] == "P":
                total += -pos.qty * parse(t)[2] * MULTIPLIER * self.fx(t, day)
        return total

    def free_cash(self, acct: Account, day) -> float:
        return acct.cash - self.collateral(acct, day)

    def trade(self, acct: Account, ticker: str, qty: int, day) -> Fill | None:
        """Ordre direct au prix d'ouverture : qty > 0 achète (ou rachète une option vendue), qty < 0 vend.

        Vendre une option qu'on ne détient pas ouvre une position vendue : on encaisse le premium,
        et un put vendu doit être entièrement garanti par du cash libre.
        """
        pos = acct.positions.get(ticker)
        if qty > 0 and pos and pos.qty < 0:
            return self._buy_to_close(acct, ticker, min(qty, -pos.qty), day)
        if qty > 0:
            return self.buy(acct, ticker, qty, day)
        if pos and pos.qty > 0:
            return self.sell(acct, ticker, -qty, day)
        if is_option(ticker):
            return self._sell_to_open(acct, ticker, -qty, day)
        return None  # pas de vente à découvert d'actions

    def _sell_to_open(self, acct: Account, ticker: str, qty: int, day) -> Fill | None:
        px = self.data.price(ticker, day, "Open")
        if not px or qty <= 0:
            return None
        underlying, kind, strike, _ = parse(ticker)
        fx = self.fx(ticker, day)
        if kind == "P" and self.free_cash(acct, day) < qty * strike * MULTIPLIER * fx:
            return None  # put non garanti : refusé
        if kind == "C":
            covered = acct.positions.get(underlying)
            short_calls = sum(-p.qty for t, p in acct.positions.items()
                              if is_option(t) and p.qty < 0 and parse(t)[0] == underlying and parse(t)[1] == "C")
            if not covered or covered.qty < (short_calls + qty) * MULTIPLIER:
                return None  # call non couvert : refusé
        gross = qty * px * (1 - self.slippage(ticker)) * MULTIPLIER * fx
        fee = gross * self.cfg.trade_fx_fee if currency_of(ticker) == "USD" else 0.0
        fee += self.cfg.option_fee_per_contract * qty
        proceeds = gross - fee
        acct.cash += proceeds
        pos = acct.positions.get(ticker)
        if pos:
            pos.avg_cost_cad = (pos.avg_cost_cad * -pos.qty + proceeds) / (-pos.qty + qty)
            pos.qty -= qty
        else:
            acct.positions[ticker] = Position(ticker, -qty, proceeds / qty, str(pd.Timestamp(day).date()))
        return Fill(ticker, "sell", qty, px * (1 - self.slippage(ticker)), currency_of(ticker), fx, fee, proceeds)

    def _buy_to_close(self, acct: Account, ticker: str, qty: int, day, px: float | None = None) -> Fill | None:
        px = px or self.data.price(ticker, day, "Open")
        if not px or qty <= 0:
            return None
        unit, unit_fee = self._unit_cost(ticker, px, day)
        cost = unit * qty
        acct.cash -= cost  # racheter une option vendue est toujours permis : c'est réduire le risque
        pos = acct.positions[ticker]
        pos.qty += qty
        if pos.qty == 0:
            del acct.positions[ticker]
        return Fill(ticker, "buy", qty, px * (1 + self.slippage(ticker)), currency_of(ticker), self.fx(ticker, day),
                    unit_fee * qty, cost)

    def stop_out(self, acct: Account, ticker: str, level: float, direction: str, day) -> tuple[Fill, float] | None:
        """Stop-loss posé chez le courtier : il se déclenche pendant la séance si le prix franchit `level`.

        direction "below" : vendre une position si le prix tombe à `level` ou plus bas.
        direction "above" : racheter une option vendue si son prix monte à `level` ou plus haut.
        Exécuté au prix du stop, ou à l'ouverture si le titre a déjà ouvert au-delà (un trou à l'ouverture).
        Renvoie l'exécution et le prix de déclenchement, ou None si le stop n'a pas été touché.
        """
        pos = acct.positions.get(ticker)
        opening = self.data.price(ticker, day, "Open")
        if not pos or not opening:
            return None
        if direction == "below":
            low = self.data.extreme(ticker, day, "low")
            if opening <= level:
                px = opening
            elif low is not None and low <= level:
                px = level
            else:
                return None
            fill = self.sell(acct, ticker, pos.qty, day, px=px) if pos.qty > 0 else None
        else:
            high = self.data.extreme(ticker, day, "high")
            if opening >= level:
                px = opening
            elif high is not None and high >= level:
                px = level
            else:
                return None
            fill = self._buy_to_close(acct, ticker, -pos.qty, day, px=px) if pos.qty < 0 else None
        return (fill, px) if fill else None

    def settle_expired(self, acct: Account, day) -> list[Fill]:
        """Options arrivées à expiry : réglées à leur valeur intrinsèque, ou exercées pour les options vendues."""
        fills = []
        for t, pos in list(acct.positions.items()):
            if not is_option(t):
                continue
            underlying, kind, strike, expiry = parse(t)
            if expiry > pd.Timestamp(day):
                continue
            spot = self.data.price(underlying, day) or 0.0
            intrinsic = max(spot - strike, 0.0) if kind == "C" else max(strike - spot, 0.0)
            if pos.qty < 0:
                fills.extend(self._settle_short(acct, t, pos, underlying, kind, strike, intrinsic, day))
                continue
            fx = self.fx(t, day)
            fee = self.cfg.trade_fx_fee if currency_of(t) == "USD" else 0.0
            proceeds = pos.qty * intrinsic * MULTIPLIER * fx * (1 - fee)
            acct.cash += proceeds
            del acct.positions[t]
            fills.append(Fill(t, "expire", pos.qty, intrinsic, currency_of(t), fx, 0.0, proceeds))
        return fills

    def _settle_short(self, acct, t, pos, underlying, kind, strike, intrinsic, day) -> list[Fill]:
        """Une option vendue expire sans valeur (on garde le premium) ou est exercée contre nous."""
        del acct.positions[t]
        n, fx = -pos.qty, self.fx(t, day)
        if intrinsic <= 0:
            return [Fill(t, "expire", n, 0.0, currency_of(t), fx, 0.0, 0.0)]
        shares = n * MULTIPLIER
        if kind == "P":  # on achète les actions au strike
            cost = shares * strike * fx
            acct.cash -= cost
            held = acct.positions.get(underlying)
            if held:
                held.avg_cost_cad = (held.avg_cost_cad * held.qty + cost) / (held.qty + shares)
                held.qty += shares
            else:
                acct.positions[underlying] = Position(underlying, shares, cost / shares, str(pd.Timestamp(day).date()))
            return [Fill(t, "assign", n, strike, currency_of(t), fx, 0.0, cost)]
        held = acct.positions.get(underlying)  # call couvert : nos actions partent au strike
        delivered = min(shares, held.qty if held else 0)
        proceeds = delivered * strike * fx - (shares - delivered) * intrinsic * fx
        acct.cash += proceeds
        if held:
            held.qty -= delivered
            if held.qty == 0:
                del acct.positions[underlying]
        return [Fill(t, "assign", n, strike, currency_of(t), fx, 0.0, proceeds)]

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
