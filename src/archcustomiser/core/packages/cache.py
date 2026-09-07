"""Zwischenspeicher fuer die Paketdatenbanken.

Gespeichert werden die **rohen, unveraenderten** ``.db``-Dateien plus eine
kleine JSON-Datei mit Metadaten. Bewusst kein ``pickle`` und kein abgeleitetes
Binaerformat: eine Datei aus dem Netz zu deserialisieren, die beim Laden Code
ausfuehren kann, waere eine unnoetige Angriffsflaeche. JSON und tar sind beide
rein deklarativ.

Schreibvorgaenge sind atomar (``os.replace``), damit ein Abbruch keinen halben
Cache hinterlaesst, und durch eine Sperrdatei geschuetzt, damit zwei laufende
Instanzen sich nicht ins Gehege kommen.

Der Cache ist rein abgeleitet -- ihn zu loeschen ist immer unbedenklich.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import time
from dataclasses import dataclass
from dataclasses import replace as _replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..paths import cache_dir, ensure_dir
from .errors import CacheError

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
LOCK_STALE_SECONDS = 300
_SAFE_REPO = re.compile(r"^[a-z0-9][a-z0-9._-]*$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class CacheEntry:
    repo: str
    path: Path
    etag: str | None
    last_modified: datetime | None
    fetched_at: datetime
    sha256: str
    size: int
    package_count: int
    # Woher die Daten stammen. Steht in der Metadatei, wurde aber bisher beim
    # Laden nicht mitgenommen -- weshalb ein erneutes store() die Mirror-Adresse
    # mangels besserem Wissen durch den oertlichen Cache-Pfad ersetzte.
    url: str = ""

    def read(self) -> bytes:
        return self.path.read_bytes()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def _parse_iso(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class CacheLock:
    """Einfache Sperrdatei mit Erkennung verwaister Sperren."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._acquired = False

    def __enter__(self) -> "CacheLock":
        try:
            ensure_dir(self.path.parent)
        except OSError as exc:
            raise CacheError(
                f"Zwischenspeicher {self.path.parent} kann nicht angelegt werden.", str(exc)
            ) from exc
        for _ in range(2):
            try:
                descriptor = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(f"{os.getpid()}\n{time.time()}\n")
                self._acquired = True
                return self
            except FileExistsError:
                if self._is_stale():
                    log.warning("Verwaiste Sperrdatei %s wird entfernt", self.path)
                    try:
                        self.path.unlink()
                    except OSError:
                        pass
                    continue
                # Eine zweite Instanz aktualisiert gerade. Kein Fehler: der
                # Aufrufer arbeitet dann mit dem vorhandenen Cache weiter.
                log.info("Zwischenspeicher wird von einem anderen Prozess aktualisiert")
                return self
            except OSError as exc:
                raise CacheError("Sperrdatei nicht anlegbar.", str(exc)) from exc
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._acquired:
            try:
                self.path.unlink()
            except OSError:
                pass
            self._acquired = False

    @property
    def acquired(self) -> bool:
        return self._acquired

    def _is_stale(self) -> bool:
        try:
            age = time.time() - self.path.stat().st_mtime
        except OSError:
            return True
        return age > LOCK_STALE_SECONDS


