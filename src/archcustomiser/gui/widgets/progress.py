"""Fortschrittsbalken, die nicht springen.

Ein Bau meldet seinen Fortschritt in Spruengen: die Marken von mkarchiso
liegen bis zu fuenfzehn Prozentpunkte auseinander. Ein Balken, der diese Zahlen
direkt uebernimmt, ruckelt -- und ein ruckelnder Balken sieht nach einem Fehler
aus, auch wenn alles stimmt.

Deshalb wird der angezeigte Wert an den gemeldeten herangefuehrt. Die Zusage
bleibt dieselbe wie im Parser: **monoton**, nie zurueck.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from .. import motion
from ..design import mit_alpha, tokens


class SmoothProgressBar(QWidget):
    """Ein flacher Balken mit weich laufendem Wert."""

    def __init__(self, hoehe: int = 6, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._wert = 0.0
        self._ziel = 0.0
        self.setFixedHeight(hoehe)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_target(self, anteil: float) -> None:
        """Setzt den gemeldeten Stand (0 bis 1)."""
        anteil = max(0.0, min(1.0, float(anteil)))
        # Monoton: ein zurueckspringender Balken sieht nach einem Fehler aus.
        if anteil <= self._ziel:
            return
        self._ziel = anteil
        motion.animate(
            self,
            von=self._wert,
            bis=anteil,
            dauer=motion.LANGSAM,
            setzen=self._setze_wert,
        )

    def reset(self) -> None:
        self._wert = 0.0
        self._ziel = 0.0
        self.update()

    def value(self) -> float:
        return self._wert

    def _setze_wert(self, wert) -> None:
        self._wert = float(wert)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 -- Qt
        p = tokens().palette
        maler = QPainter(self)
        maler.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        maler.setPen(Qt.PenStyle.NoPen)

        flaeche = QRectF(self.rect())
        radius = flaeche.height() / 2
        maler.setBrush(QColor(mit_alpha(p.text_subtle, 0.25)))
        maler.drawRoundedRect(flaeche, radius, radius)

        if self._wert > 0:
            gefuellt = QRectF(flaeche)
            gefuellt.setWidth(max(flaeche.height(), flaeche.width() * self._wert))
            maler.setBrush(QColor(p.accent))
            maler.drawRoundedRect(gefuellt, radius, radius)
        maler.end()
