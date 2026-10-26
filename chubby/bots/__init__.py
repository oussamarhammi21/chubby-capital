from .base import Bot, BotContext, Decision, Param, ai_version
from .chubbies import RULE_BOTS
from .matcha import Matcha

ALL_BOTS = RULE_BOTS + [Matcha]
AI_BOTS = [ai_version(cls) for cls in ALL_BOTS]


def all_bots(ia: bool = False) -> list[Bot]:
    """Les Chubbies de l'arène. Avec ia=True, chacun a aussi sa version coachée par Claude.

    Les versions IA ne tournent qu'en direct : Claude connaît déjà l'histoire des marchés,
    les faire jouer sur un backtest reviendrait à tricher.
    """
    return [cls() for cls in ALL_BOTS + (AI_BOTS if ia else [])]


def bot_class(bot_id: str) -> type[Bot] | None:
    return next((cls for cls in ALL_BOTS + AI_BOTS if cls.id == bot_id), None)


def all_tickers() -> list[str]:
    return sorted({t for cls in ALL_BOTS for t in cls.universe + cls.signals})


__all__ = ["Bot", "BotContext", "Decision", "Param", "ALL_BOTS", "AI_BOTS", "all_bots", "bot_class", "all_tickers"]
