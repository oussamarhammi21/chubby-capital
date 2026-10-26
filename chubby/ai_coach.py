"""Le coach Claude : chaque semaine, il relit la semaine d'un Chubby IA et ajuste ses réglages.

Il ne passe aucun ordre. Il ne touche qu'à l'agressivité et aux réglages déclarés par le bot,
toujours dans leurs bornes, et il écrit sa leçon dans le journal. La personnalité ne change jamais.
Ne tourne qu'en direct : sur un backtest, Claude connaîtrait déjà la suite.
"""

from __future__ import annotations

import json
import logging

from .bots import Bot
from .scoring import metrics
from .store import Store

log = logging.getLogger(__name__)

MODEL = "claude-opus-5-5"
AGGRESSIVENESS = (0.5, 1.5)

SYSTEM = """Tu es le coach intérieur d'un Chubby, un bot de trading qui investit de l'argent fictif \
dans une arène (actions canadiennes et américaines). Chaque semaine, tu relis ce qu'il a fait et tu \
ajustes ses réglages pour qu'il progresse.

Règles :
- Reste fidèle à sa personnalité : tu règles sa stratégie, tu ne la changes pas.
- Ajuste par petites touches. Une seule semaine est surtout du bruit : ne change un réglage que si \
plusieurs semaines ou un risque clair le justifient. Garder les mêmes réglages est souvent la bonne réponse.
- Le but est un bon score sur la durée : rendement, mais aussi pire baisse et régularité.
- Tiens compte des conseils de son coach humain s'il y en a dans le journal.
- Chaque réglage doit rester entre ses bornes.
- La leçon est écrite en français, à la première personne, comme si le Chubby parlait, en une ou deux phrases."""


def _schema(bot: Bot) -> dict:
    return {
        "type": "object",
        "properties": {
            "aggressiveness": {"type": "number"},
            "params": {
                "type": "object",
                "properties": {name: {"type": "number"} for name in bot.params},
                "required": list(bot.params),
                "additionalProperties": False,
            },
            "lesson": {"type": "string"},
            "reasoning": {"type": "string"},
        },
        "required": ["aggressiveness", "params", "lesson", "reasoning"],
        "additionalProperties": False,
    }


def _context(store: Store, bot: Bot, twin_id: str | None, market: dict, starting_cash: float) -> str:
    db = store.db
    row = store.bot_row(bot.id)
    eq = store.equity_series(bot.id)
    current = store.get_params(bot.id)
    recent = eq.tail(21)

    def perf(series):
        if len(series) < 2:
            return None
        m = metrics(series, float(series.iloc[0]))
        return {"rendement": round(m.total_return, 4), "pire_baisse": round(m.max_drawdown, 4)}

    twin = store.equity_series(twin_id) if twin_id else None
    payload = {
        "chubby": {"nom": bot.name, "personnalite": bot.personality, "strategie": (bot.__doc__ or "").strip()},
        "reglages": {
            "agressivite": {"valeur": row["aggressiveness"], "bornes": list(AGGRESSIVENESS),
                            "role": "multiplie la part investie et la taille des positions (1,0 = neutre)"},
            **{name: {"valeur": current.get(name, spec.default), "bornes": [spec.low, spec.high],
                      "entier": spec.integer, "role": spec.help} for name, spec in bot.params.items()},
        },
        "performance": {
            "capital": round(float(eq.iloc[-1]), 2) if len(eq) else starting_cash,
            "depuis_le_debut": perf(eq),
            "cinq_dernieres_seances": perf(eq.tail(6)),
            "quatre_dernieres_semaines": perf(recent),
            "jumeau_sans_coach_quatre_semaines": perf(twin.tail(21)) if twin is not None and len(twin) else None,
        },
        "derniers_trades": [dict(r) for r in db.execute(
            "SELECT day, ticker, side, qty, ROUND(value_cad, 2) AS montant_cad FROM trades"
            " WHERE bot_id = ? ORDER BY id DESC LIMIT 15", (bot.id,))],
        "journal": [f"{r['day']} : {r['text']}" for r in db.execute(
            "SELECT day, text FROM journal WHERE bot_id = ? ORDER BY id DESC LIMIT 12", (bot.id,))],
        "positions": [r["ticker"] for r in db.execute("SELECT ticker FROM positions WHERE bot_id = ?", (bot.id,))],
        "marche": market,
    }
    return json.dumps(payload, ensure_ascii=False, indent=1)


