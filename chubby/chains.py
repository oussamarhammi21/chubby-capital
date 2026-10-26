"""Vraies chaînes d'options (Yahoo Finance), pour l'arène en direct.

Dans l'arène en direct, une option est évaluée à son vrai prix du jour (milieu entre l'offre et
la demande, sinon dernier prix échangé), et les Chubbies choisissent parmi les échéances et prix
d'exercice qui existent vraiment. Les backtests, eux, gardent le modèle de Black-Scholes : il n'y a
pas d'historique gratuit des prix d'options.
"""

from __future__ import annotations

import logging
import math

import pandas as pd

from . import options as opt

log = logging.getLogger(__name__)


def _finite(v) -> float | None:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and v > 0 else None


def quote_of(row) -> float | None:
    """Prix d'une ligne de chaîne : milieu de l'offre et de la demande, sinon dernier prix."""
    bid, ask = _finite(row.get("bid")), _finite(row.get("ask"))
    if bid and ask and ask >= bid:
        return round((bid + ask) / 2, 2)
    return _finite(row.get("lastPrice"))


class YahooChains:
    """Chaînes d'options du jour, téléchargées à la demande et gardées en mémoire.

    `fetch_expiries(sous_jacent)` et `fetch_table(sous_jacent, échéance)` sont remplaçables pour les tests :
    la table a les colonnes strike, bid, ask, lastPrice et kind (C ou P).
    """

    def __init__(self, today, fetch_expiries=None, fetch_table=None):
        self.today = pd.Timestamp(today).normalize()
        self._fetch_expiries = fetch_expiries or _yahoo_expiries
        self._fetch_table = fetch_table or _yahoo_table
        self._expiries: dict[str, list[pd.Timestamp]] = {}
        self._tables: dict[tuple[str, pd.Timestamp], pd.DataFrame] = {}
        self.used: dict[str, float] = {}  # prix réels utilisés aujourd'hui, gardés dans la base

    def is_live(self, day) -> bool:
        return pd.Timestamp(day).normalize() == self.today

    def expiries(self, underlying: str) -> list[pd.Timestamp]:
        if underlying not in self._expiries:
            try:
                self._expiries[underlying] = sorted(pd.Timestamp(e) for e in self._fetch_expiries(underlying))
            except Exception as e:  # réseau, symbole sans options…
                log.warning("Chaîne d'options introuvable pour %s : %s", underlying, e)
                self._expiries[underlying] = []
        return self._expiries[underlying]

    def table(self, underlying: str, expiry: pd.Timestamp) -> pd.DataFrame:
        key = (underlying, pd.Timestamp(expiry))
        if key not in self._tables:
            try:
                self._tables[key] = self._fetch_table(underlying, key[1])
            except Exception as e:
                log.warning("Chaîne %s %s illisible : %s", underlying, key[1].date(), e)
                self._tables[key] = pd.DataFrame(columns=["strike", "bid", "ask", "lastPrice", "kind"])
        return self._tables[key]

    def quote(self, ticker: str) -> float | None:
        underlying, kind, strike, expiry = opt.parse(ticker)
        if expiry not in self.expiries(underlying):
            return None
        t = self.table(underlying, expiry)
        rows = t[(t["kind"] == kind) & ((t["strike"] - strike).abs() < 1e-6)]
        if rows.empty:
            return None
        price = quote_of(rows.iloc[0])
        if price is not None:
            self.used[ticker] = price
        return price

    def listed(self, underlying: str, kind: str, strike: float, expiry: pd.Timestamp) -> tuple[float, pd.Timestamp] | None:
        """Le contrat coté le plus proche de celui voulu : échéance la plus proche (au moins 14 jours),
        puis prix d'exercice le plus proche parmi ceux qui ont un prix."""
        choices = [e for e in self.expiries(underlying) if (e - self.today).days >= 14]
        if not choices:
            return None
        best = min(choices, key=lambda e: abs((e - expiry).days))
        t = self.table(underlying, best)
        t = t[t["kind"] == kind]
        t = t[[quote_of(r) is not None for _, r in t.iterrows()]]
        if t.empty:
            return None
        k = float(t.iloc[int((t["strike"] - strike).abs().to_numpy().argmin())]["strike"])
        return k, best


def _yahoo_expiries(underlying: str) -> list[str]:
    import yfinance as yf
    return list(yf.Ticker(underlying).options)


def _yahoo_table(underlying: str, expiry: pd.Timestamp) -> pd.DataFrame:
    import yfinance as yf
    chain = yf.Ticker(underlying).option_chain(str(expiry.date()))
    parts = []
    for kind, frame in (("C", chain.calls), ("P", chain.puts)):
        f = frame[["strike", "bid", "ask", "lastPrice"]].copy()
        f["kind"] = kind
        parts.append(f)
    return pd.concat(parts, ignore_index=True)
