"""Données de marché : prix d'ouverture et de clôture, dividendes, taux USD/CAD.

Toutes les lectures sont bornées à une date : un bot ne peut jamais voir le futur.
"""

from __future__ import annotations

import logging
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)


def _day(d) -> pd.Timestamp:
    ts = pd.Timestamp(d)
    return (ts.tz_localize(None) if ts.tzinfo else ts).normalize()


class FrameData:
    """Données en mémoire : un DataFrame (Open, Close, Dividends, et si possible High et Low) par ticker.

    Les lectures passent par des tableaux numpy et une recherche binaire sur les dates :
    un backtest en fait des centaines de milliers.
    """

    def __init__(self, frames: dict[str, pd.DataFrame], usdcad: pd.Series, calendar_ticker: str):
        self.frames = {t: f.sort_index() for t, f in frames.items() if not f.empty}
        self.fx = usdcad.sort_index()
        self.calendar_ticker = calendar_ticker
        self._index = {t: f.index for t, f in self.frames.items()}
        self._close_series = {t: f["Close"].astype(float) for t, f in self.frames.items()}
        self._close = {t: f["Close"].to_numpy(dtype=float) for t, f in self.frames.items()}
        self._open = {t: f["Open"].to_numpy(dtype=float) for t, f in self.frames.items()}
        self._div = {t: f["Dividends"].fillna(0).to_numpy(dtype=float) for t, f in self.frames.items()}
        # Plus haut et plus bas du jour (servent aux stop-loss) ; à défaut, l'ouverture et la clôture.
        self._low = {t: (f["Low"] if "Low" in f else f[["Open", "Close"]].min(axis=1)).to_numpy(dtype=float)
                     for t, f in self.frames.items()}
        self._high = {t: (f["High"] if "High" in f else f[["Open", "Close"]].max(axis=1)).to_numpy(dtype=float)
                      for t, f in self.frames.items()}
        self._fx = self.fx.to_numpy(dtype=float)

    @property
    def tickers(self) -> list[str]:
        return sorted(self.frames)

    def trading_days(self, start=None, end=None) -> list[pd.Timestamp]:
        idx = self.frames[self.calendar_ticker].index
        if start is not None:
            idx = idx[idx >= _day(start)]
        if end is not None:
            idx = idx[idx <= _day(end)]
        return list(idx)

    def has(self, ticker: str) -> bool:
        return ticker in self.frames

    def _known(self, ticker: str, day: pd.Timestamp) -> int:
        """Nombre de séances connues à cette date (la dernière est day lui-même s'il y a eu bourse)."""
        return int(self._index[ticker].searchsorted(day, side="right"))

    def price(self, ticker: str, day, field: str = "Close") -> float | None:
        """Dernier prix connu à cette date (le marché américain peut être fermé un jour où le TSX ouvre)."""
        if ticker not in self.frames:
            return None
        day = _day(day)
        n = self._known(ticker, day)
        if n == 0:
            return None
        if field == "Open" and self._index[ticker][n - 1] == day and not np.isnan(self._open[ticker][n - 1]):
            return float(self._open[ticker][n - 1])
        return float(self._close[ticker][n - 1])

    def extreme(self, ticker: str, day, which: str) -> float | None:
        """Plus bas (which="low") ou plus haut ("high") de la séance de ce jour-là, None s'il n'y a pas eu bourse."""
        if ticker not in self.frames:
            return None
        day = _day(day)
        n = self._known(ticker, day)
        if n == 0 or self._index[ticker][n - 1] != day:
            return None
        v = (self._low if which == "low" else self._high)[ticker][n - 1]
        return None if np.isnan(v) else float(v)

    def closes(self, ticker: str, day, lookback: int) -> pd.Series:
        if ticker not in self.frames:
            return pd.Series(dtype=float)
        n = self._known(ticker, _day(day))
        return self._close_series[ticker].iloc[max(0, n - lookback):n]

    def dividend(self, ticker: str, day) -> float:
        """Dividende par action dont la date ex-dividende est ce jour-là."""
        if ticker not in self.frames:
            return 0.0
        day = _day(day)
        n = self._known(ticker, day)
        return float(self._div[ticker][n - 1]) if n and self._index[ticker][n - 1] == day else 0.0

    def dividends_ttm(self, ticker: str, day) -> float:
        """Somme des dividendes par action sur les 365 derniers jours."""
        if ticker not in self.frames:
            return 0.0
        day = _day(day)
        lo = int(self._index[ticker].searchsorted(day - pd.Timedelta(days=365), side="left"))
        return float(self._div[ticker][lo:self._known(ticker, day)].sum())

    def usdcad(self, day) -> float:
        n = int(self.fx.index.searchsorted(_day(day), side="right"))
        return float(self._fx[n - 1] if n else self._fx[0])


class YFinanceData(FrameData):
    """Télécharge les historiques avec yfinance et les garde en cache CSV."""

    @classmethod
    def download(cls, tickers, start, end, calendar_ticker: str, fx_ticker: str,
                 cache_dir: str | Path | None = None, refresh: bool = False) -> "YFinanceData":
        import yfinance as yf

        # yfinance appelle des fonctions de pandas que pandas 3.1 annonce comme dépréciées :
        # des centaines d'avertissements sans conséquence pour nous, qu'on fait taire.
        warnings.filterwarnings("ignore", module=r"yfinance(\..*)?")

        cache = Path(cache_dir) if cache_dir else None
        if cache:
            cache.mkdir(parents=True, exist_ok=True)
        wanted = sorted(set(tickers) | {calendar_ticker, fx_ticker})
        frames: dict[str, pd.DataFrame] = {}
        for t in wanted:
            path = cache / f"{t.replace('=', '_')}_{_day(start).date()}_{_day(end).date()}.csv" if cache else None
            df = pd.read_csv(path, index_col=0, parse_dates=True) if path and path.exists() and not refresh else None
            if df is None or "Low" not in df:  # les anciens caches n'avaient pas le plus haut et le plus bas
                hist = yf.Ticker(t).history(start=_day(start), end=_day(end) + pd.Timedelta(days=1),
                                            auto_adjust=False, actions=True)
                if hist.empty:
                    log.warning("Aucune donnée pour %s, ticker ignoré.", t)
                    continue
                df = hist[["Open", "High", "Low", "Close", "Dividends"]].copy()
                df.index = pd.DatetimeIndex([_day(i) for i in df.index])
                if path:
                    df.to_csv(path)
            frames[t] = df.dropna(subset=["Close"])
        if calendar_ticker not in frames:
            raise RuntimeError(f"Impossible de charger le calendrier ({calendar_ticker}).")
        fx = frames.pop(fx_ticker)["Close"] if fx_ticker in frames else pd.Series([1.35], index=[_day(start)])
        return cls(frames, fx, calendar_ticker)
