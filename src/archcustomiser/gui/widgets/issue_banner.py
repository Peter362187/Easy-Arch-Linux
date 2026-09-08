"""Hinweisleiste am Kopf einer Seite.

Zeigt Fehler, Warnungen und Hinweise des Resolvers. Traegt ein Problem einen
maschinell anwendbaren Vorschlag, erscheint daneben eine Schaltflaeche -- der
Benutzer muss den Widerspruch dann nicht selbst aufloesen.

Die Zeile ist selbstgezeichnet. Die alte Fassung setzte ein Stylesheet ohne
Selektor auf den Rahmen; das vererbte Hintergrund und den linken Randstreifen
an den Beheben-Knopf, der dadurch sein Plattform-Aussehen verlor.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...core.resolver import Fix, Issue
from ..design import mit_alpha, tokens
from ..design.typo import BODY, schrift


class _Zeile(QWidget):
    """Ein Befund mit Farbstreifen und optionalem Loesungsknopf."""

    fixRequested = Signal(object)

    def __init__(self, issue: Issue, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.issue = issue
        werte = tokens()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            werte.space.md + 4, werte.space.sm, werte.space.md, werte.space.sm
        )
        layout.setSpacing(werte.space.md)

        text = QLabel(issue.message)
        text.setWordWrap(True)
        text.setFont(schrift(BODY))
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(text, 1)

        if issue.fix is not None:
            knopf = QPushButton(issue.fix.label)
            knopf.setProperty("variant", "ghost")
            fix: Fix = issue.fix
            knopf.clicked.connect(lambda _c=False, f=fix: self.fixRequested.emit(f))
            layout.addWidget(knopf, 0)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

    def paintEvent(self, event) -> None:
        werte = tokens()
        p = werte.palette
        farbe = {
            "error": p.danger,
            "warning": p.warning,
            "info": p.info,
        }.get(self.issue.severity, p.info)

        maler = QPainter(self)
        maler.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        maler.setPen(Qt.PenStyle.NoPen)

        flaeche = QRectF(self.rect())
        maler.setBrush(QColor(mit_alpha(farbe, 0.12)))
        maler.drawRoundedRect(flaeche, werte.radius.md, werte.radius.md)

        streifen = QRectF(flaeche.left(), flaeche.top(), 4.0, flaeche.height())
        maler.setBrush(QColor(farbe))
        maler.drawRoundedRect(streifen, 2.0, 2.0)
        maler.end()


class IssueBanner(QWidget):
    """Liste der Probleme einer Seite -- Fehler zuerst."""

    fixRequested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        werte = tokens()
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(werte.space.xs)
        self.hide()

    def set_issues(self, issues: tuple[Issue, ...]) -> None:
        while self._layout.count():
            eintrag = self._layout.takeAt(0)
            widget = eintrag.widget()
            if widget is not None:
                widget.deleteLater()

        if not issues:
            self.hide()
            return

        # Fehler zuerst -- was den Weiter-Knopf blockiert, gehoert nach oben.
        rang = {"error": 0, "warning": 1, "info": 2}
        for issue in sorted(issues, key=lambda i: rang.get(i.severity, 3)):
            zeile = _Zeile(issue)
            zeile.fixRequested.connect(self.fixRequested)
            self._layout.addWidget(zeile)
        self.show()
