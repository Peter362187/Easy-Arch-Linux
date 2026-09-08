"""Zusammenfassung und Dry-Run.

Zeigt den vollstaendigen Bauplan, bevor irgendetwas geschieht: Auswahl,
aufgeloeste Paketliste, systemd-Symlinks, abgeleiteter ISO-Dateiname und alle
offenen Hinweise.

Die dritte Registerkarte zeigt die erzeugte ``archinstall.json``. Das ist kein
Beiwerk: archiso baut nur ein Live-System, und ohne diese Konfiguration koennte
der Benutzer das Ergebnis nicht dauerhaft installieren. Sie hier sichtbar zu
machen, deckt fehlende semantische Zuordnungen im Katalog sofort auf.

Die vierte zeigt den Profilbaum, bevor eine einzige Datei entsteht.
"""

from __future__ import annotations

import json
import logging

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
)

from ...core.archiso import GeneratedProfile
from ...core.archiso.errors import ProfileError
from ...core.catalog import Category
from ...core.plan import BuildPlan, build_plan, plan_as_text
from ...core.resolver import Issue
from ..design import tokens
from ..design.typo import SUBTITLE, format_size, mono, schrift
from ..packages_worker import PackageController
from ..store import SelectionStore
from ..widgets.common import brush, copy_to_clipboard, setze_rolle
from .base import PageBase

log = logging.getLogger(__name__)


