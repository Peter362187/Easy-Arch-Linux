"""Anwendungsstart der Oberflaeche."""

from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from ..core.catalog import CatalogError, load_catalog
from ..core.environment import detect_environment
from ..core.packages import PackageService
from ..core.profiles import ProfileService
from . import motion
from .design import ThemeManager
from .main_window import MainWindow
from .packages_worker import PackageController
from .settings import Settings
from .store import SelectionStore

log = logging.getLogger(__name__)


def run(argv: list[str] | None = None) -> int:
    # Eine vorhandene Anwendung wiederverwenden. Qt laesst nur eine zu und
    # wirft sonst ("Please destroy the QApplication singleton"); ausserdem
    # laesst sich ``run()`` nur so ueberhaupt in einem Test aufrufen -- und
    # genau daran lag es, dass der Startpfad jahrelang ungeprueft blieb.
    vorhanden = QApplication.instance()
    app = vorhanden if isinstance(vorhanden, QApplication) else QApplication(
        argv if argv is not None else sys.argv
    )
    app.setApplicationName("Arch Linux ISO Builder")
    app.setOrganizationName("ArchCustomiser")
    # Fusion auf allen Plattformen: der Windows-Stil zeichnet Teile selbst und
    # ignoriert dabei Farben aus dem Stylesheet -- eine dunkle Oberflaeche
    # haette dort weiterhin hellgraue Rahmen.
    app.setStyle("Fusion")
    _set_window_icon(app)

    try:
        catalog = load_catalog()
    except CatalogError as exc:
        # Ohne Katalog gibt es nichts anzuzeigen -- aber der Grund muss
        # sichtbar sein, nicht nur im Log stehen.
        QMessageBox.critical(
            None,
            "Katalog fehlerhaft",
            f"Der Optionskatalog konnte nicht geladen werden:\n\n{exc}",
        )
        log.error("Katalog fehlerhaft: %s", exc)
        return 2

    settings = Settings()
    # Eine Eigenschaft, keine Methode -- die Klammern riefen das Ergebnis auf
    # ("'bool' object is not callable") und liessen das Programm beim Start
    # abstuerzen, bevor ein Fenster zu sehen war.
    motion.set_reduced(settings.animationen_reduzieren)
    theme = ThemeManager(settings)
    theme.apply()

    environment = detect_environment()
    store = SelectionStore(catalog)
    controller = PackageController(PackageService())
    profiles = ProfileService(catalog)

    fenster = MainWindow(
        catalog, store, controller, profiles, settings, theme, environment
    )
    fenster.show()

    # Frueher stand hier eine modale Infobox "Hinweis zur Bauumgebung", die bei
    # jedem Start unter Windows erschien. Der allererste Eindruck war damit ein
    # Dialog mit dem Wort "Hinweis" -- das sieht nach einem Fehler aus, obwohl
    # keiner vorliegt: alles ausser dem eigentlichen Bau funktioniert hier
    # vollstaendig. Dieselbe Auskunft steht jetzt ruhig auf der Startseite.
    if not environment.can_build:
        log.info("Bauumgebung: %s", environment.summary())

    from .widgets.intro import zeige_einmal

    zeige_einmal(fenster)

    # Paketdaten laufen im Hintergrund an; das Fenster ist sofort bedienbar.
    controller.start()

    return app.exec()


def _set_window_icon(app: QApplication) -> None:
    """Das Fenstersymbol -- die Anwendung hatte bisher keines.

    Ohne Symbol zeigen Taskleiste und Fensterwechsel das leere Standardbild von
    Qt, und in einer Reihe offener Fenster ist das Programm nicht wiederzufinden.
    """
    from PySide6.QtGui import QIcon

    from ..core.paths import package_root

    symbol = package_root() / "assets" / "icons" / "archcustomiser.svg"
    if symbol.is_file():
        app.setWindowIcon(QIcon(str(symbol)))
    else:
        log.debug("Kein Fenstersymbol unter %s", symbol)