def market_snapshot(data, day) -> dict:
    out = {}
    for t, label in (("XIC.TO", "TSX"), ("QQQ", "Nasdaq"), ("SPY", "S&P 500")):
        closes = data.closes(t, day, 210)
        if len(closes) < 22:
            continue
        sma200 = float(closes.tail(200).mean())
        out[label] = {"semaine": round(float(closes.iloc[-1] / closes.iloc[-6] - 1), 4),
                      "mois": round(float(closes.iloc[-1] / closes.iloc[-22] - 1), 4),
                      "au_dessus_moyenne_200j": bool(closes.iloc[-1] > sma200)}
    return out


def ask_claude(client, bot: Bot, context: str) -> dict | None:
    import anthropic

    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM,
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": _schema(bot)}},
            messages=[{"role": "user", "content": f"Voici la semaine de {bot.name}. Propose ses réglages.\n\n{context}"}],
        )
    except anthropic.APIStatusError as e:
        log.error("Claude a refusé la requête pour %s (%s) : %s", bot.name, e.status_code, e.message)
        return None
    except anthropic.APIConnectionError:
        log.error("Impossible de joindre l'API Claude pour %s.", bot.name)
        return None
    if response.stop_reason == "refusal":
        log.warning("Claude a décliné le coaching de %s.", bot.name)
        return None
    text = next((b.text for b in response.content if b.type == "text"), None)
    try:
        return json.loads(text) if text else None
    except json.JSONDecodeError:
        log.error("Réponse illisible pour %s.", bot.name)
        return None


def apply_advice(store: Store, bot: Bot, advice: dict, day) -> dict:
    """Applique le conseil en le bornant. Renvoie les changements effectués."""
    row = store.bot_row(bot.id)
    old_aggr = row["aggressiveness"]
    new_aggr = round(max(AGGRESSIVENESS[0], min(AGGRESSIVENESS[1], float(advice.get("aggressiveness", old_aggr)))), 3)
    current = store.get_params(bot.id)
    new_params, changes = {}, {}
    for name, spec in bot.params.items():
        old = current.get(name, spec.default)
        new = spec.clamp(advice.get("params", {}).get(name, old))
        new_params[name] = new
        if abs(new - old) > 1e-9:
            changes[name] = [old, new]
    if abs(new_aggr - old_aggr) > 1e-9:
        changes["agressivite"] = [old_aggr, new_aggr]
    store.set_aggressiveness(bot.id, new_aggr)
    store.set_params(bot.id, new_params)
    store.add_xp(bot.id, 25)
    lesson = str(advice.get("lesson", "")).strip()[:400] or "Rien à changer cette semaine."
    store.journal(bot.id, day, lesson)
    store.event(day, bot.id, "ai_coach", lesson=lesson, changes=changes)
    return changes


def weekly_review(store: Store, data, bots: list[Bot], day, client=None, starting_cash: float = 10_000.0) -> int:
    """Coache toutes les versions IA. Renvoie le nombre de Chubbies coachés."""
    targets = [b for b in bots if b.ai]
    if not targets:
        return 0
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    market = market_snapshot(data, day)
    done = 0
    for bot in targets:
        twin = bot.id.removesuffix("-ia")
        advice = ask_claude(client, bot, _context(store, bot, twin, market, starting_cash))
        if advice is None:
            continue
        changes = apply_advice(store, bot, advice, str(day)[:10])
        log.info("%s coaché : %s", bot.name, changes or "aucun changement")
        done += 1
    store.commit()
    return done
