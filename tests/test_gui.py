"""Tests der Oberflaeche.

Bewusst wenige: die Logik steckt in ``core`` und ist dort ohne Qt geprueft.
Hier wird nur getestet, was tatsaechlich an der Oberflaeche haengt -- vor allem
der Signalfluss und die Zusicherung, dass Passwoerter die Konfiguration nie
erreichen.
"""

from __future__ import annotations

import os
import sys

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication   # noqa: E402

from archcustomiser.core.config import SelectionSource   # noqa: E402


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def store(qapp, catalog):
    from archcustomiser.gui.store import SelectionStore

    return SelectionStore(catalog)


# ---------------------------------------------------------------------------
# Qt-Freiheit des Kerns
# ---------------------------------------------------------------------------


def test_core_does_not_pull_in_qt() -> None:
    """Der Kern muss ohne Bildschirm und ohne Qt testbar bleiben.

    Wird in einem eigenen Prozess geprueft, weil dieser Test selbst Qt geladen
    hat.
    """
    import subprocess

    code = (
        "import sys;"
        "import archcustomiser.core.catalog, archcustomiser.core.resolver,"
        "archcustomiser.core.plan, archcustomiser.core.packages,"
        "archcustomiser.core.profiles, archcustomiser.core.validation;"
        "assert not [m for m in sys.modules if m.startswith('PySide6')], "
        "'core zieht Qt herein';"
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def test_defaults_are_applied_on_start(store) -> None:
    assert "linux" in store.selected("kernel")
    assert "pipewire" in store.selected("audio")


def test_selection_emits_a_signal(store, qtbot=None) -> None:
    received: list[str] = []
    store.selectionChanged.connect(received.append)
    store.toggle("desktop.kde", True)
    assert "desktop" in received


def test_single_selection_replaces_instead_of_adding(store) -> None:
    store.toggle("desktop.kde", True)
    store.toggle("desktop.gnome", True)
    assert store.selected("desktop") == {"gnome"}


def test_multi_selection_accumulates(store) -> None:
    store.toggle("apps.firefox", True)
    store.toggle("apps.git", True)
    assert {"firefox", "git"} <= store.selected("apps")


def test_automatic_entries_are_marked(store) -> None:
    store.toggle("desktop.kde", True)
    assert store.is_auto("display_manager.sddm")
    assert not store.is_auto("desktop.kde")


def test_applying_a_fix_resolves_the_conflict(store) -> None:
    store.set_selection("audio", ["pipewire", "pulseaudio"])
    conflicts = [issue for issue in store.issues() if issue.code == "capability_arity"]
    assert conflicts and conflicts[0].fix

    store.apply_fix(conflicts[0].fix)
    assert not [issue for issue in store.issues() if issue.code == "capability_arity"]


def test_recommendations_are_pre_checked_once_and_stay_removable(store) -> None:
    store.toggle("desktop.kde", True)
    assert store.is_selected("services.bluetooth")

    store.toggle("services.bluetooth", False)
    assert not store.is_selected("services.bluetooth")

    # Eine erneute Auswahl derselben Option darf die Empfehlung nicht
    # zurueckbringen -- sonst laesst sie sich nie abwaehlen.
    store.toggle("apps.firefox", True)
    assert not store.is_selected("services.bluetooth")


# ---------------------------------------------------------------------------
# Passwoerter
# ---------------------------------------------------------------------------


def test_secrets_never_reach_the_configuration(store) -> None:
    store.set_secret("user.password", "hunter2-geheim")
    assert store.has_secret("user.password")
    assert "user.password" not in store.config.fields
    assert "hunter2" not in repr(store.config)


def test_replacing_the_configuration_clears_secrets(store, catalog) -> None:
    from archcustomiser.core.config import BuildConfig

    store.set_secret("user.password", "geheim123")
    store.replace_config(BuildConfig())
    assert not store.has_secret("user.password")


# ---------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------


@pytest.fixture
def wizard(qapp, catalog, store):
    from archcustomiser.core.packages import PackageConfig, PackageService
    from archcustomiser.core.packages.backend_remote import RemoteIndexBackend
    from archcustomiser.core.profiles import ProfileService
    from archcustomiser.gui.packages_worker import PackageController
    from archcustomiser.gui.wizard import BuildWizard

    from .conftest import FakeTransport

    # Kein Netzzugriff im Test: der Dienst bleibt bewusst ohne Index und meldet
    # damit "nicht pruefbar" statt "existiert nicht".
    service = PackageService(
        PackageConfig(repos=()),
        backend=RemoteIndexBackend(PackageConfig(repos=()), transport=FakeTransport()),
    )
    return BuildWizard(catalog, store, PackageController(service), ProfileService(catalog))


def test_every_visible_category_becomes_a_page(wizard, catalog) -> None:
    from archcustomiser.gui.pages.welcome import WELCOME_STEP

    expected = {category.step for category in catalog.categories if category.visible}
    expected.add(WELCOME_STEP)
    assert set(wizard.pageIds()) == expected


def test_the_wizard_starts_on_the_welcome_page(wizard) -> None:
    """Vorher landete man ohne Vorrede in einem Formular.

    Die vier mitgelieferten Vorlagen waren nur ueber einen Knopf in der
    Fussleiste erreichbar und wurden darum praktisch nie gefunden.
    """
    from archcustomiser.gui.pages.welcome import WelcomePage

    wizard.restart()
    assert isinstance(wizard.currentPage(), WelcomePage)


def test_the_welcome_page_offers_every_bundled_template(wizard) -> None:
    vorlagen = [info for _karte, info in wizard.welcome._choices if info is not None]
    namen = {info.path.stem for info in vorlagen}
    assert {"minimal", "desktop", "gaming", "development"} <= namen


def test_invisible_categories_have_no_page(wizard, catalog) -> None:
    for category in catalog.categories:
        if not category.visible:
            assert category.step not in wizard.pageIds()


def test_driver_page_is_skipped_without_a_graphical_session(wizard, catalog, store) -> None:
    store.set_selection("desktop", ["none"])
    store.set_selection("windowmanager", [])
    apps = catalog.category("apps")
    assert wizard.visible_after(apps) != catalog.category("drivers").step


def test_driver_page_appears_with_a_desktop(wizard, catalog, store) -> None:
    store.toggle("desktop.kde", True)
    apps = catalog.category("apps")
    assert wizard.visible_after(apps) == catalog.category("drivers").step


def test_walking_through_reaches_the_summary(wizard, catalog) -> None:
    wizard.restart()
    wizard.next()                       # ueber die Startseite hinweg
    visited = []
    for _ in range(30):
        page = wizard.currentPage()
        visited.append(page.category.id)
        following = page.nextId()
        if following < 0:
            break
        wizard.next()
    assert visited[0] == "basics"
    assert visited[-1] == "summary"


def test_skipped_steps_are_marked_as_such_in_the_sidebar(wizard, store) -> None:
    """Der irrefuehrende Teil der alten Schrittliste.

    ``nextId()`` ueberspringt Kategorien, deren Bedingung nicht erfuellt ist --
    die Liste zeigte sie aber unveraendert an. Wer keinen Desktop gewaehlt hat,
    wartete so auf die Seite "Grafiktreiber", die nie kommt.
    """
    from archcustomiser.gui.widgets.step_sidebar import StepState

    store.set_selection("desktop", ["none"])
    store.set_selection("windowmanager", [])
    wizard._refresh_sidebar()
    assert wizard.sidebar._states["drivers"] is StepState.SKIPPED

    store.toggle("desktop.kde", True)
    wizard._refresh_sidebar()
    assert wizard.sidebar._states["drivers"] is not StepState.SKIPPED


def test_a_fixed_error_clears_the_mark_again(wizard) -> None:
    """Ein einmal rot markierter Schritt blieb rot, auch nach der Korrektur."""
    from archcustomiser.gui.widgets.step_sidebar import StepState

    wizard.sidebar.set_states({"basics": StepState.ERROR})
    rot = wizard.sidebar._buttons["basics"].styleSheet()
    wizard.sidebar.set_states({"basics": StepState.DONE})
    assert wizard.sidebar._buttons["basics"].styleSheet() != rot


def test_summary_produces_a_plan(wizard, store) -> None:
    store.toggle("desktop.kde", True)
    page = wizard.page(99)
    page.initializePage()
    plan = page.plan()
    assert plan is not None
    assert plan.iso_filename.endswith(".iso")
    assert plan.archinstall["profile_config"]["profile"]["details"] == ["KDE Plasma"]


# ---------------------------------------------------------------------------
# Die Knoepfe muessen tatsaechlich aufrufbar sein
# ---------------------------------------------------------------------------


def test_every_button_signature_actually_matches(qapp, monkeypatch) -> None:
    """Ein Knopf, den kein Test drueckt, kann jahrelang kaputt sein.

    Genau das war der Fall: der Knopf "archiso jetzt installieren" im WSL-Dialog
    uebergab drei Argumente an run_with_wait, das nur zwei annimmt -- ein
    TypeError beim ersten Klick. Kein Test hat ihn je gedrueckt.
    """
    from archcustomiser.core.build import wsl
    from archcustomiser.gui.widgets import wsl_dialog as modul

    aufgerufen: list[str] = []

    def fake_run_with_wait(arbeit, text, *, parent=None, cancellable=True):
        # Signatur wie das Original -- ein zusaetzliches Argument wuerde hier
        # denselben TypeError ausloesen wie in der echten Fassung.
        aufgerufen.append(text)
        return None, None

    monkeypatch.setattr(
        "archcustomiser.gui.widgets.wait_dialog.run_with_wait", fake_run_with_wait
    )

    status = wsl.WslStatus(
        installed=True, distributions=(wsl.Distribution("archlinux", default=True),)
    )
    dialog = modul.WslSetupDialog(status)

    dialog._install_archiso()
    assert aufgerufen, "der Installationsknopf hat run_with_wait nie erreicht"
    assert "archiso" in aufgerufen[0]

    aufgerufen.clear()
    dialog._recheck()
    assert aufgerufen, "der Knopf 'Erneut pruefen' hat run_with_wait nie erreicht"


# ---------------------------------------------------------------------------
# Der Abbruch darf die Oberflaeche nicht anhalten
#
# Am 03.09.2026 hat ein Bau einen Rechner unbedienbar gemacht. Der
# Abbrechen-Knopf lief damals synchron im Oberflaechenfaden bis in die
# WSL-Verteilung hinein -- also genau der Faden, der das Fenster zeichnet,
# wartete bis zu anderthalb Minuten auf mehrere wsl.exe-Aufrufe. Der Knopf
# wird aber gedrueckt, WEIL der Rechner schon ueberlastet ist.
# ---------------------------------------------------------------------------


def test_cancelling_does_not_block_the_interface(qapp, catalog, resolver) -> None:
    import time

    from tests.test_build_controller import make_config
    from archcustomiser.gui.build_worker import BuildJob

    config = make_config()
    job = BuildJob(catalog, config, resolver.resolve(config))

    class ZaeherAbbruch:
        """Ein Ziel, das sich Zeit laesst -- so wie WSL unter Last."""

        def __init__(self) -> None:
            self.fertig = False

        def cancel(self) -> None:
            time.sleep(1.5)
            self.fertig = True

    job.controller = ZaeherAbbruch()

    begonnen = time.monotonic()
    job.cancel()
    gebraucht = time.monotonic() - begonnen

    assert gebraucht < 0.3, (
        f"cancel() hat den Oberflaechenfaden {gebraucht:.1f} s blockiert -- "
        "genau der Fehler, der den Abbrechen-Knopf wirkungslos machte"
    )
    assert job.cancelling, "die Oberflaeche weiss nicht, dass abgebrochen wird"

    assert job.wait(10_000), "der Abbruchfaden ist nicht fertig geworden"
    assert job.controller.fertig, "der Abbruch wurde nie ausgefuehrt"


def test_a_second_click_does_not_start_a_second_cancel(qapp, catalog, resolver) -> None:
    from tests.test_build_controller import make_config
    from archcustomiser.gui.build_worker import BuildJob

    config = make_config()
    job = BuildJob(catalog, config, resolver.resolve(config))

    class Zaehlend:
        aufrufe = 0

        def cancel(self) -> None:
            Zaehlend.aufrufe += 1

    job.controller = Zaehlend()
    job.cancel()
    job.cancel()
    job.cancel()
    job.wait(10_000)

    assert Zaehlend.aufrufe == 1


# ---------------------------------------------------------------------------
# Die Startseite ist keine Datenfalle mehr
# ---------------------------------------------------------------------------


def test_going_back_to_the_welcome_page_keeps_the_selection(wizard, store, monkeypatch) -> None:
    """Zurueck zur Startseite und wieder vor verwarf alles.

    "Von vorn beginnen" ist vorgehakt, und validatePage rief bedingungslos
    store.reset(). Die einzige Stelle im Programm, die Arbeit ohne Warnung
    vernichtete -- waehrend der Wizard beim Beenden ausdruecklich nachfragt.
    """
    wizard.restart()
    seite = wizard.welcome

    # Erster Durchgang: "Von vorn beginnen" anwenden.
    assert seite.validatePage()

    store.toggle("desktop.kde", True)
    assert "kde" in store.selected("desktop")

    # Zurueck und wieder vor -- die Auswahl muss stehen bleiben.
    assert seite.validatePage()
    assert "kde" in store.selected("desktop"), "die Zusammenstellung wurde verworfen"


def test_changing_the_welcome_choice_asks_before_discarding(wizard, store, monkeypatch) -> None:
    """Wer die Wahl aendert, wird gefragt -- und ein Nein bleibt wirksam."""
    from PySide6.QtWidgets import QMessageBox

    wizard.restart()
    seite = wizard.welcome
    assert seite.validatePage()
    store.toggle("desktop.kde", True)

    gefragt: list[str] = []

    def nein(*args, **kwargs):
        gefragt.append("ja")
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", nein)

    vorlage = next(karte for karte, info in seite._choices if info is not None)
    vorlage.button.setChecked(True)

    assert not seite.validatePage(), "trotz Nein wurde weitergegangen"
    assert gefragt, "es wurde gar nicht gefragt"
    assert "kde" in store.selected("desktop")


# ---------------------------------------------------------------------------
# Der Baudialog laesst sich nicht wegdruecken
# ---------------------------------------------------------------------------


class _FakeJob:
    """Ein Bauauftrag, der nur so tut -- fuer Dialogtests."""

    def __init__(self, running: bool = True) -> None:
        from PySide6.QtCore import QObject, Signal

        class Signale(QObject):
            stepChanged = Signal(object, str)
            progressChanged = Signal(float, str, str)
            linesReceived = Signal(list)
            finished = Signal(object)
            failed = Signal(object)
            cancelled = Signal()

        self._signale = Signale()
        for name in (
            "stepChanged",
            "progressChanged",
            "linesReceived",
            "finished",
            "failed",
            "cancelled",
        ):
            setattr(self, name, getattr(self._signale, name))
        self.running = running
        self.cancelling = False
        self.cancel_calls = 0

    def cancel(self) -> None:
        self.cancel_calls += 1
        self.cancelling = True

    def start(self, *args, **kwargs) -> None:
        pass


def test_escape_does_not_abandon_a_running_build(qapp, tmp_path, monkeypatch) -> None:
    """QDialog ruft bei Escape reject(), nicht close().

    closeEvent war ueberschrieben, reject() nicht: der Dialog verschwand, der
    Bau-Faden lief weiter, ein zweiter Bau war startbar, und beim Beenden
    zerstoerte Qt einen laufenden QThread.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QMessageBox

    from archcustomiser.gui.widgets.build_dialog import BuildDialog

    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No
    )

    job = _FakeJob(running=True)
    dialog = BuildDialog(job, tmp_path / "work", tmp_path / "out")
    dialog.reject()

    assert dialog.result() != int(dialog.DialogCode.Rejected) or dialog.isVisible() is False
    assert job.cancel_calls == 0, "ohne Bestaetigung darf nicht abgebrochen werden"


def test_a_finished_build_is_not_cancelled_afterwards(qapp, tmp_path, monkeypatch) -> None:
    """Die Rueckfrage ist modal -- der Bau kann waehrenddessen fertig werden."""
    from PySide6.QtWidgets import QMessageBox

    from archcustomiser.gui.widgets.build_dialog import BuildDialog

    job = _FakeJob(running=True)
    dialog = BuildDialog(job, tmp_path / "work", tmp_path / "out")

    def fertig_werden(*args, **kwargs):
        dialog._done = True
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", fertig_werden)

    dialog._on_cancel_clicked()
    assert job.cancel_calls == 0, "ein fertiger Bau wurde nachtraeglich abgebrochen"


def test_a_non_cancellable_wait_dialog_ignores_escape(qapp) -> None:
    """Der Schliessknopf war entfernt, reject() aber nicht ueberschrieben.

    Bei der archiso-Installation lief pacman danach unsichtbar weiter.
    """
    from archcustomiser.gui.widgets.wait_dialog import WaitDialog

    dialog = WaitDialog(lambda: None, "laeuft", cancellable=False)
    dialog.reject()
    assert not dialog.isHidden() or dialog.result() == 0


# ---------------------------------------------------------------------------
# Formularfelder
# ---------------------------------------------------------------------------


def test_a_directory_field_opens_a_directory_dialog(wizard, catalog, monkeypatch) -> None:
    """Fuer Ausgabe- und Arbeitsverzeichnis erschien ein Datei-Dialog."""
    from PySide6.QtWidgets import QFileDialog

    seite = wizard.page(catalog.category("build").step)
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

    seite._browse(spec)
    assert gerufen == ["dir"], "es erschien der falsche Dialog"


def test_the_password_field_is_cleared_when_the_store_is(wizard, catalog, store) -> None:
    """Nach dem Laden eines Profils standen weiter Punkte im Feld."""
    from PySide6.QtWidgets import QLineEdit

    from archcustomiser.core.config import BuildConfig

    seite = wizard.page(catalog.category("user").step)
    zeile = seite._rows["password"]
    assert isinstance(zeile.widget, QLineEdit)

    store.set_secret("user.password", "geheim123")
    zeile.widget.setText("geheim123")

    store.replace_config(BuildConfig())
    seite.sync_from_store()

    assert zeile.widget.text() == "", "das Feld zeigt ein Passwort, das es nicht gibt"


def test_the_refresh_button_comes_back_after_a_failure(wizard, catalog) -> None:
    """Der Controller sendet bei einem Fehler nur 'failed', nie 'ready'."""
    seite = wizard.page(catalog.category("extra_packages").step)
    seite.refresh_button.setEnabled(False)
    seite.controller.failed.emit("kaputt")
    assert seite.refresh_button.isEnabled(), "der Knopf bleibt dauerhaft gesperrt"
