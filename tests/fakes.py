"""Marché simulé et déterministe pour tester sans Internet."""

import numpy as np
import pandas as pd

from chubby.bots import all_tickers
from chubby.data import FrameData

DIVIDEND_PAYERS = {"ENB.TO", "RY.TO", "BNS.TO", "TD.TO", "BMO.TO", "FTS.TO", "BCE.TO", "T.TO", "TRP.TO",
                   "CU.TO", "PPL.TO", "KO", "PG", "JNJ", "PEP", "VZ", "XIC.TO", "VFV.TO", "VCN.TO", "ZAG.TO"}


def fake_market(days: int = 500, seed: int = 7, extra: dict | None = None) -> FrameData:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-02", periods=days)
    frames = {}
    for i, t in enumerate(sorted(set(all_tickers()) | {"XIC.TO"})):
        vol = 0.04 if t in {"TQQQ", "SOXL", "UPRO", "TNA"} else 0.015
        drift = rng.normal(0.0003, 0.0004)
        close = 50 * np.exp(np.cumsum(rng.normal(drift, vol, days)))
        open_ = close * (1 + rng.normal(0, 0.003, days))
        divs = np.zeros(days)
        if t in DIVIDEND_PAYERS:
            divs[60::63] = close[60::63] * 0.012  # environ 4,8 % par an, versé chaque trimestre
        frames[t] = pd.DataFrame({"Open": open_, "Close": close, "Dividends": divs}, index=idx)
    if extra:
        frames.update(extra)
    fx = pd.Series(1.35 + rng.normal(0, 0.005, days).cumsum() * 0.1, index=idx)
    return FrameData(frames, fx, "XIC.TO")
