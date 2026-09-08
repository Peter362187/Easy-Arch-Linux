"""Die kurze Einblendung beim Start.

Ein Programm, das eine halbe Stunde lang eine ISO baut, darf beim Oeffnen eine
knappe Sekunde auf sich aufmerksam machen. Laenger nicht: eine Einblendung, die
man wegklicken moechte, ist eine zu viel.

Sie erscheint einmal je Prozess und nie, wenn Bewegungen reduziert sind oder
offscreen gezeichnet wird -- in beiden Faellen waere sie nur eine Verzoegerung.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget

from .. import motion
from ..design import tokens
from ..design.typo import SUBTITLE, TITLE, schrift

_gezeigt = False


class IntroOverlay(QWidget):
    """Logo und Name, kurz eingeblendet ueber dem Fenster."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._anteil = 0.0
        self._deckkraft = 1.0
        self._logo: QPixmap | None = _logo(parent.devicePixelRatioF())
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setGeometry(parent.rect())

    # -- Ablauf ---------------------------------------------------------------
    def spielen(self) -> None:
        motion.animate(
            self,
            von=0.0,
            bis=1.0,
            dauer=motion.INTRO,
            kurve=QEasingCurve.Type.OutCubic,
            setzen=self._setze_anteil,
            fertig=self._ausblenden,
        )

    def _setze_anteil(self, wert) -> None:
        self._anteil = float(wert)
        self.update()

    def _ausblenden(self) -> None:
        # Die Uhr ist ein Kind des Overlays: wird das Fenster vorher
        # geschlossen, stirbt sie mit und fasst nichts Geloeschtes mehr an.
        uhr = QTimer(self)
        uhr.setSingleShot(True)
        uhr.timeout.connect(self._verschwinden)
        uhr.start(motion.duration(motion.SCHNELL))

    def _verschwinden(self) -> None:
        motion.animate(
            self,
            von=1.0,
            bis=0.0,
            dauer=motion.NORMAL,
            setzen=self._setze_deckkraft,
            fertig=self.deleteLater,
        )

    def _setze_deckkraft(self, wert) -> None:
        self._deckkraft = float(wert)
        self.update()

    # -- Zeichnen -------------------------------------------------------------
    def paintEvent(self, event) -> None:
        werte = tokens()
        p = werte.palette
        maler = QPainter(self)
        maler.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        maler.setOpacity(self._deckkraft)

        maler.fillRect(self.rect(), QColor(p.bg))

        mitte = QRectF(self.rect()).center()
        skala = 0.85 + 0.15 * self._anteil

        if self._logo is not None:
            kante = 96.0 * skala
            ziel = QRectF(mitte.x() - kante / 2, mitte.y() - kante / 2 - 24, kante, kante)
            maler.setOpacity(self._deckkraft * min(1.0, self._anteil * 2))
            maler.drawPixmap(ziel.toRect(), self._logo)

        maler.setOpacity(self._deckkraft * max(0.0, self._anteil * 1.5 - 0.5))
        maler.setFont(schrift(TITLE, fett=True))
        maler.setPen(QColor(p.text))
        titel = QRectF(0, mitte.y() + 40, self.width(), 32)
        maler.drawText(titel, int(Qt.AlignmentFlag.AlignCenter), "ArchCustomiser")

        maler.setFont(schrift(SUBTITLE))
        maler.setPen(QColor(p.text_muted))
        unter = QRectF(0, mitte.y() + 74, self.width(), 24)
        maler.drawText(unter, int(Qt.AlignmentFlag.AlignCenter), "Arch Linux ISO Builder")
        maler.end()


def _logo(dpr: float) -> QPixmap | None:
    from ...core.paths import package_root

    datei = package_root() / "assets" / "icons" / "archcustomiser.svg"
    if not datei.is_file():
        return None
    renderer = QSvgRenderer(str(datei))
    if not renderer.isValid():
        return None
    kante = int(96 * max(1.0, dpr))
    bild = QPixmap(kante, kante)
    bild.setDevicePixelRatio(max(1.0, dpr))
    bild.fill(Qt.GlobalColor.transparent)
    maler = QPainter(bild)
    renderer.render(maler)
    maler.end()
    return bild


def zeige_einmal(fenster: QWidget) -> IntroOverlay | None:
    """Blendet das Intro ein -- hoechstens einmal je Prozess."""
    global _gezeigt
    if _gezeigt or motion.is_reduced():
        return None
    _gezeigt = True
    overlay = IntroOverlay(fenster)
    overlay.show()
    overlay.raise_()
    overlay.spielen()
    return overlay


__all__ = ["IntroOverlay", "zeige_einmal"]
