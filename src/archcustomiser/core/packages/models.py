"""Datentypen der Paketschicht."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum, auto


@dataclass(frozen=True, slots=True)
class Provide:
    """Ein ``%PROVIDES%``-Eintrag, z.B. ``libcap.so=2-64``."""

    name: str
    version: str | None = None


@dataclass(frozen=True, slots=True)
class PackageInfo:
    name: str
    version: str = ""
    repo: str = ""
    arch: str = ""
    description: str = ""
    groups: tuple[str, ...] = ()
    provides: tuple[Provide, ...] = ()
    depends: tuple[str, ...] = ()
    optdepends: tuple[str, ...] = ()
    replaces: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    installed_size: int | None = None
    compressed_size: int | None = None
    build_date: datetime | None = None


@dataclass(frozen=True, slots=True)
class RepoMeta:
    name: str
    source: str
    fetched_at: datetime
    last_modified: datetime | None = None
    package_count: int = 0
    etag: str | None = None


@dataclass(frozen=True, slots=True)
class IndexMetadata:
    backend: str
    arch: str
    repos: tuple[RepoMeta, ...]
    schema_version: int = 1
    missing_repos: tuple[str, ...] = ()
    """Repositorien, die nicht geladen werden konnten.

    Ohne dieses Feld galt ein Teilindex als vollstaendig: fiel nur ``core``
    aus, meldete die Pruefung 'linux', 'base' und alles andere daraus als
    "nicht gefunden" -- blockierend, mit Tippfehler-Vorschlaegen. Genau die
    falsche Aussage, die die zentrale Zusicherung der Schicht verbietet.
    """

    @property
    def complete(self) -> bool:
        """Ob ueber *alle* angefragten Repositorien Daten vorliegen."""
        return not self.missing_repos

    @property
    def data_updated_at(self) -> datetime | None:
        """Der aelteste Repo-Stand.

        Bewusst der aelteste und nicht der neueste: die Aussage
        "so aktuell sind meine Paketdaten" muss fuer *alle* Repositories gelten.
        Und bewusst der serverseitige Stand, nicht der Abrufzeitpunkt -- die
        Spezifikation (Abschnitt 16) fragt, wann die Paketdaten zuletzt
        aktualisiert wurden, nicht wann wir zuletzt nachgesehen haben.
        """
        stamps = [repo.last_modified for repo in self.repos if repo.last_modified]
        return min(stamps) if stamps else None

    @property
    def age(self) -> timedelta | None:
        reference = self.data_updated_at
        if reference is None:
            return None
        if reference.tzinfo is None:
            reference = reference.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - reference

    @property
    def package_count(self) -> int:
        return sum(repo.package_count for repo in self.repos)

    @property
    def repo_names(self) -> tuple[str, ...]:
        return tuple(repo.name for repo in self.repos)


class Freshness(str, Enum):
    """Ampelstufen fuer die Anzeige des Datenalters."""

    FRESH = "fresh"
    AGING = "aging"
    STALE = "stale"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CachePolicy:
    stale_after: timedelta = timedelta(hours=6)     # ab hier neu nachfragen
    warn_after: timedelta = timedelta(hours=24)     # ab hier gelb
    block_after: timedelta = timedelta(days=7)      # ab hier vor dem Build nachfragen
    query_ttl: timedelta = timedelta(hours=1)
    schema_version: int = 1

    def freshness(self, age: timedelta | None) -> Freshness:
        if age is None:
            return Freshness.UNKNOWN
        if age >= self.block_after or age >= self.warn_after:
            return Freshness.STALE if age >= self.block_after else Freshness.AGING
        return Freshness.FRESH


class EntryKind(Enum):
    """Was ein eingegebener Name tatsaechlich ist."""

    PACKAGE = auto()          # exakter Paketname (auch Meta-Pakete wie base-devel)
    GROUP = auto()            # Paketgruppe, z.B. plasma
    PROVIDES_UNIQUE = auto()  # virtuelles Paket mit genau einem Anbieter
    PROVIDES_AMBIG = auto()   # mehrere Anbieter -- der Benutzer muss waehlen
    AUR = auto()
    NOT_FOUND = auto()
    INVALID_NAME = auto()
    UNVERIFIED = auto()       # Index unvollstaendig -- keine Aussage moeglich

    @property
    def is_usable(self) -> bool:
        return self in (
            EntryKind.PACKAGE,
            EntryKind.GROUP,
            EntryKind.PROVIDES_UNIQUE,
            EntryKind.UNVERIFIED,
        )

    @property
    def is_blocking(self) -> bool:
        return self in (EntryKind.NOT_FOUND, EntryKind.INVALID_NAME, EntryKind.PROVIDES_AMBIG)


@dataclass(frozen=True, slots=True)
class Resolution:
    """Das Ergebnis fuer genau einen eingegebenen Namen."""

    query: str
    normalized: str
    kind: EntryKind
    repo: str | None = None
    version: str | None = None
    description: str = ""
    members: tuple[str, ...] = ()      # Gruppenmitglieder bzw. Anbieter
    chosen: str | None = None          # bei mehreren Anbietern: die Wahl
    suggestions: tuple[str, ...] = ()
    constraint: str | None = None
    notes: tuple[str, ...] = ()
    installed_size: int | None = None

    @property
    def profile_name(self) -> str:
        """Was in packages.x86_64 landet.

        Gruppen bleiben Gruppen -- pacstrap loest sie zur Bauzeit auf dem dann
        aktuellen Stand auf. Eine hier eingefrorene Mitgliederliste waere schon
        beim naechsten Repo-Update veraltet.
        """
        return self.chosen or self.normalized

    @property
    def size_text(self) -> str:
        """Ein 400-KB-Paket als '0 MB' anzuzeigen sieht nach einem Fehler aus."""
        if not self.installed_size:
            return ""
        megabytes = self.installed_size / 1_048_576
        if megabytes < 0.1:
            return f", {self.installed_size / 1024:.0f} KB"
        if megabytes < 10:
            return f", {megabytes:.1f} MB"
        return f", {megabytes:.0f} MB"

    @property
    def message(self) -> str:
        """Fertig formulierter Text fuer die Oberflaeche."""
        if self.kind is EntryKind.PACKAGE:
            return f"{self.repo}/{self.normalized} {self.version}{self.size_text}"
        if self.kind is EntryKind.GROUP:
            return f"Paketgruppe mit {len(self.members)} Paketen"
        if self.kind is EntryKind.PROVIDES_UNIQUE:
            # Bei getroffener Anbieterwahl stand hier weiterhin der
            # alphabetisch erste Anbieter -- also nicht der, der
            # tatsaechlich in die Paketliste wandert.
            anbieter = self.chosen or (self.members[0] if self.members else self.normalized)
            return f"wird bereitgestellt von {anbieter}"
        if self.kind is EntryKind.PROVIDES_AMBIG:
            return f"wird von {len(self.members)} Paketen bereitgestellt -- bitte eines auswaehlen"
        if self.kind is EntryKind.AUR:
            return f"AUR {self.version or ''}".strip()
        if self.kind is EntryKind.NOT_FOUND:
            if self.suggestions:
                return "nicht gefunden. Meinten Sie: " + ", ".join(self.suggestions) + "?"
            return "in den offiziellen Repositories nicht gefunden"
        if self.kind is EntryKind.INVALID_NAME:
            return self.notes[0] if self.notes else "ungueltiger Paketname"
        return "nicht pruefbar -- Paketdaten nicht verfuegbar"


@dataclass(frozen=True, slots=True)
class BackendProblem:
    repo: str | None
    message: str
    recoverable: bool = True


@dataclass(frozen=True, slots=True)
class ValidationReport:
    entries: tuple[Resolution, ...] = ()
    index_meta: IndexMetadata | None = None
    degraded: bool = False
    """Der Index ist unvollstaendig, veraltet oder gar nicht vorhanden.

    Solange das gilt, darf kein Name als "existiert nicht" gemeldet werden --
    das ist die zentrale Zusicherung dieser Schicht.
    """
    problems: tuple[BackendProblem, ...] = ()

    @property
    def is_clean(self) -> bool:
        return not self.blocking

    @property
    def blocking(self) -> tuple[Resolution, ...]:
        return tuple(entry for entry in self.entries if entry.kind.is_blocking)

    @property
    def ambiguous(self) -> tuple[Resolution, ...]:
        return tuple(entry for entry in self.entries if entry.kind is EntryKind.PROVIDES_AMBIG)

    def required_repositories(self) -> tuple[str, ...]:
        """Repositorien, die fuer die geprueften Namen aktiviert sein muessen.

        Der Katalog nennt ``repos: [multilib]`` nur an den Optionen, die es
        brauchen. Ein frei eingegebenes ``lib32-...`` wurde dagegen gegen
        multilib geprueft und als gefunden gemeldet -- aber die erzeugte
        pacman.conf aktivierte das Repository nicht, und pacstrap brach
        mitten im Bau mit "target not found" ab.

        ``core`` und ``extra`` sind in jeder pacman.conf ohnehin aktiv und
        bleiben deshalb aussen vor.
        """
        immer_da = {"core", "extra"}
        return tuple(
            sorted(
                {
                    entry.repo
                    for entry in self.entries
                    if entry.kind.is_usable and entry.repo and entry.repo not in immer_da
                }
            )
        )

    def profile_packages(self) -> tuple[str, ...]:
        """Namen fuer packages.x86_64 -- Gruppen unexpandiert."""
        return tuple(
            entry.profile_name for entry in self.entries if entry.kind.is_usable
        )

    def expanded_packages(self) -> tuple[str, ...]:
        """Namen fuer die Anzeige im Dry-Run -- Gruppen aufgeloest."""
        names: list[str] = []
        for entry in self.entries:
            if entry.kind is EntryKind.GROUP:
                names.extend(entry.members)
            elif entry.kind.is_usable:
                names.append(entry.profile_name)
        return tuple(dict.fromkeys(names))

    def aur_packages(self) -> tuple[str, ...]:
        """Getrennt, weil pacstrap AUR-Pakete nicht installieren kann."""
        return tuple(
            entry.normalized for entry in self.entries if entry.kind is EntryKind.AUR
        )


@dataclass(frozen=True, slots=True)
class BackendCapabilities:
    name: str
    can_refresh: bool = False
    can_resolve_dependencies: bool = False
    requires_root: bool = False
    repos: tuple[str, ...] = field(default_factory=tuple)
