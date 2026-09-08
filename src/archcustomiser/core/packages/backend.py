"""Backend-Protokoll und Konfiguration.

Zwei Umsetzungen teilen sich denselben Parser und denselben Indextyp:

* ``RemoteIndexBackend`` laedt die ``.db``-Dateien von einem Spiegelserver --
  funktioniert unter Windows und Arch gleichermassen.
* ``PacmanSyncBackend`` liest ``/var/lib/pacman/sync/`` direkt -- kein
  Subprozess, keine Root-Rechte, keine uebersetzte Ausgabe zu parsen.

Die echte Abhaengigkeitsaufloesung steht bewusst in einem *getrennten*
Protokoll: nur pacman kann sie, und ein Nachbau waere gegen die Vorgabe der
Spezifikation (Abschnitt 15). Wo pacman fehlt, blendet die Oberflaeche die
Vorschau aus, statt eine plausible, aber erfundene Liste anzuzeigen.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Protocol, runtime_checkable

from .index import RepoIndex
from .models import BackendCapabilities, CachePolicy, IndexMetadata, PackageInfo

DEFAULT_REPOS: tuple[str, ...] = ("core", "extra", "multilib")
# ACHTUNG: Dieser Spiegel fuehrt ausschliesslich x86_64. Arch Linux ARM ist
# ein eigenes Projekt mit eigenen Servern UND einem anderen Pfadschema
# ($arch/$repo statt $repo/os/$arch) -- ein anderer arch-Wert allein
# ergaebe hier also 404, keine ARM-Pakete.
DEFAULT_MIRROR = "https://geo.mirror.pkgbuild.com/$repo/os/$arch"

ProgressCallback = Callable[[str, float], None]
CancelCallback = Callable[[], bool]


class RefreshPolicy(Enum):
    NEVER = auto()     # nur Zwischenspeicher, kein Netz (Offline-Betrieb)
    IF_STALE = auto()  # Standard: bedingter Abruf, wenn die Frist abgelaufen ist
    FORCE = auto()     # Schaltflaeche "Jetzt aktualisieren"


@dataclass(frozen=True, slots=True)
class PackageConfig:
    """Gemeinsame Einstellungen der Paketschicht.

    ``repos`` ist bewusst eine gemeinsame Quelle fuer Pruefung und Build: wird
    hier gegen andere Repositories geprueft als spaeter installiert wird, gilt
    ein Paket als gefunden, das beim Build fehlt. ``steam`` liegt zum Beispiel
    in ``multilib``, das in der Standard-pacman.conf auskommentiert ist.
    """

    arch: str = "x86_64"
    repos: tuple[str, ...] = DEFAULT_REPOS
    mirrors: tuple[str, ...] = (DEFAULT_MIRROR,)
    enable_aur: bool = False
    policy: CachePolicy = field(default_factory=CachePolicy)
    connect_timeout: float = 20.0
    read_timeout: float = 120.0

    def with_repos(self, repos: Sequence[str]) -> PackageConfig:
        return PackageConfig(
            arch=self.arch,
            repos=tuple(repos),
            mirrors=self.mirrors,
            enable_aur=self.enable_aur,
            policy=self.policy,
            connect_timeout=self.connect_timeout,
            read_timeout=self.read_timeout,
        )

    def mirror_url(self, mirror: str, repo: str) -> str:
        return mirror.replace("$repo", repo).replace("$arch", self.arch).rstrip("/") + f"/{repo}.db"


@dataclass(frozen=True, slots=True)
class DependencyPreview:
    """Ergebnis einer echten pacman-Transaktion (nur auf Arch verfuegbar)."""

    requested: tuple[str, ...]
    resolved: tuple[PackageInfo, ...]
    added_by_dependency: tuple[str, ...]
    total_download_size: int = 0
    total_installed_size: int = 0
    conflicts: tuple[str, ...] = ()


@runtime_checkable
class PackageBackend(Protocol):
    name: str

    def capabilities(self) -> BackendCapabilities: ...

    def index_metadata(self) -> IndexMetadata | None:
        """Billig, ohne Netz und ohne vollstaendiges Parsen.

        ``None`` bedeutet: es liegen ueberhaupt keine Daten vor.
        """

    def load_index(
        self,
        *,
        policy: RefreshPolicy = RefreshPolicy.IF_STALE,
        progress: ProgressCallback | None = None,
        cancel: CancelCallback | None = None,
    ) -> RepoIndex:
        """Wirft ``BackendUnavailable`` oder ``RepositoryDataError``.

        Niemals fuer "Paket existiert nicht" -- das ist ein Ergebnis, kein
        Fehler.
        """


@runtime_checkable
class SupportsDependencyPreview(Protocol):
    """Nur pacman kann das. Bewusst optional."""

    def preview_transaction(
        self, packages: Sequence[str], *, timeout: float = 120.0
    ) -> DependencyPreview: ...
