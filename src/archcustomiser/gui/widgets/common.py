"""Bausteine, die vorher in jeder Datei noch einmal gebaut wurden.

Die Durchsicht fand vier wortgleiche Kopien, jede mit eigenem funktionslokalem
Import:

* ``_brush(colour)`` viermal -- in ``free_packages``, ``summary``,
  ``preflight_dialog`` und ``build_dialog``.
* Das Kopieren in die Zwischenablage dreimal.
* Das Oeffnen eines Pfades zweimal.
* Das Ueberschrift-Idiom (``setBold`` plus ``setPointSize(+1)``) fuenfmal.
* Ein Monospace-Feld mit Kopieren-Knopf dreimal.

Das ist nicht nur Wiederholung: solange jede Datei ihre eigene Gestaltung baut,
laesst sich ein Design-System gar nicht durchsetzen. Deshalb stehen die
Bausteine hier, und sie holen ihre Werte aus ``theme``.
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

from .. import theme

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


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------


class HeadlineLabel(QLabel):
    """Eine Ueberschrift in der Groesse des Design-Systems."""

    def __init__(self, text: str, level: int = 1, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setFont(theme.headline_font(level))
        self.setWordWrap(True)


class HintLabel(QLabel):
    """Nebentext -- Beschreibungen, Statuszeilen, Erlaeuterungen."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("hint")
        self.setFont(theme.small_font())
        self.setStyleSheet(f"color: {theme.muted()};")
        self.setWordWrap(True)


class CodeBlock(QWidget):
    """Ein Befehl oder Pfad zum Kopieren.

    Wurde vorher dreimal einzeln gebaut -- in ``export_dialog``, ``wsl_dialog``
    und ``summary`` --, jedes Mal mit ``QFont("Consolas", 9)``, das es unter
    Linux nicht gibt.
    """

    def __init__(
        self,
        text: str = "",
        *,
        lines: int = 1,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE_XS)

        self.editor = QPlainTextEdit(text)
        self.editor.setReadOnly(True)
        self.editor.setFont(theme.mono_font())
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        # Mindest- statt Festhoehe: bei 125 % Schriftskalierung schnitt die
        # feste Hoehe den Text ab.
        zeilenhoehe = self.editor.fontMetrics().lineSpacing()
        self.editor.setMinimumHeight(zeilenhoehe * lines + theme.SPACE_MD)
        if lines == 1:
            self.editor.setMaximumHeight(zeilenhoehe * 3 + theme.SPACE_MD)
        layout.addWidget(self.editor)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        self.copy_button = QPushButton("Kopieren")
        self.copy_button.clicked.connect(
            lambda: copy_to_clipboard(self.editor.toPlainText(), self.copy_button)
        )
        row.addWidget(self.copy_button)
        layout.addLayout(row)

    def set_text(self, value: str) -> None:
        self.editor.setPlainText(value)

    def text(self) -> str:
        return self.editor.toPlainText()


class SearchField(QWidget):
    """Ein Filterfeld ueber einer langen Liste.

    Die Programmseite hat vierundzwanzig Eintraege in sechs Gruppen; Treiber
    und Dienste sind aehnlich lang. Ohne Filter blieb nur scrollen und lesen.
    """

    def __init__(self, placeholder: str = "Suchen ...", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QLineEdit

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE_SM)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.setClearButtonEnabled(True)
        layout.addWidget(self.edit, 1)

        self.count_label = HintLabel("")
        layout.addWidget(self.count_label, 0, Qt.AlignmentFlag.AlignVCenter)

    @property
    def textChanged(self):
        return self.edit.textChanged

    def text(self) -> str:
        return self.edit.text().strip()

    def set_result_count(self, shown: int, total: int) -> None:
        self.count_label.setText("" if shown == total else f"{shown} von {total}")


def passende_mindestgroesse(wunsch_breite: int, wunsch_hoehe: int) -> tuple[int, int]:
    """Eine Mindestgroesse, die auf den Bildschirm passt.

    Feste Werte sind logische Pixel und skalieren nicht mit der DPI -- die
    verfuegbare logische Flaeche schrumpft dabei aber. Bei 1920x1080 auf 150 %
    bleiben logisch 1280x720: eine feste Mindesthoehe von 720 ist dann genau die
    volle Bildschirmhoehe, ohne Platz fuer Task- oder Menueleiste, und das
    Fenster laesst sich nicht kleiner ziehen.

    Auf einem 1366x768-Notebook bei 125 % (logisch 1092x614) passt es sicher
    nicht. Deshalb hoechstens neunzig Prozent dessen, was wirklich da ist -- die
    Scrollbereiche der Seiten fangen den Rest ab.
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
