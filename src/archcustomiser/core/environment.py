"""Erkennung der Bauumgebung (Spec Abschnitt 11).

Prueft, ob die Werkzeuge fuer einen ISO-Build vorhanden sind, und meldet
verstaendlich, was fehlt. Auf Windows -- wo entwickelt wird -- ist die Antwort
schlicht "hier nicht moeglich"; die GUI bleibt trotzdem vollstaendig bedienbar,
damit Konfiguration, Profile und Dry-Run getestet werden koennen.

Zwei Punkte, die aus der aktuellen archiso-Dokumentation stammen und leicht
uebersehen werden:

* **Root ist nicht mehr noetig.** Seit archiso 89 kapselt mkarchiso die
  privilegierten Schritte in ``unshare --map-auto --map-root-user``. Dafuer
  braucht der aufrufende Benutzer aber Eintraege in ``/etc/subuid`` und
  ``/etc/subgid`` -- fehlen die, scheitert der Build mit einer wenig
  aussagekraeftigen Meldung. Deshalb wird beides hier geprueft.
* **grub-mkstandalone wird nur fuer den Bootmodus ``uefi.grub`` gebraucht.**
  In mkarchiso ruft es ausschliesslich ``_make_bootmode_uefi.grub`` auf;
  ``grubenv`` und ``loopback.cfg`` entstehen dagegen in
  ``_make_common_grubenv_and_loopbackcfg`` per ``printf`` und ``sed``, also
  ohne grub. Wer systemd-boot waehlt, braucht das Paket nicht -- deshalb steht
  es nicht bei den unbedingt noetigen Werkzeugen.
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    package: str
    purpose: str
    required: bool = True
    path: str | None = None

    @property
    def found(self) -> bool:
        return self.path is not None


@dataclass(frozen=True, slots=True)
class Environment:
    platform: str
    can_build: bool
    tools: tuple[Tool, ...]
    problems: tuple[str, ...] = ()
    hints: tuple[str, ...] = ()
    privilege_mode: str = "unavailable"   # rootless | pkexec | root | unavailable
    subid_ok: bool = False
    userns_ok: bool = False
    pacman_available: bool = False

    @property
    def missing_required(self) -> tuple[Tool, ...]:
        return tuple(tool for tool in self.tools if tool.required and not tool.found)

    def install_hint(self) -> str:
        """Was der Benutzer tun kann -- oder eine ehrliche Auskunft.

        Frueher stand hier bedingungslos ein pacman-Befehl. Auf Ubuntu oder
        Fedora lautete die Empfehlung damit sinngemaess "installiere pacman mit
        pacman" -- ein Befehl, der genau daran scheitert, dass pacman fehlt.
        Das Feld ``pacman_available`` unterscheidet den Fall seit jeher; es
        wurde nur nirgends gelesen.
        """
        packages = sorted({tool.package for tool in self.missing_required})
        if not packages:
            return ""
        if self.pacman_available:
            return "sudo pacman -S --needed " + " ".join(packages)
        return (
            "Diese Werkzeuge stammen aus Arch Linux und stehen auf dieser "
            "Verteilung nicht zur Verfuegung. Es gibt zwei Wege: den Bau in "
            "einem Container mit dem archlinux-Abbild, oder das erzeugte Profil "
            "auf ein Arch-System uebertragen und dort bauen."
        )

    def summary(self) -> str:
        if self.can_build:
            return f"Bereit zum Bauen ({self.privilege_mode})."
        if self.problems:
            return self.problems[0]
        return "ISO-Build auf diesem System nicht moeglich."


# Host-Werkzeuge laut archiso README.rst. "package" nennt das Arch-Paket,
# damit die Fehlermeldung direkt einen Installationsbefehl anbieten kann.
_REQUIRED_TOOLS: tuple[tuple[str, str, str], ...] = (
    ("mkarchiso", "archiso", "Erzeugt das ISO-Abbild"),
    ("pacman", "pacman", "Paketverwaltung"),
    ("pacstrap", "arch-install-scripts", "Installiert Pakete ins Abbild"),
    ("mksquashfs", "squashfs-tools", "Komprimiert das Dateisystem"),
    ("xorriso", "libisoburn", "Schreibt die ISO-9660-Struktur"),
    ("mkfs.fat", "dosfstools", "Erzeugt die EFI-Systempartition"),
    ("mcopy", "mtools", "Befuellt die EFI-Partition ohne mount"),
    ("mmd", "mtools", "Legt Verzeichnisse in der EFI-Partition an"),
    ("gzip", "gzip", "Kompression"),
    ("bsdtar", "libarchive", "Archivverarbeitung"),
    ("awk", "awk", "Textverarbeitung in mkarchiso"),
    ("openssl", "openssl", "Pruefsummen und Passwort-Hashes"),
)

# Nur unter einer bestimmten Auswahl noetig. Sie hier als "required" zu
# fuehren blockierte Builds, die einwandfrei durchgelaufen waeren: wer
# systemd-boot gewaehlt hatte, wurde ohne Grund am Bauen gehindert, weil das
# grub-Paket fehlte. Geprueft wird stattdessen in der Vorabpruefung, die die
# Bootmodi kennt.
CONDITIONAL_TOOLS: dict[str, tuple[str, str, str]] = {
    "uefi.grub": (
        "grub-mkstandalone",
        "grub",
        "Erzeugt das GRUB-Abbild fuer den UEFI-Start",
    ),
}

_OPTIONAL_TOOLS: tuple[tuple[str, str, str], ...] = (
    ("mkfs.erofs", "erofs-utils", "Alternative zu SquashFS"),
    ("qemu-system-x86_64", "qemu-base", "Testet die fertige ISO ohne echten Rechner"),
    ("fakeroot", "base-devel", "Aktualisiert Paketdaten ohne Root-Rechte"),
    ("archinstall", "archinstall", "Installationsprogramm fuer die Live-ISO"),
)


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


def _has_subid_entry(path: Path, user: str, uid: int) -> bool:
    """Prueft /etc/subuid bzw. /etc/subgid auf einen Eintrag mit >= 65536 IDs."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    for line in content.splitlines():
        parts = line.strip().split(":")
        if len(parts) != 3:
            continue
        owner, _, count = parts
        if owner not in (user, str(uid)):
            continue
        try:
            if int(count) >= 65536:
                return True
        except ValueError:
            continue
    return False


