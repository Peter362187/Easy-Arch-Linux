"""Ein Zustandszeichen, das sich zeichnet statt zu erscheinen.

Gebraucht in der Vorabpruefung und in der Schrittliste des Baus. Der Unterschied
zu einem Textzeichen ist nicht Zierde: ein Haken, der sich in 200 ms zieht,
sagt "das ist gerade passiert", waehrend ein Haken, der einfach dasteht, nur
sagt "das ist so". Bei einer Liste, die nach und nach abgearbeitet wird, ist das
die ganze Information.

Der laufende Zustand ist der einzige Dauerlaeufer der Anwendung -- und er ist
an ``show``/``hide`` gebunden. Ein unsichtbarer Spinner, der weiterdreht, ist
genau die Leerlauflast, die es hier nicht geben soll.
"""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from .. import motion
from ..design import tokens


class Zustand(Enum):
    OFFEN = "offen"
    LAEUFT = "laeuft"
    OK = "ok"
    WARNUNG = "warnung"
    FEHLER = "fehler"


class AnimatedCheck(QWidget):
    """Kreis mit Haken, Ausrufezeichen, Kreuz oder drehendem Bogen."""

    def __init__(self, groesse: int = 20, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._groesse = groesse
        self._zustand = Zustand.OFFEN
        self._fortschritt = 0.0
        self._winkel = 0.0
        self.setFixedSize(groesse, groesse)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        # Nur fuer den laufenden Zustand; startet in showEvent, stoppt in
        # hideEvent -- und nur, wenn er ueberhaupt gebraucht wird.
        self._takt = QTimer(self)
        self._takt.setInterval(33)
        self._takt.timeout.connect(self._drehen)

    # -- Zustand --------------------------------------------------------------
    def set_zustand(self, zustand: Zustand, *, animiert: bool = True) -> None:
        if zustand == self._zustand:
            return
        vorher = self._zustand
        self._zustand = zustand
        self._takt_pruefen()

        if zustand in (Zustand.OK, Zustand.WARNUNG, Zustand.FEHLER):
            start = 0.0 if vorher is not zustand else self._fortschritt
            motion.animate(
                self,
                von=start,
                bis=1.0,
                dauer=motion.NORMAL if animiert else 0,
                setzen=self._setze_fortschritt,
            )
        else:
            self._fortschritt = 0.0
            self.update()

    def zustand(self) -> Zustand:
        return self._zustand

    def _setze_fortschritt(self, wert) -> None:
        self._fortschritt = float(wert)
        self.update()

    def _drehen(self) -> None:
        self._winkel = (self._winkel + 9.0) % 360.0
        self.update()

    def _takt_pruefen(self) -> None:
        soll = self._zustand is Zustand.LAEUFT and self.isVisible() and not motion.is_reduced()
        if soll and not self._takt.isActive():
            self._takt.start()
        elif not soll and self._takt.isActive():
            self._takt.stop()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._takt_pruefen()

    def hideEvent(self, event) -> None:
        self._takt.stop()
        super().hideEvent(event)

    # -- Zeichnen -------------------------------------------------------------
    def paintEvent(self, event) -> None:
        p = tokens().palette
        maler = QPainter(self)
        maler.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        flaeche = QRectF(self.rect()).adjusted(2, 2, -2, -2)

        farbe = {
            Zustand.OFFEN: p.text_subtle,
            Zustand.LAEUFT: p.accent,
            Zustand.OK: p.success,
            Zustand.WARNUNG: p.warning,
            Zustand.FEHLER: p.danger,
        }[self._zustand]

        if self._zustand is Zustand.LAEUFT:
            maler.setPen(QPen(QColor(p.border), 2.0))
            maler.setBrush(Qt.BrushStyle.NoBrush)
            maler.drawEllipse(flaeche)
            maler.setPen(QPen(QColor(farbe), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            maler.drawArc(flaeche, int(-self._winkel * 16), 100 * 16)
            maler.end()
            return

        maler.setPen(QPen(QColor(farbe), 2.0))
        maler.setBrush(Qt.BrushStyle.NoBrush)
        maler.drawEllipse(flaeche)

        if self._zustand is Zustand.OFFEN or self._fortschritt <= 0:
            maler.end()
            return

        maler.setPen(
            QPen(QColor(farbe), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        )
        pfad = self._zeichen(flaeche)
        maler.drawPath(_teilpfad(pfad, self._fortschritt))
        maler.end()

    def _zeichen(self, flaeche: QRectF) -> QPainterPath:
        pfad = QPainterPath()
        mitte = flaeche.center()
        if self._zustand is Zustand.OK:
            pfad.moveTo(flaeche.left() + flaeche.width() * 0.26, mitte.y() + 0.5)
            pfad.lineTo(mitte.x() - flaeche.width() * 0.03, flaeche.bottom() - flaeche.height() * 0.28)
            pfad.lineTo(flaeche.right() - flaeche.width() * 0.22, flaeche.top() + flaeche.height() * 0.28)
        elif self._zustand is Zustand.FEHLER:
            rand = flaeche.width() * 0.28
            pfad.moveTo(flaeche.left() + rand, flaeche.top() + rand)
            pfad.lineTo(flaeche.right() - rand, flaeche.bottom() - rand)
            pfad.moveTo(flaeche.right() - rand, flaeche.top() + rand)
            pfad.lineTo(flaeche.left() + rand, flaeche.bottom() - rand)
        else:  # Warnung: ein Ausrufezeichen
            pfad.moveTo(mitte.x(), flaeche.top() + flaeche.height() * 0.24)
            pfad.lineTo(mitte.x(), mitte.y() + flaeche.height() * 0.08)
            pfad.moveTo(mitte.x(), flaeche.bottom() - flaeche.height() * 0.20)
            pfad.lineTo(mitte.x(), flaeche.bottom() - flaeche.height() * 0.18)
        return pfad


def _teilpfad(pfad: QPainterPath, anteil: float) -> QPainterPath:
    """Der Anfang eines Pfades -- damit sich ein Zeichen zeichnen kann."""
    anteil = max(0.0, min(1.0, anteil))
    if anteil >= 1.0:
        return pfad
    ergebnis = QPainterPath()
    if anteil <= 0.0 or pfad.isEmpty():
        return ergebnis
    schritte = 28
    ergebnis.moveTo(pfad.pointAtPercent(0.0))
    for i in range(1, schritte + 1):
        ergebnis.lineTo(pfad.pointAtPercent(anteil * i / schritte))
    return ergebnis
