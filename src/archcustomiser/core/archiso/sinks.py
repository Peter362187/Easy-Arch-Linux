"""Ausgabewege fuer einen ``ProfileTree``.

``DirectorySink`` schreibt ein echtes Verzeichnis mit echten Symlinks -- der
natuerliche Weg auf Linux, wo direkt danach ``mkarchiso`` laufen kann.

``TarSink`` schreibt ein ``.tar.gz``. In einem tar-Archiv sind Symlinks und
Dateirechte blosse Metadaten und damit unabhaengig vom Dateisystem des Rechners.
Das ist der Weg, der auch unter Windows funktioniert: Archiv erzeugen, auf ein
Arch-System kopieren, entpacken, bauen.

Beide Senken schreiben erst vollstaendig und machen das Ergebnis dann in einem
Schritt sichtbar. Ein Abbruch hinterlaesst damit kein halbes Profil.
"""

from __future__ import annotations

import gzip
import io
import logging
import os
import shutil
import tarfile
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from .errors import SinkError, SymlinksUnsupportedError, TargetNotEmptyError
from .tree import ProfileTree

log = logging.getLogger(__name__)

MARKER_NAME = ".archcustomiser-profile"


def looks_like_ours(verzeichnis: Path) -> bool:
    """Ob ein Verzeichnis ein von diesem Programm erzeugtes Profil ist.

    Der Marker ist das sichere Kennzeichen; die zweite Bedingung faengt
    Profile aus einer frueheren Fassung ab, die ihn noch nicht trugen.

    Die Funktion steht hier auf Modulebene, weil auch ``build/targets.py`` sie
    braucht: dort wird vor dem rekursiven Loeschen geprueft, nicht vor dem
    Schreiben.
    """
    return (verzeichnis / MARKER_NAME).is_file() or (
        (verzeichnis / "profiledef.sh").is_file()
        and (verzeichnis / "airootfs").is_dir()
    )
PROGRESS_STEP = 25

ProgressCallback = Callable[[int, int], None]   # (erledigt, gesamt)


def _marker_content(iso_name: str) -> str:
    stamp = datetime.now(UTC).replace(microsecond=0).isoformat()
    return (
        "# Von ArchCustomiser erzeugtes archiso-Profil.\n"
        "# Diese Datei kennzeichnet das Verzeichnis als ueberschreibbar.\n"
        f"iso_name={iso_name}\n"
        f"created={stamp}\n"
    )


class Sink(Protocol):
    def write(self, tree: ProfileTree, *, progress: ProgressCallback | None = None) -> Path: ...


