"""Pruefungen vor dem Start eines Builds.

Ein ISO-Build dauert je nach Auswahl zwanzig Minuten bis eine Stunde. Fehler,
die sich vorher erkennen lassen, jetzt zu melden ist ungleich freundlicher, als
den Benutzer eine halbe Stunde warten und dann scheitern zu lassen.

Geprueft wird deshalb alles, was ohne Ausfuehrung feststellbar ist: Werkzeuge,
Rechte, Plattenplatz, Dateisystem und ein bereits vorhandenes
Arbeitsverzeichnis.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from ..environment import CONDITIONAL_TOOLS, Environment, detect_environment
from .errors import PreflightError

log = logging.getLogger(__name__)

# Erfahrungswerte. Das Arbeitsverzeichnis enthaelt zeitweise das entpackte
# System UND das komprimierte Abbild UND die ISO-Struktur.
# Name der Beanstandung, die bedeutet: auf DIESEM Rechner geht es grundsaetzlich
# nicht -- kein fehlendes Paket, das man nachinstallieren koennte. Die
# Oberflaeche bietet daraufhin den Profil-Export an. Als Konstante, weil die GUI
# frueher an einem Zeichenketten-Literal aus diesem Modul hing.
NOT_BUILDABLE_HERE = "Bauumgebung"

BASE_WORK_GB = 8.0
SIZE_FACTOR = 4.0


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    ok: bool
    detail: str = ""
    fatal: bool = True


@dataclass(slots=True)
class PreflightReport:
    checks: list[Check] = field(default_factory=list)
    environment: Environment | None = None
    privilege_mode: str = "unavailable"
    estimated_work_gb: float = BASE_WORK_GB

    @property
    def blocking(self) -> list[Check]:
        return [check for check in self.checks if not check.ok and check.fatal]

    @property
    def warnings(self) -> list[Check]:
        return [check for check in self.checks if not check.ok and not check.fatal]

    @property
    def ok(self) -> bool:
        return not self.blocking

    def raise_if_blocked(self) -> None:
        if self.ok:
            return
        first = self.blocking[0]
        raise PreflightError(
            first.detail or f"{first.name} ist nicht erfuellt.",
            tuple(check.detail for check in self.blocking[1:]),
            technical="; ".join(f"{c.name}: {c.detail}" for c in self.blocking),
        )


def estimate_work_space_gb(installed_mb: int) -> float:
    """Wie viel Platz das Arbeitsverzeichnis braucht.

    Faustregel: das entpackte System liegt dort einmal vollstaendig, dazu das
    komprimierte Abbild und die ISO-Struktur. Vier mal die geschaetzte
    Installationsgroesse plus ein Sockel ist erfahrungsgemaess knapp genug, um
    zu warnen, und grosszuegig genug, um nicht zu nerven.
    """
    return BASE_WORK_GB + (installed_mb / 1024.0) * SIZE_FACTOR


def free_space_gb(path: Path) -> float | None:
    """Freier Platz an der naechsten existierenden Stelle des Pfades."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free / 1_073_741_824
    except OSError:
        return None


