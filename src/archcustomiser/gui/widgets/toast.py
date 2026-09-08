"""Kurze Meldungen, die niemanden aufhalten.

Die alte Oberflaeche zeigte nach dem Laden eines Profils bis zu drei modale
Boxen hintereinander: Hinweise, Passwort-Hinweis, gegebenenfalls
Speicherbestaetigung. Drei Klicks, bevor man wieder arbeiten konnte -- fuer
Auskuenfte, die niemand bestaetigen muss.

Ein Toast sagt dasselbe, ohne den Weg zu versperren. Modale Dialoge bleiben
dort, wo eine Entscheidung noetig ist: Beenden mit ungesicherter Arbeit,
Abbruch eines laufenden Baus.

Selbstgezeichnet und ohne Kindwidgets -- ein Widget mit Kindern liesse sich
nicht als Ganzes einblenden, ohne einen ``QGraphicsOpacityEffect`` zu
benutzen, und der rendert jeden Frame in ein Zwischenbild.
"""

from __future__ import annotations

from enum import Enum

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter
from PySide6.QtWidgets import QWidget

from .. import motion
from ..design import tokens
from ..design.typo import BODY, schrift

ANZEIGEDAUER_MS = 4500
MAX_SICHTBAR = 3


class Art(Enum):
    INFO = "info"
    ERFOLG = "erfolg"
    WARNUNG = "warnung"
    FEHLER = "fehler"


class Toast(QWidget):
    """Eine einzelne Meldung."""

    angeklickt = Signal()

    def __init__(self, text: str, art: Art, aktion: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = text
        self._art = art
        self._aktion = aktion
        self._deckkraft = 0.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not aktion)
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if aktion else Qt.CursorShape.ArrowCursor
        )
        self.setFixedSize(self._gemessene_groesse())

    def _gemessene_groesse(self):
        from PySide6.QtCore import QSize

        werte = tokens()
        metrik = QFontMetricsF(schrift(BODY))
        breite = metrik.horizontalAdvance(self._text) + werte.space.xl * 2
        if self._aktion:
            breite += metrik.horizontalAdvance(self._aktion) + werte.space.lg
        breite = min(max(breite, 220.0), 520.0)
        hoehe = metrik.height() + werte.space.md * 2
        return QSize(int(breite), int(hoehe))

    def setze_deckkraft(self, wert) -> None:
        self._deckkraft = float(wert)
        self.update()

    def mousePressEvent(self, event) -> None:
        if self._aktion:
            self.angeklickt.emit()
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        werte = tokens()
        p = werte.palette
        maler = QPainter(self)
        maler.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        maler.setOpacity(self._deckkraft)

        farbe = {
            Art.INFO: p.info,
            Art.ERFOLG: p.success,
            Art.WARNUNG: p.warning,
            Art.FEHLER: p.danger,
        }[self._art]

        flaeche = QRectF(self.rect())
        maler.setPen(Qt.PenStyle.NoPen)
        maler.setBrush(QColor(p.surface_alt))
        maler.drawRoundedRect(flaeche, werte.radius.lg, werte.radius.lg)

        streifen = QRectF(flaeche.left(), flaeche.top(), 4.0, flaeche.height())
        maler.setBrush(QColor(farbe))
        maler.drawRoundedRect(streifen, 2.0, 2.0)

        maler.setFont(schrift(BODY))
        maler.setPen(QColor(p.text))
        textkasten = flaeche.adjusted(werte.space.lg, 0, -werte.space.md, 0)
        if self._aktion:
            metrik = QFontMetricsF(schrift(BODY, fett=True))
            aktionsbreite = metrik.horizontalAdvance(self._aktion) + werte.space.md
            textkasten.setWidth(textkasten.width() - aktionsbreite)
            maler.drawText(
                textkasten, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), self._text
            )
            maler.setFont(schrift(BODY, fett=True))
            maler.setPen(QColor(p.accent))
            maler.drawText(
                flaeche.adjusted(0, 0, -werte.space.lg, 0),
                int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
                self._aktion,
            )
        else:
            maler.drawText(
                textkasten, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), self._text
            )
        maler.end()


class ToastHost(QObject):
    """Stapelt Meldungen unten rechts im Fenster."""

    def __init__(self, fenster: QWidget) -> None:
        super().__init__(fenster)
        self._fenster = fenster
        self._sichtbar: list[Toast] = []
        fenster.installEventFilter(self)

    def zeige(
        self,
        text: str,
        art: Art = Art.INFO,
        *,
        aktion: str = "",
        bei_aktion=None,
    ) -> Toast:
        toast = Toast(text, art, aktion, self._fenster)
        if bei_aktion is not None:
            toast.angeklickt.connect(lambda: (bei_aktion(), self._schliessen(toast)))
        self._sichtbar.append(toast)
        while len(self._sichtbar) > MAX_SICHTBAR:
            self._schliessen(self._sichtbar[0])

        toast.show()
        toast.raise_()
        self._anordnen()

        start = toast.pos() + QPoint(0, 16)
        toast.move(start)
        motion.animate(
            toast,
            von=0.0,
            bis=1.0,
            dauer=motion.NORMAL,
            setzen=toast.setze_deckkraft,
        )
        self._anordnen()

        # Die Uhr haengt am Toast, nicht am Wirt. ``QTimer.singleShot`` haette
        # nach viereinhalb Sekunden auch dann noch zugeschlagen, wenn das
        # Fenster laengst zu ist: der Rueckruf haette dann ein geloeschtes
        # C++-Objekt angefasst ("Internal C++ object already deleted"). Als
        # Kind des Toasts stirbt die Uhr mit ihm.
        uhr = QTimer(toast)
        uhr.setSingleShot(True)
        uhr.setInterval(ANZEIGEDAUER_MS)
        uhr.timeout.connect(lambda: self._schliessen(toast))
        uhr.start()
        return toast

    def _schliessen(self, toast: Toast) -> None:
        if toast not in self._sichtbar:
            return
        self._sichtbar.remove(toast)
        motion.animate(
            toast,
            von=toast._deckkraft,
            bis=0.0,
            dauer=motion.SCHNELL,
            setzen=toast.setze_deckkraft,
            fertig=toast.deleteLater,
        )
        self._anordnen()

    def schliesse_alle(self) -> None:
        """Beim Schliessen des Fensters -- nichts soll nachklingen."""
        for toast in list(self._sichtbar):
            self._sichtbar.remove(toast)
            toast.deleteLater()

    def _anordnen(self) -> None:
        werte = tokens()
        unten = self._fenster.height() - werte.space.xl
        for toast in reversed(self._sichtbar):
            x = self._fenster.width() - toast.width() - werte.space.xl
            y = unten - toast.height()
            toast.move(x, y)
            unten = y - werte.space.sm

    def eventFilter(self, objekt, ereignis) -> bool:
        if objekt is self._fenster and ereignis.type() == QEvent.Type.Resize:
            self._anordnen()
        return False
