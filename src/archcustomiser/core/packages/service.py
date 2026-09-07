"""Fassade der Paketschicht.

Synchron und ohne Qt -- die Oberflaeche kapselt das in einen Worker-Thread.
Diese Trennung ist der Grund, warum die gesamte Schicht ohne laufende GUI
testbar ist.

Aussenverhalten in einem Satz: Der Index wird einmal geladen (im Hintergrund,
mit Fortschrittsmeldung), danach ist jede Pruefung eine Suche im Speicher --
ohne Netzzugriff und ohne wahrnehmbare Verzoegerung, egal wie viele Namen.
"""

from __future__ import annotations

import logging
import shutil
import sys
from collections.abc import Sequence
from datetime import timedelta

from .aur import AurClient
from .backend import (
    CancelCallback,
    PackageBackend,
    PackageConfig,
    ProgressCallback,
    RefreshPolicy,
    SupportsDependencyPreview,
)
from .backend_pacman import PacmanSyncBackend, read_pacman_repos
from .backend_pacman import is_available as pacman_available
from .backend_remote import RemoteIndexBackend
from .errors import PackageLayerError
from .index import RepoIndex
from .models import (
    BackendProblem,
    EntryKind,
    Freshness,
    IndexMetadata,
    Resolution,
    ValidationReport,
)
from .validator import validate_all

log = logging.getLogger(__name__)


def default_backend(config: PackageConfig) -> PackageBackend:
    """Waehlt das passende Backend fuer dieses System."""
    if sys.platform.startswith("linux") and pacman_available():
        repos = read_pacman_repos()
        if repos:
            config = config.with_repos(repos)
        log.info("Verwende lokale pacman-Datenbanken (%s)", ", ".join(config.repos))
        return PacmanSyncBackend(config)
    log.info("Verwende Paketdaten vom Spiegelserver (%s)", ", ".join(config.repos))
    return RemoteIndexBackend(config)