def run_preflight(
    work_dir: Path,
    out_dir: Path,
    *,
    installed_mb: int = 0,
    bootmodes: Sequence[str] = (),
    environment: Environment | None = None,
) -> PreflightReport:
    """Sammelt alle Befunde, ohne beim ersten Problem abzubrechen.

    Der Benutzer soll alles auf einmal sehen und nicht nach jeder Korrektur
    einen neuen Fehler praesentiert bekommen.
    """
    env = environment or detect_environment()
    report = PreflightReport(environment=env, privilege_mode=env.privilege_mode)
    needed = estimate_work_space_gb(installed_mb)
    report.estimated_work_gb = needed

    # -- Plattform ------------------------------------------------------------
    if sys.platform != "linux":
        report.checks.append(
            Check(
                NOT_BUILDABLE_HERE,
                False,
                "Direkt auf diesem System laeuft kein Bau -- archiso, pacman "
                "und mkarchiso sind Linux-Werkzeuge. Der Bau kann stattdessen "
                "in einem Container mit dem archlinux-Abbild laufen, oder das "
                "erzeugte Profil wird auf ein Arch-System uebertragen.",
            )
        )
        return report

    # -- Ist das ueberhaupt ein Arch-System? ----------------------------------
    # Ohne pacman fehlen nicht einzelne Werkzeuge, sondern die Grundlage. Das
    # ist keine Beanstandung, die der Benutzer auf diesem Rechner beheben
    # koennte -- deshalb dieselbe Kennzeichnung wie bei einem Nicht-Linux, damit
    # die Oberflaeche den Ausweg anbietet statt eines ausgegrauten Knopfes.
    if not env.pacman_available:
        report.checks.append(
            Check(
                NOT_BUILDABLE_HERE,
                False,
                "Dieses System ist kein Arch Linux -- pacman und archiso fehlen, "
                "und archiso ist in keiner anderen Verteilung paketiert. Der Bau "
                "kann stattdessen in einem Container laufen, oder das erzeugte "
                "Profil wird auf ein Arch-System uebertragen.",
            )
        )
        return report

    # -- Werkzeuge ------------------------------------------------------------
    missing = [tool.name for tool in env.missing_required]
    report.checks.append(
        Check(
            "Werkzeuge",
            not missing,
            (
                f"Es fehlen: {', '.join(missing)}.\n{env.install_hint()}"
                if missing
                else f"{len(env.tools)} geprueft"
            ),
        )
    )

    # -- Werkzeuge, die nur unter bestimmter Auswahl noetig sind --------------
    fehlend_bedingt = _missing_conditional_tools(env, bootmodes)
    if fehlend_bedingt:
        report.checks.append(
            Check(
                "Bootloader-Werkzeuge",
                False,
                "Fuer den gewaehlten Startmodus fehlt: "
                + ", ".join(f"{name} (Paket {paket})" for name, paket in fehlend_bedingt)
                + ". Nachinstallieren mit: sudo pacman -S --needed "
                + " ".join(sorted({paket for _name, paket in fehlend_bedingt})),
            )
        )

    # -- Rechte ---------------------------------------------------------------
    if env.privilege_mode == "unavailable":
        report.checks.append(
            Check(
                "Rechte",
                False,
                "Weder ein Build ohne Administratorrechte noch pkexec sind "
                "verfuegbar. Entweder Sub-ID-Bereiche einrichten oder polkit "
                "installieren.",
            )
        )
    else:
        report.checks.append(
            Check("Rechte", True, f"Modus: {env.privilege_mode}", fatal=False)
        )
        if env.privilege_mode == "pkexec":
            report.checks.append(
                Check(
                    "Rechte ohne Root",
                    False,
                    "Der Build laeuft mit erhoehten Rechten ueber pkexec, weil "
                    "Sub-ID-Bereiche fehlen. Ohne Root ginge es mit: "
                    "sudo usermod --add-subuids 100000-165535 "
                    "--add-subgids 100000-165535 $USER",
                    fatal=False,
                )
            )

    # Die naechsten vier gelten unabhaengig vom Ziel: sie betreffen das
    # Dateisystem dieses Rechners. Beim Bau im Container gilt dasselbe, weil das
    # Arbeitsverzeichnis dorthin eingehaengt wird -- deshalb stehen sie als
    # eigene Funktionen da und nicht dreimal im Text.
    _check_space(report, work_dir, needed)
    _check_filesystem(report, work_dir)
    _check_existing_work_dir(report, work_dir)
    _check_output_writable(report, out_dir)

    log.info(
        "Vorabpruefung: %s (%d Beanstandungen, %d Hinweise)",
        "bestanden" if report.ok else "nicht bestanden",
        len(report.blocking),
        len(report.warnings),
    )
    return report


def _check_space(report: PreflightReport, work_dir: Path, needed: float) -> None:
    available = free_space_gb(work_dir)
    if available is None:
        report.checks.append(
            Check("Plattenplatz", False, f"{work_dir} ist nicht erreichbar.")
        )
    elif available < needed:
        report.checks.append(
            Check(
                "Plattenplatz",
                False,
                f"In {work_dir} sind {available:.1f} GB frei, gebraucht werden "
                f"etwa {needed:.0f} GB.",
            )
        )
    else:
        report.checks.append(
            Check(
                "Plattenplatz",
                True,
                f"{available:.0f} GB frei, ~{needed:.0f} GB noetig",
                fatal=False,
            )
        )


