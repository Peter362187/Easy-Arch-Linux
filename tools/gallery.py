"""Bildschirmfotos der Oberflaeche -- offscreen, ohne dass ein Fenster aufgeht.

Zwei Zwecke:

* **Sehen, was Tests nicht sehen.** Ein Test prueft Endzustaende; ob eine Karte
  zu eng steht oder eine Beschriftung abgeschnitten ist, sieht man nur im Bild.
* **Doku.** Die Bilder in ``docs/screenshots`` entstehen hier und lassen sich
  jederzeit neu erzeugen, statt von Hand abfotografiert zu werden.

Aufruf::

    python tools/gallery.py                 # nach docs/screenshots
    python tools/gallery.py --out /tmp/bild # woandershin
    python tools/gallery.py --nur dunkel    # nur eine Erscheinung

Es wird nichts gebaut und nichts installiert -- nur gezeichnet.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Vor jedem Qt-Import: sonst sucht Qt einen Bildschirm, den es hier nicht gibt.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ARCHCUSTOMISER_MOTION", "off")

WURZEL = Path(__file__).resolve().parent.parent
if str(WURZEL / "src") not in sys.path:
    sys.path.insert(0, str(WURZEL / "src"))

BREITE = 1280
HOEHE = 820


def _fenster(dunkel: bool, tmp: Path):
    from PySide6.QtCore import QSettings

    from archcustomiser.core.catalog import load_catalog
    from archcustomiser.core.packages import PackageConfig, PackageService
    from archcustomiser.core.packages.backend_remote import RemoteIndexBackend
    from archcustomiser.core.profiles import ProfileService
    from archcustomiser.gui.design import ThemeManager
    from archcustomiser.gui.main_window import MainWindow
    from archcustomiser.gui.packages_worker import PackageController
    from archcustomiser.gui.settings import Settings
    from archcustomiser.gui.store import SelectionStore

    einstellungen = Settings(
        QSettings(str(tmp / f"{'dunkel' if dunkel else 'hell'}.ini"), QSettings.Format.IniFormat)
    )
    einstellungen.theme_mode = "dark" if dunkel else "light"
    theme = ThemeManager(einstellungen)
    theme.apply()

    katalog = load_catalog()
    store = SelectionStore(katalog)
    # Eine gefuellte Auswahl zeigt mehr als ein leeres Programm.
    store.toggle("desktop.kde", True)
    store.toggle("apps.firefox", True)
    store.toggle("apps.git", True)

    # Kein Netz: der Dienst bleibt ohne Index und meldet "nicht pruefbar".
    leer = PackageConfig(repos=())
    dienst = PackageService(leer, backend=RemoteIndexBackend(leer))

    fenster = MainWindow(
        katalog,
        store,
        PackageController(dienst),
        ProfileService(katalog),
        einstellungen,
        theme,
        None,
    )
    fenster.resize(BREITE, HOEHE)
    fenster.show()
    return fenster


def _aufnehmen(fenster, ziel: Path, name: str) -> Path:
    datei = ziel / f"{name}.png"
    fenster.grab().save(str(datei))
    return datei


def erzeuge(ziel: Path, erscheinungen: list[str]) -> list[Path]:
    import tempfile

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")

    ziel.mkdir(parents=True, exist_ok=True)
    gemacht: list[Path] = []

    with tempfile.TemporaryDirectory() as roh:
        tmp = Path(roh)
        for erscheinung in erscheinungen:
            dunkel = erscheinung == "dunkel"
            fenster = _fenster(dunkel, tmp)
            for schritt in fenster.model.steps:
                if not fenster.model.anklickbar(schritt):
                    continue
                fenster._gehe_zu(schritt.id, animiert=False)
                app.processEvents()
                gemacht.append(
                    _aufnehmen(fenster, ziel, f"{erscheinung}-{schritt.id}")
                )
            fenster.hide()
            fenster.setParent(None)
            fenster.deleteLater()
            app.processEvents()
    return gemacht


def main(argv: list[str] | None = None) -> int:
    zerleger = argparse.ArgumentParser(description=__doc__)
    zerleger.add_argument(
        "--out",
        default=str(WURZEL / "docs" / "screenshots"),
        help="Zielverzeichnis (Vorgabe: docs/screenshots)",
    )
    zerleger.add_argument(
        "--nur",
        choices=["dunkel", "hell"],
        help="nur eine Erscheinung aufnehmen",
    )
    argumente = zerleger.parse_args(argv)

    erscheinungen = [argumente.nur] if argumente.nur else ["dunkel", "hell"]
    dateien = erzeuge(Path(argumente.out), erscheinungen)
    for datei in dateien:
        print(datei)
    print(f"{len(dateien)} Bilder in {argumente.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
