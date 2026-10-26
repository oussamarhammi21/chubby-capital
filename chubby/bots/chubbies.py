"""Les six Chubbies et leur personnalité d'investisseur."""

from __future__ import annotations

import pandas as pd

from .. import indicators as ind
from .base import Bot, BotContext, Decision


class Tortue(Bot):
    """Achète le marché canadien et américain, puis ne bouge presque plus."""

    id, name, personality = "tortue", "Tortue", "Indicielle passive"
    universe = ["XIC.TO", "VFV.TO"]

    def decide(self, ctx: BotContext) -> Decision:
        invested = ctx.invest_fraction(0.98)
        if not ctx.account.positions or ctx.has_idle_cash(invested):
            half = invested / 2
            return Decision({"XIC.TO": half, "VFV.TO": half},
                            ["J'achète l'indice canadien et l'indice américain. Ensuite, je fais la sieste."])
        return Decision(None)


class Flan(Bot):
    """Garde les meilleurs payeurs de dividendes, en évitant les rendements trop beaux pour être vrais."""

    id, name, personality = "flan", "Flan", "Rentier · dividendes"
    universe = ["ENB.TO", "RY.TO", "BNS.TO", "TD.TO", "BMO.TO", "FTS.TO", "BCE.TO", "T.TO",
                "TRP.TO", "CU.TO", "PPL.TO", "KO", "PG", "JNJ", "PEP", "VZ"]
    holdings = 8

    def decide(self, ctx: BotContext) -> Decision:
        invested = ctx.invest_fraction(0.97)
        if ctx.account.positions and not ctx.first_day_of_month() and not ctx.has_idle_cash(invested):
            return Decision(None)
        scored = []
        for t in self.universe:
            closes = ctx.closes(t, 220)
            px, sma200 = ctx.price(t), ind.sma(closes, 200)
            if not px or sma200 is None:
                continue
            yld = ctx.data.dividends_ttm(t, ctx.day) / px
            if 0.02 <= yld <= 0.09 and px > 0.9 * sma200:  # pas de « piège à rendement »
                scored.append((yld, t))
        picks = [t for _, t in sorted(scored, reverse=True)[: self.holdings]]
        if not picks:
            return Decision(None, ["Aucun dividende ne me plaît ce mois-ci, je garde mon cash."])
        top = ", ".join(picks[:3])
        return Decision(self.equal_weights(picks, invested),
                        [f"Mes {len(picks)} meilleurs payeurs ce mois-ci, dont {top}. Je réinvestis tout."])


class Dumpling(Bot):
    """Portefeuille diversifié à poids fixes, rééquilibré quand il dérive."""

    id, name, personality = "dumpling", "Dumpling", "Équilibré · diversification"
    mix = {"VCN.TO": 0.25, "VFV.TO": 0.25, "XEF.TO": 0.12, "XEC.TO": 0.05, "ZAG.TO": 0.28, "CGL.TO": 0.05}
    universe = list(mix)

    def decide(self, ctx: BotContext) -> Decision:
        scale = ctx.invest_fraction(0.98)
        targets = {t: w * scale for t, w in self.mix.items()}
        drift = max(abs(ctx.weights.get(t, 0.0) - w) for t, w in targets.items())
        if not ctx.account.positions or drift > 0.05 or ctx.has_idle_cash(scale):
            return Decision(targets, [f"Mes poids ont dérivé de {drift:.0%}, je rééquilibre."])
        return Decision(None)


class Mochi(Bot):
    """Suit la tendance : garde les titres les plus forts sur 3 mois, coupe ce qui casse.

    Pour limiter les frais, un titre détenu reste tant qu'il est dans le top 10 :
    Mochi ne remplace que ce qui décroche vraiment.
    """

    id, name, personality = "mochi", "Mochi", "Chasseur de tendance · momentum"
    universe = ["SHOP.TO", "RY.TO", "CNQ.TO", "CP.TO", "ATD.TO", "CSU.TO", "NVDA", "MSFT", "AAPL", "AMZN",
                "META", "GOOGL", "AVGO", "LLY", "COST", "NFLX", "AMD", "TSLA", "JPM", "V"]
    holdings, keep_rank = 5, 10

    def decide(self, ctx: BotContext) -> Decision:
        held = set(ctx.account.positions)
        broken = set()
        for t in held:
            closes = ctx.closes(t, 60)
            sma50 = ind.sma(closes, 50)
            if sma50 and closes.iloc[-1] < 0.97 * sma50:
                broken.add(t)
        weekly = ctx.day_index % 5 == 0 or not held
        if not weekly:
            if not broken:
                return Decision(None)
            keep = {t: w for t, w in ctx.weights.items() if t not in broken}
            return Decision(keep, [f"{', '.join(sorted(broken))} casse sa moyenne 50 jours, je coupe."])
        ranked = []
        for t in self.universe:
            closes = ctx.closes(t, 70)
            r63, sma50 = ind.pct_return(closes, 63), ind.sma(closes, 50)
            if r63 is not None and sma50 and closes.iloc[-1] > sma50 and r63 > 0:
                ranked.append((r63, t))
        order = [t for _, t in sorted(ranked, reverse=True)]
        kept = [t for t in order[: self.keep_rank] if t in held and t not in broken]
        new = [t for t in order if t not in kept][: max(0, self.holdings - len(kept))]
        if set(kept + new) == held:
            return Decision(None)
        if not kept and not new:
            return Decision({}, ["Aucune tendance claire. Je reste au cash."])
        weights = {t: w for t, w in ctx.weights.items() if t in kept}  # on ne touche pas aux gagnants
        room = max(0.0, ctx.invest_fraction(0.96) - sum(weights.values()))
        weights.update({t: room / len(new) for t in new} if new else {})
        dropped = sorted(held - set(kept))
        notes = [f"J'ajoute {', '.join(new)}." if new else "Je garde mes gagnants."]
        if dropped:
            notes.append(f"Je lâche {', '.join(dropped)}, qui sort du top {self.keep_rank}.")
        return Decision(weights, notes)