class PackageCache:
    """Verwaltet ``<cache>/pkgdb/<arch>/``."""

    def __init__(self, root: Path | None = None, arch: str = "x86_64") -> None:
        self.arch = arch
        self.root = (root or cache_dir()) / "pkgdb" / arch

    # -- Pfade ----------------------------------------------------------------
    def _check_repo(self, repo: str) -> str:
        if not _SAFE_REPO.match(repo):
            # Repo-Namen kommen aus pacman.conf oder der Konfiguration; ein
            # Name mit Pfadtrennern duerfte nie einen Dateipfad bilden.
            raise CacheError(f"Unzulaessiger Repository-Name: {repo!r}")
        return repo

    def db_path(self, repo: str) -> Path:
        return self.root / f"{self._check_repo(repo)}.db"

    def meta_path(self, repo: str) -> Path:
        return self.root / f"{self._check_repo(repo)}.meta.json"

    def lock(self) -> CacheLock:
        return CacheLock(self.root / ".lock")

    # -- Lesen ----------------------------------------------------------------
    def load(self, repo: str) -> CacheEntry | None:
        """Gibt den Eintrag zurueck, wenn er vollstaendig und unversehrt ist."""
        db_path = self.db_path(repo)
        meta_path = self.meta_path(repo)
        if not db_path.is_file() or not meta_path.is_file():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Metadaten fuer %s unlesbar (%s), Eintrag verworfen", repo, exc)
            self.discard(repo)
            return None
        if not isinstance(meta, dict) or meta.get("schema_version") != SCHEMA_VERSION:
            log.info("Cache-Format fuer %s veraltet, Eintrag verworfen", repo)
            self.discard(repo)
            return None

        try:
            data = db_path.read_bytes()
        except OSError as exc:
            log.warning("Datenbank %s unlesbar (%s)", db_path, exc)
            return None

        digest = hashlib.sha256(data).hexdigest()
        if digest != meta.get("sha256"):
            # Halb geschriebene oder manipulierte Datei -- lieber neu laden.
            log.warning("Pruefsumme fuer %s stimmt nicht, Eintrag verworfen", repo)
            self.discard(repo)
            return None

        return CacheEntry(
            repo=repo,
            path=db_path,
            etag=meta.get("etag"),
            url=str(meta.get("url") or ""),
            last_modified=_parse_iso(meta.get("last_modified")),
            fetched_at=_parse_iso(meta.get("fetched_at")) or _now(),
            sha256=digest,
            size=len(data),
            package_count=int(meta.get("package_count") or 0),
        )

    # -- Schreiben ------------------------------------------------------------
    def store(
        self,
        repo: str,
        data: bytes,
        *,
        url: str,
        etag: str | None,
        last_modified: datetime | None,
        package_count: int,
    ) -> CacheEntry:
        db_path = self.db_path(repo)
        digest = hashlib.sha256(data).hexdigest()
        fetched = _now()

        self._write_atomic(db_path, data)
        self._write_atomic(
            self.meta_path(repo),
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "repo": repo,
                    "url": url,
                    "etag": etag,
                    "last_modified": _iso(last_modified),
                    "fetched_at": _iso(fetched),
                    "sha256": digest,
                    "size": len(data),
                    "package_count": package_count,
                },
                indent=2,
            ).encode("utf-8"),
        )
        return CacheEntry(
            repo=repo,
            path=db_path,
            etag=etag,
            url=url,
            last_modified=last_modified,
            fetched_at=fetched,
            sha256=digest,
            size=len(data),
            package_count=package_count,
        )

    def touch(self, entry: CacheEntry) -> CacheEntry:
        """Nach einer 304-Antwort: nur den Pruefzeitpunkt fortschreiben.

        ``last_modified`` bleibt unangetastet -- das ist der echte Repo-Stand
        und genau die Zahl, die dem Benutzer angezeigt wird.
        """
        fetched = _now()
        try:
            meta = json.loads(self.meta_path(entry.repo).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
        meta.update({"schema_version": SCHEMA_VERSION, "fetched_at": _iso(fetched)})
        self._write_atomic(self.meta_path(entry.repo), json.dumps(meta, indent=2).encode("utf-8"))
        # dataclasses.replace statt Neuaufbau: die frueher hier von Hand
        # aufgezaehlten Felder liessen 'url' aus, und nach jeder
        # 304-Antwort stand die Herkunft der Daten auf ''.
        return _replace(entry, fetched_at=fetched)

    def _write_atomic(self, path: Path, data: bytes) -> None:
        try:
            ensure_dir(path.parent)
        except OSError as exc:
            raise CacheError(f"Verzeichnis {path.parent} nicht anlegbar.", str(exc)) from exc

        handle = None
        try:
            handle = tempfile.NamedTemporaryFile(
                "wb", dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp", delete=False
            )
            with handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            # os.replace ist auch auf Windows atomar, wenn das Ziel existiert.
            os.replace(handle.name, path)
        except OSError as exc:
            if handle is not None:
                try:
                    os.unlink(handle.name)
                except OSError:
                    pass
            raise CacheError(f"Schreiben nach {path} fehlgeschlagen.", str(exc)) from exc

    # -- Aufraeumen -----------------------------------------------------------
    def discard(self, repo: str) -> None:
        for path in (self.db_path(repo), self.meta_path(repo)):
            try:
                path.unlink()
            except OSError:
                pass

    def clear(self) -> int:
        """Loescht den gesamten Zwischenspeicher. Immer unbedenklich."""
        removed = 0
        if not self.root.is_dir():
            return 0
        for path in self.root.iterdir():
            try:
                if path.is_file():
                    path.unlink()
                    removed += 1
            except OSError as exc:
                log.warning("%s nicht loeschbar: %s", path, exc)
        return removed
