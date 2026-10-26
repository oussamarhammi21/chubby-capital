"""Les Chubbies et leur personnalité d'investisseur."""

from __future__ import annotations

import pandas as pd

from .. import indicators as ind
from .. import options as opt
from .base import Bot, BotContext, Decision, Param

# Grandes capitalisations du TSX et de Wall Street. Liste large plutôt que des gagnants choisis à la main.
CANADA_LARGE = ["RY.TO", "TD.TO", "SHOP.TO", "ENB.TO", "BN.TO", "CNQ.TO", "CP.TO", "CNR.TO", "BMO.TO", "BNS.TO",
                "CM.TO", "TRI.TO", "ATD.TO", "CSU.TO", "SU.TO", "MFC.TO", "WCN.TO", "TRP.TO", "IFC.TO", "SLF.TO",
                "NTR.TO", "ABX.TO", "AEM.TO", "FNV.TO", "WPM.TO", "CVE.TO", "IMO.TO", "TOU.TO", "GWO.TO", "POW.TO",
                "L.TO", "DOL.TO", "QSR.TO", "BCE.TO", "T.TO", "FTS.TO", "EMA.TO", "CCO.TO", "TECK-B.TO", "MG.TO",
                "GIB-A.TO", "WSP.TO", "NA.TO", "FFH.TO", "SAP.TO", "MRU.TO", "CTC-A.TO", "X.TO", "OTEX.TO", "H.TO"]
US_LARGE = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "AVGO", "TSLA", "BRK-B", "LLY", "JPM", "V", "UNH",
            "XOM", "MA", "JNJ", "PG", "HD", "COST", "ABBV", "MRK", "WMT", "NFLX", "CRM", "BAC", "CVX", "KO", "AMD",
            "PEP", "ORCL", "ADBE", "TMO", "LIN", "ACN", "MCD", "CSCO", "ABT", "WFC", "DIS", "INTC", "QCOM", "TXN",
            "CAT", "IBM", "GE", "AMGN", "PFE", "NKE", "BA", "UPS"]
BROAD = CANADA_LARGE + US_LARGE


class Tortue(Bot):
    """Achète le marché canadien et américain, puis ne bouge presque plus."""

    id, name, personality = "tortue", "Tortue", "Indicielle passive"
    universe = ["XIC.TO", "VFV.TO"]
    usd_account = False  # que des FNB canadiens : le forfait USD ne lui servirait à rien
    params = {"canada": Param(0.5, 0.2, 0.8, "part de l'indice canadien (XIC) ; le reste va au S&P 500 (VFV)")}
    rules = [
        "Met {canada:.0%} dans l'indice canadien (XIC, ~220 sociétés du TSX) et le reste dans le S&P 500 (VFV, 500 grandes sociétés américaines).",
        "Garde environ 2 % en cash. Réinvestit les dividendes quand ils s'accumulent.",
        "Ne rééquilibre que si un poids s'éloigne de plus de 10 points de sa cible. Sinon, elle ne fait rien : c'est le groupe témoin de l'arène.",
    ]

    def watchlist(self, ctx):
        invested, canada = ctx.invest_fraction(0.98), self.p(ctx, "canada")
        targets = {"XIC.TO": invested * canada, "VFV.TO": invested * (1 - canada)}
        return [(t, f"poids {ctx.weights.get(t, 0.0):.0%} pour une cible de {w:.0%} ; rééquilibre à 10 points d'écart")
                for t, w in targets.items()]

    def decide(self, ctx: BotContext) -> Decision:
        invested, canada = ctx.invest_fraction(0.98), self.p(ctx, "canada")
        targets = {"XIC.TO": invested * canada, "VFV.TO": invested * (1 - canada)}
        drift = max(abs(ctx.weights.get(t, 0.0) - w) for t, w in targets.items())
        if not ctx.account.positions or ctx.has_idle_cash(invested) or drift > 0.10:
            return Decision(targets, ["J'achète l'indice canadien et l'indice américain. Ensuite, je fais la sieste."])
        return Decision(None)


