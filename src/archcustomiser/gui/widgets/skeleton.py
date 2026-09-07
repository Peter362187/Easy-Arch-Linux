"""Platzhalter waehrend die Paketdaten laden.

Der Index braucht beim ersten Start einige Sekunden. Vorher stand die Seite
solange leer da; man wusste nicht, ob noch etwas kommt. Ein Platzhalter in der
Form des spaeteren Inhalts beantwortet genau das -- ohne einen Fortschritt zu
behaupten, den niemand kennt.

Der Schimmer laeuft nur, solange das Widget sichtbar ist. Ein Platzhalter auf
einer verdeckten Seite, der weiterschimmert, waere Leerlauflast.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget

from .. import motion
from ..design import mit_alpha, tokens


class SkeletonRows(QWidget):
    """Mehrere Zeilen unterschiedlicher Laenge, die sanft schimmern."""

    def __init__(self, zeilen: int = 4, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._zeilen = zeilen
        self._phase = 0.0
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(zeilen * 28)

        self._takt = QTimer(self)
        self._takt.setInterval(40)
        self._takt.timeout.connect(self._weiter)

    def _weiter(self) -> None:
        self._phase = (self._phase + 0.02) % 1.0
        self.update()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not motion.is_reduced():
            self._takt.start()

    def hideEvent(self, event) -> None:
        self._takt.stop()
        super().hideEvent(event)

    def paintEvent(self, event) -> None:
        werte = tokens()
        p = werte.palette
        maler = QPainter(self)
        maler.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        maler.setPen(Qt.PenStyle.NoPen)

        hoehe = 14.0
        abstand = 28.0
        breiten = (0.92, 0.68, 0.84, 0.55, 0.76, 0.62)

        for i in range(self._zeilen):
            oben = i * abstand + (abstand - hoehe) / 2
            breite = self.width() * breiten[i % len(breiten)]
            kasten = QRectF(0, oben, breite, hoehe)

            verlauf = QLinearGradient(kasten.left(), 0, kasten.right(), 0)
            ruhe = QColor(mit_alpha(p.text_subtle, 0.16))
            hell = QColor(mit_alpha(p.text_subtle, 0.30))
            mitte = (self._phase + i * 0.08) % 1.0
            verlauf.setColorAt(0.0, ruhe)
            verlauf.setColorAt(max(0.0, mitte - 0.12), ruhe)
            verlauf.setColorAt(mitte, hell)
            verlauf.setColorAt(min(1.0, mitte + 0.12), ruhe)
            verlauf.setColorAt(1.0, ruhe)

            maler.setBrush(verlauf)
            maler.drawRoundedRect(kasten, hoehe / 2, hoehe / 2)
        maler.end()