def detect_environment() -> Environment:
    """Untersucht das laufende System. Wirft nie."""
    platform = sys.platform

    if platform != "linux":
        return Environment(
            platform=platform,
            can_build=False,
            tools=(),
            problems=(
                "Direkt auf diesem System kann nicht gebaut werden -- archiso, "
                "pacman und mkarchiso sind Linux-Werkzeuge.",
            ),
            hints=(
                # Frueher stand hier "nur unter Linux moeglich". Seit es das
                # Container-Ziel gibt, ist das schlicht falsch: unter macOS baut
                # das Programm in einem Container. Dieser Text erscheint als
                # Statuszeile auf der Startseite -- er darf niemanden davon
                # abhalten, es zu versuchen.
                "Das Programm sucht sich beim Bauen selbst einen Weg -- unter "
                "macOS etwa einen Container mit dem archlinux-Abbild. "
                "Zusammenstellen, Profile und Paketpruefung funktionieren hier "
                "ohnehin vollstaendig.",
            ),
            privilege_mode="unavailable",
        )

    tools: list[Tool] = []
    for name, package, purpose in _REQUIRED_TOOLS:
        tools.append(Tool(name, package, purpose, True, shutil.which(name)))
    for name, package, purpose in _OPTIONAL_TOOLS:
        tools.append(Tool(name, package, purpose, False, shutil.which(name)))
    # Bedingt noetige Werkzeuge werden mit erfasst -- sichtbar in --check-env,
    # aber nicht blockierend. Ob sie gebraucht werden, weiss erst die
    # Vorabpruefung.
    for name, package, purpose in CONDITIONAL_TOOLS.values():
        tools.append(Tool(name, package, purpose, False, shutil.which(name)))

    problems: list[str] = []
    hints: list[str] = []

    missing = [tool for tool in tools if tool.required and not tool.found]
    if missing:
        names = ", ".join(tool.name for tool in missing)
        problems.append(f"Es fehlen benoetigte Werkzeuge: {names}")

    # Rootless-Build: User-Namespaces plus Sub-ID-Bereiche.
    max_userns = _read_int(Path("/proc/sys/user/max_user_namespaces"))
    userns_ok = max_userns is None or max_userns > 0
    if not userns_ok:
        hints.append(
            "User-Namespaces sind deaktiviert "
            "(/proc/sys/user/max_user_namespaces = 0). Der Build ohne Root-Rechte "
            "funktioniert damit nicht."
        )

    uid = os.getuid()  # type: ignore[attr-defined]
    try:
        import pwd

        user = pwd.getpwuid(uid).pw_name  # type: ignore[attr-defined]
    except Exception:
        user = os.environ.get("USER", "")

    subid_ok = uid == 0 or (
        _has_subid_entry(Path("/etc/subuid"), user, uid)
        and _has_subid_entry(Path("/etc/subgid"), user, uid)
    )
    if not subid_ok and uid != 0:
        hints.append(
            "Fuer einen Build ohne Root-Rechte werden Sub-ID-Bereiche benoetigt. "
            f"Einrichten mit: sudo usermod --add-subuids 100000-165535 "
            f"--add-subgids 100000-165535 {user or '<benutzer>'}"
        )

    if uid == 0:
        privilege_mode = "root"
    elif userns_ok and subid_ok:
        privilege_mode = "rootless"
    elif shutil.which("pkexec"):
        privilege_mode = "pkexec"
        hints.append(
            "Der Build laeuft ueber pkexec mit erhoehten Rechten, weil die "
            "Voraussetzungen fuer den rechtefreien Weg fehlen."
        )
    else:
        privilege_mode = "unavailable"
        problems.append(
            "Weder ein rechtefreier Build noch pkexec sind verfuegbar. "
            "Bitte polkit installieren oder Sub-ID-Bereiche einrichten."
        )

    environment = Environment(
        platform=platform,
        can_build=not problems,
        tools=tuple(tools),
        problems=tuple(problems),
        hints=tuple(hints),
        privilege_mode=privilege_mode,
        subid_ok=subid_ok,
        userns_ok=userns_ok,
        pacman_available=shutil.which("pacman") is not None,
    )
    log.info(
        "Bauumgebung: %s (Rechte: %s, %d von %d Werkzeugen gefunden)",
        "bereit" if environment.can_build else "nicht bereit",
        environment.privilege_mode,
        sum(1 for tool in tools if tool.found),
        len(tools),
    )
    return environment