def _check_filesystem(report: PreflightReport, work_dir: Path) -> None:
    """Das Arbeitsverzeichnis braucht erweiterte Attribute und echte
    Benutzerkennungen. Auf FAT oder NTFS scheitert der Bau mitten im Entpacken
    der Pakete."""
    filesystem = _filesystem_of(work_dir)
    if filesystem and filesystem.lower() in ("vfat", "fat32", "exfat", "ntfs", "fuseblk"):
        report.checks.append(
            Check(
                "Dateisystem",
                False,
                f"{work_dir} liegt auf {filesystem}. Das Arbeitsverzeichnis braucht "
                f"ein Linux-Dateisystem (ext4, btrfs, xfs) -- sonst gehen "
                f"Dateirechte und Eigentuemer beim Entpacken verloren.",
            )
        )
    elif filesystem:
        report.checks.append(Check("Dateisystem", True, filesystem, fatal=False))


def _check_existing_work_dir(report: PreflightReport, work_dir: Path) -> None:
    if work_dir.exists() and any(work_dir.iterdir()):
        report.checks.append(
            Check(
                "Arbeitsverzeichnis",
                False,
                f"{work_dir} ist nicht leer. mkarchiso setzt einen frueheren Lauf "
                f"fort und uebernimmt dabei dessen Baudatum -- das Ergebnis "
                f"koennte veraltete Teile enthalten. Besser vorher loeschen.",
                fatal=False,
            )
        )


def _check_output_writable(report: PreflightReport, out_dir: Path) -> None:
    probe = out_dir
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if not os.access(probe, os.W_OK):
        report.checks.append(
            Check("Ausgabeverzeichnis", False, f"Keine Schreibrechte in {probe}.")
        )


def _missing_conditional_tools(
    env: Environment, bootmodes: Sequence[str]
) -> list[tuple[str, str]]:
    """Werkzeuge, die nur unter der getroffenen Auswahl gebraucht werden.

    ``grub-mkstandalone`` stand frueher bei den unbedingt noetigen Werkzeugen.
    Das blockierte Builds, die einwandfrei durchgelaufen waeren: mkarchiso ruft
    es ausschliesslich fuer den Bootmodus ``uefi.grub`` auf, wer systemd-boot
    gewaehlt hat braucht das Paket nie.
    """
    vorhanden = {tool.name for tool in env.tools if tool.found}
    fehlend: list[tuple[str, str]] = []
    for bootmode in bootmodes:
        eintrag = CONDITIONAL_TOOLS.get(bootmode)
        if eintrag is None:
            continue
        name, paket, _zweck = eintrag
        if name not in vorhanden:
            fehlend.append((name, paket))
    return fehlend


def _filesystem_of(path: Path) -> str:
    """Dateisystemtyp aus /proc/mounts -- ohne Fremdbibliothek.

    Gesucht wird der laengste passende Einhaengepunkt, weil verschachtelte
    Einhaengungen sonst falsch zugeordnet wuerden.
    """
    mounts = Path("/proc/mounts")
    if not mounts.is_file():
        return ""

    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        resolved = probe.resolve()
    except OSError:
        return ""

    best = ""
    best_length = -1
    try:
        for line in mounts.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            mount_point, filesystem = parts[1], parts[2]
            try:
                point = Path(mount_point)
            except ValueError:
                continue
            if resolved == point or point in resolved.parents:
                if len(mount_point) > best_length:
                    best, best_length = filesystem, len(mount_point)
    except OSError:
        return ""
    return best


# ---------------------------------------------------------------------------
# Vorabpruefung fuer einen Bau in WSL
# ---------------------------------------------------------------------------


