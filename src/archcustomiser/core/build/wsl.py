"""Anbindung an das Windows-Subsystem fuer Linux.

Damit laesst sich eine ISO auf einem Windows-Rechner erzeugen, ohne zweiten
Computer und ohne Dual-Boot: Windows uebernimmt das Einstellen, ein Linux
innerhalb von Windows das Bauen.

Zwei Eigenheiten von ``wsl.exe``, die man kennen muss:

* **Die eigenen Meldungen kommen in UTF-16-LE.** ``--status``, ``--list`` und
  jede Fehlermeldung des Verwaltungswerkzeugs. Wer sie als UTF-8 liest,
  bekommt Zeichensalat. Die Ausgabe eines *aufgerufenen Linux-Programms*
  (``wsl -e ...``) ist dagegen ganz normal UTF-8 -- beides muss also
  unterschiedlich behandelt werden.
* **Die Meldungstexte sind uebersetzt.** Auf einem deutschen Windows steht dort
  „ist nicht installiert". Deshalb wird nie auf Text geprueft, sondern auf
  Exit-Codes und auf strukturell erkennbare Ausgabe.

Der entscheidende Kniff beim Uebertragen des Profils: Es wird **nicht** auf ein
Windows-Laufwerk geschrieben und von dort gelesen. Unter ``/mnt/c`` gehen
symbolische Verknuepfungen und Dateirechte verloren -- und ein archiso-Profil
besteht zu einem Drittel aus Verknuepfungen. Stattdessen wandert ein
tar-Archiv hinueber, das *innerhalb* von Linux ausgepackt wird. Im Archiv sind
Verknuepfungen blosse Metadaten und ueberstehen den Weg unbeschadet.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from ..subprocess_util import run as run_ohne_fenster
from .errors import BuildError

log = logging.getLogger(__name__)

# wsl.exe meldet das, wenn das Subsystem gar nicht eingerichtet ist.
EXIT_NOT_INSTALLED = 50

# Verteilungen, die auf Arch aufbauen und archiso mitbringen koennen.
ARCH_FAMILY = ("archlinux", "arch")

DEFAULT_TIMEOUT = 60.0
INSTALL_COMMAND = "wsl --install archlinux"


class WslError(BuildError):
    """Etwas mit WSL hat nicht funktioniert."""


class WslNotAvailable(WslError):
    def __init__(self, technical: str = "") -> None:
        super().__init__(
            "Auf diesem Rechner ist das Windows-Subsystem fuer Linux nicht "
            "eingerichtet. Es wird gebraucht, weil eine Arch-ISO nur mit "
            "archiso zusammengebaut werden kann -- und das gibt es nur unter "
            "Arch Linux.",
            technical,
        )


class NoArchDistribution(WslError):
    def __init__(self, available: tuple[str, ...] = ()) -> None:
        found = (
            f"\n\nVorhanden sind: {', '.join(available)}."
            if available
            else "\n\nEs ist noch keine Linux-Verteilung eingerichtet."
        )
        super().__init__(
            "In WSL wurde kein Arch Linux gefunden. Zum Bauen einer Arch-ISO "
            "wird Arch selbst gebraucht -- pacman und archiso gibt es nur dort."
            + found,
            f"available={available}",
        )
        self.available = available


@dataclass(frozen=True, slots=True)
class Distribution:
    name: str
    state: str = ""
    version: int = 2
    default: bool = False

    @property
    def name_looks_like_arch(self) -> bool:
        """Erster, billiger Hinweis -- ohne die Verteilung zu starten.

        Reicht als alleiniges Kriterium nicht: eine Verteilung kann beliebig
        heissen. Wer seine Installation "meinlinux" nennt, wurde frueher nie
        gefunden, obwohl ``WslTarget.is_arch()`` sie ueber ``/etc/os-release``
        zweifelsfrei erkannt haette.
        """
        lowered = self.name.lower()
        return any(lowered.startswith(prefix) for prefix in ARCH_FAMILY)

    # Rueckwaertsvertraeglicher Name.
    @property
    def is_arch(self) -> bool:
        return self.name_looks_like_arch

    @property
    def running(self) -> bool:
        # Der Zustandstext ist uebersetzt; "Running"/"Wird ausgefuehrt".
        return self.state.lower().startswith(("running", "wird"))


@dataclass(slots=True)
class WslStatus:
    installed: bool = False
    distributions: tuple[Distribution, ...] = ()
    problem: str = ""

    @property
    def arch_distributions(self) -> tuple[Distribution, ...]:
        return tuple(d for d in self.distributions if d.is_arch)

    @property
    def preferred(self) -> Distribution | None:
        """Die Arch-Verteilung, die verwendet wird -- nach dem Namen."""
        return _bevorzugte(self.arch_distributions)

    def find_arch(self, probe: Callable[[str], bool] | None = None) -> Distribution | None:
        """Sucht eine Arch-Verteilung -- notfalls anhand ihres Inhalts.

        Zuerst nach dem Namen, weil das nichts kostet. Findet sich so keine,
        werden die uebrigen Verteilungen gefragt, ob sie Arch sind: ``probe``
        liest dort ``/etc/os-release``. Das startet die Verteilung und dauert
        einen Moment, passiert aber nur, wenn es sonst gar nicht ginge.
        """
        nach_namen = self.preferred
        if nach_namen is not None or probe is None:
            return nach_namen
        rest = [d for d in self.distributions if not d.name_looks_like_arch]
        return _bevorzugte(tuple(d for d in rest if probe(d.name)))

    @property
    def usable(self) -> bool:
        return self.installed and self.preferred is not None


def _bevorzugte(kandidaten: tuple[Distribution, ...]) -> Distribution | None:
    """Die Standardverteilung, sonst die erste."""
    if not kandidaten:
        return None
    for distribution in kandidaten:
        if distribution.default:
            return distribution
    return kandidaten[0]


# ---------------------------------------------------------------------------
# Aufrufe
# ---------------------------------------------------------------------------


def wsl_executable() -> str | None:
    found = shutil.which("wsl")
    if found:
        return found
    # In manchen Umgebungen fehlt das Verzeichnis im Suchpfad.
    fallback = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "wsl.exe"
    return str(fallback) if fallback.is_file() else None


def _decode_management(data: bytes) -> str:
    """Ausgabe von wsl.exe selbst -- UTF-16-LE.

    Die Unterscheidung erfolgt an den **Bytes**, nicht am Ergebnis: reiner
    ASCII-Text in UTF-8 laesst sich ebenfalls als UTF-16 dekodieren, nur eben
    zu Unsinn (aus \u201eHallo" wuerden zwei chinesische Zeichen). Ein verlaesslicher
    Unterschied sind dagegen die Nullbytes: in UTF-16-LE ist bei lateinischem
    Text etwa jedes zweite Byte null, in UTF-8 kein einziges.
    """
    if not data:
        return ""

    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace").replace("\ufeff", "")

    # In UTF-16-LE traegt nur ein Zeichen unter U+0100 ein Nullbyte. Eine
    # japanische Meldung (Katakana) kommt damit unter jede feste Quote --
    # die frueher hier stehende Schwelle von einem Viertel griff dort nicht,
    # und der Text wurde als UTF-8 zu Zeichensalat. UTF-8 enthaelt dagegen
    # *kein einziges* Nullbyte: eines genuegt als Verdacht. Bestaetigt wird
    # er ueber die Plausibilitaet des Ergebnisses.
    if data.count(0) and len(data) % 2 == 0:
        try:
            kandidat = data.decode("utf-16-le").replace("\ufeff", "")
        except UnicodeDecodeError:
            kandidat = None
        if kandidat is not None and _lesbar(kandidat):
            return kandidat
    return data.decode("utf-8", errors="replace")


def _lesbar(text: str) -> bool:
    """Ob ein dekodierter Text wie eine Meldung aussieht.

    Reiner ASCII-Text in UTF-8 laesst sich ebenfalls als UTF-16 dekodieren,
    nur eben zu Unsinn. Der Unterschied: echte Meldungen enthalten ausser
    Zeilenumbruch und Tabulator keine Steuerzeichen und kein Ersatzzeichen.
    """
    if not text:
        return False
    erlaubt = (chr(13), chr(10), chr(9))
    return not any(
        zeichen == "\ufffd" or (zeichen < " " and zeichen not in erlaubt)
        for zeichen in text
    )


def _run_management(arguments: Sequence[str], timeout: float = DEFAULT_TIMEOUT):
    executable = wsl_executable()
    if executable is None:
        raise WslNotAvailable("wsl.exe nicht gefunden")
    try:
        completed = run_ohne_fenster(
            [executable, *arguments],
            capture_output=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise WslNotAvailable(f"{executable}: {exc}") from exc
    return completed


def detect() -> WslStatus:
    """Ermittelt, ob und womit gebaut werden kann. Wirft nie.

    Gefragt wird zuerst ``--status``: nur dieser Unterbefehl meldet mit dem
    eindeutigen Code 50, dass das Subsystem gar nicht eingerichtet ist.
    ``--list`` liefert im selben Fall bloss eine 1, die sich nicht von „keine
    Verteilung vorhanden" unterscheiden liesse.
    """
    if os.name != "nt":
        return WslStatus(problem="WSL gibt es nur unter Windows.")

    executable = wsl_executable()
    if executable is None:
        return WslStatus(problem="wsl.exe wurde nicht gefunden.")

    try:
        status = _run_management(["--status"])
    except WslError as exc:
        return WslStatus(problem=exc.user_message)

    if status.returncode == EXIT_NOT_INSTALLED:
        return WslStatus(
            problem=_first_line(_decode_management(status.stderr))
            or "Das Windows-Subsystem fuer Linux ist nicht installiert."
        )

    try:
        listing = _run_management(["--list", "--verbose"])
    except WslError as exc:
        return WslStatus(installed=True, problem=exc.user_message)

    output = _decode_management(listing.stdout) or _decode_management(listing.stderr)
    distributions = parse_distribution_list(output)
    if not distributions:
        return WslStatus(
            installed=True,
            problem=_first_line(output) or "Es ist keine Linux-Verteilung eingerichtet.",
        )
    return WslStatus(installed=True, distributions=distributions)


def parse_distribution_list(output: str) -> tuple[Distribution, ...]:
    """Zerlegt die Ausgabe von ``wsl --list --verbose``.

    Die Spaltenueberschriften sind uebersetzt, die Struktur aber nicht: ein
    Stern markiert die Standardverteilung, danach folgen Name, Zustand und
    Version. Ausgewertet wird deshalb die Struktur, nicht der Text.
    """
    found: list[Distribution] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        default = stripped.startswith("*")
        if default:
            stripped = stripped[1:].strip()

        parts = stripped.split()
        if len(parts) < 2:
            continue
        # Die letzte Spalte ist die WSL-Version; fehlt sie, ist es die Kopfzeile.
        if not parts[-1].isdigit():
            continue
        try:
            version = int(parts[-1])
        except ValueError:
            continue
        name = parts[0]
        state = " ".join(parts[1:-1])
        found.append(Distribution(name=name, state=state, version=version, default=default))
    return tuple(found)


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


# ---------------------------------------------------------------------------
# Befehle innerhalb der Verteilung
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WslResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class WslTarget:
    """Fuehrt Befehle in einer WSL-Verteilung aus.

    Alle Aufrufe gehen ueber ``wsl -d <name> -e <programm> ...`` -- eine feste
    Argumentliste ohne Shell. Nur wo ausdruecklich eine Shell noetig ist (etwa
    fuer eine Umleitung), wird ``bash -lc`` verwendet, und dann mit sorgfaeltig
    gequoteten Werten.
    """

    def __init__(self, distribution: str, user: str | None = None) -> None:
        self.distribution = distribution
        self.user = user

    # -- Grundlagen -----------------------------------------------------------
    def prefix(self) -> list[str]:
        executable = wsl_executable()
        if executable is None:
            raise WslNotAvailable("wsl.exe nicht gefunden")
        argv = [executable, "-d", self.distribution]
        if self.user:
            argv += ["-u", self.user]
        return argv

    def wrap(self, argv: Sequence[str], *, as_root: bool = False) -> list[str]:
        """Macht aus einem Linux-Aufruf einen Windows-Aufruf.

        ``as_root`` geht ueber ``wsl -u root``. Das braucht keine
        Windows-Adminrechte -- die Verteilung selbst entscheidet, wer darin root
        ist -- und ist der Weg fuer pacman, wenn der angemeldete Benutzer kein
        root ist und ``sudo`` fehlt (in einer frischen Arch-Verteilung ist das
        der Normalfall).
        """
        vorspann = list(self.prefix())
        if as_root:
            vorspann += ["-u", "root"]
        return [*vorspann, "-e", *[str(item) for item in argv]]

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float = DEFAULT_TIMEOUT,
        as_root: bool = False,
    ) -> WslResult:
        """Fuehrt einen Befehl aus und liefert seine Ausgabe.

        Die Ausgabe stammt vom Linux-Programm und ist deshalb UTF-8 -- anders
        als die Meldungen von wsl.exe selbst.
        """
        try:
            completed = run_ohne_fenster(
                self.wrap(argv, as_root=as_root),
                capture_output=True,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise WslError(f"Der Aufruf in WSL ist fehlgeschlagen: {exc}") from exc
        # stdout kommt vom Linux-Programm und ist UTF-8. stderr kann von
        # beiden stammen: vom Programm (UTF-8) oder von wsl.exe selbst, wenn
        # der Aufruf gar nicht zustande kam -- und dessen Meldungen sind
        # UTF-16-LE. Ohne die Unterscheidung landete "Es gibt keine Verteilung
        # mit dem angegebenen Namen." mit Nullbytes im Fehlerdialog.
        return WslResult(
            returncode=completed.returncode,
            stdout=completed.stdout.decode("utf-8", errors="replace"),
            stderr=_decode_management(completed.stderr),
        )

    # -- Pfade ----------------------------------------------------------------
    def to_linux_path(self, windows_path: Path) -> str:
        """Uebersetzt einen Windows-Pfad in die WSL-Sicht.

        Bewusst ueber ``wslpath`` statt ueber eigenes Zusammensetzen: die
        Zuordnung haengt von der WSL-Konfiguration ab und laesst sich nicht
        zuverlaessig erraten.
        """
        result = self.run(["wslpath", "-a", str(windows_path)])
        if not result.ok:
            raise WslError(
                f"Der Pfad {windows_path} konnte in WSL nicht aufgeloest werden.",
                result.stderr.strip(),
            )
        return result.stdout.strip()

    def home(self) -> PurePosixPath:
        """Das Heimatverzeichnis in der Verteilung.

        Schlaegt der Aufruf fehl, wird das gemeldet statt stillschweigend
        ``/root`` anzunehmen. Ein geratenes Heimatverzeichnis heisst, dass der
        gesamte Build am falschen Ort landet -- mitsamt mehreren Gigabyte
        Arbeitsverzeichnis, die dann niemand mehr wiederfindet.
        """
        result = self.run(["sh", "-c", "printf %s \"$HOME\""])
        text = result.stdout.strip()
        if not result.ok or not text:
            raise WslError(
                f"Das Heimatverzeichnis in {self.distribution!r} liess sich nicht "
                f"ermitteln. Laeuft die Verteilung?",
                result.stderr.strip() or f"Rueckgabewert {result.returncode}",
            )
        return PurePosixPath(text)

    # -- Abfragen -------------------------------------------------------------
    def has_command(self, name: str) -> bool:
        return self.run(["sh", "-c", f"command -v {name} >/dev/null 2>&1"]).ok

    def is_arch(self) -> bool:
        """Prueft die Herkunft ueber os-release, nicht ueber den Namen.

        Eine Verteilung kann beliebig heissen; entscheidend ist, ob pacman und
        die Arch-Kennung vorhanden sind.
        """
        result = self.run(["sh", "-c", ". /etc/os-release 2>/dev/null && printf '%s %s' \"$ID\" \"$ID_LIKE\""])
        text = result.stdout.lower()
        return "arch" in text or self.has_command("pacman")

    def free_space_gb(self, linux_path: str) -> float | None:
        result = self.run(["df", "-B1", "--output=avail", linux_path])
        if not result.ok:
            return None
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        for line in reversed(lines):
            if line.isdigit():
                return int(line) / 1_073_741_824
        return None

    def subid_ready(self) -> bool:
        """Ob ein Build ohne Root-Rechte moeglich ist."""
        return self.run(
            [
                "sh",
                "-c",
                'user=$(id -un); grep -q "^${user}:" /etc/subuid && grep -q "^${user}:" /etc/subgid',
            ]
        ).ok

    def userns_ready(self) -> bool:
        result = self.run(["sh", "-c", "cat /proc/sys/user/max_user_namespaces 2>/dev/null || echo 1"])
        try:
            return int(result.stdout.strip() or "1") > 0
        except ValueError:
            return True


# ---------------------------------------------------------------------------
# Vorbereitung
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class WslReadiness:
    """Was in der Verteilung noch fehlt."""

    target: WslTarget | None = None
    is_arch: bool = False
    has_archiso: bool = False
    has_tar: bool = True
    subid_ready: bool = False
    userns_ready: bool = True
    free_gb: float | None = None
    problems: list[str] = field(default_factory=list)
    remedies: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.target is not None and self.is_arch and self.has_archiso


def check_readiness(target: WslTarget, needed_gb: float = 20.0) -> WslReadiness:
    """Prueft die Verteilung, ohne etwas zu veraendern."""
    readiness = WslReadiness(target=target)

    readiness.is_arch = target.is_arch()
    if not readiness.is_arch:
        readiness.problems.append(
            f"Die Verteilung {target.distribution!r} ist kein Arch Linux. "
            f"pacman und archiso gibt es nur dort."
        )
        readiness.remedies.append(INSTALL_COMMAND)
        return readiness

    readiness.has_archiso = target.has_command("mkarchiso")
    if not readiness.has_archiso:
        readiness.problems.append("In der Verteilung fehlt das Paket 'archiso'.")
        readiness.remedies.append("sudo pacman -Syu --needed archiso")

    readiness.has_tar = target.has_command("tar")
    if not readiness.has_tar:
        readiness.problems.append("In der Verteilung fehlt 'tar'.")
        readiness.remedies.append("sudo pacman -S --needed tar")

    readiness.userns_ready = target.userns_ready()
    readiness.subid_ready = target.subid_ready()
    if not readiness.subid_ready:
        # Kein Hindernis, aber es kostet sonst eine Rechteabfrage.
        readiness.remedies.append(
            "sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 $USER"
        )

    home = target.home()
    readiness.free_gb = target.free_space_gb(str(home))
    if readiness.free_gb is not None and readiness.free_gb < needed_gb:
        readiness.problems.append(
            f"In der Verteilung sind nur {readiness.free_gb:.0f} GB frei, "
            f"gebraucht werden etwa {needed_gb:.0f} GB."
        )
        readiness.remedies.append(
            "Speicherplatz in WSL freigeben oder die virtuelle Platte vergroessern."
        )
    return readiness


def install_hint() -> str:
    """Der Befehl, mit dem sich Arch in WSL einrichten laesst.

    Bewusst nur ein Hinweis und keine Ausfuehrung: die Einrichtung braucht
    Administratorrechte und einen Neustart. Das ist eine Entscheidung des
    Benutzers, nicht dieses Programms.
    """
    return INSTALL_COMMAND