class DirectorySink:
    """Schreibt das Profil als Verzeichnis.

    Ueberschreibt nur, was erkennbar von diesem Programm stammt. Ein
    Zielverzeichnis wie ``Dokumente`` soll nicht versehentlich mit mehreren
    hundert Dateien ueberzogen werden.
    """

    def __init__(self, target: Path, *, iso_name: str = "profil", force: bool = False) -> None:
        self.target = Path(target)
        self.iso_name = iso_name
        self.force = force

    def write(self, tree: ProfileTree, *, progress: ProgressCallback | None = None) -> Path:
        self._check_target()

        # In ein Nachbarverzeichnis schreiben und erst am Ende umbenennen.
        parent = self.target.parent
        try:
            parent.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=f".{self.target.name}.", dir=str(parent)))
        except OSError as exc:
            raise SinkError(
                f"Das Verzeichnis {parent} ist nicht beschreibbar.", str(exc)
            ) from exc

        try:
            self._materialise(tree, staging, progress)
            (staging / MARKER_NAME).write_text(
                _marker_content(self.iso_name), encoding="utf-8"
            )
            self._swap(staging)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

        log.info("Profil geschrieben nach %s (%s)", self.target, tree.describe())
        return self.target

    # -- intern ---------------------------------------------------------------
    def _check_target(self) -> None:
        if not self.target.exists():
            return
        if not self.target.is_dir():
            raise SinkError(f"{self.target} ist eine Datei, kein Verzeichnis.")

        entries = list(self.target.iterdir())
        if not entries:
            return
        if self.force:
            return

        if not looks_like_ours(self.target):
            raise TargetNotEmptyError(str(self.target), len(entries))

    def _materialise(
        self, tree: ProfileTree, root: Path, progress: ProgressCallback | None
    ) -> None:
        total = tree.file_count + tree.symlink_count
        done = 0

        streng = _strenge_pfade(tree)

        for entry in tree.files.values():
            destination = root / entry.path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(entry.content)
            if entry.path in streng:
                # /etc/shadow traegt den Passwort-Hash. Die Rechte aus
                # tree.permissions wirken erst im fertigen Abbild (mkarchiso
                # kopiert das airootfs mit --no-preserve=mode); auf dem Host
                # lag die Datei bis hierher mit 0644 -- fuer jeden anderen
                # lokalen Benutzer lesbar, und in einem exportierten Profil
                # sogar weitergegeben. sha512crypt mit 5000 Runden ist offline
                # angreifbar.
                _nur_fuer_mich(destination)
            done += 1
            if progress and done % PROGRESS_STEP == 0:
                progress(done, total)

        for link in tree.symlinks.values():
            destination = root / link.path
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                if destination.exists() or destination.is_symlink():
                    destination.unlink()
                os.symlink(link.target, destination)
            except (OSError, NotImplementedError) as exc:
                # Windows verlangt dafuer den Entwicklermodus oder Adminrechte.
                raise SymlinksUnsupportedError(str(destination), str(exc)) from exc
            done += 1
            if progress and done % PROGRESS_STEP == 0:
                progress(done, total)

        if progress:
            progress(total, total)

    def _swap(self, staging: Path) -> None:
        if self.target.exists():
            backup = self.target.with_name(self.target.name + ".alt")
            shutil.rmtree(backup, ignore_errors=True)
            try:
                os.replace(self.target, backup)
            except OSError:
                # Unter Windows scheitert das, wenn eine Datei offen ist.
                shutil.rmtree(self.target, ignore_errors=True)
                backup = None      # type: ignore[assignment]
            try:
                os.replace(staging, self.target)
            finally:
                if backup is not None:
                    shutil.rmtree(backup, ignore_errors=True)
        else:
            os.replace(staging, self.target)


