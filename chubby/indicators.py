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


def cross_level(closes, n: int, factor: float = 1.0) -> float | None:
    """Prix de la prochaine clôture qui la placerait exactement à `factor` × sa moyenne n jours (moyenne qui
    inclut cette clôture). Sert à traduire « sous sa SMA 50 » en un vrai prix à surveiller."""
    v = _values(closes)
    if len(v) < n - 1:
        return None
    return float(factor * v[-(n - 1):].sum() / (n - factor))


def rsi_level(closes, target: float, n: int = 14) -> float | None:
    """Prix de la prochaine clôture qui amènerait le RSI à `target` (None s'il faudrait bouger de plus de 40 %)."""
    v = _values(closes)
    if len(v) < n:
        return None
    lo, hi = v[-1] * 0.6, v[-1] * 1.4
    f = lambda p: rsi(np.append(v, p), n) - target  # noqa: E731  (croît avec le prix)
    if f(lo) > 0 or f(hi) < 0:
        return None
    for _ in range(40):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) < 0 else (lo, mid)
    return float((lo + hi) / 2)


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
