"""Der Bauablauf ueber WSL.

Der Unterschied zum lokalen Bau ist nicht das Ausfuehren -- das erledigt
``WslExecutionTarget`` -- sondern das **Uebertragen des Profils**.

Der naheliegende Weg waere, das Profil auf ein Windows-Laufwerk zu schreiben
und in WSL ueber ``/mnt/e/...`` darauf zuzugreifen. Das geht schief: unter
``/mnt`` liegt ein Windows-Dateisystem, und dort gibt es weder symbolische
Verknuepfungen noch Linux-Dateirechte. Ein archiso-Profil besteht zu einem
Drittel aus Verknuepfungen -- der Build braeche mitten im Kopieren ab.

Deshalb der Umweg ueber ein tar-Archiv: darin sind Verknuepfungen und Rechte
blosse Metadaten. Das Archiv wandert nach Windows, wird von WSL aus gelesen und
**innerhalb des Linux-Dateisystems** ausgepackt. Dort entstehen echte
Verknuepfungen.

Aus demselben Grund liegen Arbeits- und Ausgabeverzeichnis in Linux, nicht auf
``/mnt`` -- und erst die fertige ISO wird nach Windows kopiert, wo der Benutzer
sie erwartet.
"""

from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..archiso import TarSink
from ..archiso.quoting import shell_quote
from ..archiso.tree import ProfileTree
from .wsl import WslError, WslTarget

log = logging.getLogger(__name__)

BUILD_ROOT = ".cache/archcustomiser"
PROFILE_NAME = "profile"
EXTRACT_TIMEOUT = 900.0


@dataclass(frozen=True, slots=True)
class WslPaths:
    """Wo im Linux-Dateisystem gearbeitet wird."""

    root: PurePosixPath
    profile: PurePosixPath
    work: PurePosixPath
    out: PurePosixPath

    def as_strings(self) -> tuple[str, str, str]:
        return str(self.profile), str(self.work), str(self.out)


def prepare_paths(target: WslTarget, iso_name: str) -> WslPaths:
    """Legt die Verzeichnisstruktur in der Verteilung an."""
    home = target.home()
    safe = "".join(c for c in iso_name if c.isalnum() or c in "-_") or "profil"
    root = PurePosixPath(str(home)) / BUILD_ROOT / safe
    paths = WslPaths(
        root=root,
        profile=root / PROFILE_NAME,
        work=root / "work",
        out=root / "out",
    )
    result = target.run(["mkdir", "-p", str(paths.root), str(paths.out)])
    if not result.ok:
        raise WslError(
            f"Das Arbeitsverzeichnis {paths.root} konnte in WSL nicht angelegt werden.",
            result.stderr.strip(),
        )
    return paths


