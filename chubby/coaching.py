"""Le coaching : ton conseil change l'agressivité d'un Chubby pour ses prochaines séances."""

from __future__ import annotations

from datetime import date

from .store import Store

ADVICE = {
    "moins": (0.85, "prends moins de risque"),
    "plus": (1.15, "ose un peu plus"),
    "garde": (1.0, "garde ton plan"),
}


def coach(store: Store, bot_id: str, advice: str) -> dict | None:
    row = store.bot_row(bot_id)
    if row is None or advice not in ADVICE:
        return None
    factor, words = ADVICE[advice]
    old = row["aggressiveness"]
    value = max(0.4, min(2.0, old * factor))
    day = store.get_meta("last_day") or str(date.today())
    store.set_aggressiveness(bot_id, value)
    store.add_xp(bot_id, 15)
    store.journal(bot_id, day, f"Mon coach m'a dit : « {words} ». Agressivité {old:.2f} → {value:.2f}.")
    store.event(day, bot_id, "coach", advice=words, aggressiveness=round(value, 2))
    store.commit()
    return {"id": bot_id, "name": row["name"], "old": old, "aggressiveness": value, "advice": words}