class TarSink:
    """Schreibt das Profil als ``.tar.gz``.

    Reproduzierbar: feste Zeitstempel, Eigentuemer 0:0, sortierte Eintraege.
    Zweimal erzeugen ergibt dieselben Bytes -- damit laesst sich vergleichen,
    was sich zwischen zwei Laeufen tatsaechlich geaendert hat.
    """

    def __init__(
        self,
        target: Path,
        *,
        root_name: str = "profil",
        mtime: int = 0,
        compresslevel: int = 6,
    ) -> None:
        self.target = Path(target)
        self.root_name = root_name.strip("/") or "profil"
        self.mtime = mtime
        self.compresslevel = compresslevel

    def write(self, tree: ProfileTree, *, progress: ProgressCallback | None = None) -> Path:
        payload = self.to_bytes(tree, progress=progress)
        try:
            self.target.parent.mkdir(parents=True, exist_ok=True)
            handle = tempfile.NamedTemporaryFile(
                "wb", dir=str(self.target.parent), prefix=f".{self.target.name}.", delete=False
            )
            with handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(handle.name, self.target)
        except OSError as exc:
            raise SinkError(f"{self.target} konnte nicht geschrieben werden.", str(exc)) from exc

        log.info("Archiv geschrieben: %s (%d KB)", self.target, len(payload) // 1024)
        return self.target

    def to_bytes(self, tree: ProfileTree, *, progress: ProgressCallback | None = None) -> bytes:
        """Das fertige Archiv im Speicher -- so testen die Tests es."""
        raw = io.BytesIO()
        total = tree.file_count + tree.symlink_count
        done = 0
        streng = _strenge_pfade(tree)

        with tarfile.open(fileobj=raw, mode="w", format=tarfile.GNU_FORMAT) as archive:
            root = tarfile.TarInfo(self.root_name)
            root.type = tarfile.DIRTYPE
            root.mode = 0o755
            root.mtime = self.mtime
            archive.addfile(root)

            for directory in self._directories(tree):
                info = tarfile.TarInfo(f"{self.root_name}/{directory}")
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                info.mtime = self.mtime
                archive.addfile(info)

            for path in tree.paths():
                entry = tree.files.get(path)
                if entry is not None:
                    info = tarfile.TarInfo(f"{self.root_name}/{path}")
                    info.size = len(entry.content)
                    # Dieselbe Ueberlegung wie bei der Verzeichnis-Senke: ein
                    # exportiertes Profil wird weitergegeben, und /etc/shadow
                    # darf darin nicht weltlesbar liegen. Der feste Modus
                    # bleibt sonst erhalten -- er haelt das Archiv bytegleich
                    # reproduzierbar.
                    info.mode = 0o600 if path in streng else 0o644
                    info.mtime = self.mtime
                    archive.addfile(info, io.BytesIO(entry.content))
                else:
                    link = tree.symlinks[path]
                    info = tarfile.TarInfo(f"{self.root_name}/{path}")
                    info.type = tarfile.SYMTYPE
                    info.linkname = link.target
                    info.mode = 0o777
                    info.mtime = self.mtime
                    archive.addfile(info)
                done += 1
                if progress and done % PROGRESS_STEP == 0:
                    progress(done, total)

        if progress:
            progress(total, total)

        # mtime=0 im gzip-Kopf, sonst waere das Ergebnis nie bytegleich.
        compressed = io.BytesIO()
        with gzip.GzipFile(
            fileobj=compressed, mode="wb", compresslevel=self.compresslevel, mtime=0
        ) as gz:
            gz.write(raw.getvalue())
        return compressed.getvalue()

    @staticmethod
    def _directories(tree: ProfileTree) -> tuple[str, ...]:
        found: set[str] = set()
        for path in tree.paths():
            parts = path.split("/")[:-1]
            for index in range(1, len(parts) + 1):
                found.add("/".join(parts[:index]))
        return tuple(sorted(found))


def _strenge_pfade(tree: ProfileTree) -> frozenset[str]:
    """Die Baumpfade, deren Rechte im Abbild keinen Fremdzugriff zulassen.

    ``tree.permissions`` ist nach den Pfaden *im Abbild* geschluesselt
    (``/etc/shadow``), die Dateien liegen im Baum aber unter ``airootfs/...``.
    Diese Funktion bildet das eine auf das andere ab.

    Beruecksichtigt wird alles, was fuer Gruppe und andere keinerlei Recht
    vorsieht -- also 0400, 0600 und 0700. Genau dort stehen die Geheimnisse.
    """
    streng: set[str] = set()
    for abbildpfad, eintrag in tree.permissions.items():
        try:
            rechte = int(str(eintrag.mode), 8)
        except (TypeError, ValueError):
            continue
        if rechte & 0o077:
            continue
        baumpfad = "airootfs/" + str(abbildpfad).lstrip("/")
        if baumpfad in tree.files:
            streng.add(baumpfad)
    return frozenset(streng)


def _nur_fuer_mich(pfad: Path) -> None:
    """Entzieht Gruppe und anderen jedes Recht -- soweit das System das kennt.

    Unter Windows gibt es keine POSIX-Modi; dort ist der Aufruf wirkungslos
    und darf deshalb nicht scheitern.
    """
    if os.name == "nt":
        return
    try:
        pfad.chmod(0o600)
    except OSError:
        log.debug("Rechte von %s liessen sich nicht einschraenken", pfad, exc_info=True)
