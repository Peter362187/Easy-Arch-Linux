"""Verwaltet Erscheinung, Akzentfarbe und den Wechsel zwischen beiden.

Der Wechsel zur Laufzeit war das eigentliche Problem der alten Oberflaeche:
Farben lagen in Stylesheets, die beim Bau der Widgets entstanden. Eine Funktion
zum Verwerfen des gemerkten Modus gab es -- gerufen hat sie niemand, und
gebracht haette sie nichts.

Hier gilt deshalb: **Farben werden nie eingebrannt.** Selbstzeichnende Widgets
lesen die Tokens in ``paintEvent``, alles andere haengt am Stylesheet der
Anwendung. Ein Wechsel setzt Palette und Stylesheet neu und sendet ein Signal;
mehr braucht kein Widget zu tun als ``update()``.

Der Uebergang selbst ist ein Schnappschuss, der ausblendet: das neue Aussehen
steht bereits, waehrend das alte Bild darueber verschwindet. Das ist die
einzige Stelle der Anwendung mit einem ``QGraphicsOpacityEffect`` -- er liegt
auf einem einzelnen Label ohne Kinder, wo er nichts kosten kann.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QLabel, QWidget

from .. import motion
from ..settings import THEME_DUNKEL, THEME_HELL, THEME_SYSTEM, Settings
from .qss import build_stylesheet
from .tokens import Tokens, tokens_for

log = logging.getLogger(__name__)

_aktuell: Tokens = tokens_for(dunkel=True, akzent="#1793d1")


def tokens() -> Tokens:
    """Die gerade gueltigen Werte -- fuer jede Zeichenroutine."""
    return _aktuell


class ThemeManager(QObject):
    """Setzt Palette und Stylesheet und meldet jede Aenderung."""

    themeChanged = Signal(object)

    def __init__(self, settings: Settings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._fenster: QWidget | None = None

    # -- oeffentlich ----------------------------------------------------------
    def apply(self, *, animiert: bool = False) -> None:
        """Uebernimmt Modus und Akzent aus den Einstellungen."""
        global _aktuell

        schnappschuss = self._schnappschuss() if animiert else None

        _aktuell = tokens_for(dunkel=self._ist_dunkel(), akzent=self.settings.accent)
        app = QApplication.instance()
        if app is not None:
            app.setPalette(self._qt_palette(_aktuell))
            app.setStyleSheet(build_stylesheet(_aktuell))

        from ..widgets import icons

        icons.cache_leeren()
        self.themeChanged.emit(_aktuell)

        if schnappschuss is not None:
            self._ausblenden(schnappschuss)

    def set_mode(self, modus: str) -> None:
        self.settings.theme_mode = modus
        self.apply(animiert=True)

    def set_accent(self, farbe: str) -> None:
        self.settings.accent = farbe
        self.apply(animiert=True)

    def toggle(self) -> None:
        """Zwischen hell und dunkel wechseln -- ohne den Umweg ueber System."""
        self.set_mode(THEME_HELL if _aktuell.palette.dunkel else THEME_DUNKEL)

    def bind(self, fenster: QWidget) -> None:
        """Merkt sich das Fenster, ueber dem der Uebergang liegt."""
        self._fenster = fenster

    # -- intern ---------------------------------------------------------------
    def _ist_dunkel(self) -> bool:
        modus = self.settings.theme_mode
        if modus == THEME_DUNKEL:
            return True
        if modus == THEME_HELL:
            return False
        return self._system_ist_dunkel()

    @staticmethod
    def _system_ist_dunkel() -> bool:
        """Die Erscheinung des Systems -- aus der Palette, nicht geraten.

        Wahrgenommene Helligkeit, nicht der arithmetische Mittelwert: Gruen
        traegt zur empfundenen Helligkeit dreimal so viel bei wie Blau.
        """
        app = QApplication.instance()
        if app is None:
            return True
        farbe = app.palette().color(QPalette.ColorRole.Window)
        helligkeit = 0.299 * farbe.red() + 0.587 * farbe.green() + 0.114 * farbe.blue()
        return helligkeit < 128

    @staticmethod
    def _qt_palette(werte: Tokens) -> QPalette:
        """Auch die Qt-Palette mitziehen.

        Das Stylesheet deckt nicht alles ab: Auswahlfarben in nativen Dialogen,
        die Farbe eines deaktivierten Textes, der Hintergrund eines
        Datei-Dialogs. Ohne die Palette bliebe ein Datei-Dialog im dunklen
        Modus hell.
        """
        p = werte.palette
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(p.bg))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(p.text))
        palette.setColor(QPalette.ColorRole.Base, QColor(p.surface))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(p.surface_alt))
        palette.setColor(QPalette.ColorRole.Text, QColor(p.text))
        palette.setColor(QPalette.ColorRole.Button, QColor(p.surface_alt))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(p.text))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(p.accent))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(p.accent_text))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(p.surface_alt))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(p.text))
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(p.text_subtle))
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Text,
            QColor(p.text_subtle),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.ButtonText,
            QColor(p.text_subtle),
        )
        return palette

    def _schnappschuss(self) -> QLabel | None:
        """Das aktuelle Bild als Label ueber dem Fenster."""
        fenster = self._fenster
        if fenster is None or motion.is_reduced() or not fenster.isVisible():
            return None
        try:
            bild = fenster.grab()
        except Exception:
            log.debug("Schnappschuss fuer den Designwechsel nicht moeglich", exc_info=True)
            return None
        label = QLabel(fenster)
        label.setPixmap(bild)
        label.setGeometry(0, 0, fenster.width(), fenster.height())
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        label.raise_()
        label.show()
        return label

    @staticmethod
    def _ausblenden(label: QLabel) -> None:
        effekt = QGraphicsOpacityEffect(label)
        label.setGraphicsEffect(effekt)
        motion.animate(
            label,
            von=1.0,
            bis=0.0,
            dauer=motion.LANGSAM,
            setzen=effekt.setOpacity,
            fertig=label.deleteLater,
        )