class Flan(Bot):
    """Garde les meilleurs payeurs de dividendes, en évitant les rendements trop beaux pour être vrais."""

    id, name, personality = "flan", "Flan", "Rentier · dividendes"
    universe = ["ENB.TO", "RY.TO", "BNS.TO", "TD.TO", "BMO.TO", "FTS.TO", "BCE.TO", "T.TO",
                "TRP.TO", "CU.TO", "PPL.TO", "KO", "PG", "JNJ", "PEP", "VZ"]
    usd_account = False  # surtout canadien : il paie la conversion sur ses quelques titres américains
    params = {
        "holdings": Param(8, 4, 12, "nombre de titres gardés", integer=True),
        "min_yield": Param(0.02, 0.01, 0.04, "rendement en dividendes minimum"),
        "max_yield": Param(0.09, 0.06, 0.12, "rendement maximum (au-delà, c'est souvent un piège)"),
    }
    rules = [
        "Choisit parmi 16 grands payeurs de dividendes (banques, pipelines, télécoms, services publics, Coca-Cola, P&G…).",
        "Rendement des 12 derniers mois entre {min_yield:.1%} et {max_yield:.1%} : au-delà, le dividende risque d'être coupé.",
        "Écarte un titre qui vaut moins de 90 % de sa moyenne 200 jours (une société en chute libre).",
        "Garde les {holdings:.0f} meilleurs rendements à parts égales, et revoit sa liste le premier jour de chaque mois.",
        "Les dividendes de ses quelques titres américains subissent une retenue de 15 % (dans un CELI).",
    ]

    def watchlist(self, ctx):
        lo, hi = self.p(ctx, "min_yield"), self.p(ctx, "max_yield")
        out = []
        for t in self.universe:
            if t in ctx.account.positions:
                continue
            px, sma200 = ctx.price(t), ind.sma(ctx.closes(t, 220), 200)
            if not px or sma200 is None:
                continue
            yld = ctx.data.dividends_ttm(t, ctx.day) / px
            why = ("rendement trop bas" if yld < lo else "rendement trop beau pour être vrai" if yld > hi
                   else "sous sa moyenne 200 jours" if px <= 0.9 * sma200 else "éligible, pas dans son top")
            out.append((yld, t, f"rendement {yld:.1%} : {why}"))
        return [(t, why) for _, t, why in sorted(out, reverse=True)[:8]]

    def decide(self, ctx: BotContext) -> Decision:
        invested = ctx.invest_fraction(0.97)
        if ctx.account.positions and not ctx.first_day_of_month() and not ctx.has_idle_cash(invested):
            return Decision(None)
        lo, hi = self.p(ctx, "min_yield"), self.p(ctx, "max_yield")
        scored = []
        for t in self.universe:
            closes = ctx.closes(t, 220)
            px, sma200 = ctx.price(t), ind.sma(closes, 200)
            if not px or sma200 is None:
                continue
            yld = ctx.data.dividends_ttm(t, ctx.day) / px
            if lo <= yld <= hi and px > 0.9 * sma200:
                scored.append((yld, t))
        picks = [t for _, t in sorted(scored, reverse=True)[: int(self.p(ctx, "holdings"))]]
        if not picks:
            return Decision(None, ["Aucun dividende ne me plaît ce mois-ci, je garde mon cash."])
        return Decision(self.equal_weights(picks, invested),
                        [f"Mes {len(picks)} meilleurs payeurs ce mois-ci, dont {', '.join(picks[:3])}. Je réinvestis tout."])


class Dumpling(Bot):
    """Portefeuille diversifié (actions de partout, obligations, un peu d'or), rééquilibré quand il dérive."""

    id, name, personality = "dumpling", "Dumpling", "Équilibré · diversification"
    stocks = {"VCN.TO": 0.25, "VFV.TO": 0.25, "XEF.TO": 0.12, "XEC.TO": 0.05}
    universe = list(stocks) + ["ZAG.TO", "CGL.TO"]
    usd_account = False
    params = {
        "bonds": Param(0.28, 0.10, 0.50, "part en obligations (ZAG)"),
        "gold": Param(0.05, 0.0, 0.15, "part en or (CGL)"),
        "drift": Param(0.05, 0.03, 0.10, "écart toléré avant de rééquilibrer"),
    }
    rules = [
        "Mélange fixe : actions canadiennes (VCN), américaines (VFV), internationales (XEF) et émergentes (XEC), "
        "{bonds:.0%} d'obligations (ZAG) et {gold:.0%} d'or (CGL).",
        "Quand un poids s'éloigne de plus de {drift:.0%} de sa cible, il revend ce qui a trop monté et rachète ce qui a baissé.",
        "Les obligations et l'or amortissent les chutes de la bourse : il gagne moins dans les belles années, perd moins dans les mauvaises.",
    ]

    def _targets(self, ctx):
        scale = ctx.invest_fraction(0.98)
        bonds, gold = self.p(ctx, "bonds"), self.p(ctx, "gold")
        equity_share = max(0.0, 1 - bonds - gold) / sum(self.stocks.values())
        mix = {t: w * equity_share for t, w in self.stocks.items()} | {"ZAG.TO": bonds, "CGL.TO": gold}
        return {t: w * scale for t, w in mix.items() if w > 0}

    def watchlist(self, ctx):
        return [(t, f"poids {ctx.weights.get(t, 0.0):.0%} pour une cible de {w:.0%} ; rééquilibre à {self.p(ctx, 'drift'):.0%} d'écart")
                for t, w in self._targets(ctx).items()]

    def decide(self, ctx: BotContext) -> Decision:
        scale = ctx.invest_fraction(0.98)
        bonds, gold = self.p(ctx, "bonds"), self.p(ctx, "gold")
        equity_share = max(0.0, 1 - bonds - gold) / sum(self.stocks.values())
        mix = {t: w * equity_share for t, w in self.stocks.items()} | {"ZAG.TO": bonds, "CGL.TO": gold}
        targets = {t: w * scale for t, w in mix.items() if w > 0}
        drift = max(abs(ctx.weights.get(t, 0.0) - targets.get(t, 0.0)) for t in self.universe)
        if not ctx.account.positions or drift > self.p(ctx, "drift") or ctx.has_idle_cash(scale):
            return Decision(targets, [f"Mes poids ont dérivé de {drift:.0%}, je rééquilibre."])
        return Decision(None)


