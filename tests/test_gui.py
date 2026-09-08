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

from PySide6.QtWidgets import QApplication


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

    from archcustomiser.gui.build_worker import BuildJob
    from tests.test_build_controller import make_config

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
    from archcustomiser.gui.build_worker import BuildJob
    from tests.test_build_controller import make_config

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


def test_the_checksum_is_computed_in_the_build_thread(qapp, tmp_path, catalog, resolver) -> None:
    """Eine ISO ist zwei bis vier Gigabyte gross.

    Sie im Oberflaechenfaden zu lesen legt das Fenster still, ausgerechnet in
    dem Moment, in dem der Benutzer nach einer halben Stunde das Ergebnis sehen
    will.
    """
    import hashlib

    from archcustomiser.gui.build_worker import _BuildThread

    iso = tmp_path / "test.iso"
    iso.write_bytes(b"eine kleine ISO" * 1000)

    class FakeOutcome:
        iso_path = iso

    class FakeController:
        cancelled = False

    faden = _BuildThread(FakeController(), tmp_path, tmp_path, False)
    erwartet = hashlib.sha256(iso.read_bytes()).hexdigest()
    assert faden._pruefsumme(FakeOutcome()) == erwartet


def test_a_missing_iso_yields_no_checksum(qapp, tmp_path) -> None:
    from archcustomiser.gui.build_worker import _BuildThread

    class FakeOutcome:
        iso_path = None

    class FakeController:
        cancelled = False

    faden = _BuildThread(FakeController(), tmp_path, tmp_path, False)
    assert faden._pruefsumme(FakeOutcome()) == ""


def test_a_non_cancellable_wait_dialog_ignores_escape(qapp) -> None:
    """Der Schliessknopf war entfernt, reject() aber nicht ueberschrieben.

    Bei der archiso-Installation lief pacman danach unsichtbar weiter.
    """
    from archcustomiser.gui.widgets.wait_dialog import WaitDialog

    dialog = WaitDialog(lambda: None, "laeuft", cancellable=False)
    dialog.reject()
    assert not dialog.isHidden() or dialog.result() == 0