class SummaryPage(PageBase):
    def __init__(
        self,
        category: Category,
        store: SelectionStore,
        controller: PackageController,
    ) -> None:
        super().__init__(category, store)
        self.controller = controller
        self._plan: BuildPlan | None = None
        self._profile: GeneratedProfile | None = None
        self._profile_error = ""
        self._aufbauen()

    def _aufbauen(self) -> None:
        self.headline = QLabel()
        self.headline.setWordWrap(True)
        self.headline.setFont(schrift(SUBTITLE, fett=True))
        self._root.addWidget(self.headline)

        self.tabs = QTabWidget()

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Einstellung", "Wert"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setColumnWidth(0, 280)
        self.tabs.addTab(self.tree, "Bauplan")

        self.symlinks = QPlainTextEdit()
        self.symlinks.setReadOnly(True)
        self.symlinks.setFont(mono())
        self.tabs.addTab(self.symlinks, "systemd-Verknuepfungen")

        self.archinstall = QPlainTextEdit()
        self.archinstall.setReadOnly(True)
        self.archinstall.setFont(mono())
        self.tabs.addTab(self.archinstall, "Installationskonfiguration")

        self.files = QTreeWidget()
        self.files.setHeaderLabels(["Datei", "Art", "Groesse", "Herkunft"])
        self.files.setRootIsDecorated(False)
        self.files.setAlternatingRowColors(True)
        self.files.setColumnWidth(0, 420)
        self.tabs.addTab(self.files, "Profildateien")

        self._root.addWidget(self.tabs, 1)

        fuss = QHBoxLayout()
        self.copy_button = QPushButton("Bauplan in die Zwischenablage")
        self.copy_button.setProperty("variant", "ghost")
        self.copy_button.clicked.connect(self._kopieren)
        fuss.addWidget(self.copy_button)
        fuss.addStretch(1)
        self.verdict = QLabel()
        self.verdict.setWordWrap(True)
        fuss.addWidget(self.verdict, 1)
        self._root.addLayout(fuss)

    # -- Inhalt ---------------------------------------------------------------
    def sync_from_store(self) -> None:
        self._neu_aufbauen()

    def _neu_aufbauen(self) -> None:
        p = tokens().palette
        config = self.store.config
        resolution = self.store.resolution()
        report = self.controller.validate(
            list(resolution.package_names) + list(config.extra_packages),
            provider_choices=config.provider_choices,
        )
        plan = build_plan(self.store.catalog, config, resolution, report)
        self._plan = plan

        self.headline.setText(
            f"{config.distro_name} {config.version}  ->  {plan.iso_filename}"
        )

        self.tree.clear()
        for abschnitt in plan.sections:
            eltern = QTreeWidgetItem([abschnitt.title, ""])
            font = eltern.font(0)
            font.setBold(True)
            eltern.setFont(0, font)
            for zeile in abschnitt.lines:
                schluessel, trenner, wert = zeile.partition(": ")
                eltern.addChild(
                    QTreeWidgetItem([schluessel, wert] if trenner else ["", zeile])
                )
            for detail in abschnitt.detail:
                kind = QTreeWidgetItem(["", detail])
                kind.setForeground(1, brush(p.text_muted))
                eltern.addChild(kind)
            self.tree.addTopLevelItem(eltern)
            eltern.setExpanded(len(abschnitt.lines) <= 12)

        self.symlinks.setPlainText(
            "\n".join(f"{link}\n    -> {ziel}" for link, ziel in plan.symlinks)
            or "Keine Dienste aktiviert."
        )
        self.archinstall.setPlainText(
            json.dumps(plan.archinstall, indent=2, ensure_ascii=False)
        )

        if plan.warnings:
            self.verdict.setText(f"{len(plan.warnings)} Hinweis(e) -- siehe unten")
            self._verdict_rolle("warnung")
        else:
            self.verdict.setText(
                "Die Konfiguration ist vollstaendig und in sich stimmig."
            )
            self._verdict_rolle("erfolg")

        self._dateien_zeigen()

        if plan.warnings:
            eintrag = QTreeWidgetItem(["Hinweise", ""])
            font = eintrag.font(0)
            font.setBold(True)
            eintrag.setFont(0, font)
            for warnung in plan.warnings:
                eintrag.addChild(QTreeWidgetItem(["", warnung]))
            self.tree.addTopLevelItem(eintrag)
            eintrag.setExpanded(True)

        self.completeChanged.emit()

    def _verdict_rolle(self, rolle: str) -> None:
        setze_rolle(self.verdict, rolle)

    def plan(self) -> BuildPlan | None:
        return self._plan

    def profile(self) -> GeneratedProfile | None:
        """Das zuletzt erzeugte archiso-Profil, falls erzeugbar."""
        return self._profile

    def _dateien_zeigen(self) -> None:
        """Erzeugt den Profilbaum im Speicher und zeigt ihn an.

        Bewusst ohne etwas zu schreiben: der Benutzer soll sehen koennen, was
        entstehen wuerde, bevor er ein Verzeichnis auswaehlt.
        """
        from ...core.archiso import ProfileGenerator

        p = tokens().palette
        self.files.clear()
        self._profile = None
        self._profile_error = ""

        resolution = self.store.resolution()
        if not resolution.is_valid:
            self.files.addTopLevelItem(
                QTreeWidgetItem(
                    ["Die Konfiguration ist noch nicht vollstaendig.", "", "", ""]
                )
            )
            self.set_local_issues(())
            return

        try:
            profil = ProfileGenerator(
                self.store.catalog, self.store.config, resolution, self.store.secrets
            ).generate()
        except ProfileError as exc:
            # Frueher landete die Meldung nur als Zeile in dieser Tabelle --
            # ohne Banner, ohne Protokolleintrag, und "ISO erstellen" blieb
            # anklickbar. Der Benutzer startete dann einen Bau, der gar nicht
            # anlaufen konnte.
            log.warning("Profil nicht erzeugbar: %s", exc.technical or exc.user_message)
            self._profile_error = exc.user_message
            self.files.addTopLevelItem(QTreeWidgetItem([exc.user_message, "", "", ""]))
            self._profilfehler_melden()
            self.completeChanged.emit()
            return

        self.set_local_issues(())
        self._profile = profil
        for pfad in profil.tree.paths():
            verweis = profil.tree.symlink(pfad)
            if verweis is not None:
                eintrag = QTreeWidgetItem([pfad, "Verknuepfung", "", verweis.origin])
                eintrag.setToolTip(0, f"zeigt auf {verweis.target}")
                eintrag.setForeground(1, brush(p.accent_lesbar))
            else:
                datei = profil.tree.files[pfad]
                eintrag = QTreeWidgetItem(
                    [
                        pfad,
                        "Datei",
                        format_size(datei.size) or f"{datei.size} B",
                        datei.origin,
                    ]
                )
            self.files.addTopLevelItem(eintrag)

        summe = QTreeWidgetItem([f"-- {profil.tree.describe()} --", "", "", ""])
        font = summe.font(0)
        font.setBold(True)
        summe.setFont(0, font)
        self.files.addTopLevelItem(summe)

    def _kopieren(self) -> None:
        if self._plan is None:
            return
        # Der gemeinsame Helfer setzt die Beschriftung nach kurzer Zeit zurueck.
        copy_to_clipboard(plan_as_text(self._plan), self.copy_button)

    def is_complete(self) -> bool:
        return (
            self._plan is not None
            and self._plan.resolution.is_valid
            and not self._profile_error
        )

    def _profilfehler_melden(self) -> None:
        """Die Meldung dorthin bringen, wo der Benutzer hinsieht."""
        self.set_local_issues(
            (
                Issue(
                    severity="error",
                    code="profile_not_generatable",
                    category_id=self.category.id,
                    message=(
                        "Das archiso-Profil laesst sich mit dieser Auswahl nicht "
                        f"erzeugen: {self._profile_error}"
                    ),
                ),
            )
        )


__all__ = ["SummaryPage"]