class Mochi(Bot):
    """Suit la tendance : garde les titres les plus forts sur 3 mois, coupe ce qui casse.

    Pour limiter les frais, un titre détenu reste tant qu'il est dans le top 10 :
    Mochi ne remplace que ce qui décroche vraiment.
    """

    id, name, personality = "mochi", "Mochi", "Chasseur de tendance · momentum"
    universe = BROAD
    params = {
        "holdings": Param(5, 3, 8, "nombre de titres visés", integer=True),
        "keep_rank": Param(10, 6, 15, "un titre détenu reste tant qu'il est dans ce top", integer=True),
        "lookback": Param(63, 42, 126, "période de mesure de la tendance, en séances", integer=True),
    }
    rules = [
        "Chaque semaine, classe 100 grandes sociétés canadiennes et américaines selon leur hausse des {lookback:.0f} dernières séances.",
        "Ne retient que celles au-dessus de leur moyenne 50 jours, et vise les {holdings:.0f} plus fortes.",
        "Garde un titre tant qu'il reste dans le top {keep_rank:.0f}, pour ne pas payer des frais à chaque petite variation.",
        "Chaque soir, vend un titre qui passe 3 % sous sa moyenne 50 jours : la tendance est cassée.",
    ]

    def _ranking(self, ctx) -> list[tuple[float, str]]:
        lookback = int(self.p(ctx, "lookback"))
        ranked = []
        for t in self.universe:
            closes = ctx.closes(t, lookback + 10)
            r, sma50 = ind.pct_return(closes, lookback), ind.sma(closes, 50)
            if r is not None and sma50 and closes.iloc[-1] > sma50 and r > 0:
                ranked.append((r, t))
        return sorted(ranked, reverse=True)

    def watchlist(self, ctx):
        lookback = int(self.p(ctx, "lookback"))
        return [(t, f"{i}e du classement : {r:+.0%} sur {lookback} séances, au-dessus de sa moyenne 50 jours")
                for i, (r, t) in enumerate(self._ranking(ctx), 1) if t not in ctx.account.positions][:8]

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
        keep_rank = int(self.p(ctx, "keep_rank"))
        order = [t for _, t in self._ranking(ctx)]
        kept = [t for t in order[:keep_rank] if t in held and t not in broken]
        new = [t for t in order if t not in kept][: max(0, int(self.p(ctx, "holdings")) - len(kept))]
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
            notes.append(f"Je lâche {', '.join(dropped)}, qui sort du top {keep_rank}.")
        return Decision(weights, notes)


