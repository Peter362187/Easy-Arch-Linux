"""Was landet in der ISO -- immer sichtbar, auf jeder Seite.

Die alte Oberflaeche beantwortete diese Frage genau einmal, auf der letzten
Seite. Bis dahin klickte man vierzehn Schritte lang Optionen an, ohne zu sehen,
was daraus folgt: dass "Plasma" achtzig Pakete mitzieht, dass "Steam" das
multilib-Repository braucht, dass die Auswahl gerade auf 4,3 GB gewachsen ist.

Hier steht es neben der Seite und aendert sich mit. Der Inhalt kommt
ausschliesslich aus ``store.resolution()`` und ``store.config`` -- das Panel
rechnet nichts selbst aus, sonst gaebe es zwei Wahrheiten.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...core.catalog import EnableIn
from ..design import tokens
from ..design.typo import CAPTION, DISPLAY, schrift
from .common import HintLabel

log = logging.getLogger(__name__)

# Ein Klick kann ein Dutzend Signale ausloesen; einmal neu zeichnen reicht.
NEUZEICHNEN_MS = 50
MAX_NAMEN = 40


class _Abschnitt(QWidget):
    """Eine Ueberschrift mit Werten darunter."""

    def __init__(self, titel: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        werte = tokens()
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)

        self.kopf = QLabel(titel)
        self.kopf.setFont(schrift(CAPTION, fett=True))
        self.kopf.setProperty("rolle", "gedaempft")
        self._layout.addWidget(self.kopf)

        self.inhalt = QLabel("")
        self.inhalt.setWordWrap(True)
        self.inhalt.setFont(schrift(CAPTION))
        self.inhalt.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._layout.addWidget(self.inhalt)
        self._layout.addSpacing(werte.space.xs)

    def setze(self, text: str, *, rolle: str = "") -> None:
        self.inhalt.setText(text)
        if self.inhalt.property("rolle") != rolle:
            self.inhalt.setProperty("rolle", rolle)
            self.inhalt.style().unpolish(self.inhalt)
            self.inhalt.style().polish(self.inhalt)
        self.setVisible(bool(text))


class IsoPanel(QWidget):
    """Die rechte Spalte: der aktuelle Stand der ISO."""

    zusammenklappen = Signal()

    def __init__(self, store, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        werte = tokens()
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self.setMinimumWidth(240)
        self.setMaximumWidth(360)

        aussen = QVBoxLayout(self)
        aussen.setContentsMargins(
            werte.space.md, werte.space.md, werte.space.md, werte.space.md
        )
        aussen.setSpacing(werte.space.sm)

        kopf = QLabel("Was landet in der ISO")
        kopf.setProperty("rolle", "titel")
        aussen.addWidget(kopf)

        # Die Paketzahl ist die eine Zahl, auf die es ankommt -- sie bekommt
        # entsprechend Platz und zaehlt sichtbar hoch.
        self.zahl = QLabel("0")
        self.zahl.setFont(schrift(DISPLAY, fett=True))
        aussen.addWidget(self.zahl)
        self.zahl_text = HintLabel("Pakete")
        aussen.addWidget(self.zahl_text)

        inneres = QWidget()
        self._liste = QVBoxLayout(inneres)
        self._liste.setContentsMargins(0, 0, 0, 0)
        self._liste.setSpacing(werte.space.xs)

        self.groesse = _Abschnitt("Geschaetzte Groesse")
        self.kernel = _Abschnitt("Kernel")
        self.repos = _Abschnitt("Repositorien")
        self.gruppen = _Abschnitt("Paketgruppen")
        self.dienste = _Abschnitt("Dienste im Live-System")
        self.aur = _Abschnitt("AUR")
        self.eigene = _Abschnitt("Eigene Pakete")
        self.namen = _Abschnitt("Paketliste")
        self.probleme = _Abschnitt("Offen")
        self.datei = _Abschnitt("Ergebnis")
        for abschnitt in (
            self.groesse,
            self.kernel,
            self.datei,
            self.repos,
            self.gruppen,
            self.dienste,
            self.aur,
            self.eigene,
            self.probleme,
            self.namen,
        ):
            self._liste.addWidget(abschnitt)
        self._liste.addStretch(1)

        rolle = QScrollArea()
        rolle.setWidgetResizable(True)
        rolle.setFrameShape(QScrollArea.Shape.NoFrame)
        rolle.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rolle.setWidget(inneres)
        aussen.addWidget(rolle, 1)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(NEUZEICHNEN_MS)
        self._timer.timeout.connect(self.refresh)

        store.resolutionChanged.connect(self._timer.start)
        store.packagesChanged.connect(self._timer.start)
        store.fieldChanged.connect(lambda _b: self._timer.start())
        self.refresh()

    # -- Inhalt ---------------------------------------------------------------
    def refresh(self) -> None:
        resolution = self.store.resolution()
        config = self.store.config

        namen = resolution.package_names
        self.zahl.setText(str(len(namen)))
        self.zahl_text.setText("Paket" if len(namen) == 1 else "Pakete")

        self.groesse.setze(
            f"~ {resolution.estimated_size_mb} MB installiert"
            if resolution.estimated_size_mb
            else ""
        )
        # ``kernel_suffix`` ist bereits der vollstaendige Paketname
        # ("linux", "linux-zen") -- ein vorangestelltes "linux" ergab
        # daraus "linuxlinux".
        self.kernel.setze(resolution.kernel_suffix)
        self.datei.setze(f"{config.iso_filename}\nDatentraeger: {config.iso_label}")
        self.repos.setze(", ".join(resolution.repositories) or "core, extra")
        self.gruppen.setze(", ".join(resolution.package_groups))

        dienste = resolution.services_for(EnableIn.LIVE)
        self.dienste.setze(
            ", ".join(sorted(dienst.unit for dienst in dienste))
        )
        self.aur.setze(
            ", ".join(resolution.aur_packages) + "  (wird nicht mitgebaut)"
            if resolution.aur_packages
            else "",
            rolle="warnung",
        )
        self.eigene.setze(", ".join(config.extra_packages))

        blockierend = resolution.blocking_issues
        self.probleme.setze(
            "\n".join(f"- {problem.message}" for problem in blockierend[:4]),
            rolle="fehler",
        )

        # Die vollstaendige Liste steht in der Zusammenfassung; hier reicht ein
        # Ausschnitt, der zeigt, worum es geht.
        gekuerzt = list(namen[:MAX_NAMEN])
        rest = len(namen) - len(gekuerzt)
        text = ", ".join(gekuerzt)
        if rest > 0:
            text += f" und {rest} weitere"
        self.namen.setze(text)
        self.namen.inhalt.setToolTip(self._herkunft_text(resolution))

    @staticmethod
    def _herkunft_text(resolution) -> str:
        """Woher jedes Paket kommt -- die Antwort auf 'warum ist das drin?'."""
        zeilen = [
            f"{paket.name}  <-  {', '.join(paket.origins) or 'unbekannt'}"
            for paket in resolution.packages[:MAX_NAMEN]
        ]
        return "\n".join(zeilen)


class IsoPanelRahmen(QWidget):
    """Panel plus Schalter zum Ein- und Ausklappen."""

    def __init__(self, store, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.panel = IsoPanel(store)
        layout.addWidget(self.panel, 1)

    def setze_sichtbar(self, sichtbar: bool) -> None:
        self.setVisible(sichtbar)


__all__ = ["IsoPanel", "IsoPanelRahmen"]
