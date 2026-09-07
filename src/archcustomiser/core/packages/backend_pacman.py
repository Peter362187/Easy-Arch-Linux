"""Backend fuer Arch-Systeme: liest ``/var/lib/pacman/sync`` direkt.

Warum die Dateien lesen statt ``pacman -Si`` aufzurufen:

* Kein Subprozess je Abfrage, damit auch kein Weg, ueber Argumente etwas
  einzuschleusen.
* Keine uebersetzte oder formatierte Ausgabe, die sich zwischen
  pacman-Versionen aendern kann.
* Die Datei-Aenderungszeit ist unmittelbar die Antwort auf die Frage, wann die
  Paketdaten zuletzt aktualisiert wurden.
* Dieselben Dateien, derselbe Parser wie beim Fernzugriff -- ein Codepfad,
  zwei Quellen.

Zum Aktualisieren wird ``pacman -Sy`` in eine *eigene* Datenbank unterhalb des
Zwischenspeichers geschrieben, mit ``fakeroot`` und ohne Root-Rechte. Das ist
das Muster, das ``checkupdates`` aus pacman-contrib verwendet. Die
Systemdatenbank wird dabei nicht angefasst -- ein Programm, das ISOs baut, hat
keinen Grund, den Paketstand des Rechners zu veraendern.
"""

from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from ..paths import cache_dir, ensure_dir
from .backend import (
    CancelCallback,
    DependencyPreview,
    PackageConfig,
    ProgressCallback,
    RefreshPolicy,
)
from .errors import BackendUnavailable, PacmanInvocationError, PacmanNotAvailable, RepositoryDataError
from .index import RepoIndex, build_index
from .models import BackendCapabilities, IndexMetadata, PackageInfo, RepoMeta
from .names import validate_name
from .runner import Runner, SubprocessRunner
from .syncdb import parse_syncdb

log = logging.getLogger(__name__)

SYNC_DIR = Path("/var/lib/pacman/sync")
PACMAN_CONF = Path("/etc/pacman.conf")

_SECTION = re.compile(r"^\[([^\]]+)\]\s*$")


def read_pacman_repos(path: Path = PACMAN_CONF) -> tuple[str, ...]:
    """Aktive Repositories aus pacman.conf, in Reihenfolge.

    ``Include``-Zeilen werden nicht verfolgt: die Abschnittsnamen stehen immer
    in der Hauptdatei, und ``Include`` verweist nur auf die Spiegelliste.
    """
    if not path.is_file():
        return ()
    repos: list[str] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            match = _SECTION.match(stripped)
            if match and match.group(1) != "options":
                repos.append(match.group(1))
    except OSError as exc:
        log.debug("pacman.conf nicht lesbar: %s", exc)
    return tuple(repos)


def is_available() -> bool:
    return shutil.which("pacman") is not None and SYNC_DIR.is_dir()