class Muffin(Bot):
    """Achète les bons titres quand ils sont survendus, revend quand ils rebondissent."""

    id, name, personality = "muffin", "Muffin", "Chasseur d'aubaines · value"
    universe = BROAD
    params = {
        "max_positions": Param(6, 3, 8, "nombre maximum de positions", integer=True),
        "rsi_buy": Param(30, 20, 35, "RSI sous lequel un titre est « en solde »"),
        "rsi_sell": Param(60, 55, 70, "RSI au-dessus duquel il revend (rebond)"),
        "stop": Param(0.08, 0.05, 0.15, "perte maximale tolérée par position, vérifiée à la clôture"),
        "crash_stop": Param(0.15, 0.10, 0.30, "stop de secours chez le courtier, déclenché pendant la séance"),
        "max_days": Param(35, 20, 60, "durée maximale de détention, en jours", integer=True),
    }
    rules = [
        "Cherche, parmi 100 grandes sociétés, celles dont le RSI (force sur 14 jours) est sous {rsi_buy:.0f} : elles ont beaucoup baissé récemment.",
        "Ne les achète que si elles restent au-dessus de 95 % de leur moyenne 200 jours : une bonne société en solde, pas une qui s'effondre.",
        "Au plus {max_positions:.0f} positions, environ 15 % du capital chacune, les plus survendues d'abord.",
        "Revend quand le RSI remonte au-dessus de {rsi_sell:.0f} (le rebond est venu) ou après {max_days:.0f} jours.",
        "Vend le soir un titre qui clôture à -{stop:.0%} ou plus sous son prix d'achat. Il ne regarde qu'à la clôture : "
        "un titre survendu plonge souvent pendant la journée puis remonte, et vendre au creux ferait rater le rebond.",
        "Stop de secours chez le courtier à -{crash_stop:.0%}, déclenché pendant la séance : la protection contre un vrai effondrement.",
    ]
    slot = 0.15

    def watchlist(self, ctx):
        rsi_buy, out = self.p(ctx, "rsi_buy"), []
        for t in self.universe:
            if t in ctx.account.positions:
                continue
            closes = ctx.closes(t, 220)
            r, sma200 = ind.rsi(closes), ind.sma(closes, 200)
            if r is not None and sma200 and closes.iloc[-1] > 0.95 * sma200:
                out.append((r, t, f"RSI {r:.0f} : " + ("en solde, il achète" if r < rsi_buy else f"il attend qu'il passe sous {rsi_buy:.0f}")))
        return [(t, why) for _, t, why in sorted(out)[:8]]

    def stops(self, positions, params, fx):
        # Seulement le stop de secours : le -8 % se vérifie à la clôture, dans decide(). Testé sur 2022-2025, un stop
        # serré pendant la séance coûtait 12 points à Muffin (il vendait au creux de la journée, avant le rebond).
        stop = self.param(params, "crash_stop")
        return {t: (round(p.avg_cost_cad * (1 - stop) / fx(t), 2), "below") for t, p in positions.items() if p.qty > 0}

    def decide(self, ctx: BotContext) -> Decision:
        weights = dict(ctx.weights)
        notes, changed = [], False
        rsi_sell, stop, max_days = self.p(ctx, "rsi_sell"), self.p(ctx, "stop"), self.p(ctx, "max_days")
        values = ctx.broker.value_of(ctx.account, ctx.day)
        for t, pos in ctx.account.positions.items():
            r = ind.rsi(ctx.closes(t, 30))
            unit_value = values.get(t, 0.0) / pos.qty
            held_days = (ctx.day - pd.Timestamp(pos.opened_on)).days
            if (r is not None and r > rsi_sell) or held_days > max_days or unit_value < (1 - stop) * pos.avg_cost_cad:
                weights.pop(t, None)
                changed = True
                why = "il a rebondi" if r and r > rsi_sell else "trop long" if held_days > max_days else f"stop à -{stop:.0%}"
                notes.append(f"Je revends {t} ({why}).")
        rsi_buy, bargains = self.p(ctx, "rsi_buy"), []
        for t in self.universe:
            if t in weights:
                continue
            closes = ctx.closes(t, 220)
            r, sma200 = ind.rsi(closes), ind.sma(closes, 200)
            if r is not None and sma200 and r < rsi_buy and closes.iloc[-1] > 0.95 * sma200:
                bargains.append((r, t))
        room = max(0, int(self.p(ctx, "max_positions")) - len(weights))
        for r, t in sorted(bargains)[:room]:  # les plus survendus d'abord
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
    """Le casse-cou : achète des options sur l'indice ou le FNB le plus en forme.

    Calls quand le Nasdaq est au-dessus de sa moyenne 200 jours, petits puts quand il est en dessous.
    Une seule position à la fois, une partie du capital en primes, le reste au cash.
    Sort sur objectif de gain, sur stop, si la tendance s'inverse ou à 7 jours de l'échéance.
    """

    id, name, personality = "jalapeno", "Jalapeño", "Casse-cou · options"
    universe = ["QQQ", "SPY", "IWM", "SMH"]
    params = {
        "budget": Param(0.30, 0.10, 0.50, "part du capital mise en primes d'options"),
        "otm": Param(0.05, 0.0, 0.10, "distance du prix d'exercice (hors de la monnaie)"),
        "take_profit": Param(1.0, 0.5, 2.0, "gain qui déclenche la vente (1,0 = +100 %)"),
        "stop": Param(0.5, 0.3, 0.7, "perte qui déclenche la vente (0,5 = -50 %)"),
        "dte": Param(40, 21, 75, "jours avant l'échéance au moment de l'achat", integer=True),
    }
    rules = [
        "Regarde le Nasdaq (QQQ) : au-dessus de sa moyenne 200 jours, le marché est haussier ; en dessous, baissier.",
        "Marché haussier : achète des calls (paris à la hausse) sur l'indice ou le FNB le plus en forme parmi QQQ, SPY, IWM et SMH. "
        "Marché baissier : petits puts (paris à la baisse).",
        "Prix d'exercice à {otm:.0%} du cours, échéance dans environ {dte:.0f} jours. Une seule position à la fois.",
        "Met {budget:.0%} de son capital en primes (la moitié en marché baissier). Le reste dort au cash et rapporte des intérêts.",
        "Vend à +{take_profit:.0%} de gain, à 7 jours de l'échéance, ou si la tendance s'inverse. Ordre stop à -{stop:.0%}.",
        "Une option peut perdre toute sa valeur : c'est le Chubby le plus risqué de l'arène.",
    ]

    def stops(self, positions, params, fx):
        stop = self.param(params, "stop")
        return {t: (max(0.01, round(p.avg_cost_cad * (1 - stop) / (opt.MULTIPLIER * fx(t)), 2)), "below")
                for t, p in positions.items() if opt.is_option(t) and p.qty > 0}

    def watchlist(self, ctx):
        nasdaq = ctx.closes("QQQ", 210)
        sma200 = ind.sma(nasdaq, 200)
        bull = sma200 is not None and nasdaq.iloc[-1] > sma200
        out = []
        for t in self.universe:
            closes = ctx.closes(t, 30)
            r10, sma20 = ind.pct_return(closes, 10), ind.sma(closes, 20)
            if r10 is None or not sma20:
                continue
            above = closes.iloc[-1] > sma20
            ready = (bull and above and r10 > 0) or (not bull and not above and r10 < 0)
            out.append((-abs(r10), t, f"{r10:+.1%} sur 10 jours, {'au-dessus de' if above else 'sous'} sa moyenne 20 jours : "
                        + (f"prêt pour des {'calls' if bull else 'puts'}" if ready else "pas dans le bon sens")))
        return [(t, why) for _, t, why in sorted(out)]

    def decide(self, ctx: BotContext) -> Decision:
        held = [t for t in ctx.account.positions if opt.is_option(t)]
        if held:
            return self._manage(ctx, held[0])
        nasdaq = ctx.closes("QQQ", 210)
        sma200 = ind.sma(nasdaq, 200)
        if sma200 is None:
            return Decision(None)
        bull = nasdaq.iloc[-1] > sma200
        trend = {}
        for t in self.universe:
            closes = ctx.closes(t, 30)
            r10, sma20 = ind.pct_return(closes, 10), ind.sma(closes, 20)
            if r10 is None or not sma20:
                continue
            if bull and closes.iloc[-1] > sma20 and r10 > 0:
                trend[t] = r10
            if not bull and closes.iloc[-1] < sma20 and r10 < 0:
                trend[t] = -r10
        if not trend:
            return Decision(None)
        under = max(trend, key=trend.get)
        kind = "C" if bull else "P"
        sym = ctx.data.contract(under, kind, ctx.day, self.p(ctx, "otm"), self.p(ctx, "dte"))
        _, _, strike, expiry = opt.parse(sym)
        budget = self.p(ctx, "budget") * (1 if bull else 0.5)
        weight = min(0.6, budget * ctx.aggressiveness)
        premium = ctx.data.price(sym, ctx.day)
        side = "calls" if kind == "C" else "puts"
        return Decision({sym: weight}, [
            f"J'achète des {side} {under} {strike:g} échéance {expiry.date()} "
            f"(prime ≈ {premium * 100:,.0f} $ US par contrat). {'Ça monte' if bull else 'Ça baisse'}, j'y vais !"])

    def _manage(self, ctx: BotContext, sym: str) -> Decision:
        under, kind, strike, expiry = opt.parse(sym)
        pos = ctx.account.positions[sym]
        value = ctx.broker.value_of(ctx.account, ctx.day).get(sym, 0.0)
        gain = value / (pos.avg_cost_cad * pos.qty) - 1
        closes = ctx.closes(under, 30)
        sma20 = ind.sma(closes, 20)
        reversed_ = sma20 and ((kind == "C" and closes.iloc[-1] < sma20) or (kind == "P" and closes.iloc[-1] > sma20))
        days_left = (expiry - ctx.day).days
        why = None
        if gain >= self.p(ctx, "take_profit"):
            why = f"objectif atteint ({gain:+.0%})"
        elif gain <= -self.p(ctx, "stop"):
            why = f"stop ({gain:+.0%})"
        elif days_left <= 7:
            why = f"échéance dans {days_left} jours ({gain:+.0%})"
        elif reversed_:
            why = f"la tendance s'inverse ({gain:+.0%})"
        if why is None:
            return Decision(None)
        return Decision({}, [f"Je vends mon {opt.describe(sym)} : {why}."])


