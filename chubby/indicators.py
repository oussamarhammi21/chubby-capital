"""Indicateurs techniques simples, calculés sur des séries de clôtures (en numpy : appelés très souvent)."""

import numpy as np


def _values(closes) -> np.ndarray:
    return closes.to_numpy(dtype=float) if hasattr(closes, "to_numpy") else np.asarray(closes, dtype=float)


def sma(closes, n: int) -> float | None:
    v = _values(closes)
    return float(v[-n:].mean()) if len(v) >= n else None


def pct_return(closes, n: int) -> float | None:
    v = _values(closes)
    if len(v) <= n:
        return None
    return float(v[-1] / v[-1 - n] - 1)


def rsi(closes, n: int = 14) -> float | None:
    v = _values(closes)
    if len(v) <= n:
        return None
    delta = np.diff(v[-(n + 1):])
    gain = delta[delta > 0].sum() / n
    loss = -delta[delta < 0].sum() / n
    if loss == 0:
        return 100.0
    return float(100 - 100 / (1 + gain / loss))
