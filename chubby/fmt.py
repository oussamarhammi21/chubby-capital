"""Mise en forme des nombres à la française dans les textes des Chubbies."""

import re


def french(text: str) -> str:
    """3,600 $ -> 3 600 $ ; 2.5% -> 2,5 %."""
    text = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "\u202f", text)  # séparateur de milliers
    text = re.sub(r"(?<=\d)\.(?=\d)", ",", text)  # virgule décimale
    return re.sub(r"(?<=\d)%", " %", text)
