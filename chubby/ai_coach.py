"""Le coach Claude : chaque semaine, il relit la semaine d'un Chubby IA et ajuste ses réglages.

Il ne passe aucun ordre. Il ne touche qu'à l'agressivité et aux réglages déclarés par le bot,
toujours dans leurs bornes, et il écrit sa leçon dans le journal. La personnalité ne change jamais.
Ne tourne qu'en direct : sur un backtest, Claude connaîtrait déjà la suite.
"""

from __future__ import annotations

import json
import logging

import pandas as pd

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


class ClaudeCodeClient:
    """Passe par Claude Code (la commande `claude`, connectée à ton forfait Claude) au lieu de l'API.

    Même interface que le client anthropic pour ask_claude : pas besoin de clé API ni de crédit.
    """

    def __init__(self, exe: str, timeout: int = 600):
        import types
        self.exe, self.timeout = exe, timeout
        self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(create=self._create))

    def _create(self, system, messages, output_config=None, **_):
        import subprocess
        import types
        schema = (output_config or {}).get("format", {}).get("schema")
        prompt = (f"{system}\n\n{messages[-1]['content']}\n\nRéponds uniquement par un objet JSON"
                  + (f" qui respecte ce schéma : {json.dumps(schema, ensure_ascii=False)}" if schema else "")
                  + ", sans texte autour.")
        out = subprocess.run([self.exe, "-p", "--output-format", "json"], input=prompt, capture_output=True,
                             text=True, encoding="utf-8", timeout=self.timeout)
        if out.returncode != 0:
            raise RuntimeError(f"Claude Code a échoué : {(out.stderr or out.stdout).strip()[:300]}")
        result = json.loads(out.stdout)
        if result.get("is_error"):
            raise RuntimeError(f"Claude Code a échoué : {str(result.get('result'))[:300]}")
        text = _strip_fences(str(result.get("result", "")))
        return types.SimpleNamespace(stop_reason="end_turn", content=[types.SimpleNamespace(type="text", text=text)])


def _strip_fences(text: str) -> str:
    text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start != -1 and end > start else text


def make_client():
    """Le client du coach : l'API si une clé ANTHROPIC_API_KEY est définie, sinon Claude Code s'il est installé."""
    import os
    import shutil
    if os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic
        return anthropic.Anthropic()
    exe = shutil.which("claude")
    if exe:
        log.info("Pas de clé API : le coach passe par Claude Code (%s).", exe)
        return ClaudeCodeClient(exe)
    raise RuntimeError("ni clé ANTHROPIC_API_KEY dans .env, ni Claude Code installé (commande « claude »)")


def ask_claude(client, bot: Bot, context: str) -> dict | None:
    try:
        import anthropic
        api_errors = (anthropic.APIStatusError, anthropic.APIConnectionError)
    except ModuleNotFoundError:
        api_errors = ()

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
    except api_errors as e:
        log.error("L'API Claude n'a pas répondu pour %s : %s", bot.name, e)
        return None
    except Exception as e:  # Claude Code : pas connecté, délai dépassé…
        log.error("Claude n'a pas pu coacher %s : %s", bot.name, e)
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
        client = make_client()
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


REVIEWED_KEY = "ai_review_day"


def due_week(store: Store) -> str | None:
    """La dernière séance d'une semaine finie que le coach n'a pas encore revue, ou None.

    Une semaine est finie dès que sa séance du vendredi est jouée, ou dès qu'une séance de la semaine suivante l'est
    (PC éteint le vendredi, vendredi férié, séance rattrapée le lundi par le tableau de bord…). Avant, la revue ne
    partait que si la dernière séance jouée était un vendredi : une séance du vendredi rattrapée plus tard était
    jouée, mais la semaine n'était jamais revue.
    """
    last = store.get_meta("last_day")
    if last is None:
        return None
    last = pd.Timestamp(last)
    if last.weekday() == 4:
        target = str(last.date())
    else:
        monday = last - pd.Timedelta(days=last.weekday())
        row = store.db.execute("SELECT MAX(day) AS d FROM snapshots WHERE day < ?", (str(monday.date()),)).fetchone()
        if row is None or row["d"] is None:
            return None  # l'arène a commencé cette semaine : rien à revoir
        target = row["d"]
    reviewed = store.get_meta(REVIEWED_KEY) or store.db.execute(
        "SELECT MAX(day) AS d FROM events WHERE kind = 'ai_coach'").fetchone()["d"]
    if reviewed is not None:
        t = pd.Timestamp(target)
        if pd.Timestamp(reviewed) >= t - pd.Timedelta(days=t.weekday()):
            return None  # déjà revue (le lundi de sa semaine ou après)
    return target


def review_if_due(store: Store, data, bots: list[Bot], client=None, starting_cash: float = 10_000.0) -> int | None:
    """Lance la revue de la semaine si elle est due. Rend None si rien n'était dû, sinon le nombre de Chubbies coachés.

    La semaine n'est marquée comme revue que si au moins un Chubby a été coaché : sinon (pas de clé, pas de
    réseau…) on réessaie à la séance suivante.
    """
    if not any(b.ai for b in bots):
        return None
    target = due_week(store)
    if target is None:
        return None
    n = weekly_review(store, data, bots, target, client=client, starting_cash=starting_cash)
    if n:
        store.set_meta(REVIEWED_KEY, target)
        store.commit()
    return n
