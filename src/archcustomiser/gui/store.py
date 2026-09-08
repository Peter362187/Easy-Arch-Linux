"""Zentraler Zustand der Oberflaeche.

Die einzige Stelle, an der ``BuildConfig`` veraendert wird. Alle Seiten lesen
von hier und schreiben hierher; keine Seite haelt eigenen Zustand. Das macht
die Zurueck-Navigation trivial: eine Seite zeichnet sich beim Betreten einfach
neu aus dem Store.

Der Store liegt bewusst in ``gui/`` und nicht in ``core/``: er braucht Qt fuer
die Signale, und ``core`` soll ohne Qt importierbar und testbar bleiben.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from PySide6.QtCore import QObject, Signal

from ..core.catalog import Catalog, SelectionMode
from ..core.config import BuildConfig, SelectionSource
from ..core.resolver import Fix, Issue, Resolution, Resolver
from ..core.secrets import SecretStore

log = logging.getLogger(__name__)


class StoreContext:
    """Auswertungskontext fuer ``visible_when``/``enabled_when``.

    Stand vorher dreimal im Code -- in ``pages/selection.py``,
    ``pages/form.py`` und ``wizard.py`` --, jedes Mal Zeile fuer Zeile gleich
    und jedes Mal unter einem anderen Namen. Er gehoert zum Store, weil er nur
    aus ihm liest.
    """

    __slots__ = ("store",)

    def __init__(self, store: SelectionStore) -> None:
        self.store = store

    def is_selected(self, ref: str) -> bool:
        return self.store.is_selected(ref)

    def has_capability(self, name: str) -> bool:
        return bool(self.store.resolution().capabilities.get(name))

    def field_value(self, binding: str) -> Any:
        return self.store.field(binding)


class SelectionStore(QObject):
    """Haelt die Konfiguration und den daraus abgeleiteten Zustand."""

    selectionChanged = Signal(str)     # category_id
    fieldChanged = Signal(str)         # binding
    resolutionChanged = Signal()
    issuesChanged = Signal()
    packagesChanged = Signal()

    def __init__(
        self,
        catalog: Catalog,
        config: BuildConfig | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self.resolver = Resolver(catalog)
        self.secrets = SecretStore()
        self._config = config or BuildConfig(catalog_version=catalog.catalog_version)
        self._resolution: Resolution | None = None
        self._applying = False        # verhindert Signalschleifen
        # Von der Paketpruefung nachgereicht (set_package_report).
        self._paket_repositories: tuple[str, ...] = ()
        if config is None:
            self._apply_defaults()
        self._recompute()

    # -- Zugriff --------------------------------------------------------------
    @property
    def config(self) -> BuildConfig:
        return self._config

    def resolution(self) -> Resolution:
        if self._resolution is None:
            self._recompute()
        assert self._resolution is not None
        return self._resolution

    def context(self) -> StoreContext:
        """Der Auswertungskontext fuer Katalog-Praedikate."""
        return StoreContext(self)

    def issues(self, category_id: str | None = None) -> tuple[Issue, ...]:
        resolution = self.resolution()
        if category_id is None:
            return resolution.issues
        return resolution.issues_for(category_id)

    def selected(self, category_id: str) -> frozenset[str]:
        return frozenset(
            ref.split(".", 1)[1]
            for ref in self.resolution().effective_refs
            if ref.startswith(f"{category_id}.")
        )

    def is_selected(self, ref: str) -> bool:
        return ref in self.resolution().effective_refs

    def is_auto(self, ref: str) -> bool:
        """Automatisch ergaenzt -- in der Oberflaeche gesperrt dargestellt."""
        return ref in self.resolution().auto_refs

    def source_of(self, ref: str) -> SelectionSource | None:
        return self._config.source_of(ref)

    # -- Auswahl aendern ------------------------------------------------------
    def toggle(self, ref: str, checked: bool, *, source: SelectionSource = SelectionSource.USER) -> None:
        if self._applying:
            return
        category_id, _, option_id = ref.partition(".")
        category = self.catalog.category(category_id)
        if category is None:
            return

        if checked and category.selection_mode in (SelectionMode.SINGLE, SelectionMode.SINGLE_OPTIONAL):
            self._config.set_selection(category_id, (option_id,), source)
        elif checked:
            self._config.add(ref, source)
        else:
            self._config.remove(ref)

        # Empfehlungen gelten fuer beide Auswahlarten. Sie hier und nicht im
        # Resolver zu setzen ist Absicht: der Benutzer soll sie abwaehlen
        # koennen, ohne dass sie beim naechsten Neuberechnen zurueckkehren.
        if checked:
            self._apply_recommendations(ref)

        self._recompute()
        self.selectionChanged.emit(category_id)

    def set_selection(
        self,
        category_id: str,
        option_ids: Iterable[str],
        *,
        source: SelectionSource = SelectionSource.USER,
    ) -> None:
        if self._applying:
            return
        self._config.set_selection(category_id, option_ids, source)
        self._recompute()
        self.selectionChanged.emit(category_id)

    def apply_fix(self, fix: Fix) -> None:
        """Setzt einen Loesungsvorschlag aus einer Fehlermeldung um."""
        for ref in fix.deselect:
            self._config.remove(ref)
        for ref in fix.select:
            self._config.add(ref, SelectionSource.USER)
        self._recompute()
        touched = {ref.split(".", 1)[0] for ref in fix.select + fix.deselect}
        for category_id in touched:
            self.selectionChanged.emit(category_id)

    def _apply_recommendations(self, ref: str) -> None:
        """Weiche Empfehlungen einmalig vorhaken.

        Bewusst nur beim Anklicken und nicht im Resolver: der Benutzer soll sie
        abwaehlen koennen, ohne dass sie sofort wieder erscheinen.
        """
        for candidate in self.resolver.auto_recommendations(self._config, ref):
            self._config.add(candidate, SelectionSource.DEFAULT)

    # -- Formularfelder -------------------------------------------------------
    def field(self, binding: str, default: Any = None) -> Any:
        return self._config.field(binding, default)

    def set_field(self, binding: str, value: Any) -> None:
        if self._applying:
            return
        if self._config.field(binding) == value:
            return
        self._config.set_field(binding, value)
        self._recompute()
        self.fieldChanged.emit(binding)

    def set_secret(self, binding: str, value: str) -> None:
        """Passwoerter gehen ausschliesslich hierher, nie in ``BuildConfig``."""
        self.secrets.set(binding, value)
        self.fieldChanged.emit(binding)

    def has_secret(self, binding: str) -> bool:
        return self.secrets.has(binding)

    # -- Zusatzpakete ---------------------------------------------------------
    def extra_packages(self) -> tuple[str, ...]:
        return tuple(self._config.extra_packages)

    def set_extra_packages(self, names: Iterable[str]) -> None:
        new = list(dict.fromkeys(names))
        if new == self._config.extra_packages:
            return
        self._config.extra_packages = new
        self._recompute()
        self.packagesChanged.emit()

    def set_extra_repositories(self, names: Iterable[str]) -> None:
        """Welche Repositories die Freitextpakete brauchen.

        Wird von der Freitextseite nach jeder Pruefung gesetzt: nur dort ist
        bekannt, aus welchem Repository ein eingetippter Name stammt.
        """
        new = list(dict.fromkeys(names))
        if new == self._config.extra_repositories:
            return
        self._config.extra_repositories = new
        self._recompute()
        self.packagesChanged.emit()

    def set_provider_choice(self, virtual: str, provider: str) -> None:
        self._config.provider_choices[virtual] = provider
        self._recompute()
        self.packagesChanged.emit()

    # -- Konfiguration ersetzen ----------------------------------------------
    def replace_config(self, config: BuildConfig) -> None:
        """Beim Laden eines Profils.

        Fehlende **Felder** bekommen die Vorgabe aus dem Katalog. Ohne das
        klafften Anzeige und Ergebnis auseinander: ein Profil ohne
        ``user.*`` -- etwa das mitgelieferte ``minimal.yaml`` -- zeigte im
        Formular "Benutzerkonto anlegen" und den Namen "arch", weil die Seite
        ``spec.default`` anzeigt. ``BuildConfig`` kannte den Wert aber nicht,
        also legte der Generator kein Konto an und sperrte zugleich root: eine
        Live-ISO, an der sich niemand anmelden kann.

        Fehlende **Auswahlen** bekommen bewusst *keine* Vorgabe: die steuert
        das Profil, und ein leeres Feld dort heisst "nicht gewaehlt".
        """
        self._applying = True
        try:
            self._config = config
            self._config.catalog_version = self.catalog.catalog_version
            self.secrets.clear()
            self._fill_missing_fields()
        finally:
            self._applying = False
        self._recompute()
        for category in self.catalog.categories:
            self.selectionChanged.emit(category.id)
        self.packagesChanged.emit()

    def reset(self) -> None:
        self._config = BuildConfig(catalog_version=self.catalog.catalog_version)
        self.secrets.clear()
        self._apply_defaults()
        self._recompute()
        for category in self.catalog.categories:
            self.selectionChanged.emit(category.id)

    # -- intern ---------------------------------------------------------------
    def _fill_missing_fields(self) -> None:
        """Katalog-Vorgaben fuer Felder, die das Profil nicht nennt."""
        for category in self.catalog.categories:
            for spec in category.fields:
                if spec.default is None or spec.secret:
                    continue
                if self._config.field(spec.binding) is None:
                    self._config.set_field(spec.binding, spec.default)

    def _apply_defaults(self) -> None:
        for category in self.catalog.categories:
            if category.default_selection:
                self._config.set_selection(
                    category.id, category.default_selection, SelectionSource.DEFAULT
                )
            for spec in category.fields:
                if spec.default is not None and not spec.secret:
                    self._config.set_field(spec.binding, spec.default)

    def set_package_report(self, report) -> None:
        """Uebernimmt, was die Paketpruefung ueber die Repositorien weiss.

        Die Aufloesung kennt nur die Repositorien, die der Katalog an seinen
        Optionen nennt. Ein frei eingegebenes multilib-Paket blieb damit ohne
        aktiviertes Repository in der erzeugten pacman.conf -- der Bau brach
        erst nach Minuten mit "target not found" ab.
        """
        neue = tuple(report.required_repositories()) if report is not None else ()
        if neue == self._paket_repositories:
            return
        self._paket_repositories = neue
        self._recompute()

    def _recompute(self) -> None:
        previous = self._resolution
        self._resolution = self.resolver.resolve(self._config).mit_repositories(
            self._paket_repositories
        )
        if previous is None or previous.issues != self._resolution.issues:
            self.issuesChanged.emit()
        self.resolutionChanged.emit()