def transfer_profile(
    target: WslTarget, tree: ProfileTree, paths: WslPaths, iso_name: str
) -> None:
    """Bringt das Profil mit allen Verknuepfungen nach Linux.

    Der Weg ueber das Archiv ist keine Bequemlichkeit, sondern notwendig:
    direkt auf ein Windows-Laufwerk geschrieben verloere das Profil seine
    symbolischen Verknuepfungen, und mkarchiso koennte die Dienste nicht
    aktivieren.
    """
    handle = tempfile.NamedTemporaryFile(
        suffix=".tar.gz", prefix=f"{iso_name}-profil-", delete=False
    )
    handle.close()
    archive = Path(handle.name)

    try:
        TarSink(archive, root_name=PROFILE_NAME).write(tree)
        log.info("Profil gepackt: %s (%d KB)", archive, archive.stat().st_size // 1024)

        linux_archive = target.to_linux_path(archive)

        # Ein frueherer Lauf darf nicht durchschlagen. Schlaegt das Loeschen
        # fehl, packt tar in den alten Stand hinein und vermischt beide
        # Profile -- das faellt erst beim Bauen auf, wenn ueberhaupt.
        aufraeumen = target.run(["rm", "-rf", "--", str(paths.profile)])
        if not aufraeumen.ok:
            raise WslError(
                f"Das Profilverzeichnis {paths.profile} liess sich in WSL nicht "
                f"loeschen. Ein alter Stand wuerde sich mit dem neuen vermischen.",
                aufraeumen.stderr.strip() or f"Rueckgabewert {aufraeumen.returncode}",
            )

        result = target.run(
            ["tar", "xzf", linux_archive, "-C", str(paths.root)],
            timeout=EXTRACT_TIMEOUT,
        )
        if not result.ok:
            raise WslError(
                "Das Profil konnte in WSL nicht ausgepackt werden.",
                result.stderr.strip(),
            )

        # Gegenprobe: sind die Verknuepfungen tatsaechlich angekommen?
        check = target.run(
            ["sh", "-c", f"find {shell_quote(str(paths.profile))} -type l | wc -l"]
        )
        count = check.stdout.strip()
        expected = tree.symlink_count
        log.info("Profil ausgepackt: %s Verknuepfungen (erwartet %d)", count, expected)
        if expected and not (check.ok and count.isdigit()):
            # Genau der Fall, den die Gegenprobe abfangen soll: schlaegt der
            # find-Aufruf fehl oder liefert er nichts, galt die Uebertragung
            # bisher stillschweigend als in Ordnung.
            raise WslError(
                "Nach dem Auspacken liess sich nicht pruefen, ob die "
                "symbolischen Verknuepfungen angekommen sind. Ohne sie "
                "liessen sich die Systemdienste im Abbild nicht aktivieren.",
                f"find-Ausgabe={count!r} rueckgabewert={check.returncode}",
            )
        if count.isdigit() and expected and int(count) < expected:
            raise WslError(
                "Beim Uebertragen sind symbolische Verknuepfungen verlorengegangen. "
                "Ohne sie liessen sich die Systemdienste im fertigen Abbild nicht "
                "aktivieren.",
                f"gefunden={count} erwartet={expected} ziel={paths.profile}",
            )
    finally:
        try:
            archive.unlink()
        except OSError:
            pass


def cleanup(
    target: WslTarget,
    paths: WslPaths,
    *,
    keep_work_dir: bool = False,
    remove_output: bool = False,
) -> None:
    """Raeumt in der Verteilung auf.

    Das Arbeitsverzeichnis eines Desktop-Abbilds belegt schnell 30 GB in der
    virtuellen Platte von WSL -- und die gibt Windows nicht von selbst wieder
    frei.

    ``remove_output`` entfernt zusaetzlich die dort erzeugte ISO. Das geschieht
    **nur**, wenn sie zuvor erfolgreich nach Windows geholt wurde -- sonst
    waere die Arbeit einer halben Stunde verloren. Ohne diesen Schritt laege
    jede ISO doppelt vor und die virtuelle Platte liefe nach einigen Bauten
    unbemerkt voll.
    """
    targets = [str(paths.profile)]
    if not keep_work_dir:
        _loese_einhaengungen(target, paths)
        targets.append(str(paths.work))
    if remove_output:
        targets.append(str(paths.out))
    for path in targets:
        # --one-file-system: rm steigt sonst in eine noch eingehaengte
        # Dateisystemgrenze ab. Bei einem abgebrochenen pacstrap koennen unter
        # work/x86_64/airootfs noch proc, sys, devtmpfs, devpts, tmpfs und ein
        # Bind auf /run liegen -- ein rm -rf als root loescht dann Geraeteknoten
        # in /dev und den Inhalt von /run der Verteilung.
        result = target.run(
            ["rm", "-rf", "--one-file-system", "--", path], timeout=600.0
        )
        if not result.ok:
            log.warning("%s liess sich in WSL nicht loeschen: %s", path, result.stderr.strip())


def _loese_einhaengungen(target: WslTarget, paths: WslPaths) -> None:
    """Haengt aus, was ein abgebrochener pacstrap hinterlassen hat.

    ``pacstrap`` haengt acht Dateisysteme in den Zielbaum ein und loest sie in
    einer EXIT-Falle wieder. Beim Abbruch bekommen mkarchiso, pacstrap und
    pacman gleichzeitig ein Signal; laeuft pacman noch, scheitert das
    Aushaengen mit EBUSY und die Einhaengungen bleiben stehen.

    Fehler werden bewusst nur protokolliert: ist nichts eingehaengt, meldet
    ``umount`` das als Fehler, und ein Aufraeumen darf daran nicht scheitern.
    Wirkt es nicht, faengt ``--one-file-system`` beim Loeschen den Rest ab.
    """
    wurzel = f"{paths.work}/x86_64/airootfs"
    ergebnis = target.run(["umount", "-R", "--", wurzel], as_root=True, timeout=120.0)
    if not ergebnis.ok:
        log.debug(
            "Nichts auszuhaengen unter %s (%s)", wurzel, ergebnis.stderr.strip()
        )

