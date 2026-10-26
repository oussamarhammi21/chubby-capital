"""Options d'achat (calls) et de vente (puts), évaluées avec Black-Scholes.

Il n'existe pas d'historique gratuit des prix d'options : on les calcule à partir du sous-jacent,
avec une volatilité implicite estimée sur la volatilité réalisée des 21 dernières séances.
Symbole : OPT:<sous-jacent>:<C|P>:<prix d'exercice>:<échéance AAAA-MM-JJ>.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

PREFIX = "OPT:"
MULTIPLIER = 100  # un contrat = 100 actions
RISK_FREE = 0.04
IV_PREMIUM = 1.1  # la volatilité implicite est en général un peu au-dessus de la réalisée


def is_option(ticker: str) -> bool:
    return ticker.startswith(PREFIX)


def symbol(underlying: str, kind: str, strike: float, expiry) -> str:
    return f"{PREFIX}{underlying}:{kind}:{strike:g}:{pd.Timestamp(expiry).date()}"


def parse(ticker: str) -> tuple[str, str, float, pd.Timestamp]:
    _, underlying, kind, strike, expiry = ticker.split(":")
    return underlying, kind, float(strike), pd.Timestamp(expiry)


def describe(ticker: str) -> str:
    underlying, kind, strike, expiry = parse(ticker)
    return f"{'call' if kind == 'C' else 'put'} {underlying} {strike:g} ({expiry.date()})"


def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def black_scholes(spot: float, strike: float, years: float, vol: float, kind: str, rate: float = RISK_FREE) -> float:
    intrinsic = max(spot - strike, 0.0) if kind == "C" else max(strike - spot, 0.0)
    if years <= 0 or vol <= 0:
        return intrinsic
    d1 = (math.log(spot / strike) + (rate + vol * vol / 2) * years) / (vol * math.sqrt(years))
    d2 = d1 - vol * math.sqrt(years)
    if kind == "C":
        return spot * _ncdf(d1) - strike * math.exp(-rate * years) * _ncdf(d2)
    return strike * math.exp(-rate * years) * _ncdf(-d2) - spot * _ncdf(-d1)


def third_friday(year: int, month: int) -> pd.Timestamp:
    first = pd.Timestamp(year=year, month=month, day=1)
    return first + pd.Timedelta(days=(4 - first.weekday()) % 7 + 14)


def pick_expiry(day, days_out: float) -> pd.Timestamp:
    """Échéance mensuelle standard (3e vendredi) la plus proche de day + days_out, au moins 3 semaines."""
    target = pd.Timestamp(day) + pd.Timedelta(days=days_out)
    candidates = []
    for k in range(-1, 3):
        m = (target.month - 1 + k) % 12 + 1
        y = target.year + (target.month - 1 + k) // 12
        candidates.append(third_friday(y, m))
    valid = [c for c in candidates if (c - pd.Timestamp(day)).days >= 21]
    return min(valid, key=lambda c: abs((c - target).days))


def pick_strike(spot: float, otm: float, kind: str) -> float:
    raw = spot * (1 + otm) if kind == "C" else spot * (1 - otm)
    step = 1 if spot < 100 else 5 if spot < 500 else 10
    return float(round(raw / step) * step)


class OptionData:
    """Enveloppe les données de marché : les symboles d'options ont un prix calculé, le reste est inchangé."""

    def __init__(self, data):
        self.inner = data

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def volatility(self, underlying: str, day) -> float:
        closes = self.inner.closes(underlying, day, 22)
        if len(closes) < 10:
            return 0.3
        vol = float(np.log(closes).diff().dropna().std() * math.sqrt(252)) * IV_PREMIUM
        return min(1.5, max(0.12, vol))

    def price(self, ticker: str, day, field: str = "Close") -> float | None:
        if not is_option(ticker):
            return self.inner.price(ticker, day, field)
        underlying, kind, strike, expiry = parse(ticker)
        spot = self.inner.price(underlying, day, field)
        if not spot:
            return None
        years = max((expiry - pd.Timestamp(day)).days, 0) / 365
        return max(0.01, round(black_scholes(spot, strike, years, self.volatility(underlying, day), kind), 2))

    def closes(self, ticker: str, day, lookback: int) -> pd.Series:
        return pd.Series(dtype=float) if is_option(ticker) else self.inner.closes(ticker, day, lookback)

    def dividend(self, ticker: str, day) -> float:
        return 0.0 if is_option(ticker) else self.inner.dividend(ticker, day)

    def dividends_ttm(self, ticker: str, day) -> float:
        return 0.0 if is_option(ticker) else self.inner.dividends_ttm(ticker, day)
