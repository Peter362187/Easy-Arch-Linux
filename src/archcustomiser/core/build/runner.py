"""Startet ``mkarchiso`` und liest seine Ausgabe mit.

Die Stelle, die mkarchiso startet -- den einzigen Prozess, dessen
Fortschritt mitgelesen werden muss. (Das einmalige Bauen des
Container-Abbilds laeuft ueber ``core/build/container.py``.)

Vier Dinge, die hier bewusst so und nicht anders gemacht sind:

* **``-v`` ist nicht optional.** Ohne den Schalter steht ``quiet=y`` und
  mkarchiso gibt keine einzige INFO-Zeile aus -- es gaebe dann keinerlei
  Fortschrittsinformation, nur einen Prozess, der zwanzig Minuten schweigt.
* **Ausgabe wird byteweise gelesen, nicht zeilenweise.** mksquashfs und xorriso
  schreiben ihren Fortschritt mit Wagenruecklauf statt Zeilenumbruch. Ein
  ``for line in process.stdout`` wuerde waehrend der laengsten Bauphase blockieren.
* **Kein ``shell=True``, feste Argumentliste.** Pfade kommen aus Dialogen.
* **``-r`` wird nicht verwendet.** Der Schalter loescht das Arbeitsverzeichnis
  am Ende -- aber schon *waehrend* der ISO-Erzeugung Teile davon, und er
  verweigert die Arbeit, wenn das Verzeichnis vorher existierte. Aufgeraeumt
  wird stattdessen selbst, nach Auswertung des Ergebnisses.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..subprocess_util import popen as popen_ohne_fenster
from .errors import BuildCancelled, BuildFailed
from .progress import ProgressParser, ProgressState, summarise_failure
from .targets import ExecutionTarget, LocalTarget

log = logging.getLogger(__name__)

# Trennt an Wagenruecklauf UND Zeilenumbruch -- mksquashfs und xorriso
# schreiben ihren Fortschritt ohne Zeilenumbruch.
_NEWLINE = bytes([10])
_RETURN = bytes([13])
_TRENNER = re.compile(b"[" + _RETURN + _NEWLINE + b"]")

READ_SIZE = 4096
TERMINATE_GRACE_SECONDS = 8.0

# Ein Build erzeugt einige zehntausend Zeilen; bei einem haengenden Werkzeug
# koennen es beliebig viele werden. Behalten wird das Ende, denn dort steht der
# Fehler -- summarise_failure braucht ohnehin nur die letzten Zeilen.
MAX_KEPT_LINES = 5000

# Schutz gegen ein Werkzeug, das weder Wagenruecklauf noch Zeilenumbruch
# schreibt: ohne Obergrenze waechst der Zwischenpuffer, bis der Speicher voll
# ist.
MAX_BUFFER_BYTES = 1 << 20

LineCallback = Callable[[str], None]
ProgressCallback = Callable[[ProgressState], None]


@dataclass(slots=True)
class BuildResult:
    returncode: int
    iso_path: Path | None
    duration_seconds: float
    iso_location: str = ""
    """Wo die ISO auf dem Zielrechner liegt -- in dessen eigener Schreibweise.

    Beim Bau in WSL ist das ein Linux-Pfad. Er darf nicht durch ``pathlib``
    laufen: unter Windows wuerden die Schraegstriche zu Backslashes, und kein
    Werkzeug faende die Datei mehr. Genau daran ist der erste echte Bau
    gescheitert -- ``cp`` suchte nach einer Datei mit Backslashes im Namen.
    ``iso_path`` wird erst gesetzt, wenn die Datei hier angekommen ist.
    """
    lines: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0 and bool(self.iso_location)

    @property
    def size_mb(self) -> float:
        if self.iso_path is None or not self.iso_path.is_file():
            return 0.0
        return self.iso_path.stat().st_size / 1_048_576


class MkarchisoRunner:
    """Fuehrt einen einzelnen mkarchiso-Lauf aus."""

    def __init__(
        self,
        profile_dir: Path,
        work_dir: Path,
        out_dir: Path,
        *,
        privilege_mode: str = "rootless",
        source_date_epoch: int | None = None,
        executable: str | None = None,
        target: ExecutionTarget | None = None,
    ) -> None:
        # Bewusst als Text und nicht als Path: bei einem Bau in WSL sind das
        # Linux-Pfade, und pathlib wuerde daraus unter Windows '\home\jason'
        # machen.
        self.profile_dir = str(profile_dir)
        self.work_dir = str(work_dir)
        self.out_dir = str(out_dir)
        self.privilege_mode = privilege_mode
        self.source_date_epoch = source_date_epoch
        self.executable = executable
        self.target: ExecutionTarget = target or LocalTarget(executable)
        self._process: subprocess.Popen[bytes] | None = None
        self._cancelled = threading.Event()
        # Schuetzt das Fenster zwischen "Prozess starten" und "Prozess
        # eintragen". Ohne die Sperre konnte ein Abbruch genau dazwischen
        # landen: cancel() sah noch kein Prozessobjekt, run() pruefte danach
        # nicht mehr auf Abbruch -- der Build lief vollstaendig durch und warf
        # erst am Ende BuildCancelled. Vierzig Minuten fuer nichts.
        self._lock = threading.Lock()

    # -- Aufruf zusammenbauen -------------------------------------------------
    def build_argv(self) -> list[str]:
        # Ein ausdruecklich gesetztes Programm hat Vorrang; sonst entscheidet
        # das Ziel, wie mkarchiso dort heisst.
        argv = [
            self.executable or self.target.resolve_executable(),
            "-v",                       # ohne das gibt es keine Fortschrittsmeldungen
            "-w", self.work_dir,
            "-o", self.out_dir,
            self.profile_dir,
        ]

        if self.privilege_mode == "pkexec":
            # pkexec setzt die Umgebung selbst zurueck; der Aufruf bleibt eine
            # feste Argumentliste ohne Shell.
            argv = ["pkexec", *argv]
        elif self.privilege_mode == "sudo":
            argv = ["sudo", "--", *argv]

        # Diese Variablen muss mkarchiso sehen -- auch ueber eine
        # Systemgrenze hinweg.
        return self.target.wrap(argv, env=self.target_environment())

    def target_environment(self) -> dict[str, str]:
        """Was mkarchiso selbst braucht -- unabhaengig davon, wo es laeuft.

        ``SOURCE_DATE_EPOCH`` liest mkarchiso ausdruecklich aus der Umgebung
        (``[[ -v SOURCE_DATE_EPOCH ]] || …``). Ohne diesen Wert setzt es einen
        eigenen Zeitstempel, und ein wiederverwendetes Arbeitsverzeichnis
        friert den alten ein.

        ``LC_ALL`` setzt mkarchiso zwar selbst, aber die von ihm aufgerufenen
        Werkzeuge starten frueher -- deshalb hier ebenfalls.
        """
        env: dict[str, str] = {"LC_ALL": "C.UTF-8", "LANG": "C.UTF-8"}
        if self.source_date_epoch is not None:
            env["SOURCE_DATE_EPOCH"] = str(self.source_date_epoch)
        return env

    def environment(self) -> dict[str, str]:
        env = dict(os.environ)
        # mkarchiso setzt LC_ALL selbst, aber nur intern. Hier ebenfalls, damit
        # auch die Ausgabe der aufgerufenen Werkzeuge englisch und damit
        # auswertbar bleibt.
        env["LC_ALL"] = "C.UTF-8"
        env["LANG"] = "C.UTF-8"
        if self.source_date_epoch is not None:
            # mkarchiso schreibt work_dir/build_date und liest es beim naechsten
            # Lauf zurueck. Ohne festen Wert waere ein wiederverwendetes
            # Arbeitsverzeichnis in der Zeit eingefroren.
            env["SOURCE_DATE_EPOCH"] = str(self.source_date_epoch)
        # Das Ziel darf die Umgebung noch anpassen -- bei WSL etwa den PATH von
        # Eintraegen befreien, die es nicht abbilden kann.
        return self.target.sanitize_environment(env)

    # -- Ausfuehren -----------------------------------------------------------
    def run(
        self,
        *,
        on_line: LineCallback | None = None,
        on_progress: ProgressCallback | None = None,
        expected_iso: str = "",
    ) -> BuildResult:
        argv = self.build_argv()
        parser = ProgressParser()
        lines: deque[str] = deque(maxlen=MAX_KEPT_LINES)
        started = time.monotonic()

        log.info("Starte: %s", " ".join(argv))
        self.target.make_dirs(self.out_dir, self.work_dir)

        with self._lock:
            # Vor dem Start pruefen. Bisher setzte ein Abbruch zu diesem
            # Zeitpunkt nur das Ereignis -- der Prozess startete trotzdem und
            # lief bis zum Ende durch.
            if self._cancelled.is_set():
                raise BuildCancelled()
            try:
                process = popen_ohne_fenster(
                    argv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,   # Reihenfolge bleibt so erhalten
                    stdin=subprocess.DEVNULL,   # mkarchiso darf nichts erfragen
                    bufsize=0,
                    env=self.environment(),
                    cwd=self.target.cwd(),
                )
            except OSError as exc:
                raise BuildFailed(
                    127, (f"{argv[0]} liess sich nicht starten: {exc}",)
                ) from exc
            self._process = process
        buffer = b""
        try:
            assert process.stdout is not None
            while True:
                chunk = process.stdout.read(READ_SIZE)
                if not chunk:
                    break
                buffer += chunk
                # An \r UND \n trennen; einen unvollstaendigen Rest behalten.
                buffer, ready = _take_complete(buffer)
                if len(buffer) > MAX_BUFFER_BYTES:
                    # Ein Werkzeug ohne Zeilenende. Lieber ein Bruchstueck
                    # ausgeben als den Speicher volllaufen lassen.
                    ready.append(buffer)
                    buffer = b""
                for raw in ready:
                    text = raw.decode("utf-8", errors="replace")
                    lines.append(text)
                    if on_line is not None:
                        on_line(text)
                    state = parser.feed(text)
                    if on_progress is not None:
                        on_progress(state)
        finally:
            if buffer:
                text = buffer.decode("utf-8", errors="replace")
                lines.append(text)
                if on_line is not None:
                    on_line(text)
                parser.feed(text)
            # Die Pipe schliessen, bevor auf den Prozess gewartet wird.
            # Bricht die Schleife durch eine Ausnahme ab -- ein on_line-Rueckruf
            # ist ein Qt-Signal und kann werfen --, dann wartet wait() sonst auf
            # einen Prozess, der seinerseits auf Platz in der vollen Pipe
            # wartet. Beide warten aufeinander.
            if process.stdout is not None:
                try:
                    process.stdout.close()
                except OSError:
                    pass
            returncode = process.wait()
            with self._lock:
                self._process = None

        duration = time.monotonic() - started

        if self._cancelled.is_set():
            parser.finish(False)
            raise BuildCancelled()

        iso_location = self._locate_iso(expected_iso)
        parser.finish(returncode == 0 and iso_location is not None)
        if on_progress is not None:
            on_progress(parser.state)

        result = BuildResult(
            returncode=returncode,
            iso_path=None,
            iso_location=iso_location or "",
            duration_seconds=duration,
            lines=list(lines),
            errors=parser.errors,
            warnings=parser.warnings,
        )

        if returncode != 0:
            raise BuildFailed(
                returncode,
                tuple([summarise_failure(parser.errors)]) if parser.errors else (),
                stage=parser.state.label,
            )
        if iso_location is None:
            raise BuildFailed(
                0,
                (
                    f"mkarchiso meldet Erfolg, aber im Ausgabeverzeichnis "
                    f"{self.out_dir} liegt keine ISO-Datei.",
                ),
                stage=parser.state.label,
            )
        log.info("ISO erzeugt: %s (%.0f s)", iso_location, duration)
        return result

    def cancel(self) -> None:
        """Bricht den Lauf ab -- ueber das Ziel, nicht am Ziel vorbei.

        Frueher stand hier terminate/kill auf dem lokalen Prozess. Das ist nur
        lokal richtig: ``wsl.exe`` leitet keine Signale weiter, und bei einem
        Container traefe es den Client statt des Containers. Der Bau lief in
        beiden Faellen weiter, waehrend die Oberflaeche "abgebrochen" meldete.
        """
        self._cancelled.set()
        with self._lock:
            process = self._process
        log.info("Abbruch angefordert")
        self.target.cancel_run(process, grace_seconds=TERMINATE_GRACE_SECONDS)

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    # -- intern ---------------------------------------------------------------
    def _locate_iso(self, expected: str) -> str | None:
        """Den Dateinamen kennen wir selbst -- er folgt aus profiledef.sh.

        Zusaetzlich durchsucht das Ziel sein Ausgabeverzeichnis, falls
        mkarchiso den Namen anders zusammensetzt als erwartet.
        """
        # Bewusst als Text: bei einem Bau in WSL ist das ein Linux-Pfad.
        return self.target.find_iso(self.out_dir, expected)


def _take_complete(buffer: bytes) -> tuple[bytes, list[bytes]]:
    """Zerlegt den Puffer in vollstaendige Zeilen und einen Rest.

    Getrennt wird an ``\\r`` und ``\\n``. Der Rest bleibt im Puffer, bis das
    naechste Stueck kommt -- sonst wuerde eine in der Mitte zerschnittene
    Zeile zweimal auftauchen.

    Gesucht wird mit ``rfind`` statt mit einer Python-Schleife ueber jedes
    einzelne Byte. Der Unterschied faellt erst auf, wenn ein Werkzeug laenger
    ohne Zeilenende schreibt: der Puffer waechst dann bis zur Obergrenze von
    einem Megabyte, und jedes gelesene 4-KB-Stueck durchlief ihn vorher
    vollstaendig -- in Summe eine Viertelmilliarde Schleifendurchlaeufe,
    waehrend die Pipe des Bauprozesses volllaeuft.
    """
    ende = max(buffer.rfind(_NEWLINE), buffer.rfind(_RETURN))
    if ende < 0:
        return buffer, []
    fertig = buffer[: ende + 1]
    return buffer[ende + 1 :], [teil for teil in _TRENNER.split(fertig) if teil]
