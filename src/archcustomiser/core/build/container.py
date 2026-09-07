"""Bauen in einem Container mit dem offiziellen archlinux-Abbild.

Der Gedanke ist derselbe wie bei WSL: **nicht das Zielsystem arch-faehig
machen, sondern ein Arch danebenstellen.** Unter Windows ist das eine
WSL-Verteilung, unter Ubuntu, Fedora oder macOS ein Container.

Das ist der einzige Weg, der auf einem Nicht-Arch-Linux ueberhaupt in Frage
kommt: ``archiso`` ist in keiner anderen Verteilung paketiert -- Debian,
Ubuntu, Fedora und openSUSE fuehren es schlicht nicht. ``pacman`` und
``arch-install-scripts`` gibt es dort zwar, aber ohne ``mkarchiso`` nuetzt das
nichts.

**Warum ``--privileged`` noetig ist.** Nicht wegen mkarchiso -- das ruft weder
``mount`` noch ``losetup`` noch ``mknod`` auf; das EFI-Abbild entsteht ueber
``mkfs.fat`` auf einer Datei und wird mit mtools befuellt. Der Grund ist
``pacstrap``: dessen ``chroot_setup`` haengt acht Dateisysteme in den Zielbaum
ein (proc, sysfs, devtmpfs, devpts, tmpfs, Bind auf /run), und die brauchen
alle ``CAP_SYS_ADMIN``.

**Warum rootless nicht reicht**, obwohl es verlockend waere: ``devtmpfs`` hat
im Kernel kein ``FS_USERNS_MOUNT``-Flag und laesst sich in einem
User-Namespace grundsaetzlich nicht einhaengen. Das ist keine Nachlaessigkeit
von podman, sondern eine Eigenschaft des Kernels -- und deshalb wird es dem
Benutzer gesagt, statt es zu verstecken.

Dass der Weg trotzdem tragfaehig ist, zeigt Arch selbst: die offizielle
archinstall-ISO wird in GitHub Actions mit genau dieser Kombination gebaut --
``archlinux/archlinux:latest`` mit ``--privileged``.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Sequence

from .errors import BuildError

log = logging.getLogger(__name__)

# Unter Windows oeffnet jeder Unterprozess sonst kurz ein schwarzes
# Konsolenfenster -- bei einem Bau mit vielen Aufrufen flackert der
# Bildschirm. Auf allen anderen Systemen ist die Kennzahl 0, also wirkungslos.
KEIN_FENSTER = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# podman zuerst: kein Hintergrunddienst, unter Fedora vorinstalliert, und der
# Container laeuft als der aufrufende Benutzer. Dockers Daemon laeuft als root,
# die fertige ISO gehoert dann root, und --privileged bedeutet dort echtes
# Host-Root. Deshalb geduldet, nicht empfohlen.
ENGINES = ("podman", "docker")

BASE_IMAGE = "docker.io/library/archlinux:latest"
LOCAL_IMAGE = "localhost/archcustomiser-archiso:latest"

# Das Abbild einmal bauen und behalten. Die Alternative -- bei jedem Bau
# "pacman -Sy archiso" im Container -- kostet jedes Mal mehrere hundert MB und
# einige Minuten, und ohne Netz ginge gar nichts mehr.
#
# ``grub`` gehoert dazu, obwohl archiso es nicht mitzieht: der Bootmodus
# ``uefi.grub`` braucht ``grub-mkstandalone`` (siehe CONDITIONAL_TOOLS in
# environment.py). Ohne das Paket brach mkarchiso beim ersten echten Lauf am
# 07.09.2026 mit "grub-install is not available on this host" ab -- und die
# Vorabpruefung haette dem Benutzer gesagt, er solle das Abbild neu bauen,
# obwohl genau dieses Abbild dabei wieder ohne grub entstanden waere. Eine
# Sackgasse fuer zwoelf Megabyte.
CONTAINERFILE = f"""FROM {BASE_IMAGE}
RUN pacman -Sy --noconfirm --needed archiso grub && pacman -Scc --noconfirm
"""

DEFAULT_TIMEOUT = 120.0
IMAGE_BUILD_TIMEOUT = 1800.0


class ContainerError(BuildError):
    """Etwas mit der Container-Umgebung stimmt nicht.

    Erbt bewusst von ``BuildError``: ``controller.run`` faengt nur
    ``(BuildError, ProfileError)``, und genau daran haengt das Schreiben der
    Protokolldatei. Als blosse ``Exception`` rutschte ein gescheiterter
    Container-Bau daran vorbei -- der Benutzer bekam eine Fehlermeldung, aber
    kein Protokoll, mit dem sich etwas anfangen liesse.
    """

    def __init__(self, user_message: str, technical: str = "") -> None:
        super().__init__(user_message, technical)
        # BuildError setzt technical auf user_message, wenn nichts angegeben
        # ist. Hier soll ein leerer technischer Teil leer bleiben.
        self.technical = technical


@dataclass(frozen=True, slots=True)
class ContainerResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass(slots=True)
class ContainerStatus:
    """Was auf diesem Rechner an Container-Werkzeug vorliegt."""

    engine: str | None = None
    version: str = ""
    rootless: bool = False
    image_ready: bool = False
    problem: str = ""
    remedy: str = ""

    @property
    def usable(self) -> bool:
        return self.engine is not None and not self.problem


def find_engine() -> str | None:
    """Die erste verfuegbare Engine -- podman vor docker."""
    for name in ENGINES:
        if shutil.which(name):
            return name
    return None


def _run(argv: Sequence[str], *, timeout: float = DEFAULT_TIMEOUT) -> ContainerResult:
    try:
        fertig = subprocess.run(
            [str(item) for item in argv],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            shell=False,
            creationflags=KEIN_FENSTER,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ContainerError(
            "Der Container-Aufruf ist fehlgeschlagen.", str(exc)
        ) from exc
    return ContainerResult(fertig.returncode, fertig.stdout, fertig.stderr)


def install_hint_for_this_system() -> str:
    """Der Befehl, der auf DIESEM System zum Ziel fuehrt.

    Frueher stand hier fest "sudo apt install podman" -- auf Fedora, openSUSE
    oder einem Mac also ein Befehl, den es dort nicht gibt. Genau derselbe
    Fehler wie beim frueheren "sudo pacman -S" auf Ubuntu.
    """
    import sys

    if sys.platform == "darwin":
        return "Docker Desktop installieren (docker.com), oder: brew install podman"
    for verwalter, befehl in (
        ("dnf", "sudo dnf install podman"),
        ("zypper", "sudo zypper install podman"),
        ("pacman", "sudo pacman -S --needed podman"),
        ("apt", "sudo apt install podman"),
    ):
        if shutil.which(verwalter):
            return befehl
    return "podman oder docker installieren"


def _start_hint(engine: str) -> str:
    import sys

    if sys.platform == "darwin":
        return (
            "podman machine init && podman machine start"
            if engine == "podman"
            else "Docker Desktop starten"
        )
    return (
        "systemctl --user start podman.socket"
        if engine == "podman"
        else "sudo systemctl start docker"
    )


def detect() -> ContainerStatus:
    """Untersucht, ob hier in einem Container gebaut werden kann. Wirft nie."""
    engine = find_engine()
    if engine is None:
        return ContainerStatus(
            problem="Es ist weder podman noch docker installiert.",
            remedy=install_hint_for_this_system(),
        )

    try:
        version = _run([engine, "--version"], timeout=30.0)
    except ContainerError as exc:
        return ContainerStatus(
            engine=engine,
            problem=f"{engine} liess sich nicht aufrufen.",
            remedy=exc.technical,
        )
    if not version.ok:
        return ContainerStatus(
            engine=engine,
            problem=f"{engine} meldet einen Fehler.",
            remedy=version.stderr.strip(),
        )

    status = ContainerStatus(engine=engine, version=version.stdout.strip())

    # Laeuft die Engine ueberhaupt? Bei docker heisst das: laeuft der Dienst.
    #
    # Die beiden Engines beantworten das in verschiedenen Sprachen. Bis zum
    # 07.09.2026 stand hier fuer beide "{{.Host.Security.Rootless}}" -- das ist
    # podman-Vokabular. Dockers info-Vorlage arbeitet auf einer Struktur ohne
    # Feld "Host", die Vorlage scheitert, docker endet mit einem Fehlercode,
    # und die Meldung lautete "docker laeuft nicht" -- auch bei laufendem
    # Dienst. Damit war der Container-Weg auf jedem System, das nur docker hat,
    # unerreichbar; auf macOS also immer.
    vorlage = "{{.Host.Security.Rootless}}" if engine == "podman" else "{{.SecurityOptions}}"
    try:
        info = _run([engine, "info", "--format", vorlage], timeout=60.0)
    except ContainerError:
        info = ContainerResult(1)
    if not info.ok:
        # Zweiter Versuch ohne Vorlage: scheitert nur die Formatierung, laeuft
        # die Engine sehr wohl. Eine unbeantwortbare Nebenfrage darf nicht als
        # "laeuft nicht" durchgehen.
        try:
            schlicht = _run([engine, "info"], timeout=60.0)
        except ContainerError:
            schlicht = ContainerResult(1)
        if not schlicht.ok:
            status.problem = f"{engine} laeuft nicht."
            status.remedy = _start_hint(engine)
            return status
        log.warning("%s beantwortet die Rootless-Frage nicht", engine)
        status.rootless = False
    else:
        antwort = info.stdout.strip().lower()
        # podman: "true"/"false". docker: eine Liste von Sicherheitsmerkmalen,
        # in der "name=rootless" steht, wenn der Daemon rootless laeuft.
        status.rootless = antwort == "true" or "name=rootless" in antwort

    try:
        vorhanden = _run([engine, "image", "exists", LOCAL_IMAGE], timeout=60.0)
        status.image_ready = vorhanden.ok
    except ContainerError:
        status.image_ready = False

    return status


class ContainerTarget:
    """Fuehrt Befehle in einem Container aus.

    Das Gegenstueck zu ``WslTarget``: die untere Ebene, die weiss, wie man
    hinueberkommt. Deutlich kleiner, weil UTF-16-Dekodierung und das Zerlegen
    einer Verteilungsliste hier ersatzlos entfallen.
    """

    def __init__(
        self,
        engine: str | None = None,
        image: str = LOCAL_IMAGE,
        *,
        runner=None,
    ) -> None:
        gefunden = engine or find_engine()
        if gefunden is None:
            raise ContainerError(
                "Es ist weder podman noch docker installiert.",
                "shutil.which('podman') und ('docker') liefern beide None",
            )
        self.engine = gefunden
        self.image = image
        # Einhaengepunkt fuer die Tests -- der Ablauf laesst sich damit
        # vollstaendig pruefen, ohne dass ein Container startet.
        self._runner = runner or _run

    # -- Abbild ---------------------------------------------------------------
    def image_exists(self) -> bool:
        return self._runner([self.engine, "image", "exists", self.image]).ok

    def build_image(self, on_line=None) -> None:
        """Baut das Abbild einmalig aus dem offiziellen archlinux-Abbild.

        Frueher stand hier ``--file -``, also "Containerfile von stdin" -- nur
        wurde ``CONTAINERFILE`` nirgends uebergeben. ``_run`` kennt kein
        ``input=``, also erbte die Engine das stdin des Programms: aus einem
        Terminal gestartet wartete sie bis zum Zeitlimit von 1800 Sekunden, aus
        der Oberflaeche gestartet las sie sofort EOF und brach mit "no FROM
        statement" ab. Der Container-Weg konnte so nie funktionieren.

        Jetzt geht es ueber ein Wegwerfverzeichnis. Das raeumt sich selbst auf
        -- der urspruengliche Einwand gegen eine Datei auf der Platte bleibt
        also erfuellt -- und nebenbei ist der Bauzusammenhang nicht mehr das
        Arbeitsverzeichnis des Programms, das sonst vollstaendig an die Engine
        geschickt wuerde.
        """
        log.info("Container-Abbild %s wird gebaut", self.image)
        with tempfile.TemporaryDirectory(prefix="archcustomiser-abbild-") as ordner:
            datei = os.path.join(ordner, "Containerfile")
            with open(datei, "w", encoding="utf-8", newline="\n") as ziel:
                ziel.write(CONTAINERFILE)
            ergebnis = self._runner(
                [self.engine, "build", "--tag", self.image, "--file", datei, ordner],
                timeout=IMAGE_BUILD_TIMEOUT,
            )
        if not ergebnis.ok:
            raise ContainerError(
                "Das Container-Abbild liess sich nicht bauen. Meist fehlt die "
                "Internetverbindung -- beim ersten Mal werden einige hundert MB "
                "geladen.",
                ergebnis.stderr.strip()[:2000],
            )
        if on_line is not None:
            on_line("Abbild bereit")

    def ensure_image(self, on_line=None) -> None:
        if not self.image_exists():
            self.build_image(on_line)

    # -- Aufrufe --------------------------------------------------------------
    def run(self, argv: Sequence[str], *, timeout: float = DEFAULT_TIMEOUT) -> ContainerResult:
        """Fuehrt einen Befehl im Container aus -- ohne Bind-Mounts."""
        return self._runner(
            [self.engine, "run", "--rm", self.image, *[str(i) for i in argv]],
            timeout=timeout,
        )

    def has_command(self, name: str) -> bool:
        return self.run(["sh", "-c", f"command -v {name} >/dev/null 2>&1"]).ok

    def can_mount_privileged(self) -> bool:
        """Ob im Container wirklich eingehaengt werden darf. Dauert zwei Sekunden.

        Das ist die Kernbehauptung dieses Moduls, und bis zum 07.09.2026 stand
        sie als blosse Zusicherung in der Vorabpruefung -- geprueft hat sie
        niemand. Der Unterschied ist nicht akademisch: laeuft die Engine
        rootless, gibt ``--privileged`` alle Faehigkeiten nur *innerhalb* des
        Benutzer-Namensraums, nicht ``CAP_SYS_ADMIN`` im Init-Namensraum. Der
        Bau lief dann bis pacstrap und scheiterte dort am ersten Mount -- nach
        Minuten, mit einer Meldung, die niemand deuten kann.

        ``devtmpfs`` ist der richtige Pruefstein: es hat im Kernel kein
        ``FS_USERNS_MOUNT``-Flag und laesst sich in einem Benutzer-Namensraum
        grundsaetzlich nicht einhaengen. Gelingt es, gelingt auch der Rest.
        """
        befehl = [
            self.engine, "run", "--rm", "--privileged", self.image,
            "sh", "-c", "mkdir -p /t && mount -t devtmpfs devtmpfs /t && umount /t",
        ]
        try:
            return self._runner(befehl, timeout=120.0).ok
        except ContainerError:
            log.warning("Einhaenge-Probe im Container fehlgeschlagen", exc_info=True)
            return False

    def kill(self, container_name: str, *, signal: str = "TERM") -> ContainerResult:
        return self._runner(
            [self.engine, "kill", "--signal", signal, container_name], timeout=60.0
        )

    def remove(self, container_name: str) -> ContainerResult:
        return self._runner([self.engine, "rm", "--force", container_name], timeout=60.0)


_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def container_name(iso_name: str) -> str:
    """Ein Containername, der sich spaeter wiederfinden laesst.

    Ohne festen Namen gibt es beim Abbrechen nichts zu toeten -- ``podman kill``
    braucht ihn. Der Prozesskennung angehaengt, damit zwei gleichzeitige
    Programmfenster sich nicht gegenseitig den Container abschiessen.
    """
    sauber = _UNSAFE.sub("-", iso_name).strip("-") or "iso"
    return f"archcustomiser-{sauber[:40]}-{os.getpid()}"


def selinux_active() -> bool:
    """Ob SELinux erzwingt -- dann brauchen Bind-Mounts die Kennzeichnung :Z.

    Ohne sie scheitert der Bau auf Fedora, RHEL und CentOS mit
    "Permission denied", ohne dass im Container etwas darauf hindeutet.
    """
    try:
        with open("/sys/fs/selinux/enforce", "rb") as datei:
            return datei.read(1) == b"1"
    except OSError:
        return False
