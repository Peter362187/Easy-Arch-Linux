"""Das Designsystem: Werte, Stylesheet, Erscheinungswechsel.

Ausserhalb dieses Pakets steht kein ``setStyleSheet``-Aufruf. Wer eine Farbe
braucht, liest sie ueber ``tokens()``; wer eine Bewegung braucht, geht ueber
``gui.motion``.
"""

from __future__ import annotations

from .theme import ThemeManager, tokens
from .tokens import (
    Palette,
    Radius,
    Spacing,
    Tokens,
    kontrast,
    lesbare_schrift,
    mit_alpha,
    tokens_for,
)

__all__ = [
    "Palette",
    "Radius",
    "Spacing",
    "ThemeManager",
    "Tokens",
    "kontrast",
    "lesbare_schrift",
    "mit_alpha",
    "tokens",
    "tokens_for",
]
