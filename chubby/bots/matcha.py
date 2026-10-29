"""Matcha, la Chubby qui apprend : un modèle de machine learning réentraîné chaque mois.

Chaque mois, elle apprend sur les deux dernières années quels signaux (trends, volatilité,
RSI, distance aux moyennes) ont annoncé les titres qui ont le mieux fait le mois suivant.
Puis elle achète les titres que son modèle préfère aujourd'hui.

Le modèle est une régression ridge sur des rangs : simple, rapide, et ses poids se lisent,
donc elle peut expliquer ce qu'elle a appris. Aucune donnée future n'est utilisée : un exemple
n'entre dans l'entraînement que si son résultat (21 séances plus tard) est déjà connu.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .base import Bot, BotContext, Decision, Param
from .chubbies import BROAD

FEATURES = {
    "r21": "trend 1 mois", "r63": "trend 3 mois", "r126": "trend 6 mois",
    "vol21": "volatilité", "rsi14": "RSI", "d50": "écart à la SMA 50", "d200": "écart à la SMA 200",
}
HORIZON = 21  # on prédit la performance relative du mois suivant
TRAIN_SESSIONS = 504  # deux ans d'historique
SAMPLE_EVERY = 5  # un jour sur cinq suffit et garde l'entraînement rapide
MIN_SAMPLES = 1500


def feature_frame(closes: pd.Series) -> pd.DataFrame:
    c = closes.astype(float)
    f = pd.DataFrame(index=c.index)
    for n in (21, 63, 126):
        f[f"r{n}"] = c.pct_change(n)
    f["vol21"] = c.pct_change().rolling(21).std()
    delta = c.diff()
    gain, loss = delta.clip(lower=0).rolling(14).mean(), (-delta.clip(upper=0)).rolling(14).mean()
    f["rsi14"] = (100 - 100 / (1 + gain / loss.replace(0, np.nan))).fillna(100)
    f["d50"] = c / c.rolling(50).mean() - 1
    f["d200"] = c / c.rolling(200).mean() - 1
    f["fwd"] = c.shift(-HORIZON) / c - 1  # résultat futur : servira seulement quand il sera connu
    return f


def _ranks(frame: pd.DataFrame) -> pd.DataFrame:
    """Rangs entre -0,5 et +0,5 : compare les titres entre eux le même jour."""
    return frame.rank(pct=True) - 0.5


class Matcha(Bot):
    """Apprend chaque mois quels signaux ont précédé les meilleurs titres, puis suit son modèle."""

    id, name, personality = "matcha", "Matcha", "Apprentie machine learning"
    universe = BROAD
    params = {
        "holdings": Param(8, 5, 12, "nombre de titres gardés", integer=True),
        "ridge": Param(5.0, 0.5, 50.0, "prudence du modèle (plus haut = conclusions plus prudentes)"),
    }
    rules = [
        "Chaque mois, elle s'entraîne sur les deux dernières années de 100 grandes sociétés.",
        "Elle mesure 7 signaux : trend sur 1, 3 et 6 mois, volatilité, RSI, écart aux SMA 50 et 200. "
        "Elle apprend lesquels ont annoncé les titres qui ont le mieux fait le mois suivant.",
        "Son modèle est une régression ridge (prudence {ridge:.1f}). Elle écrit dans son journal les signaux qu'elle a retenus.",
        "Elle achète à parts égales les {holdings:.0f} titres que son modèle préfère aujourd'hui.",
        "Elle n'apprend que sur des exemples dont le résultat était déjà connu : jamais de triche sur le futur.",
    ]

    def __init__(self):
        self._panel: dict[str, pd.DataFrame] | None = None
        self._panel_source = None
        self.weights: np.ndarray | None = None
        self._trained: tuple | None = None  # (jour, exemples) du dernier entraînement

    def _features(self, ctx: BotContext) -> dict[str, pd.DataFrame]:
        # Les indicateurs sont calculés une fois pour toute la série : chacun ne regarde que le passé,
        # et la colonne « fwd » (le futur) n'est lue que pour des jours dont le résultat est connu.
        source = getattr(ctx.data, "inner", ctx.data)
        if self._panel is None or self._panel_source is not source:
            frames = getattr(source, "frames", {})
            self._panel = {t: feature_frame(frames[t]["Close"]) for t in self.universe
                           if t in frames and len(frames[t]) > 250}
            self._panel_source = source
        return self._panel

    def train(self, ctx: BotContext) -> int:
        """Réentraîne le modèle avec seulement ce qui était connu ce jour-là. Renvoie le nombre d'exemples."""
        rows = []
        for t, f in self._features(ctx).items():
            known = f.loc[: ctx.day]
            if len(known) <= HORIZON + 200:
                continue
            cutoff = known.index[-HORIZON - 1]  # le résultat de ce jour-là est connu aujourd'hui
            window = known.loc[:cutoff].tail(TRAIN_SESSIONS).iloc[::SAMPLE_EVERY]
            rows.append(window.assign(ticker=t))
        if not rows:
            return 0
        data = pd.concat(rows).dropna()
        if len(data) < MIN_SAMPLES:
            return len(data)
        data.index.name = "day"
        grouped = data.groupby(level="day")
        x = grouped[list(FEATURES)].transform(lambda s: s.rank(pct=True) - 0.5).to_numpy()
        y = (grouped["fwd"].transform(lambda s: s.rank(pct=True) - 0.5)).to_numpy()
        lam = self.p(ctx, "ridge")
        self.weights = np.linalg.solve(x.T @ x + lam * len(x) / 100 * np.eye(x.shape[1]), x.T @ y)
        return len(data)

    def predict(self, ctx: BotContext) -> pd.Series:
        today = {}
        for t, f in self._features(ctx).items():
            if ctx.day in f.index:
                row = f.loc[ctx.day, list(FEATURES)]
                if not row.isna().any():
                    today[t] = row
        if len(today) < 10 or self.weights is None:
            return pd.Series(dtype=float)
        x = _ranks(pd.DataFrame(today).T.astype(float))
        return pd.Series(x.to_numpy() @ self.weights, index=x.index).sort_values(ascending=False)

    def explain(self) -> str:
        order = np.argsort(-np.abs(self.weights))[:3]
        names = list(FEATURES.values())
        return ", ".join(f"{names[i]} ({'+' if self.weights[i] > 0 else '−'})" for i in order)

    def watchlist(self, ctx):
        if self.weights is None:
            self.train(ctx)
        scores = self.predict(ctx)
        return [(t, f"{i}e selon son modèle (score {s:+.2f})") for i, (t, s) in enumerate(scores.items(), 1)
                if t not in ctx.account.positions][:8]

    def decide(self, ctx: BotContext) -> Decision:
        # En direct, chaque séance démarre un nouveau programme et le modèle est oublié : ne pas le prendre
        # pour un nouveau mois, sinon elle changerait ses choix chaque soir au lieu d'une fois par mois.
        if not ctx.first_day_of_month() and ctx.account.positions:
            return Decision(None)
        if ctx.intraday and self._trained and self._trained[0] == ctx.day and self.weights is not None:
            samples = self._trained[1]  # pendant la séance, un entraînement par jour suffit
        else:
            samples = self.train(ctx)
            self._trained = (ctx.day, samples)
        if self.weights is None:
            return Decision(None, [f"Pas encore assez d'historique pour apprendre ({samples} exemples)."])
        scores = self.predict(ctx)
        picks = list(scores[scores > 0].index[: int(self.p(ctx, "holdings"))])
        count = f"{samples:,}".replace(",", " ")
        notes = [f"Modèle réentraîné sur {count} exemples. Ce qui compte le plus : {self.explain()}."]
        if not picks:
            return Decision({}, notes + ["Mon modèle n'aime rien aujourd'hui, je reste au cash."])
        notes.append(f"Mes choix du mois : {', '.join(picks)}.")
        return Decision(self.equal_weights(picks, ctx.invest_fraction(0.96)), notes)
