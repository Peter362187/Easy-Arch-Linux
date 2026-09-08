"""Auswahlseite -- rendert eine Katalogkategorie als Kartenraster.

Diese eine Klasse erzeugt alle Auswahlschritte: Desktop, Window Manager,
Kernel, Netzwerk, Audio, Programme, Treiber, Dienste. Der Unterschied zwischen
ihnen steht vollstaendig im YAML.

Ein Detail, das leicht falsch gemacht wird: Bei Einfachauswahl gilt die
Exklusivitaet fuer die **ganze Kategorie**, nicht je Gruppenkasten. Frueher
sorgte dafuer eine gemeinsame ``QButtonGroup``. Die Karten zeichnen sich jetzt
selbst und kennen keine Knoepfe mehr -- die Regel steht dort, wo sie ohnehin
schon galt: im Store, der bei Einfachauswahl ``set_selection`` benutzt.

Neu sind Filter neben der Suche: **Empfohlen** und **Ausgewaehlt**. Bei
vierundzwanzig Programmen in sechs Gruppen ist "zeig mir, was ich schon habe"
die haeufigste Frage.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core.catalog import Category, Option, SelectionMode
from ...core.config import SelectionSource
from ..design import tokens
from ..design.typo import CAPTION, SUBTITLE, schrift
from ..store import SelectionStore
from ..widgets.cards import OptionCard
from ..widgets.search import SearchField
from .base import PageBase

log = logging.getLogger(__name__)

# Ab so vielen Eintraegen lohnt ein Suchfeld.
SEARCH_THRESHOLD = 8

FILTER_EMPFOHLEN = "empfohlen"
FILTER_GEWAEHLT = "gewaehlt"


class _Gruppe(QWidget):
    """Ein Ueberschriftsblock mit seinem Kartenraster."""

    def __init__(self, titel: str, spalten: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        werte = tokens()
        aussen = QVBoxLayout(self)
        aussen.setContentsMargins(0, 0, 0, 0)
        aussen.setSpacing(werte.space.sm)

        self.kopf: QLabel | None = None
        if titel:
            self.kopf = QLabel(titel)
            self.kopf.setFont(schrift(SUBTITLE, fett=True))
            aussen.addWidget(self.kopf)

        self.raster = QGridLayout()
        self.raster.setSpacing(werte.space.sm)
        self.raster.setContentsMargins(0, 0, 0, 0)
        aussen.addLayout(self.raster)
        self.spalten = max(1, spalten)
        self._karten: list[OptionCard] = []

    def hinzu(self, karte: OptionCard) -> None:
        position = len(self._karten)
        self.raster.addWidget(karte, position // self.spalten, position % self.spalten)
        self._karten.append(karte)

    def neu_anordnen(self) -> None:
        """Sichtbare Karten luecklos setzen.

        Ohne das hinterlaesst ein Filter Loecher im Raster: die Karten behalten
        ihre Zellen, und zwischen zwei Treffern klafft eine leere Spalte.
        """
        sichtbar = [karte for karte in self._karten if not karte.isHidden()]
        for karte in self._karten:
            self.raster.removeWidget(karte)
        for position, karte in enumerate(sichtbar):
            self.raster.addWidget(
                karte, position // self.spalten, position % self.spalten
            )
        self.setVisible(bool(sichtbar))


class CatalogSelectionPage(PageBase):
    """Eine Kategorie als Kartenraster mit Suche und Filtern."""

    def __init__(self, category: Category, store: SelectionStore) -> None:
        super().__init__(category, store)
        self._karten: dict[str, OptionCard] = {}
        self._gruppen: list[_Gruppe] = []
        self.search: SearchField | None = None
        self._aufbauen()
        self.add_help_link()
        self.store.selectionChanged.connect(lambda _c: self.sync_from_store())
        self.store.resolutionChanged.connect(self.sync_from_store)

    # -- Aufbau ---------------------------------------------------------------
    def _aufbauen(self) -> None:
        werte = tokens()

        # Ein Suchfeld erst, wenn es sich lohnt. Bei vier Kerneln waere es nur
        # zusaetzliches Beiwerk.
        if len(self.category.options) >= SEARCH_THRESHOLD:
            self.search = SearchField(
                f"{len(self.category.options)} Eintraege durchsuchen ..."
            )
            self.search.textChanged.connect(lambda _t: self._filtern())
            self.search.filterChanged.connect(lambda _k, _a: self._filtern())
            if any(option.recommended for option in self.category.options):
                self.search.filter_hinzufuegen(FILTER_EMPFOHLEN, "Empfohlen")
            self.search.filter_hinzufuegen(FILTER_GEWAEHLT, "Ausgewaehlt")
            self._root.addWidget(self.search)

        behaelter = QWidget()
        aussen = QVBoxLayout(behaelter)
        aussen.setContentsMargins(0, 0, werte.space.sm, 0)
        aussen.setSpacing(werte.space.lg)

        for gruppe, optionen in self._gruppiert():
            block = _Gruppe(gruppe.label if gruppe is not None else "", self.category.columns)
            for option in optionen:
                karte = OptionCard(option, self.category.selection_mode)
                karte.toggled.connect(self._umgeschaltet)
                self._karten[option.id] = karte
                block.hinzu(karte)
            aussen.addWidget(block)
            self._gruppen.append(block)

        aussen.addStretch(1)

        rolle = QScrollArea()
        rolle.setWidgetResizable(True)
        rolle.setFrameShape(QScrollArea.Shape.NoFrame)
        # Waagerecht nie scrollen -- lange Beschreibungen brechen um.
        rolle.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rolle.setWidget(behaelter)
        self._root.addWidget(rolle, 1)

        if not self.category.options:
            leer = QLabel("Diese Kategorie enthaelt derzeit keine Optionen.")
            leer.setFont(schrift(CAPTION))
            leer.setProperty("rolle", "gedaempft")
            self._root.addWidget(leer)

    def _gruppiert(self):
        """Optionen nach Gruppen, jeweils nach ``order`` sortiert."""
        if not self.category.groups:
            return [
                (None, sorted(self.category.options, key=lambda o: (o.order, o.label)))
            ]

        ergebnis = []
        bekannt = {gruppe.id for gruppe in self.category.groups}
        for gruppe in sorted(self.category.groups, key=lambda g: g.order):
            mitglieder = [
                option for option in self.category.options if option.group == gruppe.id
            ]
            if mitglieder:
                ergebnis.append(
                    (gruppe, sorted(mitglieder, key=lambda o: (o.order, o.label)))
                )
        ohne = [
            option
            for option in self.category.options
            if not option.group or option.group not in bekannt
        ]
        if ohne:
            ergebnis.append((None, sorted(ohne, key=lambda o: (o.order, o.label))))
        return ergebnis

    # -- Filter ---------------------------------------------------------------
    def _filtern(self) -> None:
        """Blendet aus, was nicht passt -- samt leer gewordener Gruppen."""
        begriff = self.search.text().lower() if self.search is not None else ""
        filter_ = self.search.aktive_filter() if self.search is not None else set()
        gewaehlt = self.store.selected(self.category.id)
        kontext = self.store.context()

        sichtbar = 0
        for option_id, karte in self._karten.items():
            option = karte.option
            passt = (not begriff or _trifft(option, begriff)) and self._filter_passt(
                option, option_id, filter_, gewaehlt
            )
            karte.setVisible(passt and self._vom_katalog_erlaubt(option, option_id, kontext))
            if not karte.isHidden():
                sichtbar += 1

        for gruppe in self._gruppen:
            gruppe.neu_anordnen()
        if self.search is not None:
            self.search.setze_trefferzahl(sichtbar, len(self._karten))

    def _filter_passt(
        self, option: Option, option_id: str, filter_: set[str], gewaehlt: frozenset[str]
    ) -> bool:
        if FILTER_EMPFOHLEN in filter_ and not option.recommended:
            return False
        if FILTER_GEWAEHLT in filter_ and option_id not in gewaehlt:
            return False
        return True

    def _vom_katalog_erlaubt(self, option: Option, option_id: str, kontext) -> bool:
        """Ob die Option unabhaengig vom Filter ueberhaupt gezeigt wuerde."""
        ref = f"{self.category.id}.{option_id}"
        if self.store.is_auto(ref) or self.store.is_selected(ref):
            return True
        return option.visible_when.evaluate(kontext)

    def fokus_auf_suche(self) -> bool:
        if self.search is None:
            return False
        self.search.edit.setFocus()
        self.search.edit.selectAll()
        return True

    # -- Store-Anbindung ------------------------------------------------------
    def _umgeschaltet(self, option_id: str, checked: bool) -> None:
        ref = f"{self.category.id}.{option_id}"
        self.store.toggle(ref, checked, source=SelectionSource.USER)

    def sync_from_store(self) -> None:
        gewaehlt = self.store.selected(self.category.id)
        kontext = self.store.context()

        for option_id, karte in self._karten.items():
            ref = f"{self.category.id}.{option_id}"
            karte.set_checked(option_id in gewaehlt)

            auto = self.store.is_auto(ref)
            karte.set_auto(auto, self._auto_grund(ref) if auto else "")
            if auto:
                continue

            option = karte.option
            erlaubt = option.enabled_when.evaluate(kontext)
            karte.set_availability(
                erlaubt,
                "" if erlaubt else "Diese Option setzt eine andere Auswahl voraus.",
            )

        self._filtern()
        self.completeChanged.emit()

    def _auto_grund(self, ref: str) -> str:
        """Wer hat diese Option mitgezogen?"""
        resolution = self.store.resolution()
        ursachen = [
            andere.label
            for anderer_ref in sorted(resolution.effective_refs)
            if (andere := self.store.catalog.option(anderer_ref)) is not None
            and ref in andere.implies
            and anderer_ref not in resolution.auto_refs
        ]
        if ursachen:
            return f"Automatisch ergaenzt, weil {', '.join(ursachen)} das benoetigt."
        return "Automatisch ergaenzt, weil eine andere Auswahl das benoetigt."

    def is_complete(self) -> bool:
        if self.category.selection_mode is SelectionMode.SINGLE and self.category.required:
            if not self.store.selected(self.category.id):
                return False
        return super().is_complete()


def _trifft(option: Option, begriff: str) -> bool:
    """Sucht in Beschriftung, Beschreibung UND Paketnamen.

    Der Paketname ist oft das, was der Benutzer im Kopf hat -- wer "steam"
    sucht, denkt nicht an "Spieleplattform".
    """
    felder = [option.label, option.description, option.id, *option.packages]
    return any(begriff in str(feld).lower() for feld in felder if feld)


__all__ = ["CatalogSelectionPage"]