class JalapenoV2(Bot):
    """Le casse-cou devenu vendeur d'options : il encaisse les primes que les acheteurs paient.

    La « roue » : il vend un put garanti par son cash sur un FNB bon marché. Si le put expire sans
    valeur, il garde la prime et recommence. S'il est exercé, il reçoit les actions et vend des calls
    couverts dessus jusqu'à ce qu'elles soient reprises. Il rachète une option quand il a déjà gagné
    une bonne partie de la prime, ou pour couper une perte si elle s'emballe.
    Gagne souvent un peu ; peut perdre gros si le marché s'effondre.
    """

    id, name, personality = "jalapeno-v2", "Jalapeño v2", "Vendeur d'options · la roue"
    universe = ["XLF", "EEM", "SLV", "KRE", "GDX"]
    signals = ["QQQ"]
    params = {
        "otm": Param(0.05, 0.02, 0.10, "distance du prix d'exercice sous (put) ou au-dessus (call) du cours"),
        "dte": Param(35, 21, 60, "jours avant l'échéance au moment de vendre", integer=True),
        "take_profit": Param(0.5, 0.3, 0.9, "part de la prime gagnée qui déclenche le rachat"),
        "stop": Param(3.0, 2.0, 5.0, "rachat si l'option vaut ce multiple de la prime reçue"),
        "budget": Param(0.9, 0.3, 1.0, "part du cash libre engagée en garantie de puts"),
    }
    rules = [
        "Vend des puts sur des FNB abordables (XLF, EEM, SLV, KRE, GDX) : il encaisse une prime en promettant d'acheter "
        "100 actions au prix d'exercice si le FNB baisse sous ce prix.",
        "Chaque put est entièrement garanti par son cash : il engage au plus {budget:.0%} de son cash libre (la moitié si le Nasdaq est sous sa moyenne 200 jours).",
        "Prix d'exercice à {otm:.0%} sous le cours, échéance dans environ {dte:.0f} jours. Il choisit le FNB qui paie la plus grosse prime.",
        "Rachète l'option quand il a gagné {take_profit:.0%} de la prime. Ordre stop : rachat si elle vaut {stop:.1f} fois la prime reçue.",
        "S'il est exercé, il reçoit les actions et vend des calls couverts dessus jusqu'à ce qu'elles soient reprises : c'est « la roue ».",
        "Gagne souvent un peu, mais peut perdre gros dans un krach.",
    ]

    def stops(self, positions, params, fx):
        # racheter l'option vendue si elle vaut `stop` fois la prime encaissée
        stop = self.param(params, "stop")
        return {t: (round(p.avg_cost_cad / (opt.MULTIPLIER * fx(t)) * stop, 2), "above")
                for t, p in positions.items() if opt.is_option(t) and p.qty < 0}

    def watchlist(self, ctx):
        out = []
        for under in self.universe:
            spot = ctx.price(under)
            if not spot:
                continue
            sym = ctx.data.contract(under, "P", ctx.day, self.p(ctx, "otm"), self.p(ctx, "dte"))
            premium = ctx.data.price(sym, ctx.day) or 0
            strike = opt.parse(sym)[2]
            out.append((-premium / strike, under, f"{opt.describe(sym)} : prime ≈ {premium * 100:,.0f} $ US, "
                                                  f"garantie de {strike * 100:,.0f} $ US par contrat"))
        return [(t, why) for _, t, why in sorted(out)]

    def decide(self, ctx: BotContext) -> Decision:
        shorts = {t: p for t, p in ctx.account.positions.items() if opt.is_option(t) and p.qty < 0}
        for sym, pos in shorts.items():  # 1. gérer les options déjà vendues
            premium = pos.avg_cost_cad / (opt.MULTIPLIER * ctx.broker.fx(sym, ctx.day))
            now = ctx.data.price(sym, ctx.day)
            if now is None:
                continue
            if now <= premium * (1 - self.p(ctx, "take_profit")):
                return Decision(None, [f"Je rachète mon {opt.describe(sym)} : {1 - now / premium:.0%} de la prime déjà gagnée."],
                                trades=[(sym, -pos.qty)])
            if now >= premium * self.p(ctx, "stop"):
                return Decision(None, [f"Je rachète mon {opt.describe(sym)} pour couper la perte (×{now / premium:.1f})."],
                                trades=[(sym, -pos.qty)])
        if shorts:
            return Decision(None)
        shares = [t for t, p in ctx.account.positions.items() if not opt.is_option(t) and p.qty >= opt.MULTIPLIER]
        if shares:  # 2. on a été exercé : vendre des calls couverts
            under = shares[0]
            pos = ctx.account.positions[under]
            spot = ctx.price(under)
            cost = pos.avg_cost_cad / ctx.broker.fx(under, ctx.day)
            sym = ctx.data.contract(under, "C", ctx.day, self.p(ctx, "otm"), self.p(ctx, "dte"), ref=max(spot, cost))
            strike = opt.parse(sym)[2]
            n = pos.qty // opt.MULTIPLIER
            return Decision(None, [f"J'ai {pos.qty} actions {under} : je vends {n} call(s) couvert(s) à {strike:g}."],
                            trades=[(sym, -n)])
        # 3. rien en cours : vendre un put garanti sur le FNB le plus payant qui rentre dans le budget
        nasdaq = ctx.closes("QQQ", 210)
        sma200 = ind.sma(nasdaq, 200)
        budget = self.p(ctx, "budget") * ctx.aggressiveness * (0.5 if sma200 and nasdaq.iloc[-1] < sma200 else 1.0)
        free = ctx.broker.free_cash(ctx.account, ctx.day) * min(budget, 1.0)
        best = None
        for under in self.universe:
            spot = ctx.price(under)
            if not spot:
                continue
            sym = ctx.data.contract(under, "P", ctx.day, self.p(ctx, "otm"), self.p(ctx, "dte"))
            strike = opt.parse(sym)[2]
            n = int(free // (strike * opt.MULTIPLIER * ctx.broker.fx(under, ctx.day)))
            if n < 1:
                continue
            income = (ctx.data.price(sym, ctx.day) or 0) * n
            if best is None or income > best[0]:
                best = (income, sym, n, under, strike)
        if best is None or best[0] <= 0:
            return Decision(None)
        income, sym, n, under, strike = best
        return Decision(None, [f"Je vends {n} put(s) {under} {strike:g} : environ {income * 100:,.0f} $ US de prime "
                               f"à encaisser. Si {under} tombe sous {strike:g}, j'achèterai les actions."],
                        trades=[(sym, -n)])


class JalapenoV3(Bot):
    """Le casse-cou devenu patient : des calls longs, déjà dans la monnaie, sur des FNB de marché.

    Une option longue et dans la monnaie perd peu de valeur chaque jour et suit son FNB presque comme
    des actions, avec un effet de levier. Il n'en achète que quand la tendance est haussière, les
    renouvelle avant qu'elles ne vieillissent, et range tout au cash quand le marché casse.
    Un contrat porte sur 100 parts : avec 10 000 $, il doit choisir des FNB dont un contrat est abordable.
    """

    id, name, personality = "jalapeno-v3", "Jalapeño v3", "Casse-cou patient · options longues"
    # Du plus large au plus étroit ; QQQ et SPY sont souvent trop chers pour un petit compte.
    universe = ["QQQ", "SPY", "IWM", "XLK", "XLF", "XLE", "XLI", "EEM"]
    params = {
        "budget": Param(0.30, 0.10, 0.45, "part du capital mise en options"),
        "itm": Param(0.10, 0.05, 0.20, "prix d'exercice sous le cours (dans la monnaie)"),
        "dte": Param(300, 180, 540, "jours avant l'échéance au moment de l'achat", integer=True),
        "roll_days": Param(90, 60, 150, "renouvelle l'option quand il reste moins de jours que ça", integer=True),
        "roll_gain": Param(1.0, 0.5, 2.0, "renouvelle aussi après ce gain, pour encaisser (1,0 = +100 %)"),
        "exit_buffer": Param(0.03, 0.0, 0.08, "sortie si le Nasdaq passe ce pourcentage sous sa moyenne 200 jours"),
        "stop": Param(0.5, 0.3, 0.7, "stop de secours chez le courtier, déclenché pendant la séance"),
    }
    rules = [
        "Achète des calls sur des FNB de marché (Nasdaq, S&P 500, petites capitalisations, technologie, finance, énergie, "
        "industrie, émergents) : celui qui monte le plus sur 3 mois et reste au-dessus de sa moyenne 200 jours.",
        "Des options longues (environ {dte:.0f} jours) et déjà dans la monnaie ({itm:.0%} sous le cours) : elles perdent peu "
        "de valeur chaque jour et suivent le FNB presque comme des actions, avec un effet de levier.",
        "Met environ {budget:.0%} de son capital en options, en contrats entiers de 100 parts : si un contrat est trop cher "
        "(c'est souvent le cas de QQQ et SPY pour un compte de 10 000 $), il passe au FNB suivant. Le reste dort au cash.",
        "N'achète que si le Nasdaq est au-dessus de sa moyenne 200 jours. S'il passe {exit_buffer:.0%} en dessous, il vend tout et attend au cash.",
        "Renouvelle son option quand il lui reste moins de {roll_days:.0f} jours, ou après +{roll_gain:.0%} de gain pour encaisser.",
        "Stop de secours à -{stop:.0%} chez le courtier, déclenché pendant la séance.",
    ]

    def stops(self, positions, params, fx):
        stop = self.param(params, "stop")
        return {t: (max(0.01, round(p.avg_cost_cad * (1 - stop) / (opt.MULTIPLIER * fx(t)), 2)), "below")
                for t, p in positions.items() if opt.is_option(t) and p.qty > 0}

    def watchlist(self, ctx):
        out = []
        for t in self.universe:
            closes = ctx.closes(t, 210)
            r, sma200 = ind.pct_return(closes, 63), ind.sma(closes, 200)
            if r is None or not sma200:
                continue
            sym = ctx.data.contract(t, "C", ctx.day, -self.p(ctx, "itm"), self.p(ctx, "dte"))
            premium = ctx.data.price(sym, ctx.day) or 0
            cost = premium * opt.MULTIPLIER * ctx.broker.fx(sym, ctx.day)
            trend = "au-dessus de" if closes.iloc[-1] > sma200 else "sous"
            out.append((-r, t, f"{r:+.0%} sur 3 mois, {trend} sa moyenne 200 jours ; un {opt.describe(sym)} coûte ≈ {cost:,.0f} $"))
        return [(t, why) for _, t, why in sorted(out)]

    def _new_call(self, ctx: BotContext, cash: float) -> tuple[str, int, list[str]] | None:
        """Le meilleur FNB en tendance dont au moins un contrat rentre dans le budget, et le nombre de contrats."""
        budget = min(ctx.equity * self.p(ctx, "budget") * ctx.aggressiveness, cash * 0.95)
        ranked, too_expensive = [], []
        for t in self.universe:
            closes = ctx.closes(t, 210)
            r, sma200 = ind.pct_return(closes, 63), ind.sma(closes, 200)
            if r is not None and sma200 and closes.iloc[-1] > sma200 and r > 0:
                ranked.append((r, t))
        for _, t in sorted(ranked, reverse=True):
            sym = ctx.data.contract(t, "C", ctx.day, -self.p(ctx, "itm"), self.p(ctx, "dte"))
            premium = ctx.data.price(sym, ctx.day)
            if not premium:
                continue
            per_contract = premium * opt.MULTIPLIER * ctx.broker.fx(sym, ctx.day) * 1.04  # écart et frais compris
            n = int(budget // per_contract)
            if n >= 1:
                return sym, n, too_expensive
            too_expensive.append(f"{t} ({per_contract:,.0f} $ le contrat)")
        return None if not too_expensive else ("", 0, too_expensive)

    def decide(self, ctx: BotContext) -> Decision:
        nasdaq = ctx.closes("QQQ", 210)
        sma200 = ind.sma(nasdaq, 200)
        if sma200 is None:
            return Decision(None)
        last = nasdaq.iloc[-1]
        held = [t for t, p in ctx.account.positions.items() if opt.is_option(t) and p.qty > 0]
        if held:
            sym = held[0]
            pos = ctx.account.positions[sym]
            if last < sma200 * (1 - self.p(ctx, "exit_buffer")):
                return Decision(None, [f"Le Nasdaq casse sa moyenne 200 jours : je vends mon {opt.describe(sym)} et j'attends au cash."],
                                trades=[(sym, -pos.qty)])
            value = ctx.broker.value_of(ctx.account, ctx.day).get(sym, 0.0)
            gain = value / (pos.avg_cost_cad * pos.qty) - 1
            days_left = (opt.parse(sym)[3] - ctx.day).days
            if days_left > self.p(ctx, "roll_days") and gain < self.p(ctx, "roll_gain"):
                return Decision(None)
            pick = self._new_call(ctx, ctx.account.cash + value)
            why = f"il ne lui reste que {days_left} jours" if days_left <= self.p(ctx, "roll_days") else f"j'encaisse {gain:+.0%}"
            if not pick or not pick[0]:
                return Decision(None, [f"Je vends mon {opt.describe(sym)} ({why}) ; rien d'abordable en tendance pour le remplacer."],
                                trades=[(sym, -pos.qty)])
            new, n, _ = pick
            return Decision(None, [f"Je renouvelle mon {opt.describe(sym)} ({why}) : {n} × {opt.describe(new)}."],
                            trades=[(sym, -pos.qty), (new, n)])
        if last <= sma200:
            return Decision(None)
        pick = self._new_call(ctx, ctx.account.cash)
        if pick is None:
            return Decision(None)
        new, n, too_expensive = pick
        if not new:  # tout ce qui est en tendance coûte trop cher ; il le dit une fois par mois
            note = f"Trop cher pour moi ce mois-ci : {', '.join(too_expensive[:3])}." if ctx.first_day_of_month() else None
            return Decision(None, [note] if note else [])
        premium = ctx.data.price(new, ctx.day) or 0
        skipped = f" ({', '.join(too_expensive[:2])} : trop cher)" if too_expensive else ""
        return Decision(None, [f"La tendance est haussière : j'achète {n} × {opt.describe(new)} "
                               f"(≈ {premium * 100:,.0f} $ US le contrat){skipped}, et je suis patient."], trades=[(new, n)])


RULE_BOTS = [Tortue, Flan, Dumpling, Mochi, Muffin, Jalapeno, JalapenoV2, JalapenoV3]
