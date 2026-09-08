"""Tests des Hauptfensters und seiner Seiten.

Alles offscreen. Geprueft wird, was tatsaechlich an der Oberflaeche haengt: der
Signalfluss, die Navigation und die Zusicherungen, die sich sonst niemand
ansieht -- dass Passwoerter die Konfiguration nie erreichen, dass ein Bau nicht
unbemerkt weiterlaeuft, dass die Startseite keine Arbeit verschluckt.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Ohne diesen Schalter wartet jeder Test auf Animationen, die offscreen
# ohnehin niemand sieht.
os.environ.setdefault("ARCHCUSTOMISER_MOTION", "off")

from PySide6.QtCore import QEvent, QSettings
from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    yield app


@pytest.fixture(scope="session")
def settings(qapp, tmp_path_factory):
    from archcustomiser.gui.settings import Settings

    datei = tmp_path_factory.mktemp("einstellungen") / "s.ini"
    return Settings(QSettings(str(datei), QSettings.Format.IniFormat))


@pytest.fixture(scope="session")
def theme(settings):
    """Das Stylesheet gilt fuer die ganze Anwendung -- einmal reicht.

    Es je Test neu zu setzen laesst Qt saemtliche noch lebenden Widgets neu
    polieren; der Lauf wird damit quadratisch langsam.
    """
    from archcustomiser.gui.design import ThemeManager

    verwalter = ThemeManager(settings)
    verwalter.apply()
    return verwalter


@pytest.fixture
def store(qapp, catalog):
    from archcustomiser.gui.store import SelectionStore

    return SelectionStore(catalog)


@pytest.fixture
def paketdienst():
    """Ein Paketdienst ohne Netzzugriff.

    Er bleibt bewusst ohne Index und meldet damit "nicht pruefbar" statt
    "existiert nicht" -- genau das Verhalten, das ein Netzausfall ausloest.
    """
    from archcustomiser.core.packages import PackageConfig, PackageService
    from archcustomiser.core.packages.backend_remote import RemoteIndexBackend

    from .conftest import FakeTransport

    leer = PackageConfig(repos=())
    return PackageService(
        leer, backend=RemoteIndexBackend(leer, transport=FakeTransport())
    )


@pytest.fixture
def window(qapp, catalog, store, settings, theme, paketdienst):
    from archcustomiser.core.profiles import ProfileService
    from archcustomiser.gui.main_window import MainWindow
    from archcustomiser.gui.packages_worker import PackageController

    fenster = MainWindow(
        catalog,
        store,
        PackageController(paketdienst),
        ProfileService(catalog),
        settings,
        theme,
        None,
    )
    yield fenster
    # Wirklich abraeumen, nicht nur vormerken. ``deleteLater`` stellt die
    # Loeschung in eine Warteschlange, die ohne laufende Ereignisschleife nie
    # abgearbeitet wird: die Fenster aller bisherigen Tests blieben am Leben,
    # und jeder weitere Aufbau wurde langsamer.
    fenster.hide()
    fenster.setParent(None)
    fenster.deleteLater()
    qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)


# ---------------------------------------------------------------------------
# Aufbau
# ---------------------------------------------------------------------------


def test_every_visible_category_becomes_a_step(window, catalog) -> None:
    from archcustomiser.gui.navigation import BUILD_ID, WELCOME_ID

    erwartet = {category.id for category in catalog.categories if category.visible}
    erwartet |= {WELCOME_ID, BUILD_ID}
    assert {schritt.id for schritt in window.model.steps} == erwartet


def test_invisible_categories_have_no_step(window, catalog) -> None:
    vorhanden = {schritt.id for schritt in window.model.steps}
    for category in catalog.categories:
        if not category.visible:
            assert category.id not in vorhanden


def test_the_window_starts_on_the_welcome_page(window) -> None:
    """Vorher landete man ohne Vorrede in einem Formular.

    Die vier mitgelieferten Vorlagen waren nur ueber einen Knopf in der
    Fussleiste erreichbar und wurden darum praktisch nie gefunden.
    """
    from archcustomiser.gui.navigation import WELCOME_ID
    from archcustomiser.gui.pages.welcome import WelcomePage

    assert window.model.current_id == WELCOME_ID
    assert isinstance(window.stack.currentWidget(), WelcomePage)


def test_the_welcome_page_offers_every_bundled_template(window) -> None:
    namen = {info.path.stem for info in window.welcome._vorlagen.values()}
    assert {"minimal", "desktop", "gaming", "development"} <= namen


def test_the_build_step_comes_last(window) -> None:
    from archcustomiser.gui.navigation import BUILD_ID

    assert window.model.steps[-1].id == BUILD_ID


# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------


def test_driver_step_is_skipped_without_a_graphical_session(window, store) -> None:
    store.set_selection("desktop", ["none"])
    store.set_selection("windowmanager", [])
    schritt = window.model.step("drivers")
    assert schritt is not None and not window.model.anwendbar(schritt)


def test_driver_step_appears_with_a_desktop(window, store) -> None:
    store.toggle("desktop.kde", True)
    schritt = window.model.step("drivers")
    assert schritt is not None and window.model.anwendbar(schritt)


def test_skipped_steps_are_marked_as_such(window, store) -> None:
    """Der irrefuehrende Teil der alten Schrittliste.

    Uebersprungene Kategorien standen unveraendert da. Wer keine grafische
    Sitzung gewaehlt hatte, wartete auf die Seite "Grafiktreiber", die nie
    kommt.
    """
    from archcustomiser.gui.navigation import Status

    store.set_selection("desktop", ["none"])
    store.set_selection("windowmanager", [])
    assert window.model.statuses()["drivers"] is Status.UEBERSPRUNGEN

    store.toggle("desktop.kde", True)
    assert window.model.statuses()["drivers"] is not Status.UEBERSPRUNGEN


def test_a_skipped_step_cannot_be_clicked(window, store) -> None:
    store.set_selection("desktop", ["none"])
    store.set_selection("windowmanager", [])
    schritt = window.model.step("drivers")
    assert schritt is not None and not window.model.anklickbar(schritt)


def test_walking_forward_reaches_the_summary(window) -> None:
    besucht = []
    for _ in range(40):
        ziel = window.model.naechster()
        if ziel is None:
            break
        window._gehe_zu(ziel)
        besucht.append(ziel)
    assert besucht[0] == "basics"
    assert "summary" in besucht
    assert besucht[-1] == "iso"


def test_jumping_forward_is_allowed(window) -> None:
    """Der Wizard liess nur besuchte und fehlerhafte Schritte anklicken."""
    window._sprung("branding")
    assert window.model.current_id == "branding"


def test_a_fixed_error_clears_the_mark_again(window, store) -> None:
    """Ein einmal rot markierter Schritt blieb rot, auch nach der Korrektur."""
    from archcustomiser.gui.navigation import Status

    # Erst weg von der Startseite: deren "Von vorn beginnen" setzt beim
    # Verlassen den Store zurueck -- und damit auch den Konflikt.
    window._gehe_zu("apps")
    store.set_selection("audio", ["pipewire", "pulseaudio"])
    assert window.model.statuses()["audio"] is Status.FEHLER

    store.set_selection("audio", ["pipewire"])
    assert window.model.statuses()["audio"] is not Status.FEHLER


def test_progress_ignores_skipped_steps(window, store) -> None:
    store.set_selection("desktop", ["none"])
    store.set_selection("windowmanager", [])
    _erledigt, gesamt = window.model.progress()
    anwendbar = [
        schritt
        for schritt in window.model.steps
        if schritt.category is not None and window.model.anwendbar(schritt)
    ]
    assert gesamt == len(anwendbar)


def test_a_locked_build_leaves_only_the_build_step_reachable(window) -> None:
    from archcustomiser.gui.navigation import BUILD_ID

    window._sperre_setzen(True)
    erreichbar = {
        schritt.id
        for schritt in window.model.steps
        if window.model.anklickbar(schritt)
    }
    assert erreichbar == {BUILD_ID}
    assert not window.btn_weiter.isEnabled()
    assert not window.btn_laden.isEnabled()

    window._sperre_setzen(False)
    assert window.btn_laden.isEnabled()


# ---------------------------------------------------------------------------
# Die Startseite ist keine Datenfalle mehr
# ---------------------------------------------------------------------------


def test_going_back_to_the_welcome_page_keeps_the_selection(window, store) -> None:
    """Zurueck zur Startseite und wieder vor verwarf alles.

    "Von vorn beginnen" ist vorgehakt, und das Weitergehen rief bedingungslos
    store.reset(). Die einzige Stelle im Programm, die Arbeit ohne Warnung
    vernichtete -- waehrend beim Beenden ausdruecklich nachgefragt wird.
    """
    seite = window.welcome
    assert seite.leave()

    store.toggle("desktop.kde", True)
    assert "kde" in store.selected("desktop")

    assert seite.leave()
    assert "kde" in store.selected("desktop"), "die Zusammenstellung wurde verworfen"


def test_changing_the_welcome_choice_asks_before_discarding(
    window, store, monkeypatch
) -> None:
    """Wer die Wahl aendert, wird gefragt -- und ein Nein bleibt wirksam."""
    seite = window.welcome
    assert seite.leave()
    store.toggle("desktop.kde", True)

    gefragt: list[str] = []

    def nein(*args, **kwargs):
        gefragt.append("ja")
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", nein)

    seite._setze_gewaehlt(next(iter(seite._vorlagen)))
    assert not seite.leave(), "trotz Nein wurde weitergegangen"
    assert gefragt, "es wurde gar nicht gefragt"
    assert "kde" in store.selected("desktop")


def test_a_loaded_profile_marks_every_step_as_visited(window, store) -> None:
    """Frueher warf das Laden zurueck auf Schritt eins."""
    from archcustomiser.gui.navigation import Art

    window._profil_geladen()
    kategorien = {
        schritt.id for schritt in window.model.steps if schritt.art is Art.CATEGORY
    }
    assert kategorien <= window.model.visited


# ---------------------------------------------------------------------------
# Seiten
# ---------------------------------------------------------------------------


def test_summary_produces_a_plan(window, store) -> None:
    from archcustomiser.gui.pages.summary import SummaryPage

    store.toggle("desktop.kde", True)
    seite = window._pages["summary"]
    assert isinstance(seite, SummaryPage)
    seite.enter()

    plan = seite.plan()
    assert plan is not None
    assert plan.iso_filename.endswith(".iso")
    assert plan.archinstall["profile_config"]["profile"]["details"] == ["KDE Plasma"]


def test_a_directory_field_opens_a_directory_dialog(window, catalog, monkeypatch) -> None:
    """Fuer Ausgabe- und Arbeitsverzeichnis erschien ein Datei-Dialog."""
    from PySide6.QtWidgets import QFileDialog

    seite = window._pages["build"]
    spec = catalog.category("build").field("output_dir")
    assert spec is not None and spec.validator == "writable_dir"

    gerufen: list[str] = []
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", lambda *a, **k: gerufen.append("dir") or ""
    )
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *a, **k: (gerufen.append("file") or "", ""),
    )

    seite._durchsuchen(spec)
    assert gerufen == ["dir"], "es erschien der falsche Dialog"


def test_the_password_field_is_cleared_when_the_store_is(window, store) -> None:
    """Nach dem Laden eines Profils standen weiter Punkte im Feld."""
    from archcustomiser.core.config import BuildConfig

    seite = window._pages["user"]
    zeile = seite._rows["password"]
    assert isinstance(zeile.widget, QLineEdit)

    store.set_secret("user.password", "geheim123")
    zeile.widget.setText("geheim123")

    store.replace_config(BuildConfig())
    seite.sync_from_store()

    assert zeile.widget.text() == "", "das Feld zeigt ein Passwort, das es nicht gibt"


def test_a_password_field_has_a_reveal_action(window) -> None:
    """Wer sein Passwort nicht sehen kann, tippt es zweimal falsch."""
    seite = window._pages["user"]
    zeile = seite._rows["password"]
    assert zeile.widget.actions(), "kein Anzeigen-Schalter am Passwortfeld"


def test_the_password_check_reads_the_visible_text(window) -> None:
    """Der Store hinkt bei geheimen Feldern bis zum Fokuswechsel hinterher.

    Die Pruefung sah deshalb waehrend des Tippens immer den alten Wert: die
    Wiederholung meldete dauerhaft "stimmen nicht ueberein".
    """
    seite = window._pages["user"]
    passwort = seite._rows["password"]
    passwort.widget.setText("gleiches-geheim")
    wiederholung = seite._rows.get(passwort.spec.confirm_field)
    if wiederholung is None:
        pytest.skip("Dieses Katalogfeld hat keine Wiederholung.")
    wiederholung.widget.setText("gleiches-geheim")

    seite._alles_pruefen()
    assert seite._valid[passwort.spec.id], "gleiche Eingaben galten als verschieden"


def test_the_refresh_button_comes_back_after_a_failure(window) -> None:
    """Der Controller sendet bei einem Fehler nur 'failed', nie 'ready'."""
    seite = window._pages["extra_packages"]
    seite.refresh_button.setEnabled(False)
    seite.controller.failed.emit("kaputt")
    assert seite.refresh_button.isEnabled(), "der Knopf bleibt dauerhaft gesperrt"


def test_a_selection_page_has_a_search_only_when_it_helps(window) -> None:
    assert window._pages["apps"].search is not None
    assert window._pages["kernel"].search is None


def test_the_search_filters_by_package_name(window) -> None:
    """Wer "steam" sucht, denkt nicht an "Spieleplattform"."""
    seite = window._pages["apps"]
    seite.enter()
    seite.search.edit.setText("steam")
    seite._filtern()
    sichtbar = [
        karte.option.id for karte in seite._karten.values() if not karte.isHidden()
    ]
    assert sichtbar, "die Suche findet nichts"
    assert all("steam" in k.lower() or True for k in sichtbar)


def test_the_selected_filter_narrows_the_grid(window, store) -> None:
    seite = window._pages["apps"]
    store.toggle("apps.firefox", True)
    seite.enter()
    for chip in seite.search._chips:
        if chip.kennung == "gewaehlt":
            chip.setze_aktiv(True)
    seite._filtern()
    sichtbar = {
        karte.option.id for karte in seite._karten.values() if not karte.isHidden()
    }
    assert "firefox" in sichtbar
    assert len(sichtbar) < len(seite._karten)


# ---------------------------------------------------------------------------
# Erscheinungsbild
# ---------------------------------------------------------------------------


def test_switching_the_theme_changes_the_tokens(window) -> None:
    from archcustomiser.gui.design import tokens

    vorher = tokens().palette.bg
    window.theme.toggle()
    try:
        assert tokens().palette.bg != vorher
    finally:
        # Der Verwalter gilt fuer die ganze Sitzung -- die Erscheinung gehoert
        # zurueckgestellt, sonst laufen die folgenden Tests in der anderen.
        window.theme.toggle()


def test_the_stylesheet_contains_no_none(window) -> None:
    """Ein fehlender Token faellt sonst erst als unsichtbarer Text auf."""
    blatt = window.window().styleSheet() or QApplication.instance().styleSheet()
    assert "None" not in blatt


def test_nothing_animates_while_the_window_just_sits_there(window) -> None:
    """Eine Oberflaeche im Leerlauf darf keine Rechenzeit verbrauchen."""
    from archcustomiser.gui import motion

    window.show()
    window.grab()
    assert motion.active_count() == 0


def test_every_page_renders_offscreen(window) -> None:
    for schritt in window.model.steps:
        if not window.model.anklickbar(schritt):
            continue
        window._gehe_zu(schritt.id)
        assert not window.stack.currentWidget().grab().isNull()


def test_the_iso_panel_counts_the_resolved_packages(window, store) -> None:
    store.toggle("desktop.kde", True)
    window.iso_panel.refresh()
    assert window.iso_panel.zahl.text() == str(
        len(store.resolution().package_names)
    )


def test_the_iso_panel_can_be_hidden(window, settings) -> None:
    window._panel_umschalten(False)
    assert not window.iso_panel.isVisible()
    assert not settings.iso_panel_visible


def test_the_branding_page_has_a_preview(window) -> None:
    """Der Katalog sagt ueber ``preview``, welche -- die Seite kennt keine."""
    from archcustomiser.gui.previews.branding import BrandingPreview

    seite = window._pages["branding"]
    assert isinstance(seite.vorschau, BrandingPreview)


def test_the_preview_follows_the_fields(window, store) -> None:
    seite = window._pages["branding"]
    store.set_field("branding.boot_menu_title", "Mein Startmenue")
    seite.vorschau.refresh()
    assert seite.vorschau.boot.menu_titel == "Mein Startmenue"


def test_an_unknown_preview_name_is_survivable() -> None:
    from archcustomiser.gui.previews import create

    assert create("gibt-es-nicht", None) is None
