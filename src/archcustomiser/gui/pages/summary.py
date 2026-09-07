"""Zusammenfassung und Dry-Run (Spec Abschnitt 14).

Zeigt den vollstaendigen Bauplan, bevor irgendetwas geschieht: Auswahl,
aufgeloeste Paketliste, systemd-Symlinks, abgeleiteter ISO-Dateiname, Alter der
Paketdaten und alle offenen Hinweise.

Die zweite Registerkarte zeigt die erzeugte ``archinstall.json``. Das ist kein
Beiwerk: archiso baut nur ein Live-System, und ohne diese Konfiguration koennte
der Benutzer das Ergebnis nicht dauerhaft installieren. Sie hier sichtbar zu
machen, deckt fehlende semantische Zuordnungen im Katalog sofort auf.
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
from .. import theme
from ..packages_worker import PackageController
from ..store import SelectionStore
from ..widgets.common import brush, copy_to_clipboard
from .base import CatalogPageBase

log = logging.getLogger(__name__)


class SummaryPage(CatalogPageBase):
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
        self._build_ui()
        # Nach dieser Seite beginnt der Build -- Qt blendet den Zurueck-Knopf
        # dann aus. Das ist gewollt: eine halb gestartete ISO-Erzeugung laesst
        # sich nicht durch Zurueckblaettern rueckgaengig machen.
        self.setCommitPage(True)

    def _build_ui(self) -> None:
        self.headline = QLabel()
        self.headline.setWordWrap(True)
        font = self.headline.font()
        font.setPointSize(font.pointSize() + 2)
        font.setBold(True)
        self.headline.setFont(font)
        self._root.addWidget(self.headline)

        self.tabs = QTabWidget()

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Einstellung", "Wert"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setColumnWidth(0, 280)
        self.tabs.addTab(self.tree, "Bauplan")

        self.symlinks = QPlainTextEdit()
        self.symlinks.setReadOnly(True)
        self.symlinks.setFont(theme.mono_font())
        self.tabs.addTab(self.symlinks, "systemd-Verknuepfungen")

        self.archinstall = QPlainTextEdit()
        self.archinstall.setReadOnly(True)
        self.archinstall.setFont(theme.mono_font())
        self.tabs.addTab(self.archinstall, "Installationskonfiguration")

        # Zeigt den erzeugten Profilbaum, bevor eine einzige Datei entsteht.
        self.files = QTreeWidget()
        self.files.setHeaderLabels(["Datei", "Art", "Groesse", "Herkunft"])
        self.files.setRootIsDecorated(False)
        self.files.setAlternatingRowColors(True)
        self.files.setColumnWidth(0, 420)
        self.tabs.addTab(self.files, "Profildateien")

        self._root.addWidget(self.tabs, 1)

        footer = QHBoxLayout()
        self.copy_button = QPushButton("Bauplan in die Zwischenablage")
        self.copy_button.clicked.connect(self._copy)
        footer.addWidget(self.copy_button)
        footer.addStretch(1)
        self.verdict = QLabel()
        self.verdict.setWordWrap(True)
        footer.addWidget(self.verdict, 1)
        self._root.addLayout(footer)

    # -- Inhalt ---------------------------------------------------------------
    def initializePage(self) -> None:
        super().initializePage()
        self._rebuild()

    def sync_from_store(self) -> None:
        self._rebuild()

    def _rebuild(self) -> None:
        config = self.store.config
        resolution = self.store.resolution()
        report = self.controller.validate(
            list(resolution.package_names) + list(config.extra_packages),
            provider_choices=config.provider_choices,
        )
        plan = build_plan(self.store.catalog, config, resolution, report)
        self._plan = plan

        self.headline.setText(
            f"{config.distro_name} {config.version}  →  {plan.iso_filename}"
        )

        self.tree.clear()
        for section in plan.sections:
            parent = QTreeWidgetItem([section.title, ""])
            font = parent.font(0)
            font.setBold(True)
            parent.setFont(0, font)
            for line in section.lines:
                key, separator, value = line.partition(": ")
                child = QTreeWidgetItem([key, value] if separator else ["", line])
                parent.addChild(child)
            for detail in section.detail:
                child = QTreeWidgetItem(["", detail])
                child.setForeground(1, brush(theme.muted()))
                parent.addChild(child)
            self.tree.addTopLevelItem(parent)
            parent.setExpanded(len(section.lines) <= 12)

        self.symlinks.setPlainText(
            "\n".join(f"{link}\n    -> {target}" for link, target in plan.symlinks)
            or "Keine Dienste aktiviert."
        )
        self.archinstall.setPlainText(
            json.dumps(plan.archinstall, indent=2, ensure_ascii=False)
        )

        if plan.warnings:
            self.verdict.setText(
                f"{len(plan.warnings)} Hinweis(e) -- siehe unten"
            )
            self.verdict.setStyleSheet(f"color:{theme.warning()};")
        else:
            self.verdict.setText("Die Konfiguration ist vollstaendig und in sich stimmig.")
            self.verdict.setStyleSheet(f"color:{theme.success()};")

        self._refresh_files()

        if plan.warnings:
            item = QTreeWidgetItem(["Hinweise", ""])
            font = item.font(0)
            font.setBold(True)
            item.setFont(0, font)
            for warning in plan.warnings:
                item.addChild(QTreeWidgetItem(["", warning]))
            self.tree.addTopLevelItem(item)
            item.setExpanded(True)

        self.completeChanged.emit()

    def plan(self) -> BuildPlan | None:
        return self._plan

    def profile(self) -> GeneratedProfile | None:
        """Das zuletzt erzeugte archiso-Profil, falls erzeugbar."""
        return self._profile

    def _refresh_files(self) -> None:
        """Erzeugt den Profilbaum im Speicher und zeigt ihn an.

        Bewusst ohne etwas zu schreiben: der Benutzer soll sehen koennen, was
        entstehen wuerde, bevor er ein Verzeichnis auswaehlt.
        """
        from ...core.archiso import ProfileGenerator

        self.files.clear()
        self._profile = None
        self._profile_error = ""

        resolution = self.store.resolution()
        if not resolution.is_valid:
            self.files.addTopLevelItem(
                QTreeWidgetItem(["Die Konfiguration ist noch nicht vollstaendig.", "", "", ""])
            )
            return

        try:
            profile = ProfileGenerator(
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
            self._show_profile_error()
            self.completeChanged.emit()
            return

        self._profile = profile
        for path in profile.tree.paths():
            link = profile.tree.symlink(path)
            if link is not None:
                item = QTreeWidgetItem([path, "Verknuepfung", "", link.origin])
                item.setToolTip(0, f"zeigt auf {link.target}")
                item.setForeground(1, brush(theme.accent()))
            else:
                entry = profile.tree.files[path]
                item = QTreeWidgetItem(
                    # theme.format_size() gibt es dafuer; hier standen die
                    # Groessen als rohe Bytezahl, was bei 3 MB unlesbar wird.
                    [path, "Datei", theme.format_size(entry.size) or f"{entry.size} B",
                     entry.origin]
                )
            self.files.addTopLevelItem(item)

        summary = QTreeWidgetItem([f"-- {profile.tree.describe()} --", "", "", ""])
        font = summary.font(0)
        font.setBold(True)
        summary.setFont(0, font)
        self.files.addTopLevelItem(summary)

    def _copy(self) -> None:
        if self._plan is None:
            return
        # Der gemeinsame Helfer setzt die Beschriftung nach kurzer Zeit zurueck.
        # Vorher blieb sie dauerhaft auf "Kopiert" stehen -- wer ein zweites Mal
        # kopierte, sah nicht, ob der Klick ankam.
        copy_to_clipboard(plan_as_text(self._plan), self.copy_button)

    def isComplete(self) -> bool:
        return (
            self._plan is not None
            and self._plan.resolution.is_valid
            and not self._profile_error
        )

    def _show_profile_error(self) -> None:
        """Die Meldung dorthin bringen, wo der Benutzer hinsieht."""
        from ...core.resolver import Issue

        self.banner.set_issues(
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