class Muffin(Bot):
    """Achète les bons titres quand ils sont survendus, revend quand ils rebondissent."""

    id, name, personality = "muffin", "Muffin", "Chasseur d'aubaines · value"
    universe = ["RY.TO", "TD.TO", "ENB.TO", "CNR.TO", "BCE.TO", "MFC.TO", "SU.TO", "NTR.TO",
                "JNJ", "PFE", "KO", "INTC", "CVS", "DIS", "NKE", "MMM"]
    max_positions, slot = 6, 0.15

    def decide(self, ctx: BotContext) -> Decision:
        weights = dict(ctx.weights)
        notes, changed = [], False
        for t, pos in ctx.account.positions.items():
            closes = ctx.closes(t, 30)
            r = ind.rsi(closes)
            unit_value = ctx.broker.value_of(ctx.account, ctx.day).get(t, 0.0) / pos.qty
            held_days = (ctx.day - pd.Timestamp(pos.opened_on)).days
            if (r is not None and r > 60) or held_days > 35 or unit_value < 0.92 * pos.avg_cost_cad:
                weights.pop(t, None)
                changed = True
                why = "il a rebondi" if r and r > 60 else "trop long" if held_days > 35 else "stop à -8 %"
                notes.append(f"Je revends {t} ({why}).")
        for t in self.universe:
            if len(weights) >= self.max_positions:
                break
            if t in weights:
                continue
            closes = ctx.closes(t, 220)
            r, sma200 = ind.rsi(closes), ind.sma(closes, 200)
            if r is not None and sma200 and r < 30 and closes.iloc[-1] > 0.95 * sma200:
                weights[t] = self.slot * ctx.aggressiveness
                changed = True
                notes.append(f"{t} est en solde (RSI {r:.0f}), j'en prends.")
        if not changed:
            return Decision(None)
        total = sum(weights.values())
        if total > 0.98:
            weights = {t: w * 0.98 / total for t, w in weights.items()}
        return Decision(weights, notes)


class Jalapeno(Bot):
    """Mise tout sur le FNB à levier le plus en forme. Les vraies options viendront en v2.

    Garde chaque position au moins 7 séances, sauf si elle casse (stop), et ne change
    de FNB que si le nouveau est nettement plus fort.
    """

    id, name, personality = "jalapeno", "Jalapeño", "Casse-cou · levier"
    universe = ["TQQQ", "SOXL", "UPRO", "TNA"]
    min_hold, switch_margin = 7, 0.03

    def decide(self, ctx: BotContext) -> Decision:
        strength = {}
        for t in self.universe:
            closes = ctx.closes(t, 30)
            r10, sma20 = ind.pct_return(closes, 10), ind.sma(closes, 20)
            if r10 is not None and sma20 and closes.iloc[-1] > sma20 and r10 > 0:
                strength[t] = r10
        best = max(strength, key=strength.get) if strength else None
        current = next(iter(ctx.account.positions), None)
        if current is None:
            if best is None:
                return Decision(None)
            return Decision({best: ctx.invest_fraction(0.95)},
                            [f"Je mets tout sur {best} ({strength[best]:+.0%} en 10 jours). YOLO."])
        closes = ctx.closes(current, 30)
        sma20 = ind.sma(closes, 20)
        if sma20 and closes.iloc[-1] < 0.95 * sma20:
            return Decision({}, [f"{current} s'effondre, je coupe et je sors au cash."])
        held_for = len(ctx.data.trading_days(ctx.account.positions[current].opened_on, ctx.day)) - 1
        if held_for < self.min_hold:
            return Decision(None)
        if best is None:
            return Decision({}, ["Tout baisse, je sors au cash… pour l'instant."])
        if best == current or strength[best] < strength.get(current, 0.0) + self.switch_margin:
            return Decision(None)
        return Decision({best: ctx.invest_fraction(0.95)},
                        [f"Je passe de {current} à {best} ({strength[best]:+.0%} en 10 jours)."])


ALL_BOTS = [Tortue, Flan, Dumpling, Mochi, Muffin, Jalapeno]


def all_bots() -> list[Bot]:
    return [cls() for cls in ALL_BOTS]


def all_tickers() -> list[str]:
    return sorted({t for cls in ALL_BOTS for t in cls.universe})