class PacmanSyncBackend:
    """Liest die lokalen Sync-Datenbanken; aktualisiert ohne Root-Rechte."""

    name = "pacman"

    def __init__(
        self,
        config: PackageConfig | None = None,
        *,
        runner: Runner | None = None,
        sync_dir: Path = SYNC_DIR,
    ) -> None:
        self.config = config or PackageConfig()
        self.runner = runner or SubprocessRunner()
        self.sync_dir = sync_dir
        self._private_dbpath = cache_dir() / "syncdb"

    # -- Protokoll ------------------------------------------------------------
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            name=self.name,
            can_refresh=shutil.which("fakeroot") is not None,
            can_resolve_dependencies=shutil.which("pacman") is not None,
            requires_root=False,
            repos=self.config.repos,
        )

    def index_metadata(self) -> IndexMetadata | None:
        repos: list[RepoMeta] = []
        for repo in self.config.repos:
            path = self._db_path(repo)
            if not path.is_file():
                continue
            stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            repos.append(
                RepoMeta(
                    name=repo,
                    source=str(path),
                    fetched_at=stamp,
                    last_modified=stamp,
                    package_count=0,
                )
            )
        if not repos:
            return None
        return IndexMetadata(backend=self.name, arch=self.config.arch, repos=tuple(repos))

    def load_index(
        self,
        *,
        policy: RefreshPolicy = RefreshPolicy.IF_STALE,
        progress: ProgressCallback | None = None,
        cancel: CancelCallback | None = None,
    ) -> RepoIndex:
        if policy is RefreshPolicy.FORCE:
            try:
                self.refresh(progress=progress)
            except BackendUnavailable as exc:
                # Aktualisieren ist ein Zusatz, kein Muss: der vorhandene Stand
                # bleibt brauchbar.
                log.warning("Aktualisierung fehlgeschlagen: %s", exc.technical)

        repo_packages: list[tuple[str, Sequence[PackageInfo]]] = []
        metas: list[RepoMeta] = []
        total = len(self.config.repos) or 1

        for position, repo in enumerate(self.config.repos):
            if cancel is not None and cancel():
                raise BackendUnavailable("Der Vorgang wurde abgebrochen.")
            if progress is not None:
                progress(f"Paketdaten {repo}", position / total)

            path = self._db_path(repo)
            if not path.is_file():
                log.info("Repository %s hat keine lokale Datenbank (%s)", repo, path)
                continue
            try:
                data = path.read_bytes()
                packages = parse_syncdb(data, repo)
            except OSError as exc:
                log.warning("%s nicht lesbar: %s", path, exc)
                continue
            except RepositoryDataError as exc:
                log.warning("%s", exc.technical)
                continue

            stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            repo_packages.append((repo, packages))
            metas.append(
                RepoMeta(
                    name=repo,
                    source=str(path),
                    fetched_at=stamp,
                    last_modified=stamp,
                    package_count=len(packages),
                )
            )

        if not repo_packages:
            raise BackendUnavailable(
                "In /var/lib/pacman/sync liegen keine lesbaren Paketdatenbanken. "
                "Bitte einmal 'sudo pacman -Sy' ausfuehren.",
                f"sync_dir={self.sync_dir}",
            )

        # Dieselbe Zusicherung wie im Remote-Backend: ein uebersprungenes
        # Repository darf den Rest nicht als vollstaendig erscheinen lassen.
        geladen = {name for name, _ in repo_packages}
        fehlend = tuple(repo for repo in self.config.repos if repo not in geladen)
        if fehlend:
            log.warning("Teilindex aus pacman: %s fehlen", ", ".join(fehlend))
        meta = IndexMetadata(
            backend=self.name,
            arch=self.config.arch,
            repos=tuple(metas),
            missing_repos=fehlend,
        )
        index = build_index(repo_packages, meta)
        if progress is not None:
            progress("fertig", 1.0)
        return index

    # -- Aktualisieren --------------------------------------------------------
    def refresh(self, *, progress: ProgressCallback | None = None) -> None:
        """``pacman -Sy`` in eine eigene Datenbank, ohne Root-Rechte."""
        pacman = shutil.which("pacman")
        if pacman is None:
            raise PacmanNotAvailable()
        fakeroot = shutil.which("fakeroot")
        if fakeroot is None:
            raise BackendUnavailable(
                "Zum Aktualisieren der Paketdaten ohne Administratorrechte wird "
                "'fakeroot' benoetigt (im Paket base-devel enthalten).",
                "fakeroot nicht im PATH",
            )

        ensure_dir(self._private_dbpath / "sync")
        if progress is not None:
            progress("Paketdatenbanken werden aktualisiert", 0.1)

        argv = [
            fakeroot,
            "--",
            pacman,
            "-Sy",
            "--dbpath",
            str(self._private_dbpath),
            "--logfile",
            "/dev/null",
            "--noconfirm",
        ]
        if self._supports_sandbox_flag(pacman):
            argv.insert(argv.index("-Sy") + 1, "--disable-sandbox-filesystem")

        self.runner.run(argv, timeout=180.0).raise_for_status()
        self.sync_dir = self._private_dbpath / "sync"
        log.info("Paketdaten aktualisiert nach %s", self.sync_dir)

    def _supports_sandbox_flag(self, pacman: str) -> bool:
        """Der Schalter existiert erst ab neueren pacman-Versionen.

        Statt eine Version zu raten wird die Hilfe abgefragt -- das ist stabil
        gegenueber Versionsnummern-Schemata.
        """
        try:
            result = self.runner.run([pacman, "-Sh"], timeout=10.0)
        except PacmanInvocationError:
            return False
        return "--disable-sandbox-filesystem" in (result.stdout + result.stderr)

    # -- echte Abhaengigkeitsaufloesung ---------------------------------------
    def preview_transaction(
        self, packages: Sequence[str], *, timeout: float = 120.0
    ) -> DependencyPreview:
        """Laesst pacman die Transaktion planen -- ohne etwas zu installieren.

        Die Spezifikation verlangt ausdruecklich, dass die Aufloesung von
        pacman kommt und nicht nachgebaut wird (Abschnitt 15). ``-Sp`` gibt nur
        aus, was installiert wuerde, und veraendert nichts.
        """
        pacman = shutil.which("pacman")
        if pacman is None:
            raise PacmanNotAvailable()

        # Jeder Name wird geprueft, bevor er die Argumentliste erreicht.
        # Zusammen mit dem '--' unten ist ein Name, der als Schalter gelesen
        # werden koennte, doppelt ausgeschlossen.
        names = [validate_name(name) for name in packages]
        if not names:
            return DependencyPreview(requested=(), resolved=(), added_by_dependency=())

        argv = [
            pacman,
            "-Sp",
            "--print-format",
            "%r/%n|%v|%s",
            "--dbpath",
            str(self.sync_dir.parent),
            "--noconfirm",
            "--",
            *names,
        ]
        result = self.runner.run(argv, timeout=timeout)
        if not result.ok:
            raise PacmanInvocationError(result.returncode, result.stderr[-2000:])

        resolved: list[PackageInfo] = []
        download = 0
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line or "|" not in line:
                continue
            location, _, rest = line.partition("|")
            version, _, size = rest.partition("|")
            repo, _, name = location.partition("/")
            try:
                download += int(size)
            except ValueError:
                pass
            resolved.append(PackageInfo(name=name, version=version, repo=repo))

        requested = set(names)
        return DependencyPreview(
            requested=tuple(names),
            resolved=tuple(resolved),
            added_by_dependency=tuple(
                package.name for package in resolved if package.name not in requested
            ),
            total_download_size=download,
        )

    # -- intern ---------------------------------------------------------------
    def _db_path(self, repo: str) -> Path:
        return self.sync_dir / f"{repo}.db"
