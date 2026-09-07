"""Auswahlseite -- rendert eine Katalogkategorie mit Optionen.

Diese eine Klasse erzeugt alle Auswahlschritte des Wizards: Desktop, Window
Manager, Kernel, Netzwerk, Audio, Programme, Treiber und Dienste. Der
Unterschied zwischen ihnen steht vollstaendig im YAML.

Ein Detail, das leicht falsch gemacht wird: Bei Einfachauswahl gilt die
Exklusivitaet fuer die **ganze Kategorie**, nicht je Gruppenkasten. Deshalb
liegen alle Auswahlknoepfe in *einer* ``QButtonGroup``, obwohl sie optisch auf
mehrere Kaesten verteilt sind -- sonst koennte man je Gruppe einen Desktop
auswaehlen.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QGridLayout,
    QGroupBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core.catalog import Category, Option, SelectionMode
from ...core.config import SelectionSource
from .. import theme
from ..store import SelectionStore
from ..widgets.common import SearchField
from ..widgets.option_widget import OptionWidget
from .base import CatalogPageBase

log = logging.getLogger(__name__)

# Ab so vielen Eintraegen lohnt ein Suchfeld.
SEARCH_THRESHOLD = 8


class CatalogSelectionPage(CatalogPageBase):
    def __init__(self, category: Category, store: SelectionStore) -> None:
        super().__init__(category, store)
        self._widgets: dict[str, OptionWidget] = {}
        self._button_group: QButtonGroup | None = None
        self._build_ui()
        self.add_help_link()
        self.store.selectionChanged.connect(self._on_selection_changed)
        self.store.resolutionChanged.connect(self.sync_from_store)

    # -- Aufbau ---------------------------------------------------------------
    def _build_ui(self) -> None:
        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.SPACE_MD)

        exclusive = self.category.selection_mode is not SelectionMode.MULTI
        if exclusive:
            self._button_group = QButtonGroup(self)
            self._button_group.setExclusive(True)

        self._boxes: list[QGroupBox] = []
        for group_id, options in self._grouped_options():
            target = outer
            box: QGroupBox | None = None
            if group_id is not None:
                box = QGroupBox(group_id.label)
                outer.addWidget(box)
                self._boxes.append(box)
                target = QVBoxLayout(box)
                target.setSpacing(theme.SPACE_SM)

            grid = QGridLayout()
            grid.setSpacing(theme.SPACE_SM)
            columns = max(1, self.category.columns)
            for position, option in enumerate(options):
                widget = self._make_widget(option)
                grid.addWidget(widget, position // columns, position % columns)
            if isinstance(target, QVBoxLayout):
                target.addLayout(grid)
            else:
                outer.addLayout(grid)

        outer.addStretch(1)

        # Ein Suchfeld erst, wenn es sich lohnt. Bei vier Kerneln waere es nur
        # zusaetzliches Beiwerk; bei den vierundzwanzig Programmen in sechs
        # Gruppen ist Scrollen und Lesen die einzige Alternative.
        self.search: SearchField | None = None
        if len(self.category.options) >= SEARCH_THRESHOLD:
            self.search = SearchField(
                f"{len(self.category.options)} Eintraege durchsuchen ..."
            )
            self.search.textChanged.connect(self._apply_filter)
            self._root.addWidget(self.search)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(container)
        # Waagerecht darf nie gescrollt werden -- lange Beschreibungen brechen um.
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._root.addWidget(scroll, 1)

    def _apply_filter(self, needle: str) -> None:
        """Blendet aus, was nicht passt -- samt leer gewordener Gruppen."""
        begriff = needle.strip().lower()
        sichtbar = 0
        for option_id, widget in self._widgets.items():
            passt = not begriff or self._matches(widget.option, begriff)
            widget.setProperty("filteredOut", not passt)
            widget.setVisible(passt and self._allowed_by_catalog(option_id))
            if widget.isVisibleTo(self):
                sichtbar += 1

        for box in self._boxes:
            # Ein Gruppenkasten ohne sichtbare Eintraege ist nur noch ein
            # leerer Rahmen.
            box.setVisible(
                any(
                    kind.isVisibleTo(box)
                    for kind in box.findChildren(OptionWidget)
                )
            )
        if self.search is not None:
            self.search.set_result_count(sichtbar, len(self._widgets))

    @staticmethod
    def _matches(option: Option, begriff: str) -> bool:
        """Sucht in Beschriftung, Beschreibung UND Paketnamen.

        Der Paketname ist oft das, was der Benutzer im Kopf hat -- wer "steam"
        sucht, denkt nicht an "Spieleplattform".
        """
        felder = [option.label, option.description, option.id, *option.packages]
        return any(begriff in str(feld).lower() for feld in felder if feld)

    def _allowed_by_catalog(self, option_id: str) -> bool:
        """Ob die Option unabhaengig vom Filter ueberhaupt gezeigt wuerde."""
        widget = self._widgets[option_id]
        context = self.store.context()
        ref = f"{self.category.id}.{option_id}"
        if self.store.is_auto(ref) or self.store.is_selected(ref):
            return True
        return widget.option.visible_when.evaluate(context)

    def _grouped_options(self):
        """Optionen nach Gruppen, jeweils nach ``order`` sortiert."""
        if not self.category.groups:
            return [(None, sorted(self.category.options, key=lambda o: (o.order, o.label)))]

        result = []
        for group in sorted(self.category.groups, key=lambda g: g.order):
            members = [option for option in self.category.options if option.group == group.id]
            if members:
                result.append((group, sorted(members, key=lambda o: (o.order, o.label))))
        ungrouped = [
            option
            for option in self.category.options
            if not option.group or option.group not in {g.id for g in self.category.groups}
        ]
        if ungrouped:
            result.append((None, sorted(ungrouped, key=lambda o: (o.order, o.label))))
        return result

    def _make_widget(self, option: Option) -> OptionWidget:
        widget = OptionWidget(option, self.category.selection_mode)
        widget.toggled.connect(self._on_option_toggled)
        if self._button_group is not None:
            self._button_group.addButton(widget.button)
        self._widgets[option.id] = widget
        return widget

    # -- Store-Anbindung ------------------------------------------------------
    def _on_option_toggled(self, option_id: str, checked: bool) -> None:
        ref = f"{self.category.id}.{option_id}"
        self.store.toggle(ref, checked, source=SelectionSource.USER)

    def _on_selection_changed(self, category_id: str) -> None:
        # Auch fremde Kategorien koennen diese Seite betreffen: eine
        # Desktop-Auswahl zieht Basiskomponenten mit und kann Optionen hier
        # verfuegbar oder unverfuegbar machen.
        self.sync_from_store()

    def sync_from_store(self) -> None:
        selected = self.store.selected(self.category.id)
        context = self.store.context()

        for option_id, widget in self._widgets.items():
            ref = f"{self.category.id}.{option_id}"
            widget.set_checked(option_id in selected)

            auto = self.store.is_auto(ref)
            widget.set_auto(auto, self._auto_reason(ref) if auto else "")

            option = widget.option
            if not auto:
                visible = option.visible_when.evaluate(context)
                enabled = visible and option.enabled_when.evaluate(context)
                # Der Suchbegriff entscheidet mit: sonst holt ein Neuzeichnen
                # aus dem Store gerade weggefilterte Karten zurueck.
                widget.setVisible(
                    (visible or option_id in selected) and self._matches_search(option)
                )
                widget.set_availability(
                    enabled,
                    "" if enabled else "Diese Option setzt eine andere Auswahl voraus.",
                )
        self._sync_group_visibility()
        self.completeChanged.emit()

    def _matches_search(self, option: Option) -> bool:
        if self.search is None:
            return True
        begriff = self.search.text().lower()
        return not begriff or self._matches(option, begriff)

    def _sync_group_visibility(self) -> None:
        for box in getattr(self, "_boxes", []):
            box.setVisible(
                any(kind.isVisibleTo(box) for kind in box.findChildren(OptionWidget))
            )

    def _auto_reason(self, ref: str) -> str:
        """Wer hat diese Option mitgezogen?"""
        causes = [
            other.label
            for other_ref in sorted(self.store.resolution().effective_refs)
            if (other := self.store.catalog.option(other_ref)) is not None
            and ref in other.implies
            and other_ref not in self.store.resolution().auto_refs
        ]
        if causes:
            return f"Automatisch ergaenzt, weil {', '.join(causes)} das benoetigt."
        return "Automatisch ergaenzt, weil eine andere Auswahl das benoetigt."
