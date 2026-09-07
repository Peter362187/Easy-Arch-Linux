"""Bausteine, die vorher in jeder Datei noch einmal gebaut wurden.

Die Durchsicht fand vier wortgleiche Kopien: ein Pinsel aus einer Farbangabe,
das Kopieren in die Zwischenablage, das Oeffnen eines Pfades und das
Ueberschrift-Idiom aus ``setBold`` plus ``setPointSize(+1)``.

Das ist nicht nur Wiederholung: solange jede Datei ihre eigene Gestaltung baut,
laesst sich ein Designsystem gar nicht durchsetzen. Deshalb stehen die
Bausteine hier, und sie holen ihre Werte aus ``design``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QBrush, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..design import tokens
from ..design.typo import BODY, CAPTION, SUBTITLE, TITLE, mono, schrift

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Kleine Helfer
# ---------------------------------------------------------------------------


def brush(colour: str) -> QBrush:
    """Ein Pinsel aus einer Farbangabe -- fuer Baum- und Tabellenzeilen."""
    return QBrush(QColor(colour))


def copy_to_clipboard(value: str, button: QPushButton | None = None) -> None:
    """Kopiert und gibt kurz Rueckmeldung.

    Der Knopftext kehrt nach anderthalb Sekunden zurueck. Vorher blieb er
    dauerhaft auf "Kopiert" stehen -- wer zweimal kopieren wollte, sah nicht,
    ob der zweite Klick angekommen war.
    """
    clipboard = QApplication.clipboard()
    if clipboard is None:
        return
    clipboard.setText(value)
    if button is None:
        return
    original = getattr(button, "_original_text", None) or button.text()
    button._original_text = original          # type: ignore[attr-defined]
    button.setText("Kopiert")
    QTimer.singleShot(1500, lambda: button.setText(original))


def open_path(path: Path | str) -> bool:
    """Oeffnet eine Datei oder einen Ordner im Dateimanager des Systems."""
    url = QUrl.fromLocalFile(str(path))
    if not QDesktopServices.openUrl(url):
        log.warning("Konnte %s nicht oeffnen", path)
        return False
    return True


def passende_mindestgroesse(wunsch_breite: int, wunsch_hoehe: int) -> tuple[int, int]:
    """Eine Mindestgroesse, die auf den Bildschirm passt.

    Feste Werte sind logische Pixel und skalieren nicht mit der DPI -- die
    verfuegbare logische Flaeche schrumpft dabei aber. Bei 1920x1080 auf 150 %
    bleiben logisch 1280x720: eine feste Mindesthoehe von 720 ist dann genau die
    volle Bildschirmhoehe, ohne Platz fuer Task- oder Menueleiste.
    """
    from PySide6.QtGui import QGuiApplication

    bildschirm = QGuiApplication.primaryScreen()
    if bildschirm is None:
        return wunsch_breite, wunsch_hoehe
    verfuegbar = bildschirm.availableGeometry()
    return (
        min(wunsch_breite, int(verfuegbar.width() * 0.9)),
        min(wunsch_hoehe, int(verfuegbar.height() * 0.9)),
    )


# ---------------------------------------------------------------------------
# Beschriftungen
# ---------------------------------------------------------------------------


class HeadlineLabel(QLabel):
    """Eine Ueberschrift in der Groesse des Designsystems."""

    def __init__(self, text: str, level: int = 1, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setFont(schrift(TITLE if level <= 1 else SUBTITLE, fett=True))
        self.setWordWrap(True)
        self.setProperty("rolle", "titel")


class HintLabel(QLabel):
    """Nebentext -- Beschreibungen, Statuszeilen, Erlaeuterungen."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setFont(schrift(CAPTION))
        self.setWordWrap(True)
        self.setProperty("rolle", "gedaempft")


class CodeBlock(QWidget):
    """Ein Befehl oder Pfad zum Kopieren.

    Der Docstring der alten Fassung behauptete, drei handgebaute Kopien
    ersetzt zu haben -- benutzt hat sie danach niemand. Hier ist sie in
    Gebrauch: im Export-Ergebnis, im WSL-Dialog und in der Bau-Ergebnisansicht.
    """

    def __init__(
        self,
        text: str = "",
        *,
        lines: int = 1,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        werte = tokens()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(werte.space.xs)

        self.editor = QPlainTextEdit(text)
        self.editor.setReadOnly(True)
        self.editor.setFont(mono())
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        # Mindest- statt Festhoehe: bei 125 % Schriftskalierung schnitt die
        # feste Hoehe den Text ab.
        zeilenhoehe = self.editor.fontMetrics().lineSpacing()
        self.editor.setMinimumHeight(zeilenhoehe * lines + werte.space.md)
        if lines == 1:
            self.editor.setMaximumHeight(zeilenhoehe * 3 + werte.space.md)
        layout.addWidget(self.editor)

        zeile = QHBoxLayout()
        zeile.setContentsMargins(0, 0, 0, 0)
        zeile.addStretch(1)
        self.copy_button = QPushButton("Kopieren")
        self.copy_button.setProperty("variant", "ghost")
        self.copy_button.clicked.connect(
            lambda: copy_to_clipboard(self.editor.toPlainText(), self.copy_button)
        )
        zeile.addWidget(self.copy_button)
        layout.addLayout(zeile)

    def set_text(self, value: str) -> None:
        self.editor.setPlainText(value)

    def text(self) -> str:
        return self.editor.toPlainText()


class Wertzeile(QWidget):
    """Beschriftung links, Wert rechts -- fuer Ergebnis- und Uebersichtslisten."""

    def __init__(
        self,
        beschriftung: str,
        wert: str = "",
        *,
        mono_wert: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        werte = tokens()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(werte.space.md)

        self._name = QLabel(beschriftung)
        self._name.setFont(schrift(CAPTION))
        self._name.setProperty("rolle", "gedaempft")
        self._name.setMinimumWidth(140)
        layout.addWidget(self._name, 0, Qt.AlignmentFlag.AlignTop)

        self._wert = QLabel(wert)
        self._wert.setFont(mono() if mono_wert else schrift(BODY))
        self._wert.setWordWrap(True)
        self._wert.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self._wert, 1)

    def setze_wert(self, wert: str) -> None:
        self._wert.setText(wert)
