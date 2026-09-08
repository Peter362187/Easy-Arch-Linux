"""Farben fuer den Maler -- nicht fuer das Stylesheet.

Es gibt zwei Empfaenger fuer eine Farbe, und sie verstehen verschiedene
Schreibweisen:

* Ein **Stylesheet** liest CSS. Dort ist ``rgba(23, 147, 209, 0.16)`` richtig,
  und genau das liefert ``tokens.mit_alpha``.
* Ein **QPainter** bekommt ein ``QColor``. Und ``QColor`` versteht ``rgba(...)``
  **nicht**: die Zeichenkette gilt als ungueltiger Name, und heraus kommt
  undurchsichtiges Schwarz.

Das ist keine graue Theorie. ``QColor(mit_alpha(p.accent, 0.16))`` stand an elf
Stellen -- ausgewaehlte Karten, der Ring der aktuellen Schrittzeile, die
Hinweisleiste, der Fortschrittsbalken, die Platzhalterzeilen. Alle elf malten
einen schwarzen Block statt eines zarten Farbschleiers. Auf dunklem Grund fiel
das kaum auf, auf hellem war die Beschriftung darauf nicht mehr zu lesen.

Deshalb diese eine Funktion. Wer fuer einen Maler eine Farbe braucht, holt sie
hier; ``mit_alpha`` bleibt fuer das Stylesheet.
"""

from __future__ import annotations

from PySide6.QtGui import QColor

__all__ = ["qfarbe"]


def qfarbe(hexwert: str, alpha: float = 1.0) -> QColor:
    """Eine Farbe fuer den Maler, mit Durchsichtigkeit.

    ``hexwert`` ist eine der Farben aus ``tokens`` (``#rrggbb``). Ein bereits
    fertiges ``rgba(...)`` wird ebenfalls angenommen und zerlegt -- sonst waere
    jede Aufrufstelle wieder eine Gelegenheit, dieselbe Falle zu treten.
    """
    if hexwert.startswith("rgba(") or hexwert.startswith("rgb("):
        farbe, eingebettet = _aus_css(hexwert)
        if eingebettet is not None:
            alpha = eingebettet * alpha
    else:
        farbe = QColor(hexwert)

    if not farbe.isValid():
        farbe = QColor(0, 0, 0)
    farbe.setAlphaF(max(0.0, min(1.0, alpha)))
    return farbe


def _aus_css(wert: str) -> tuple[QColor, float | None]:
    """Zerlegt ``rgb(r, g, b)`` und ``rgba(r, g, b, a)``."""
    inhalt = wert[wert.find("(") + 1 : wert.rfind(")")]
    teile = [stueck.strip() for stueck in inhalt.split(",")]
    try:
        rot, gruen, blau = (int(float(teil)) for teil in teile[:3])
    except (ValueError, TypeError):
        return QColor(0, 0, 0), None
    alpha: float | None = None
    if len(teile) > 3:
        try:
            alpha = float(teile[3])
        except ValueError:
            alpha = None
    return QColor(rot, gruen, blau), alpha
