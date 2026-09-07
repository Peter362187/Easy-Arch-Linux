"""Seite fuer frei eingegebene Zusatzpakete.

Waehrend des Tippens wird gegen den geladenen Index geprueft. Jede Zeile
bekommt sofort ein Ergebnis: gefunden, Gruppe, virtuelles Paket, Tippfehler
mit Vorschlaegen -- oder "nicht pruefbar", wenn keine Paketdaten vorliegen.

Der letzte Fall ist der wichtige: solange kein vollstaendiger Index da ist,
wird nichts als "existiert nicht" gemeldet. Andernfalls wuerde ein Netzausfall
den Benutzer dazu bringen, einen korrekten Paketnamen zu loeschen.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from ...core.catalog import Category
from ...core.resolver import Issue
from ...core.packages import EntryKind, parse_list
from .. import theme
from ..packages_worker import PackageController
from ..store import SelectionStore
from ..widgets.common import brush
from .base import CatalogPageBase

log = logging.getLogger(__name__)

TYPING_DELAY_MS = 350

def _colour(kind: EntryKind) -> str:
    if kind in (EntryKind.PACKAGE,):
        return theme.success()
    if kind in (EntryKind.GROUP, EntryKind.PROVIDES_UNIQUE):
        return theme.accent()
    if kind in (EntryKind.PROVIDES_AMBIG, EntryKind.AUR):
        return theme.warning()
    if kind in (EntryKind.NOT_FOUND, EntryKind.INVALID_NAME):
        return theme.danger()
    return theme.muted()

_LABELS = {
    EntryKind.PACKAGE: "Paket",
    EntryKind.GROUP: "Gruppe",
    EntryKind.PROVIDES_UNIQUE: "virtuell",
    EntryKind.PROVIDES_AMBIG: "mehrdeutig",
    EntryKind.AUR: "AUR",
    EntryKind.NOT_FOUND: "unbekannt",
    EntryKind.INVALID_NAME: "ungueltig",
    EntryKind.UNVERIFIED: "ungeprueft",
}


class FreePackagesPage(CatalogPageBase):
    def __init__(
        self,
        category: Category,
        store: SelectionStore,
        controller: PackageController,
    ) -> None:
        super().__init__(category, store)
        self.controller = controller
        self._blocking = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(TYPING_DELAY_MS)
        self._timer.timeout.connect(self._revalidate)

        self._build_ui()
        self.controller.ready.connect(lambda _ok: self._revalidate())
        self.controller.statusChanged.connect(self.status.setText)
        self.add_help_link()

    def _build_ui(self) -> None:
        hint = QLabel(
            "Ein Paket je Zeile oder durch Komma getrennt. Paketgruppen "
            "(z.B. <code>plasma</code>) sind ebenfalls erlaubt."
        )
        hint.setWordWrap(True)
        self._root.addWidget(hint)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("neovim\nhtop\nwget")
        self.editor.setMinimumHeight(110)
        self.editor.setMaximumHeight(240)
        self.editor.textChanged.connect(self._timer.start)
        self._root.addWidget(self.editor)

        self.results = QTreeWidget()
        self.results.setColumnCount(3)
        self.results.setHeaderLabels(["Eingabe", "Art", "Ergebnis"])
        self.results.setRootIsDecorated(False)
        self.results.setAlternatingRowColors(True)
        header = self.results.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._root.addWidget(self.results, 1)

        footer = QHBoxLayout()
        self.status = QLabel(self.controller.status_text())
        self.status.setFont(theme.small_font())
        self.status.setStyleSheet(f"color: {theme.muted()};")
        footer.addWidget(self.status, 1)

        self.refresh_button = QPushButton("Paketdaten aktualisieren")
        self.refresh_button.clicked.connect(self._refresh)
        footer.addWidget(self.refresh_button)
        self._root.addLayout(footer)

    # -- Ereignisse -----------------------------------------------------------
    def _refresh(self) -> None:
        from ...core.packages import RefreshPolicy

        self.refresh_button.setEnabled(False)
        self.controller.ready.connect(self._on_refreshed)
        self.controller.start(RefreshPolicy.FORCE)

    def _on_refreshed(self, _ok: bool) -> None:
        self.refresh_button.setEnabled(True)
        try:
            self.controller.ready.disconnect(self._on_refreshed)
        except (RuntimeError, TypeError):
            # Schon getrennt oder nie verbunden -- kein Grund zur Sorge, aber
            # auch kein Grund, gar nichts zu sagen.
            log.debug("Signal war bereits getrennt", exc_info=True)

    def sync_from_store(self) -> None:
        current = "\n".join(self.store.extra_packages())
        if current != self.editor.toPlainText():
            blocked = self.editor.blockSignals(True)
            try:
                self.editor.setPlainText(current)
            finally:
                self.editor.blockSignals(blocked)
        self.status.setText(self.controller.status_text())
        self._revalidate()

    def _revalidate(self) -> None:
        names = parse_list(self.editor.toPlainText())
        self.store.set_extra_packages(names)

        self.results.clear()
        self._blocking = 0
        if not names:
            self.completeChanged.emit()
            return

        report = self.controller.validate(
            names, provider_choices=self.store.config.provider_choices
        )
        # Ein frei eingegebenes multilib-Paket braucht das Repository in der
        # erzeugten pacman.conf -- der Katalog weiss davon nichts.
        self.store.set_package_report(report)
        for entry in report.entries:
            # Bei mehrdeutigen Eintraegen steht in der Ergebnisspalte eine
            # Auswahlbox. Zusaetzlicher Text wuerde darunter durchscheinen.
            ambiguous = entry.kind is EntryKind.PROVIDES_AMBIG
            item = QTreeWidgetItem(
                [
                    entry.query,
                    _LABELS.get(entry.kind, "?"),
                    "" if ambiguous else entry.message,
                ]
            )
            farbe = brush(_colour(entry.kind))
            item.setForeground(1, farbe)
            item.setForeground(2, farbe)
            item.setToolTip(1, entry.message)
            item.setToolTip(2, "\n".join(entry.notes) if entry.notes else entry.message)
            for column in range(3):
                font = item.font(column)
                font.setBold(entry.kind.is_blocking)
                item.setFont(column, font)
            item.setData(1, Qt.ItemDataRole.UserRole, entry.kind.name)
            self.results.addTopLevelItem(item)

            if ambiguous:
                self._add_provider_picker(item, entry)

            if entry.kind.is_blocking:
                self._blocking += 1

        self._publish_package_errors(report)
        self.completeChanged.emit()

    def _publish_package_errors(self, report) -> None:
        """Blockierende Paketfehler nach oben geben.

        Vorher zaehlte ein blockierender Eintrag nur ``self._blocking`` hoch und
        faerbte eine Baumzeile. Der Weiter-Knopf war grau, die Hinweisleiste
        oben blieb leer -- und wer nicht genau hinsah, suchte den Grund
        vergebens.
        """
        schlimme = [e for e in report.entries if e.kind.is_blocking]
        if not schlimme:
            self.set_local_issues(())
            return
        namen = ", ".join(e.query for e in schlimme[:5])
        if len(schlimme) > 5:
            namen += f" und {len(schlimme) - 5} weitere"
        self.set_local_issues(
            (
                Issue(
                    severity="error",
                    code="package_unknown",
                    category_id=self.category.id,
                    message=(
                        f"In den Arch-Repositories nicht gefunden: {namen}. "
                        f"Einzelheiten stehen in der Tabelle darunter."
                    ),
                ),
            )
        )

    def _add_provider_picker(self, item: QTreeWidgetItem, entry) -> None:
        """Bei mehreren Anbietern muss vorab entschieden werden.

        pacman wuerde interaktiv fragen; mkarchiso laeuft ohne Rueckfrage und
        wuerde an dieser Stelle abbrechen.
        """
        combo = QComboBox()
        combo.setToolTip(entry.message)
        combo.addItem(f"{len(entry.members)} Anbieter -- bitte einen waehlen", "")
        for provider in entry.members:
            combo.addItem(provider, provider)
        combo.currentIndexChanged.connect(
            lambda _index, c=combo, virtual=entry.normalized: self._choose_provider(virtual, c)
        )
        self.results.setItemWidget(item, 2, combo)

    def _choose_provider(self, virtual: str, combo: QComboBox) -> None:
        provider = combo.currentData()
        if provider:
            self.store.set_provider_choice(virtual, str(provider))
            self._revalidate()

    def isComplete(self) -> bool:
        return self._blocking == 0 and super().isComplete()

