"""Backend, das die Paketdatenbanken von einem Spiegelserver laedt.

Der Weg, der ueberall funktioniert -- unter Windows waehrend der Entwicklung
und auf Arch als Rueckfallebene, wenn ``/var/lib/pacman/sync`` nicht lesbar ist.

Kosten pro Programmstart: drei Anfragen. Ist der Zwischenspeicher aktuell,
antwortet der Server mit ``304`` und es werden null Nutzdaten uebertragen.
Danach kostet jede weitere Pruefung -- egal ob fuenf oder fuenfhundert Pakete --
keinen einzigen Netzzugriff mehr.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC
from pathlib import Path

from .backend import (
    CancelCallback,
    PackageConfig,
    ProgressCallback,
    RefreshPolicy,
)
from .cache import CacheEntry, PackageCache
from .errors import BackendUnavailable, MirrorError, NetworkUnavailable, RepositoryDataError
from .index import RepoIndex, build_index
from .models import BackendCapabilities, IndexMetadata, PackageInfo, RepoMeta
from .syncdb import parse_syncdb
from .transport import Transport, UrllibTransport, http_date

log = logging.getLogger(__name__)

MIRRORLIST = Path("/etc/pacman.d/mirrorlist")
MAX_MIRRORS = 4


def read_system_mirrors(path: Path = MIRRORLIST, limit: int = MAX_MIRRORS) -> tuple[str, ...]:
    """Liest die ersten aktiven Server aus der pacman-Spiegelliste.

    Nur Zeilen der Form ``Server = ...`` werden beruecksichtigt; alles andere
    wird ignoriert. Die Datei wird nicht ausgefuehrt und nicht interpretiert.
    """
    if not path.is_file():
        return ()
    found: list[str] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if separator and key.strip().lower() == "server":
                url = value.strip()
                if url.startswith(("http://", "https://")):
                    found.append(url)
            if len(found) >= limit:
                break
    except OSError as exc:
        log.debug("Spiegelliste nicht lesbar: %s", exc)
    return tuple(found)


class RemoteIndexBackend:
    """Laedt ``<mirror>/<repo>/os/<arch>/<repo>.db`` und baut daraus den Index."""

    name = "remote"

    def __init__(
        self,
        config: PackageConfig | None = None,
        *,
        transport: Transport | None = None,
        cache: PackageCache | None = None,
    ) -> None:
        self.config = config or PackageConfig()
        self.transport = transport or UrllibTransport()
        self.cache = cache or PackageCache(arch=self.config.arch)
        self._mirrors = self._resolve_mirrors()

    def _resolve_mirrors(self) -> tuple[str, ...]:
        system = read_system_mirrors()
        # Die Systemliste zuerst: sie ist in der Regel naeher und wurde vom
        # Benutzer bzw. reflector bereits sortiert.
        return tuple(dict.fromkeys(system + self.config.mirrors))

    # -- Protokoll ------------------------------------------------------------
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            name=self.name,
            can_refresh=True,
            can_resolve_dependencies=False,
            requires_root=False,
            repos=self.config.repos,
        )

    def index_metadata(self) -> IndexMetadata | None:
        repos: list[RepoMeta] = []
        for repo in self.config.repos:
            entry = self.cache.load(repo)
            if entry is None:
                continue
            repos.append(
                RepoMeta(
                    name=repo,
                    source=str(entry.path),
                    fetched_at=entry.fetched_at,
                    last_modified=entry.last_modified,
                    package_count=entry.package_count,
                    etag=entry.etag,
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
        repo_packages: list[tuple[str, Sequence[PackageInfo]]] = []
        metas: list[RepoMeta] = []
        failures: list[BackendUnavailable] = []

        total = len(self.config.repos) or 1
        with self.cache.lock() as sperre:
            if not sperre.acquired:
                # Ein anderer Prozess laedt gerade. Statt dieselben Dateien ein
                # zweites Mal zu holen, wird mit dem vorhandenen Stand
                # gearbeitet -- genau dafuer ist die Sperre da. Bisher wurde ihr
                # Ergebnis nie ausgewertet und einfach mitgeladen.
                log.info(
                    "Paketdaten werden bereits von einem anderen Vorgang "
                    "aktualisiert; der vorhandene Stand wird verwendet."
                )
                policy = RefreshPolicy.NEVER
            for position, repo in enumerate(self.config.repos):
                if cancel is not None and cancel():
                    raise BackendUnavailable("Der Vorgang wurde abgebrochen.")
                if progress is not None:
                    progress(f"Paketdaten {repo}", position / total)

                try:
                    entry, data = self._obtain(repo, policy)
                except BackendUnavailable as exc:
                    log.warning("Repository %s nicht verfuegbar: %s", repo, exc.technical)
                    failures.append(exc)
                    continue

                try:
                    packages = parse_syncdb(data, repo)
                except RepositoryDataError as exc:
                    log.warning("%s", exc.technical)
                    self.cache.discard(repo)
                    failures.append(
                        MirrorError(f"{repo}: beschaedigte Datenbank verworfen")
                    )
                    continue

                if entry.package_count != len(packages):
                    # url ist die Herkunft, nicht der Ablageort. Hier stand
                    # frueher entry.path -- damit ueberschrieb das Nachtragen
                    # der Paketzahl die Mirror-Adresse in der Metadatei mit dem
                    # oertlichen Cache-Pfad, und man sah spaeter nicht mehr,
                    # woher die Daten stammten.
                    entry = self.cache.store(
                        repo,
                        data,
                        url=entry.url,
                        etag=entry.etag,
                        last_modified=entry.last_modified,
                        package_count=len(packages),
                    )

                repo_packages.append((repo, packages))
                metas.append(
                    RepoMeta(
                        name=repo,
                        source=str(entry.path),
                        fetched_at=entry.fetched_at,
                        last_modified=entry.last_modified,
                        package_count=len(packages),
                        etag=entry.etag,
                    )
                )

        if progress is not None:
            progress("Index wird aufgebaut", 0.95)

        if not repo_packages:
            # Kein einziges Repository verfuegbar: das ist ein Fehler, kein
            # leerer Index. Ein leerer Index wuerde jedes Paket als
            # "existiert nicht" erscheinen lassen.
            if failures:
                raise failures[0]
            raise NetworkUnavailable("keine Paketdaten verfuegbar")

        geladen = {name for name, _ in repo_packages}
        fehlend = tuple(repo for repo in self.config.repos if repo not in geladen)
        if fehlend:
            log.warning("Teilindex: %s fehlen", ", ".join(fehlend))
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

    # -- intern ---------------------------------------------------------------
    def _obtain(self, repo: str, policy: RefreshPolicy) -> tuple[CacheEntry, bytes]:
        """Liefert (Cache-Eintrag, Rohdaten) fuer ein Repository."""
        cached = self.cache.load(repo)

        if policy is RefreshPolicy.NEVER:
            if cached is None:
                raise NetworkUnavailable(f"{repo}: kein zwischengespeicherter Stand vorhanden")
            return cached, cached.read()

        if policy is RefreshPolicy.IF_STALE and cached is not None:
            age = self._age(cached)
            if age is not None and age < self.config.policy.stale_after.total_seconds():
                return cached, cached.read()

        try:
            return self._download(repo, cached)
        except BackendUnavailable:
            if cached is not None:
                # Netz weg, aber ein Stand liegt vor: damit weiterarbeiten.
                # Der Aufrufer erfaehrt das Alter ueber die Metadaten.
                log.info("%s: Aktualisierung fehlgeschlagen, verwende gespeicherten Stand", repo)
                return cached, cached.read()
            raise

    def _age(self, entry: CacheEntry) -> float | None:
        from datetime import datetime

        if entry.fetched_at is None:
            return None
        return (datetime.now(UTC) - entry.fetched_at).total_seconds()

    def _download(self, repo: str, cached: CacheEntry | None) -> tuple[CacheEntry, bytes]:
        headers: dict[str, str] = {}
        if cached is not None:
            # Bedingter Abruf: der Server antwortet mit 304 und null Nutzdaten,
            # wenn sich nichts geaendert hat.
            if cached.etag:
                headers["If-None-Match"] = cached.etag
            if cached.last_modified:
                headers["If-Modified-Since"] = http_date(cached.last_modified)

        tried: list[str] = []
        last_error: BackendUnavailable | None = None

        for mirror in self._mirrors:
            url = self.config.mirror_url(mirror, repo)
            tried.append(url)
            try:
                response = self.transport.get(
                    url, headers=headers, timeout=self.config.read_timeout
                )
            except BackendUnavailable as exc:
                last_error = exc
                continue

            if response.not_modified and cached is not None:
                log.debug("%s ist unveraendert (304)", repo)
                return self.cache.touch(cached), cached.read()

            if response.status != 200 or not response.body:
                last_error = MirrorError(
                    f"{url}: HTTP {response.status}, {len(response.body)} Bytes",
                    status=response.status,
                    tried=tuple(tried),
                )
                continue

            entry = self.cache.store(
                repo,
                response.body,
                url=url,
                etag=response.etag,
                last_modified=response.last_modified,
                package_count=0,
            )
            log.info("%s aktualisiert (%d KB von %s)", repo, len(response.body) // 1024, mirror)
            return entry, response.body

        raise last_error or MirrorError(
            f"{repo}: kein Spiegelserver erreichbar", tried=tuple(tried)
        )
