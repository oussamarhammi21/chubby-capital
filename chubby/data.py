"""Données de marché : prix d'ouverture et de clôture, dividendes, taux USD/CAD.

Toutes les lectures sont bornées à une date : un bot ne peut jamais voir le futur.
"""

from __future__ import annotations

import logging
import warnings
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)


def _day(d) -> pd.Timestamp:
    ts = pd.Timestamp(d)
    return (ts.tz_localize(None) if ts.tzinfo else ts).normalize()


class FrameData:
    """Données en mémoire : un DataFrame (Open, Close, Dividends) par ticker."""

    def __init__(self, frames: dict[str, pd.DataFrame], usdcad: pd.Series, calendar_ticker: str):
        self.frames = {t: f.sort_index() for t, f in frames.items() if not f.empty}
        self.fx = usdcad.sort_index()
        self.calendar_ticker = calendar_ticker

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

    def price(self, ticker: str, day, field: str = "Close") -> float | None:
        """Dernier prix connu à cette date (le marché américain peut être fermé un jour où le TSX ouvre)."""
        f = self.frames.get(ticker)
        if f is None:
            return None
        day = _day(day)
        if field == "Open" and day in f.index:
            return float(f.at[day, "Open"])
        past = f.loc[:day, "Close"]
        return float(past.iloc[-1]) if len(past) else None

    def closes(self, ticker: str, day, lookback: int) -> pd.Series:
        f = self.frames.get(ticker)
        if f is None:
            return pd.Series(dtype=float)
        return f.loc[: _day(day), "Close"].tail(lookback)

    def dividend(self, ticker: str, day) -> float:
        """Dividende par action dont la date ex-dividende est ce jour-là."""
        f = self.frames.get(ticker)
        day = _day(day)
        if f is None or day not in f.index:
            return 0.0
        return float(f.at[day, "Dividends"])

    def dividends_ttm(self, ticker: str, day) -> float:
        """Somme des dividendes par action sur les 365 derniers jours."""
        f = self.frames.get(ticker)
        if f is None:
            return 0.0
        day = _day(day)
        return float(f.loc[day - pd.Timedelta(days=365) : day, "Dividends"].sum())

    def usdcad(self, day) -> float:
        past = self.fx.loc[: _day(day)]
        return float(past.iloc[-1]) if len(past) else float(self.fx.iloc[0])


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
            if path and path.exists() and not refresh:
                df = pd.read_csv(path, index_col=0, parse_dates=True)
            else:
                hist = yf.Ticker(t).history(start=_day(start), end=_day(end) + pd.Timedelta(days=1),
                                            auto_adjust=False, actions=True)
                if hist.empty:
                    log.warning("Aucune donnée pour %s, ticker ignoré.", t)
                    continue
                df = hist[["Open", "Close", "Dividends"]].copy()
                df.index = pd.DatetimeIndex([_day(i) for i in df.index])
                if path:
                    df.to_csv(path)
            frames[t] = df.dropna(subset=["Close"])
        if calendar_ticker not in frames:
            raise RuntimeError(f"Impossible de charger le calendrier ({calendar_ticker}).")
        fx = frames.pop(fx_ticker)["Close"] if fx_ticker in frames else pd.Series([1.35], index=[_day(start)])
        return cls(frames, fx, calendar_ticker)
