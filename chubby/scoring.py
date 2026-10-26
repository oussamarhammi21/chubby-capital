"""Score de 0 à 100 qui départage les Chubbies.

40 % rendement annuel, 30 % rendement ajusté au risque (Sharpe), 20 % pire baisse (drawdown),
10 % régularité (part des semaines positives). Un bot ne peut pas gagner seulement
en prenant des risques énormes.
"""

from dataclasses import dataclass
import math

import pandas as pd


@dataclass
class Metrics:
    total_return: float
    volatility: float
    sharpe: float
    max_drawdown: float  # négatif, ex. -0.12
    regularity: float  # entre 0 et 1
    annual_return: float | None = None  # rendement par an (sur moins d'un an : le rendement total)


def metrics(equity: pd.Series, starting_cash: float) -> Metrics:
    eq = pd.concat([pd.Series([starting_cash], index=[equity.index[0] - pd.Timedelta(days=1)]), equity]) \
        if len(equity) else pd.Series([starting_cash])
    daily = eq.pct_change().dropna()
    std = float(daily.std()) if len(daily) > 1 else 0.0
    sharpe = float(daily.mean()) / std * math.sqrt(252) if std > 0 else 0.0
    drawdown = float((eq / eq.cummax() - 1).min())
    weekly = eq.resample("W").last().pct_change().dropna() if isinstance(eq.index, pd.DatetimeIndex) else pd.Series()
    regularity = float((weekly > 0).mean()) if len(weekly) else 0.5
    total = float(eq.iloc[-1] / starting_cash - 1)
    years = max(len(daily) / 252, 1.0)  # on n'annualise pas une saison de moins d'un an
    annual = (1 + total) ** (1 / years) - 1 if total > -1 else -1.0
    return Metrics(total, std * math.sqrt(252), sharpe, drawdown, regularity, annual)


def _clamp(v: float) -> float:
    return max(0.0, min(100.0, v))


def score(m: Metrics) -> int:
    # Rendement par an : sinon, sur plusieurs années, tous les bots dépassent +20 % et le critère ne départage plus.
    yearly = m.annual_return if m.annual_return is not None else m.total_return
    ret_s = _clamp(50 + yearly * 250)  # -20 %/an -> 0, +20 %/an -> 100
    sharpe_s = _clamp(50 + m.sharpe * 25)  # Sharpe 2 -> 100
    dd_s = _clamp(100 + m.max_drawdown * 300)  # -33 % -> 0
    reg_s = m.regularity * 100
    return round(0.4 * ret_s + 0.3 * sharpe_s + 0.2 * dd_s + 0.1 * reg_s)
