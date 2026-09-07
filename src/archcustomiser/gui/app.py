"""Anwendungsstart der Oberflaeche."""

from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from ..core.catalog import CatalogError, load_catalog
from ..core.environment import detect_environment
from ..core.packages import PackageService
from ..core.profiles import ProfileService
from . import theme
from .packages_worker import PackageController
from .store import SelectionStore
from .wizard import BuildWizard

log = logging.getLogger(__name__)


def run(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName("Arch Linux ISO Builder")
    app.setOrganizationName("ArchCustomiser")
    # Ein Stylesheet fuer die ganze Anwendung, statt zwanzig verstreuter
    # setStyleSheet-Aufrufe in den einzelnen Seiten.
    app.setStyleSheet(theme.application_stylesheet())
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

    environment = detect_environment()
    store = SelectionStore(catalog)
    controller = PackageController(PackageService())
    profiles = ProfileService(catalog)

    wizard = BuildWizard(catalog, store, controller, profiles, environment)
    wizard.show()

    # Frueher stand hier eine modale Infobox "Hinweis zur Bauumgebung", die
    # bei jedem Start unter Windows erschien. Der allererste Eindruck war
    # damit ein Dialog mit dem Wort "Hinweis" -- das sieht nach einem Fehler
    # aus, obwohl keiner vorliegt: alles ausser dem eigentlichen Build
    # funktioniert hier vollstaendig. Dieselbe Auskunft steht jetzt ruhig
    # auf der Startseite.
    if not environment.can_build:
        log.info("Bauumgebung: %s", environment.summary())

    # Paketdaten laufen im Hintergrund an; der Wizard ist sofort bedienbar.
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
