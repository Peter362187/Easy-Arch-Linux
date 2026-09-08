"""Auswertung der mkarchiso-Ausgabe.

Zwei Ebenen:

**Grobstufen.** mkarchiso meldet jeden Abschnitt mit einer INFO-Zeile. Die
Reihenfolge ist im Quelltext festgelegt (``_build_iso_base`` und
``_build_iso_image``) und wurde daraus uebernommen -- nicht geraten. Jeder
Marke ist ein Prozentwert zugeordnet, gewichtet nach der tatsaechlichen Dauer:
das Installieren der Pakete und das Komprimieren des Dateisystems machen
zusammen ueber zwei Drittel der Bauzeit aus.

**Feinfortschritt.** Innerhalb der beiden langen Abschnitte gibt es echte
Zahlen: pacman zaehlt Pakete, mksquashfs und xorriso melden Prozente. Damit
bewegt sich der Balken auch dann, wenn eine Stufe zwanzig Minuten dauert.

Zwei Fallstricke, beide verifiziert:

* Die Fortschrittsausgaben von mksquashfs und xorriso enden mit ``\\r``, nicht
  mit ``\\n``. Wer nur an Zeilenumbruechen trennt, sieht sie nie.
* ``Creating a list of installed packages on live-enviroment...`` enthaelt einen
  Tippfehler im Original. Deshalb wird nur auf das Praefix verglichen.

Ohne ``-v`` gibt mkarchiso ueberhaupt keine INFO-Zeile aus. Der Runner setzt
den Schalter deshalb immer.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field

# Format aller mkarchiso-Meldungen: "[mkarchiso] INFO: <text>".
# LC_ALL=C.UTF-8 setzt mkarchiso selbst -- die Texte sind immer englisch.
MESSAGE = re.compile(r"^\[(?P<app>[^\]]+)\]\s+(?P<level>INFO|WARNING|ERROR):\s*(?P<text>.*)$")

# pacman waehrend pacstrap.
# Die Zaehlerzeilen sind eingerueckt -- pacman schreibt " (  1/312) downloading".
# Ohne das fuehrende \s* greift das Muster nie, und der Balken bliebe waehrend
# des laengsten Bauabschnitts stehen.
PACMAN_TOTAL = re.compile(r"^\s*Packages\s+\((\d+)\)")
PACMAN_COUNTER = re.compile(r"^\s*\(\s*(\d+)/(\d+)\)\s")
PACMAN_RETRIEVE = re.compile(r"^\s*::\s*Retrieving packages")
PACMAN_INSTALL = re.compile(r"^\s*::\s*Processing package changes")

# mksquashfs schreibt seinen Balken mit \r auf stderr
SQUASHFS_PERCENT = re.compile(r"(\d{1,3})%")
# xorriso: "xorriso : UPDATE :  12.34% done"
XORRISO_PERCENT = re.compile(r"UPDATE\s*:\s*([\d.]+)%")


@dataclass(frozen=True, slots=True)
class Stage:
    """Ein Abschnitt des Baus."""

    key: str
    marker: str          # Praefix der INFO-Zeile, das ihn einleitet
    label: str           # was der Benutzer liest
    start: float         # Fortschritt zu Beginn (0..1)
    end: float           # Fortschritt am Ende

    def matches(self, text: str) -> bool:
        return text.startswith(self.marker)


# Reihenfolge und Marken stammen aus dem mkarchiso-Quelltext.
# Die Gewichtung stammt aus der Erfahrung, welche Schritte lange dauern.
STAGES: tuple[Stage, ...] = (
    Stage("validate", "Validating options", "Profil wird geprueft", 0.00, 0.01),
    Stage("config", "mkarchiso configuration settings", "Einstellungen gelesen", 0.01, 0.02),
    Stage("pacman_conf", "Copying custom pacman.conf", "Paketquellen vorbereitet", 0.02, 0.03),
    Stage("airootfs", "Copying custom airootfs files", "Systemdateien kopiert", 0.03, 0.05),
    # Der laengste Abschnitt: hier laedt und installiert pacstrap alle Pakete.
    Stage("packages", "Installing packages to", "Pakete werden installiert", 0.05, 0.55),
    Stage("version", "Creating version files", "Versionsdateien geschrieben", 0.55, 0.57),
    Stage("skel", "Copying /etc/skel", "Benutzerverzeichnisse angelegt", 0.57, 0.58),
    Stage("boot", "Copying /boot out of the airootfs", "Kernel ausgelagert", 0.58, 0.60),
    # Tippfehler im Original ("enviroment") -- nur das Praefix vergleichen.
    Stage("pkglist", "Creating a list of installed packages", "Paketliste erstellt", 0.60, 0.62),
    Stage("iso9660", "Preparing kernel and initramfs for the ISO 9660", "Startdateien vorbereitet", 0.62, 0.64),
    Stage("syslinux", "Setting up SYSLINUX", "BIOS-Start eingerichtet", 0.64, 0.66),
    Stage("fat", "Preparing kernel and initramfs for the FAT", "UEFI-Dateien vorbereitet", 0.66, 0.68),
    Stage("fatimg", "Creating FAT image", "EFI-Partition erzeugt", 0.68, 0.70),
    Stage("systemd_boot", "Setting up systemd-boot", "UEFI-Start eingerichtet", 0.70, 0.73),
    Stage("grub", "Setting up GRUB", "UEFI-Start eingerichtet (GRUB)", 0.70, 0.73),
    Stage("cleanup_pacstrap", "Cleaning up in pacstrap location", "Aufraeumen", 0.73, 0.76),
    # Zweiter langer Abschnitt: die Kompression des Dateisystems.
    Stage("squashfs", "Creating SquashFS image", "Dateisystem wird komprimiert", 0.76, 0.92),
    Stage("erofs", "Creating EROFS image", "Dateisystem wird komprimiert", 0.76, 0.92),
    Stage("ext4", "Creating ext4 image", "Dateisystem-Abbild wird erzeugt", 0.76, 0.90),
    Stage("checksum", "Creating checksum file", "Pruefsumme wird berechnet", 0.92, 0.93),
    Stage("gpg", "Signing rootfs image", "Abbild wird signiert", 0.93, 0.94),
    Stage("rm_pacstrap", "Removing pacstrap directory", "Zwischendateien entfernt", 0.93, 0.94),
    Stage("iso", "Creating ISO image", "ISO-Datei wird geschrieben", 0.94, 0.99),
)

_BY_KEY = {stage.key: stage for stage in STAGES}

# Abschnitte mit eigenem Feinfortschritt.
_FINE_PACMAN = "packages"
_FINE_SQUASHFS = ("squashfs", "erofs", "ext4")
_FINE_XORRISO = "iso"


@dataclass(slots=True)
class ProgressState:
    """Der aktuelle Stand, wie ihn die Oberflaeche anzeigt."""

    fraction: float = 0.0
    stage_key: str = ""
    label: str = "Wird vorbereitet"
    detail: str = ""
    completed: tuple[str, ...] = field(default_factory=tuple)
    finished: bool = False


class ProgressParser:
    """Wandelt mkarchiso-Ausgabe in Fortschritt um.

    Der Fortschritt ist **monoton**: eine spaetere Zeile kann ihn nie
    zurueckdrehen. Ein zurueckspringender Balken sieht nach einem Fehler aus,
    auch wenn alles in Ordnung ist.
    """

    def __init__(self) -> None:
        self.state = ProgressState()
        self._completed: list[str] = []
        self._package_total = 0
        self._package_done = 0
        self._package_phase = ""   # "download" | "install"
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self._saw_final_done = False

    # -- oeffentlich ----------------------------------------------------------
    def feed(self, line: str) -> ProgressState:
        """Verarbeitet genau eine Ausgabezeile."""
        text = line.rstrip("\n\r")
        if not text.strip():
            return self.state

        message = MESSAGE.match(text)
        if message is not None:
            return self._handle_message(message.group("level"), message.group("text").strip())
        return self._handle_tool_output(text)

    def feed_all(self, chunk: str) -> Iterator[ProgressState]:
        for line in split_lines(chunk):
            yield self.feed(line)

    @property
    def stage(self) -> Stage | None:
        return _BY_KEY.get(self.state.stage_key)

    # -- intern ---------------------------------------------------------------
    def _handle_message(self, level: str, text: str) -> ProgressState:
        if level == "ERROR":
            self.errors.append(text)
            return self.state
        if level == "WARNING":
            self.warnings.append(text)
            return self.state

        if text.startswith("Done"):
            stage = self.stage
            if stage is not None:
                self._advance(stage.end, stage.label, "")
                if stage.label not in self._completed:
                    self._completed.append(stage.label)
                    self.state = ProgressState(
                        fraction=self.state.fraction,
                        stage_key=self.state.stage_key,
                        label=self.state.label,
                        detail="",
                        completed=tuple(self._completed),
                        finished=self.state.finished,
                    )
            return self.state

        for stage in STAGES:
            if stage.matches(text):
                self._package_total = 0
                self._package_done = 0
                self._package_phase = ""
                self._advance(stage.start, stage.label, "", stage_key=stage.key)
                return self.state
        return self.state

    def _handle_tool_output(self, text: str) -> ProgressState:
        key = self.state.stage_key
        if key == _FINE_PACMAN:
            return self._handle_pacman(text)
        if key in _FINE_SQUASHFS:
            return self._handle_percent(text, SQUASHFS_PERCENT, divisor=100.0)
        if key == _FINE_XORRISO:
            return self._handle_percent(text, XORRISO_PERCENT, divisor=100.0)
        return self.state

    def _handle_pacman(self, text: str) -> ProgressState:
        if PACMAN_RETRIEVE.search(text):
            self._package_phase = "download"
            self._package_done = 0
            return self._advance(self.state.fraction, self.state.label, "Pakete werden geladen")
        if PACMAN_INSTALL.search(text):
            self._package_phase = "install"
            self._package_done = 0
            return self._advance(self.state.fraction, self.state.label, "Pakete werden entpackt")

        total = PACMAN_TOTAL.match(text)
        if total is not None:
            self._package_total = int(total.group(1))
            return self._advance(
                self.state.fraction, self.state.label, f"{self._package_total} Pakete"
            )

        counter = PACMAN_COUNTER.match(text)
        if counter is not None and self._package_total:
            done, total_count = int(counter.group(1)), int(counter.group(2))
            self._package_done = done
            # Laden und Entpacken sind zwei Durchlaeufe ueber dieselbe Menge --
            # zusammen also die doppelte Anzahl Schritte.
            steps = total_count * 2 if self._package_phase else total_count
            offset = total_count if self._package_phase == "install" else 0
            share = min(1.0, (offset + done) / steps) if steps else 0.0
            stage = _BY_KEY[_FINE_PACMAN]
            fraction = stage.start + (stage.end - stage.start) * share
            verb = "geladen" if self._package_phase == "download" else "installiert"
            return self._advance(
                fraction, stage.label, f"{done} von {total_count} {verb}"
            )
        return self.state

    def _handle_percent(self, text: str, pattern: re.Pattern[str], *, divisor: float) -> ProgressState:
        match = pattern.search(text)
        if match is None:
            return self.state
        try:
            value = float(match.group(1)) / divisor
        except ValueError:
            return self.state
        stage = self.stage
        if stage is None:
            return self.state
        fraction = stage.start + (stage.end - stage.start) * min(1.0, max(0.0, value))
        return self._advance(fraction, stage.label, f"{value * 100:.0f} %")

    def _advance(
        self, fraction: float, label: str, detail: str, *, stage_key: str | None = None
    ) -> ProgressState:
        # Monoton: nie zurueckspringen.
        fraction = max(self.state.fraction, min(1.0, fraction))
        self.state = ProgressState(
            fraction=fraction,
            stage_key=stage_key if stage_key is not None else self.state.stage_key,
            label=label,
            detail=detail,
            completed=tuple(self._completed),
            finished=self.state.finished,
        )
        return self.state

    def finish(self, success: bool) -> ProgressState:
        self.state = ProgressState(
            fraction=1.0 if success else self.state.fraction,
            stage_key=self.state.stage_key,
            label="Fertig" if success else "Abgebrochen",
            detail="",
            completed=tuple(self._completed),
            finished=True,
        )
        return self.state


def split_lines(chunk: str) -> list[str]:
    """Trennt an ``\\n`` UND ``\\r``.

    mksquashfs und xorriso schreiben ihren Fortschritt mit Wagenruecklauf, ohne
    Zeilenumbruch. Wer nur an ``\\n`` trennt, bekommt einen einzigen riesigen
    Block und sieht waehrend der laengsten Bauphase gar nichts.
    """
    return [part for part in re.split(r"[\r\n]", chunk) if part]


def summarise_failure(errors: list[str]) -> str:
    """Aus den ERROR-Zeilen eine brauchbare Ursache formulieren.

    mkarchiso sammelt Validierungsfehler und meldet am Ende nur ihre Anzahl --
    die eigentlichen Angaben stehen in den Zeilen davor.
    """
    if not errors:
        return "mkarchiso hat keinen Grund genannt. Das vollstaendige Protokoll hilft weiter."
    detail = [line for line in errors if "errors were encountered" not in line]
    return "\n".join(detail[:5]) if detail else errors[0]
