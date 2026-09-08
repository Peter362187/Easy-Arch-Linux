"""Suchfeld und Filterchips ueber langen Auswahllisten.

Die Programmseite hat vierundzwanzig Eintraege in sechs Gruppen; Treiber und
Dienste sind aehnlich lang. Ohne Filter blieb nur scrollen und lesen.

Gesucht wird auch im **Paketnamen** -- der ist oft das, was der Benutzer im
Kopf hat. Wer "steam" sucht, denkt nicht an "Spieleplattform".
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QWidget,
)

from ..design import qfarbe, tokens
from ..design.typo import CAPTION, schrift


class Chip(QWidget):
    """Ein an- und abschaltbarer Filter."""

    umgeschaltet = Signal(str, bool)

    def __init__(self, kennung: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kennung = kennung
        self._text = text
        self._aktiv = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(text)
        metrik = QFontMetricsF(schrift(CAPTION))
        werte = tokens()
        self.setFixedSize(
            QSize(
                int(metrik.horizontalAdvance(text) + werte.space.lg),
                int(metrik.height() + werte.space.sm),
            )
        )

    def ist_aktiv(self) -> bool:
        return self._aktiv

    def setze_aktiv(self, wert: bool) -> None:
        if self._aktiv == wert:
            return
        self._aktiv = wert
        self.update()

    def mousePressEvent(self, event) -> None:
        self.setze_aktiv(not self._aktiv)
        self.umgeschaltet.emit(self.kennung, self._aktiv)
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.setze_aktiv(not self._aktiv)
            self.umgeschaltet.emit(self.kennung, self._aktiv)
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:
        werte = tokens()
        p = werte.palette
        maler = QPainter(self)
        maler.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        maler.setPen(Qt.PenStyle.NoPen)

        from PySide6.QtCore import QRectF

        flaeche = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = flaeche.height() / 2
        maler.setBrush(
            qfarbe(p.accent, 0.2) if self._aktiv else QColor(p.surface_alt)
        )
        maler.drawRoundedRect(flaeche, radius, radius)

        if self._aktiv or self.hasFocus():
            from PySide6.QtGui import QPen

            maler.setPen(QPen(QColor(p.accent), 1.5))
            maler.setBrush(Qt.BrushStyle.NoBrush)
            maler.drawRoundedRect(flaeche, radius, radius)

        maler.setFont(schrift(CAPTION))
        maler.setPen(QColor(p.text if self._aktiv else p.text_muted))
        maler.drawText(flaeche, int(Qt.AlignmentFlag.AlignCenter), self._text)
        maler.end()


class SearchField(QWidget):
    """Suchfeld mit Trefferzaehler und optionalen Filtern."""

    textChanged = Signal(str)
    filterChanged = Signal(str, bool)

    def __init__(self, platzhalter: str = "Suchen ...", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        werte = tokens()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(werte.space.sm)

        self.edit = QLineEdit()
        self.edit.setPlaceholderText(platzhalter)
        self.edit.setClearButtonEnabled(True)
        self.edit.setAccessibleName("Suchfeld")
        self.edit.textChanged.connect(self.textChanged)
        layout.addWidget(self.edit, 1)

        self._chips: list[Chip] = []
        self._chipbereich = QHBoxLayout()
        self._chipbereich.setContentsMargins(0, 0, 0, 0)
        self._chipbereich.setSpacing(werte.space.xs)
        layout.addLayout(self._chipbereich)

        self.zaehler = QLabel("")
        self.zaehler.setProperty("rolle", "gedaempft")
        self.zaehler.setFont(schrift(CAPTION))
        self.zaehler.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        layout.addWidget(self.zaehler, 0, Qt.AlignmentFlag.AlignVCenter)

    def filter_hinzufuegen(self, kennung: str, text: str) -> Chip:
        chip = Chip(kennung, text)
        chip.umgeschaltet.connect(self.filterChanged)
        self._chips.append(chip)
        self._chipbereich.addWidget(chip)
        return chip

    def aktive_filter(self) -> set[str]:
        return {chip.kennung for chip in self._chips if chip.ist_aktiv()}

    def text(self) -> str:
        return self.edit.text().strip()

    def setze_trefferzahl(self, sichtbar: int, gesamt: int) -> None:
        self.zaehler.setText("" if sichtbar == gesamt else f"{sichtbar} von {gesamt}")
