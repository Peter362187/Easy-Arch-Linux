"""Seitenwechsel mit Uebergang.

Animiert wird ueber **Schnappschuesse**, nicht ueber die Widgets selbst. Der
Unterschied ist nicht kosmetisch: eine Auswahlseite hat bis zu vierundzwanzig
Karten in einem Raster. Verschoebe man die Seiten direkt, rechnete Qt in jedem
Frame das gesamte Layout neu -- bei sechzig Bildern je Sekunde also sechzigmal.
Zwei Bilder zu verschieben kostet dagegen nichts.

Der Preis ist ein kurzer Moment, in dem nichts anklickbar ist. Bei 200 ms fuer
den Uebergang faellt das nicht auf; laenger darf er deshalb nicht werden.
"""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt, QVariantAnimation
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QStackedWidget, QWidget

from .. import motion


class _Uebergang(QWidget):
    """Zeichnet zwei Schnappschuesse, waehrend sie aneinander vorbeiziehen."""

    def __init__(self, alt: QPixmap, neu: QPixmap, richtung: int, parent: QWidget) -> None:
        super().__init__(parent)
        self._alt = alt
        self._neu = neu
        self._richtung = richtung
        self._anteil = 0.0
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def setze_anteil(self, wert) -> None:
        self._anteil = float(wert)
        self.update()

    def paintEvent(self, event) -> None:
        maler = QPainter(self)
        versatz = int(32 * self._richtung)

        maler.setOpacity(max(0.0, 1.0 - self._anteil * 1.4))
        maler.drawPixmap(
            QRect(int(-versatz * self._anteil), 0, self.width(), self.height()), self._alt
        )

        maler.setOpacity(min(1.0, self._anteil * 1.4))
        maler.drawPixmap(
            QRect(int(versatz * (1.0 - self._anteil)), 0, self.width(), self.height()), self._neu
        )
        maler.end()


class AnimatedStack(QStackedWidget):
    """Ein Seitenstapel, dessen Wechsel man sieht."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._laeuft: _Uebergang | None = None
        self._animation: QVariantAnimation | None = None

    def set_current(self, index: int, *, richtung: int = 1) -> None:
        """Wechselt zur Seite; ``richtung`` 1 heisst vorwaerts, -1 zurueck."""
        if index == self.currentIndex() or not (0 <= index < self.count()):
            self.setCurrentIndex(index)
            return

        if motion.is_reduced() or not self.isVisible():
            self.setCurrentIndex(index)
            return

        alt = self._bild(self.currentWidget())
        self.setCurrentIndex(index)
        # Erst das Layout durchrechnen lassen, sonst zeigt der Schnappschuss
        # die Seite in der Groesse der vorigen.
        self.layout().activate()
        neu = self._bild(self.currentWidget())
        if alt is None or neu is None:
            return

        self._abbrechen()
        overlay = _Uebergang(alt, neu, richtung, self)
        overlay.setGeometry(self.rect())
        overlay.show()
        overlay.raise_()
        self._laeuft = overlay

        def fertig() -> None:
            self._laeuft = None
            self._animation = None
            overlay.deleteLater()

        self._animation = motion.animate(
            overlay,
            von=0.0,
            bis=1.0,
            dauer=motion.NORMAL,
            setzen=overlay.setze_anteil,
            fertig=fertig,
        )

    def resizeEvent(self, event) -> None:
        # Ein Uebergang mit falscher Groesse sieht schlimmer aus als keiner.
        self._abbrechen()
        super().resizeEvent(event)

    def _abbrechen(self) -> None:
        # Erst anhalten, dann wegraeumen. Wird das Overlay unter einer
        # laufenden Animation geloescht, endet die nie regulaer -- und ihr
        # Eintrag in der Buchfuehrung von ``motion`` bliebe stehen.
        if self._animation is not None:
            self._animation.stop()
            self._animation = None
        if self._laeuft is not None:
            self._laeuft.deleteLater()
            self._laeuft = None

    @staticmethod
    def _bild(widget: QWidget | None) -> QPixmap | None:
        if widget is None or widget.width() <= 0 or widget.height() <= 0:
            return None
        # grab() setzt die devicePixelRatio selbst -- ohne sie waere das Bild
        # bei 125 % oder 150 % sichtbar unscharf.
        return widget.grab()
