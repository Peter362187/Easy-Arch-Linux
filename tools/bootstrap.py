"""Richtet die Programmumgebung ein und startet das Programm.

Der gemeinsame Kern von ``ArchCustomiser.bat`` und ``archcustomiser.sh``.
Vorher stand dieselbe Abfolge -- Python pruefen, venv anlegen, Abhaengigkeiten
installieren, Importprobe, starten -- zweimal getrennt da, einmal in cmd und
einmal in sh. Die beiden liefen auseinander:

* die ``.bat`` reichte Kommandozeilenargumente nicht durch, das Shellskript
  schon; ``--check-env`` und ``--dry-run`` waren unter Windows also gar nicht
  erreichbar,
* beide installierten ``.[dev]`` samt pytest fuer Endanwender,
* die Texte ("rund 670 MB") standen an zwei Stellen.

Dieses Modul laeuft mit dem **System-Python** und darf deshalb ausser der
Standardbibliothek nichts voraussetzen -- es wird ja gerade aufgerufen, um die
Abhaengigkeiten erst zu beschaffen.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

MINDESTVERSION = (3, 11)
PAKETE = ["-e", "."]
"""Ohne dev-Extras: pytest und pytest-qt gehoeren zur Entwicklung, nicht zur
Benutzung. Wer sie braucht, ruft ``pip install -e ".[dev]"`` selbst auf."""


def venv_python(wurzel: Path) -> Path:
    """Der Interpreter der Programmumgebung -- je nach Betriebssystem."""
    if os.name == "nt":
        return wurzel / ".venv" / "Scripts" / "python.exe"
    return wurzel / ".venv" / "bin" / "python"


def venv_pythonw(wurzel: Path) -> Path:
    """Die fensterlose Fassung; gibt es nur unter Windows."""
    return wurzel / ".venv" / "Scripts" / "pythonw.exe"


def _traegt(python: Path) -> bool:
    """Ob die Umgebung das Programm und Qt tatsaechlich laden kann.

    Pruefen statt hoffen: der Test deckt die erste Einrichtung ab und ein
    ``git pull``, das eine neue Abhaengigkeit mitgebracht hat.
    """
    if not python.is_file():
        return False
    try:
        ergebnis = subprocess.run(
            [str(python), "-c", "import archcustomiser, PySide6"],
            capture_output=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return ergebnis.returncode == 0


def _laeuft(python: Path) -> bool:
    """Ob der Interpreter der Umgebung ueberhaupt startet.

    Zeigt ``pyvenv.cfg`` auf ein entferntes oder aktualisiertes Basis-Python,
    meldet er "No Python at ..." -- und die frueheren Skripte deuteten das als
    fehlende Abhaengigkeit und rieten zu einer Internetverbindung.
    """
    if not python.is_file():
        return False
    try:
        ergebnis = subprocess.run(
            [str(python), "-c", "pass"], capture_output=True, timeout=60, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return ergebnis.returncode == 0


def sicherstellen(wurzel: Path, *, ausgabe=print) -> Path:
    """Legt die Programmumgebung an, falls noetig, und liefert ihren Python.

    Wirft ``RuntimeError`` mit einem Satz, der sagt, was zu tun ist.
    """
    if sys.version_info < MINDESTVERSION:
        gebraucht = ".".join(str(teil) for teil in MINDESTVERSION)
        raise RuntimeError(
            f"Dieses Python ist {sys.version.split()[0]}, gebraucht wird "
            f"{gebraucht} oder neuer."
        )

    python = venv_python(wurzel)
    umgebung = wurzel / ".venv"

    if umgebung.exists() and not _laeuft(python):
        ausgabe("  Die vorhandene Programmumgebung ist defekt -- sie wird neu angelegt.")
        shutil.rmtree(umgebung, ignore_errors=True)

    if not umgebung.exists():
        ausgabe("")
        ausgabe("  Einmalige Einrichtung. Das dauert ein paar Minuten und laedt")
        ausgabe("  rund 400 MB -- danach startet das Programm sofort.")
        ausgabe("")
        ergebnis = subprocess.run(
            [sys.executable, "-m", "venv", str(umgebung)], check=False
        )
        if ergebnis.returncode != 0:
            # Eine halb angelegte Umgebung ist schlimmer als gar keine: der
            # naechste Start haelt sie fuer fertig und scheitert woanders.
            shutil.rmtree(umgebung, ignore_errors=True)
            raise RuntimeError(
                "Die Programmumgebung liess sich nicht anlegen. Haeufig ist es "
                "ein zu langer Pfad, fehlendes Schreibrecht in diesem Ordner, "
                "eine volle Festplatte -- oder unter Debian und Ubuntu das "
                "fehlende Paket python3-venv."
            )

    if _traegt(python):
        return python

    ausgabe("")
    ausgabe("  Abhaengigkeiten werden installiert ...")
    ausgabe("")
    subprocess.run(
        [str(python), "-m", "pip", "install", "--upgrade", "pip"],
        capture_output=True,
        check=False,
    )
    ergebnis = subprocess.run(
        [str(python), "-m", "pip", "install", *PAKETE], cwd=str(wurzel), check=False
    )
    if ergebnis.returncode != 0:
        raise RuntimeError(
            "Die Installation ist fehlgeschlagen. Die Meldungen darueber sagen, "
            "woran es lag -- haeufig ist es eine fehlende Internetverbindung."
        )

    if not _traegt(python):
        # pip kann melden, fertig zu sein, ohne dass sich das Programm danach
        # importieren laesst. Die genaue Meldung ist dann das Wertvollste.
        pruefung = subprocess.run(
            [str(python), "-c", "import archcustomiser, PySide6"],
            capture_output=True,
            text=True,
            check=False,
        )
        raise RuntimeError(
            "Die Installation lief durch, das Programm laesst sich aber nicht "
            "laden:\n" + (pruefung.stderr or "").strip()
        )

    ausgabe("")
    ausgabe("  Fertig eingerichtet.")
    ausgabe("")
    return python


def main(argv: list[str] | None = None) -> int:
    """Einrichten und starten. Argumente gehen unveraendert weiter."""
    argumente = list(sys.argv[1:] if argv is None else argv)
    wurzel = Path(__file__).resolve().parent.parent

    fensterlos = False
    if "--fensterlos" in argumente:
        argumente.remove("--fensterlos")
        fensterlos = True

    try:
        python = sicherstellen(wurzel)
    except RuntimeError as fehler:
        print("", file=sys.stderr)
        print("  " + str(fehler), file=sys.stderr)
        print("", file=sys.stderr)
        return 1

    # Unter Windows startet die Oberflaeche ueber pythonw.exe, damit kein
    # schwarzes Fenster danebensteht. Bei einem Kommandozeilenaufruf waere das
    # falsch -- dort ist die Ausgabe der ganze Zweck.
    starter = python
    if fensterlos and not argumente:
        kandidat = venv_pythonw(wurzel)
        if kandidat.is_file():
            starter = kandidat

    aufruf = [str(starter), "-m", "archcustomiser", *argumente]
    if starter != python:
        # Losgeloest starten: das Startskript soll sich sofort beenden.
        subprocess.Popen(aufruf, cwd=str(wurzel))
        return 0
    return subprocess.run(aufruf, cwd=str(wurzel), check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