def run_wsl_preflight(
    wsl_target,
    out_dir: Path,
    *,
    installed_mb: int = 0,
    bootmodes: Sequence[str] = (),
) -> PreflightReport:
    """Prueft die WSL-Verteilung statt des Windows-Systems.

    Beim Bau in WSL liegen Werkzeuge, Plattenplatz und Rechte alle drueben.
    Das Windows-System zu pruefen waere irrefuehrend -- dort fehlt mkarchiso
    zwangslaeufig, ohne dass das ein Hindernis waere.
    """
    from .wsl import check_readiness

    needed = estimate_work_space_gb(installed_mb)
    report = PreflightReport(privilege_mode="rootless", estimated_work_gb=needed)
    readiness = check_readiness(wsl_target, needed_gb=needed)

    # Frueher kannte diese Fassung die Bootmodi gar nicht -- ein Bau mit
    # uefi.grub ohne grub-mkstandalone lief deshalb an und scheiterte erst
    # mitten in der ISO-Erzeugung.
    for bootmode in bootmodes:
        eintrag = CONDITIONAL_TOOLS.get(bootmode)
        if eintrag is None:
            continue
        werkzeug, paket, _zweck = eintrag
        if not wsl_target.has_command(werkzeug):
            report.checks.append(
                Check(
                    "Bootloader-Werkzeuge",
                    False,
                    f"Fuer den gewaehlten Startmodus fehlt {werkzeug} in der "
                    f"Verteilung. Nachinstallieren mit: "
                    f"sudo pacman -S --needed {paket}",
                )
            )

    report.checks.append(
        Check(
            "Linux-Verteilung",
            readiness.is_arch,
            f"{wsl_target.distribution}"
            + ("" if readiness.is_arch else " -- kein Arch Linux"),
        )
    )
    if not readiness.is_arch:
        return report

    report.checks.append(
        Check(
            "archiso",
            readiness.has_archiso,
            "vorhanden"
            if readiness.has_archiso
            else "fehlt. In der Verteilung installieren mit:\n"
                 "sudo pacman -Syu --needed archiso",
        )
    )
    report.checks.append(
        Check("tar", readiness.has_tar, "vorhanden" if readiness.has_tar else "fehlt", )
    )

    if readiness.free_gb is not None:
        enough = readiness.free_gb >= needed
        report.checks.append(
            Check(
                "Plattenplatz",
                enough,
                f"{readiness.free_gb:.0f} GB frei in der Verteilung, "
                f"~{needed:.0f} GB noetig"
                + (
                    ""
                    if enough
                    else ". WSL legt seine virtuelle Platte auf C: ab -- dort muss "
                         "der Platz vorhanden sein."
                ),
            )
        )

    # Ohne Sub-IDs laeuft der Build mit erhoehten Rechten weiter, das ist kein
    # Hindernis -- aber der Hinweis spart eine Rueckfrage.
    report.checks.append(
        Check(
            "Rechte ohne Root",
            readiness.subid_ready,
            "eingerichtet"
            if readiness.subid_ready
            else "Sub-ID-Bereiche fehlen. Einmalig in der Verteilung:\n"
                 "sudo usermod --add-subuids 100000-165535 "
                 "--add-subgids 100000-165535 $USER",
            fatal=False,
        )
    )
    if not readiness.userns_ready:
        report.checks.append(
            Check(
                "User-Namespaces",
                False,
                "In dieser Verteilung sind User-Namespaces abgeschaltet.",
                fatal=False,
            )
        )

    # Das Ausgabeverzeichnis liegt auf der Windows-Seite.
    probe = out_dir
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    writable = os.access(probe, os.W_OK)
    report.checks.append(
        Check(
            "Ausgabeverzeichnis",
            writable,
            str(out_dir) if writable else f"Keine Schreibrechte in {probe}.",
        )
    )
    return report


def _rootless_remedy(engine: str) -> str:
    """Was zu tun ist, wenn der Container nicht einhaengen darf.

    Fast immer ist die Ursache dieselbe: die Engine laeuft rootless. Das ist
    die Vorgabe von podman fuer normale Benutzer und auf den meisten Systemen
    genau richtig -- nur nicht fuer pacstrap, das echte Mounts braucht.
    """
    if engine == "podman":
        hilfe = (
            "podman laeuft vermutlich rootless. Der Bau braucht echte "
            "Einhaengerechte -- entweder mit 'sudo' starten, oder podman "
            "rootful einrichten."
        )
    else:
        hilfe = (
            "docker erreicht den Dienst nicht mit den noetigen Rechten. "
            "Meist hilft, den eigenen Benutzer in die Gruppe 'docker' "
            "aufzunehmen:\nsudo usermod -aG docker $USER\n"
            "Danach einmal ab- und wieder anmelden."
        )
    return (
        "Der Container darf nicht einhaengen (nachgeprueft mit devtmpfs). "
        "pacstrap wuerde mitten im Bau abbrechen.\n" + hilfe
    )