class PackageService:
    """Laedt den Index und prueft Paketnamen dagegen."""

    def __init__(
        self,
        config: PackageConfig | None = None,
        backend: PackageBackend | None = None,
    ) -> None:
        self.config = config or PackageConfig()
        self.backend = backend or default_backend(self.config)
        self._index: RepoIndex | None = None
        self._problems: tuple[BackendProblem, ...] = ()
        self._degraded = True   # bis der Index steht, wird nichts behauptet

    # -- Zustand --------------------------------------------------------------
    @property
    def index(self) -> RepoIndex | None:
        return self._index

    @property
    def degraded(self) -> bool:
        return self._degraded

    def metadata(self) -> IndexMetadata | None:
        if self._index is not None:
            return self._index.meta
        try:
            return self.backend.index_metadata()
        except Exception:
            log.debug("Metadaten nicht abrufbar", exc_info=True)
            return None

    # -- Laden ----------------------------------------------------------------
    def load(
        self,
        *,
        policy: RefreshPolicy = RefreshPolicy.IF_STALE,
        progress: ProgressCallback | None = None,
        cancel: CancelCallback | None = None,
    ) -> RepoIndex | None:
        """Laedt den Index. Gibt ``None`` zurueck, wenn nichts verfuegbar ist.

        Wirft nicht: die Oberflaeche soll bedienbar bleiben, auch wenn keine
        Paketdaten da sind. Der Zustand wird ueber ``degraded`` und
        ``problems()`` mitgeteilt.
        """
        try:
            self._index = self.backend.load_index(
                policy=policy, progress=progress, cancel=cancel
            )
            self._degraded = False
            self._problems = ()
            log.info("Paketdaten bereit: %s", self._index.describe())
            return self._index
        except PackageLayerError as exc:
            self._degraded = True
            self._problems = (BackendProblem(repo=None, message=exc.user_message),)
            log.warning("Paketdaten nicht verfuegbar: %s", exc.technical)

            # Zweiter Versuch ueber den Spiegelserver, falls das lokale Backend
            # scheitert (z.B. leeres /var/lib/pacman/sync nach der Installation).
            if not isinstance(self.backend, RemoteIndexBackend):
                log.info("Weiche auf Paketdaten vom Spiegelserver aus")
                try:
                    fallback = RemoteIndexBackend(self.config)
                    self._index = fallback.load_index(policy=policy, progress=progress)
                    self.backend = fallback
                    self._degraded = False
                    self._problems = ()
                    return self._index
                except PackageLayerError as fallback_exc:
                    self._problems += (
                        BackendProblem(repo=None, message=fallback_exc.user_message),
                    )
            return None

    def refresh(self, progress: ProgressCallback | None = None) -> RepoIndex | None:
        """Fuer die Schaltflaeche 'Jetzt aktualisieren'."""
        return self.load(policy=RefreshPolicy.FORCE, progress=progress)

    def problems(self) -> tuple[BackendProblem, ...]:
        return self._problems

    # -- Pruefen --------------------------------------------------------------
    def validate(
        self,
        names: Sequence[str],
        *,
        provider_choices: dict[str, str] | None = None,
        check_aur: bool | None = None,
    ) -> ValidationReport:
        report = validate_all(
            names,
            self._index,
            degraded=self._degraded,
            provider_choices=provider_choices,
            problems=self._problems,
            meta=self.metadata(),
        )
        use_aur = self.config.enable_aur if check_aur is None else check_aur
        if not use_aur or self._degraded:
            return report
        return self._augment_with_aur(report)

    def _augment_with_aur(self, report: ValidationReport) -> ValidationReport:
        """Nur fuer Namen, die offiziell nicht gefunden wurden.

        Offizielle Repositories haben immer Vorrang -- ein AUR-Paket
        gleichen Namens darf ein Repo-Paket nicht verdecken.
        """
        missing = [
            entry.normalized for entry in report.entries if entry.kind is EntryKind.NOT_FOUND
        ]
        if not missing:
            return report

        found = AurClient().info(missing)
        if not found:
            return report

        updated: list[Resolution] = []
        for entry in report.entries:
            info = found.get(entry.normalized) if entry.kind is EntryKind.NOT_FOUND else None
            if info is None:
                updated.append(entry)
                continue
            updated.append(
                Resolution(
                    query=entry.query,
                    normalized=entry.normalized,
                    kind=EntryKind.AUR,
                    repo="aur",
                    version=info.package.version,
                    description=info.package.description,
                    constraint=entry.constraint,
                    notes=entry.notes
                    + info.warnings
                    + (
                        "AUR-Pakete koennen nicht direkt in die ISO uebernommen "
                        "werden; sie muessen vorher lokal gebaut werden.",
                    ),
                )
            )
        return ValidationReport(
            entries=tuple(updated),
            index_meta=report.index_meta,
            degraded=report.degraded,
            problems=report.problems,
        )

    # -- Abhaengigkeitsvorschau ----------------------------------------------
    def can_preview_dependencies(self) -> bool:
        return isinstance(self.backend, SupportsDependencyPreview) and shutil.which("pacman") is not None

    # -- Frische --------------------------------------------------------------
    def age(self) -> timedelta | None:
        meta = self.metadata()
        return meta.age if meta else None

    def freshness(self) -> Freshness:
        return self.config.policy.freshness(self.age())

    def freshness_text(self) -> str:
        """Der Text fuer die Statuszeile (Spec Abschnitt 16)."""
        meta = self.metadata()
        if meta is None or meta.data_updated_at is None:
            return "Paketdaten: nicht verfuegbar"
        stamp = meta.data_updated_at.astimezone()
        age = meta.age
        return (
            f"Paketdaten vom {stamp:%d.%m.%Y %H:%M} ({_humanize(age)}) "
            f"- {meta.package_count} Pakete aus {', '.join(meta.repo_names)}"
        )

    # -- Wartung --------------------------------------------------------------


def _humanize(age: timedelta | None) -> str:
    if age is None:
        return "unbekannt"
    seconds = int(age.total_seconds())
    if seconds < 90:
        return "gerade eben"
    minutes = seconds // 60
    if minutes < 90:
        return f"vor {minutes} Minuten"
    hours = minutes // 60
    if hours < 36:
        return f"vor {hours} Stunden"
    days = hours // 24
    return f"vor {days} Tagen"
