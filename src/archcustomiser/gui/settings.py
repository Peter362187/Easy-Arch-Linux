"""Was sich der Benutzer ueber Sitzungen hinweg merkt.

Bewusst eine eigene Schicht ueber ``QSettings`` statt verstreuter Aufrufe:

* die Schluessel stehen an einer Stelle, mit ihren Vorgaben,
* Tests bekommen eine eigene Datei untergeschoben, statt in die Registrierung
  des Entwicklers zu schreiben,
* und die Frage "sollen Animationen laufen?" wird hier beantwortet, nicht in
  zwanzig Widgets.

Zur letzten Frage gehoert die **Systemeinstellung**. Wer bei Windows
"Animationen in Windows anzeigen" abschaltet, bei macOS "Bewegung reduzieren"
oder bei GNOME ``enable-animations`` -- der meint das auch fuer dieses
Programm. Die Abfrage ist best effort mit Zeitlimit: schlaegt sie fehl, wird
nichts behauptet und die Vorgabe gilt.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

from PySide6.QtCore import QObject, QSettings, Signal

log = logging.getLogger(__name__)

THEME_DUNKEL = "dark"
THEME_HELL = "light"
THEME_SYSTEM = "system"

BEWEGUNG_AUTO = "auto"
BEWEGUNG_AN = "on"
BEWEGUNG_AUS = "off"

AKZENT_VORGABE = "#1793d1"
"""Das Blau von Arch Linux -- dieselbe Farbe, die die Oberflaeche bisher als
Akzent benutzte."""


class Settings(QObject):
    """Die Einstellungen der Anwendung.

    ``backend`` laesst sich fuer Tests ersetzen: eine ``QSettings``-Instanz auf
    eine Datei in ``tmp_path`` schreibt nirgends, wo sie stoert.
    """

    changed = Signal(str)

    def __init__(self, backend: QSettings | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._store = backend if backend is not None else QSettings("ArchCustomiser", "ArchCustomiser")

    # -- Lesen und Schreiben --------------------------------------------------
    def _lies(self, schluessel: str, vorgabe: str) -> str:
        wert = self._store.value(schluessel, vorgabe)
        return str(wert) if wert is not None else vorgabe

    def _schreib(self, schluessel: str, wert: str) -> None:
        if self._lies(schluessel, "") == wert:
            return
        self._store.setValue(schluessel, wert)
        self.changed.emit(schluessel)

    # -- Design ---------------------------------------------------------------
    @property
    def theme_mode(self) -> str:
        """``dark``, ``light`` oder ``system``. Vorgabe: dunkel."""
        wert = self._lies("design/theme", THEME_DUNKEL)
        return wert if wert in (THEME_DUNKEL, THEME_HELL, THEME_SYSTEM) else THEME_DUNKEL

    @theme_mode.setter
    def theme_mode(self, wert: str) -> None:
        self._schreib("design/theme", wert)

    @property
    def accent(self) -> str:
        return self._lies("design/accent", AKZENT_VORGABE)

    @accent.setter
    def accent(self, wert: str) -> None:
        self._schreib("design/accent", wert)

    # -- Bewegung -------------------------------------------------------------
    @property
    def reduce_motion(self) -> str:
        """``auto`` (dem System folgen), ``on`` oder ``off``."""
        wert = self._lies("design/reduce_motion", BEWEGUNG_AUTO)
        return wert if wert in (BEWEGUNG_AUTO, BEWEGUNG_AN, BEWEGUNG_AUS) else BEWEGUNG_AUTO

    @reduce_motion.setter
    def reduce_motion(self, wert: str) -> None:
        self._schreib("design/reduce_motion", wert)

    @property
    def animationen_reduzieren(self) -> bool:
        """Die Antwort, auf die es ankommt.

        Eine ausdrueckliche Einstellung schlaegt die Systemeinstellung -- wer
        hier etwas festlegt, will es auch.
        """
        wahl = self.reduce_motion
        if wahl == BEWEGUNG_AN:
            return True
        if wahl == BEWEGUNG_AUS:
            return False
        return system_bevorzugt_ruhe()

    # -- Sonstiges ------------------------------------------------------------
    @property
    def iso_panel_visible(self) -> bool:
        return self._lies("ansicht/iso_panel", "1") != "0"

    @iso_panel_visible.setter
    def iso_panel_visible(self, wert: bool) -> None:
        self._schreib("ansicht/iso_panel", "1" if wert else "0")

    @property
    def letzter_profilordner(self) -> str:
        """Wo der Benutzer zuletzt ein Profil abgelegt hat.

        Vorher startete "Laden" im Paketordner der Vorlagen, "Speichern" im
        Benutzerordner und die Startseite im Heimatverzeichnis -- wer speicherte
        und danach lud, fand seine Datei nicht.
        """
        return self._lies("pfade/profile", "")

    @letzter_profilordner.setter
    def letzter_profilordner(self, wert: str) -> None:
        self._schreib("pfade/profile", wert)


# ---------------------------------------------------------------------------
# Die Systemeinstellung
# ---------------------------------------------------------------------------


def system_bevorzugt_ruhe() -> bool:
    """Ob das Betriebssystem weniger Bewegung wuenscht.

    Jeder Zweig ist gegen Fehler abgesichert: eine Barrierefreiheits-Abfrage
    darf das Programm nicht aufhalten. Im Zweifel wird ``False`` geliefert --
    also nichts behauptet.
    """
    try:
        if sys.platform == "win32":
            return _windows_ruhe()
        if sys.platform == "darwin":
            return _macos_ruhe()
        return _linux_ruhe()
    except Exception:
        log.debug("Systemeinstellung zu Animationen nicht lesbar", exc_info=True)
        return False


def _windows_ruhe() -> bool:
    """``SPI_GETCLIENTAREAANIMATION`` -- 'Animationen in Windows anzeigen'."""
    import ctypes

    SPI_GETCLIENTAREAANIMATION = 0x1042
    aktiviert = ctypes.c_int(1)
    ergebnis = ctypes.windll.user32.SystemParametersInfoW(
        SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(aktiviert), 0
    )
    if not ergebnis:
        return False
    return aktiviert.value == 0


def _macos_ruhe() -> bool:
    """``defaults read com.apple.universalaccess reduceMotion``."""
    ergebnis = subprocess.run(
        ["defaults", "read", "com.apple.universalaccess", "reduceMotion"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    return ergebnis.returncode == 0 and ergebnis.stdout.strip() == "1"


def _linux_ruhe() -> bool:
    """GNOME: ``org.gnome.desktop.interface enable-animations``."""
    if os.environ.get("ARCHCUSTOMISER_MOTION") == "off":
        return True
    ergebnis = subprocess.run(
        ["gsettings", "get", "org.gnome.desktop.interface", "enable-animations"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    return ergebnis.returncode == 0 and ergebnis.stdout.strip() == "false"
