"""Indicateurs techniques simples, calculés sur des séries de clôtures."""

import pandas as pd


def sma(closes: pd.Series, n: int) -> float | None:
    return float(closes.tail(n).mean()) if len(closes) >= n else None


def pct_return(closes: pd.Series, n: int) -> float | None:
    if len(closes) <= n:
        return None
    return float(closes.iloc[-1] / closes.iloc[-1 - n] - 1)


def rsi(closes: pd.Series, n: int = 14) -> float | None:
    if len(closes) <= n:
        return None
    delta = closes.diff().dropna().tail(n)
    gain = delta.clip(lower=0).mean()
    loss = -delta.clip(upper=0).mean()
    if loss == 0:
        return 100.0
    return float(100 - 100 / (1 + gain / loss))
