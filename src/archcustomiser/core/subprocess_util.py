"""Eine Stelle fuer alles, was ein Prozessstart unter Windows braucht.

Die Oberflaeche wird ueber ``pythonw.exe`` gestartet -- ein GUI-Prozess ohne
Konsole. Startet ein solcher Prozess ein Konsolenprogramm (``wsl.exe``,
``podman``, ``pacman``), legt Windows dafuer eine **neue, sichtbare Konsole**
an. Das gilt auch mit ``capture_output`` und Pipes: die Umleitung betrifft die
Datenstroeme, nicht das Fenster.

Sichtbar wurde das an drei Stellen:

* Bei der Umgebungserkennung blitzten zwei Fenster auf.
* Die Vorabpruefung fuer WSL macht rund acht Aufrufe -- also acht Fenster.
* Waehrend eines Abbruchs laufen ``pgrep`` und ``pkill`` im Halbsekundentakt.
  Dort flackerte es, bis der Bau endlich stand, und stahl dabei den Fokus.

Schliesst der Benutzer eines dieser Fenster, endet der Aufruf -- beim laufenden
Bau also ``wsl.exe``, waehrend mkarchiso in der Verteilung weiterlaeuft.

Zweite Zusicherung: **kein Kind erbt eine Standardeingabe.** Ein Werkzeug, das
unerwartet nachfragt (pacman nach einem Schluesselbund, sudo nach einem
Passwort), haengt sonst bis zum Zeitlimit -- bei der archiso-Installation sind
das 900 Sekunden ohne Abbrechen-Knopf. Mit ``DEVNULL`` bekommt es sofort EOF
und bricht mit einer Meldung ab, die im Dialog erscheint.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

__all__ = ["windows_flags", "run", "popen"]


def windows_flags() -> dict[str, Any]:
    """Zusatzargumente fuer ``subprocess``, damit kein Fenster erscheint.

    Ausserhalb von Windows ist das Ergebnis leer -- der Aufrufer braucht keine
    Fallunterscheidung.
    """
    if os.name != "nt":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}


def run(argv, **kwargs) -> subprocess.CompletedProcess:
    """``subprocess.run`` ohne Konsolenfenster und ohne geerbte Eingabe."""
    kwargs.setdefault("shell", False)
    kwargs.setdefault("check", False)
    if "input" not in kwargs:
        kwargs.setdefault("stdin", subprocess.DEVNULL)
    kwargs.update(windows_flags())
    return subprocess.run(argv, **kwargs)


def popen(argv, **kwargs) -> subprocess.Popen:
    """``subprocess.Popen`` ohne Konsolenfenster und ohne geerbte Eingabe."""
    kwargs.setdefault("shell", False)
    kwargs.setdefault("stdin", subprocess.DEVNULL)
    kwargs.update(windows_flags())
    return subprocess.Popen(argv, **kwargs)
