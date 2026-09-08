"""Seite fuer frei eingegebene Zusatzpakete.

Waehrend des Tippens wird gegen den geladenen Index geprueft. Jede Zeile
bekommt sofort ein Ergebnis: gefunden, Gruppe, virtuelles Paket, Tippfehler mit
Vorschlaegen -- oder "nicht pruefbar", wenn keine Paketdaten vorliegen.

Der letzte Fall ist der wichtige: solange kein vollstaendiger Index da ist,
wird nichts als "existiert nicht" gemeldet. Andernfalls wuerde ein Netzausfall
den Benutzer dazu bringen, einen korrekten Paketnamen zu loeschen.

Solange die Paketdaten laden, steht statt einer leeren Tabelle ein
Platzhaltermuster -- die Tabelle sah vorher aus wie ein Ergebnis mit null
Treffern.
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
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
)

from ...core.catalog import Category
from ...core.packages import EntryKind, parse_list
from ...core.resolver import Issue
from ..design import tokens
from ..design.typo import CAPTION, schrift
from ..packages_worker import PackageController
from ..store import SelectionStore
from ..widgets.common import brush
from ..widgets.skeleton import SkeletonRows
from .base import PageBase

log = logging.getLogger(__name__)

TYPING_DELAY_MS = 350

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


def _farbe(kind: EntryKind) -> str:
    p = tokens().palette
    if kind is EntryKind.PACKAGE:
        return p.success
    if kind in (EntryKind.GROUP, EntryKind.PROVIDES_UNIQUE):
        return p.accent
    if kind in (EntryKind.PROVIDES_AMBIG, EntryKind.AUR):
        return p.warning
    if kind in (EntryKind.NOT_FOUND, EntryKind.INVALID_NAME):
        return p.danger
    return p.text_muted


class FreePackagesPage(PageBase):
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
        self._timer.timeout.connect(self._pruefen)

        self._aufbauen()
        self.controller.ready.connect(self._geladen)
        # Einmal verbunden statt bei jedem Klick: sonst sammelten sich
        # Mehrfachverbindungen an. Und an BEIDE Ausgaenge -- der Controller
        # sendet bei einem Fehler nur 'failed', nie 'ready', und der Knopf
        # blieb dauerhaft gesperrt.
        self.controller.failed.connect(self._fehlgeschlagen)
        self.controller.statusChanged.connect(self.status.setText)
        self.add_help_link()

    def _aufbauen(self) -> None:
        werte = tokens()
        hinweis = QLabel(
            "Ein Paket je Zeile oder durch Komma getrennt. Paketgruppen "
            "(z.B. <code>plasma</code>) sind ebenfalls erlaubt."
        )
        hinweis.setWordWrap(True)
        self._root.addWidget(hinweis)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("neovim\nhtop\nwget")
        self.editor.setMinimumHeight(110)
        self.editor.setMaximumHeight(240)
        self.editor.setAccessibleName("Zusaetzliche Pakete")
        self.editor.textChanged.connect(self._timer.start)
        self._root.addWidget(self.editor)

        self.results = QTreeWidget()
        self.results.setColumnCount(3)
        self.results.setHeaderLabels(["Eingabe", "Art", "Ergebnis"])
        self.results.setRootIsDecorated(False)
        self.results.setAlternatingRowColors(True)
        kopf = self.results.header()
        kopf.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        kopf.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        kopf.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)

        self.skelett = SkeletonRows(6)
        self._stapel = QStackedWidget()
        self._stapel.addWidget(self.results)
        self._stapel.addWidget(self.skelett)
        self._root.addWidget(self._stapel, 1)

        fuss = QHBoxLayout()
        fuss.setSpacing(werte.space.sm)
        self.status = QLabel(self.controller.status_text())
        self.status.setFont(schrift(CAPTION))
        self.status.setProperty("rolle", "gedaempft")
        self.status.setWordWrap(True)
        fuss.addWidget(self.status, 1)

        self.refresh_button = QPushButton("Paketdaten aktualisieren")
        self.refresh_button.setProperty("variant", "ghost")
        self.refresh_button.clicked.connect(self._aktualisieren)
        fuss.addWidget(self.refresh_button)
        self._root.addLayout(fuss)

    # -- Ereignisse -----------------------------------------------------------
    def _aktualisieren(self) -> None:
        from ...core.packages import RefreshPolicy

        if self.controller.loading:
            # start() kehrt sonst wirkungslos zurueck, der Knopf wuerde aber
            # trotzdem gesperrt und wieder freigegeben -- ohne dass ein
            # erzwungenes Neuladen stattgefunden haette.
            return
        self.refresh_button.setEnabled(False)
        self._stapel.setCurrentWidget(self.skelett)
        self.controller.start(RefreshPolicy.FORCE)

    def _geladen(self, _ok: bool) -> None:
        self.refresh_button.setEnabled(True)
        self._stapel.setCurrentWidget(self.results)
        self._pruefen()

    def _fehlgeschlagen(self, meldung: str) -> None:
        """Auch ein Fehlschlag gibt den Knopf wieder frei."""
        log.warning("Paketdaten nicht ladbar: %s", meldung)
        self.refresh_button.setEnabled(True)
        self._stapel.setCurrentWidget(self.results)

    def sync_from_store(self) -> None:
        aktuell = "\n".join(self.store.extra_packages())
        if aktuell != self.editor.toPlainText():
            blockiert = self.editor.blockSignals(True)
            try:
                self.editor.setPlainText(aktuell)
            finally:
                self.editor.blockSignals(blockiert)
        self.status.setText(self.controller.status_text())
        if self.controller.loading:
            self._stapel.setCurrentWidget(self.skelett)
        self._pruefen()

    def _pruefen(self) -> None:
        namen = parse_list(self.editor.toPlainText())
        self.store.set_extra_packages(namen)

        self.results.clear()
        self._blocking = 0
        if not namen:
            self.set_local_issues(())
            self.completeChanged.emit()
            return

        report = self.controller.validate(
            namen, provider_choices=self.store.config.provider_choices
        )
        # Ein frei eingegebenes multilib-Paket braucht das Repository in der
        # erzeugten pacman.conf -- der Katalog weiss davon nichts.
        self.store.set_package_report(report)
        for eintrag in report.entries:
            # Bei mehrdeutigen Eintraegen steht in der Ergebnisspalte eine
            # Auswahlbox. Zusaetzlicher Text wuerde darunter durchscheinen.
            mehrdeutig = eintrag.kind is EntryKind.PROVIDES_AMBIG
            zeile = QTreeWidgetItem(
                [
                    eintrag.query,
                    _LABELS.get(eintrag.kind, "?"),
                    "" if mehrdeutig else eintrag.message,
                ]
            )
            farbe = brush(_farbe(eintrag.kind))
            zeile.setForeground(1, farbe)
            zeile.setForeground(2, farbe)
            zeile.setToolTip(1, eintrag.message)
            zeile.setToolTip(
                2, "\n".join(eintrag.notes) if eintrag.notes else eintrag.message
            )
            for spalte in range(3):
                font = zeile.font(spalte)
                font.setBold(eintrag.kind.is_blocking)
                zeile.setFont(spalte, font)
            zeile.setData(1, Qt.ItemDataRole.UserRole, eintrag.kind.name)
            self.results.addTopLevelItem(zeile)

            if mehrdeutig:
                self._anbieterwahl(zeile, eintrag)

            if eintrag.kind.is_blocking:
                self._blocking += 1

        self._paketfehler_melden(report)
        self.completeChanged.emit()

    def _paketfehler_melden(self, report) -> None:
        """Blockierende Paketfehler nach oben geben.

        Vorher faerbte ein blockierender Eintrag nur eine Baumzeile. Der
        Weiter-Knopf war grau, die Hinweisleiste oben blieb leer -- und wer
        nicht genau hinsah, suchte den Grund vergebens.
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

    def _anbieterwahl(self, zeile: QTreeWidgetItem, eintrag) -> None:
        """Bei mehreren Anbietern muss vorab entschieden werden.

        pacman wuerde interaktiv fragen; mkarchiso laeuft ohne Rueckfrage und
        wuerde an dieser Stelle abbrechen.
        """
        combo = QComboBox()
        combo.setToolTip(eintrag.message)
        combo.addItem(f"{len(eintrag.members)} Anbieter -- bitte einen waehlen", "")
        for anbieter in eintrag.members:
            combo.addItem(anbieter, anbieter)
        combo.currentIndexChanged.connect(
            lambda _i, c=combo, virtuell=eintrag.normalized: self._anbieter_setzen(
                virtuell, c
            )
        )
        self.results.setItemWidget(zeile, 2, combo)

    def _anbieter_setzen(self, virtuell: str, combo: QComboBox) -> None:
        anbieter = combo.currentData()
        if anbieter:
            self.store.set_provider_choice(virtuell, str(anbieter))
            self._pruefen()

    def is_complete(self) -> bool:
        return self._blocking == 0 and super().is_complete()


__all__ = ["FreePackagesPage"]