def run_container_preflight(
    container,
    work_dir: Path,
    out_dir: Path,
    *,
    installed_mb: int = 0,
    bootmodes: Sequence[str] = (),
) -> PreflightReport:
    """Prueft den Bau in einem Container.

    Eine Mischung aus beiden bisherigen Fassungen, und das ist kein Zufall: die
    Werkzeuge kommen aus dem Abbild (Ziel-Fakt), Plattenplatz und Dateisystem
    liegen dagegen auf dem Host, weil das Arbeitsverzeichnis dorthin eingehaengt
    wird (Host-Fakten). Beim WSL-Bau war beides drueben, lokal beides hier.
    """
    from .container import ContainerError

    report = PreflightReport(privilege_mode="container")
    needed = estimate_work_space_gb(installed_mb)
    report.estimated_work_gb = needed

    # -- Engine ---------------------------------------------------------------
    try:
        vorhanden = container.image_exists()
    except ContainerError as exc:
        report.checks.append(
            Check(
                NOT_BUILDABLE_HERE,
                False,
                f"{container.engine} laesst sich nicht aufrufen. {exc.user_message}",
            )
        )
        return report

    report.checks.append(
        Check("Container", True, f"{container.engine} bereit", fatal=False)
    )
    report.checks.append(
        Check(
            "Abbild",
            True,
            (
                f"{container.image} liegt bereit"
                if vorhanden
                else "wird beim ersten Bau erzeugt -- dabei werden einige "
                "hundert MB geladen"
            ),
            fatal=False,
        )
    )

    # -- Rechte ---------------------------------------------------------------
    # Bis zum 07.09.2026 stand hier eine reine Zusicherung: "der Container
    # laeuft privilegiert". Ob das auf DIESEM System auch reicht, hat niemand
    # nachgesehen -- und meistens reicht es nicht. Auf einem normalen Ubuntu
    # laeuft podman als normaler Benutzer rootless, und dort gibt --privileged
    # alle Faehigkeiten nur innerhalb des Benutzer-Namensraums. Der Bau lief
    # dann bis pacstrap und starb dort am ersten Mount.
    #
    # Die Probe kostet zwei Sekunden, braucht aber das Abbild. Fehlt es noch,
    # wird nichts behauptet -- die Vorabpruefung darf keine 800 MB laden.
    if vorhanden:
        try:
            darf_einhaengen = container.can_mount_privileged()
        except ContainerError:
            darf_einhaengen = False
        if darf_einhaengen:
            report.checks.append(
                Check(
                    "Rechte",
                    True,
                    "Der Container darf einhaengen (nachgeprueft). Das braucht "
                    "pacstrap, um das System im Abbild aufzubauen; Arch baut "
                    "seine eigenen ISOs genauso.",
                )
            )
        else:
            report.checks.append(
                Check(
                    "Rechte",
                    False,
                    _rootless_remedy(container.engine),
                )
            )
    else:
        report.checks.append(
            Check(
                "Rechte",
                True,
                "Der Container laeuft privilegiert (--privileged). Ob das hier "
                "ausreicht, laesst sich erst mit dem Abbild pruefen.",
                fatal=False,
            )
        )

    # -- Host-Fakten: Platz, Dateisystem, Schreibrechte -----------------------
    _check_space(report, work_dir, needed)
    _check_filesystem(report, work_dir)
    _check_existing_work_dir(report, work_dir)
    _check_output_writable(report, out_dir)

    # -- Bedingte Bootlader-Werkzeuge ----------------------------------------
    # Das Abbild bringt archiso mit, aber nicht zwingend grub.
    for bootmode in bootmodes:
        eintrag = CONDITIONAL_TOOLS.get(bootmode)
        if eintrag is None:
            continue
        werkzeug, paket, _zweck = eintrag
        try:
            fehlt = vorhanden and not container.has_command(werkzeug)
        except ContainerError:
            fehlt = False
        if fehlt:
            report.checks.append(
                Check(
                    "Bootloader-Werkzeuge",
                    False,
                    f"Im Abbild fehlt {werkzeug} (Paket {paket}). Das Abbild "
                    f"muss neu gebaut werden.",
                )
            )

    return report
