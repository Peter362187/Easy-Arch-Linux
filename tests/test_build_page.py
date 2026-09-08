"""Tests der Bauseite.

Die Bauseite ist die einzige Stelle, an der ein Fehler richtig teuer wird: sie
begleitet einen Vorgang, der eine halbe Stunde dauert und dabei zehn bis
dreissig Gigabyte schreibt. Ein Abbruch, der nicht ankommt, oder ein Fenster,
das sich schliessen laesst, waehrend im Hintergrund weiter gebaut wird, faellt
erst auf, wenn es zu spaet ist.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ARCHCUSTOMISER_MOTION", "off")

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication, QMessageBox


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    yield app


@pytest.fixture
def store(qapp, catalog):
    from archcustomiser.gui.store import SelectionStore

    return SelectionStore(catalog)


class FakeFlow(QObject):
    """Der Ablauf davor -- ohne WSL, ohne Container, ohne Wartedialog."""

    preflightReady = Signal(object, object, object, object)
    abgebrochen = Signal()
    exportGewuenscht = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.bauweg = "Testweg"
        self.starts = 0

    def start(self, plan=None) -> None:
        self.starts += 1


class FakeJob(QObject):
    """Ein Bauauftrag, der nur so tut."""

    stepChanged = Signal(object, str)
    progressChanged = Signal(float, str, str)
    linesReceived = Signal(list)
    finished = Signal(object, str)
    failed = Signal(object)
    cancelled = Signal()
    cancelFailed = Signal(object)

    def __init__(self, running: bool = True) -> None:
        super().__init__()
        self.running = running
        self.busy = running
        self.cancelling = False
        self.cancel_calls = 0
        self.starts: list[tuple] = []

    def cancel(self) -> None:
        self.cancel_calls += 1
        self.cancelling = True

    def start(self, work_dir, out_dir, *, keep_work_dir=False) -> None:
        self.starts.append((work_dir, out_dir, keep_work_dir))


def bericht(ok: bool = True):
    from archcustomiser.core.build.preflight import Check, PreflightReport

    checks = [
        Check(name="Betriebssystem", ok=True, detail="Arch Linux"),
        Check(name="archiso", ok=True, detail="v83 gefunden"),
        Check(name="Platz", ok=ok, detail="42 GB frei", fatal=not ok),
    ]
    return PreflightReport(checks=checks, estimated_work_gb=30.0)


@pytest.fixture(autouse=True)
def eigene_historie(tmp_path, monkeypatch):
    """Kein Test hinterlaesst Eintraege fuer den naechsten.

    Die Bauseite schreibt am Ende einen Historieneintrag. Ohne diese Umlenkung
    landet er im gemeinsamen Zustandsverzeichnis der Testsitzung -- und ein
    spaeterer Test, der "noch nichts gebaut" erwartet, faellt darueber.
    """
    from archcustomiser.core import history

    monkeypatch.setattr(history, "state_dir", lambda: tmp_path / "zustand")
    return tmp_path / "zustand"


@pytest.fixture
def page(qapp, store, tmp_path):
    from archcustomiser.gui.pages.build import BuildPage

    flow = FakeFlow()
    seite = BuildPage(store, flow)
    seite.flow = flow
    yield seite
    seite.deleteLater()


def vorbereiten(page, tmp_path, *, ok: bool = True) -> FakeJob:
    job = FakeJob()
    page._pruefung_zeigen(job, bericht(ok), tmp_path / "work", tmp_path / "out")
    return job


# ---------------------------------------------------------------------------
# Vorabpruefung
# ---------------------------------------------------------------------------


def test_the_page_starts_empty(page) -> None:
    from archcustomiser.gui.pages.build import SEITE_LEER

    assert page.stapel.currentIndex() == SEITE_LEER


def test_the_check_list_shows_every_finding(page, tmp_path) -> None:
    from archcustomiser.gui.pages.build import SEITE_PRUEFUNG, _Pruefzeile

    vorbereiten(page, tmp_path)
    zeilen = page.pruef_bereich.findChildren(_Pruefzeile)
    assert len(zeilen) == 3
    assert page.stapel.currentIndex() == SEITE_PRUEFUNG


def test_the_build_route_is_shown(page, tmp_path) -> None:
    """Der Bauweg wurde ermittelt, aber nie angezeigt.

    Wer nicht weiss, ob gerade lokal, in WSL oder im Container gebaut wird,
    kann einen Fehler auch nicht einordnen.
    """
    vorbereiten(page, tmp_path)
    assert "Testweg" in page.detail.text()


def test_a_blocking_finding_prevents_the_start(page, tmp_path) -> None:
    vorbereiten(page, tmp_path, ok=False)
    assert not page.los_button.isEnabled()


def test_a_clean_report_allows_the_start(page, tmp_path) -> None:
    vorbereiten(page, tmp_path)
    assert page.los_button.isEnabled()


def test_cancelling_the_flow_returns_to_the_start(page, tmp_path) -> None:
    from archcustomiser.gui.pages.build import SEITE_LEER

    vorbereiten(page, tmp_path)
    page.flow.abgebrochen.emit()
    assert page.stapel.currentIndex() == SEITE_LEER


# ---------------------------------------------------------------------------
# Bau
# ---------------------------------------------------------------------------


def test_starting_the_build_locks_the_navigation(page, tmp_path) -> None:
    gesperrt: list[bool] = []
    page.laufendGeaendert.connect(gesperrt.append)
    vorbereiten(page, tmp_path)
    page._bau_starten()
    assert gesperrt == [True]


def test_the_keep_work_dir_checkbox_reaches_the_job(page, tmp_path) -> None:
    job = vorbereiten(page, tmp_path)
    page.keep_work.setChecked(True)
    page._bau_starten()
    assert job.starts and job.starts[0][2] is True


def test_log_lines_arrive_in_the_view(page, tmp_path) -> None:
    job = vorbereiten(page, tmp_path)
    page._bau_starten()
    job.linesReceived.emit(["erste Zeile", "zweite Zeile"])
    assert "zweite Zeile" in page.log.toPlainText()


def test_the_progress_bar_follows_the_job(page, tmp_path) -> None:
    job = vorbereiten(page, tmp_path)
    page._bau_starten()
    job.progressChanged.emit(0.42, "Pakete werden installiert", "linux-firmware")
    assert page.balken.value() == pytest.approx(0.42, abs=0.01)
    assert page.headline.text() == "Pakete werden installiert"


def test_mkarchiso_substeps_appear_as_they_happen(page, tmp_path) -> None:
    """Eine feste Unterliste waere falsch.

    Je nach Auswahl laeuft systemd-boot oder GRUB, squashfs oder erofs. Was
    tatsaechlich passiert, sagt erst der Parser zur Laufzeit.
    """
    job = vorbereiten(page, tmp_path)
    page._bau_starten()
    job.progressChanged.emit(0.5, "Bootloader wird eingerichtet", "")
    assert "mkarchiso:Bootloader wird eingerichtet" in page._phasen


def test_the_step_list_marks_finished_phases(page, tmp_path) -> None:
    from archcustomiser.core.build import Step
    from archcustomiser.gui.widgets.checkmark import Zustand

    job = vorbereiten(page, tmp_path)
    page._bau_starten()
    job.stepChanged.emit(Step.WRITE, "Profil wird geschrieben")
    assert page._phasen[Step.GENERATE.value].haken.zustand() is Zustand.OK
    assert page._phasen[Step.WRITE.value].haken.zustand() is Zustand.LAEUFT


# ---------------------------------------------------------------------------
# Abbruch
# ---------------------------------------------------------------------------


def test_cancelling_asks_first(page, tmp_path, monkeypatch) -> None:
    """Nach vierzig Minuten versehentlich abzubrechen waere aergerlich."""
    job = vorbereiten(page, tmp_path)
    page._bau_starten()
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No
    )
    page._abbrechen_geklickt()
    assert job.cancel_calls == 0


def test_a_confirmed_cancel_reaches_the_job(page, tmp_path, monkeypatch) -> None:
    job = vorbereiten(page, tmp_path)
    page._bau_starten()
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    page._abbrechen_geklickt()
    assert job.cancel_calls == 1


def test_a_finished_build_is_not_cancelled_afterwards(page, tmp_path, monkeypatch) -> None:
    """Die Rueckfrage ist modal -- der Bau kann waehrenddessen fertig werden."""
    job = vorbereiten(page, tmp_path)
    page._bau_starten()

    def fertig_werden(*args, **kwargs):
        page._done = True
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", fertig_werden)
    page._abbrechen_geklickt()
    assert job.cancel_calls == 0, "ein fertiger Bau wurde nachtraeglich abgebrochen"


def test_a_second_click_does_not_ask_twice(page, tmp_path, monkeypatch) -> None:
    job = vorbereiten(page, tmp_path)
    page._bau_starten()
    job.cancelling = True
    gefragt: list[int] = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *a, **k: gefragt.append(1) or QMessageBox.StandardButton.Yes,
    )
    page._abbrechen_geklickt()
    assert not gefragt


def test_a_running_build_keeps_the_window_open(page, tmp_path) -> None:
    job = vorbereiten(page, tmp_path)
    page._bau_starten()
    assert not page.darf_schliessen()
    job.busy = False
    assert page.darf_schliessen()


def test_a_cancelled_build_unlocks_the_navigation(page, tmp_path) -> None:
    job = vorbereiten(page, tmp_path)
    page._bau_starten()
    job.busy = False
    gesperrt: list[bool] = []
    page.laufendGeaendert.connect(gesperrt.append)
    job.cancelled.emit()
    assert gesperrt == [False]


# ---------------------------------------------------------------------------
# Ergebnis
# ---------------------------------------------------------------------------


def ergebnis(tmp_path):
    from archcustomiser.core.build import BuildOutcome
    from archcustomiser.core.build.runner import BuildResult

    iso = tmp_path / "out" / "arch.iso"
    iso.parent.mkdir(parents=True, exist_ok=True)
    iso.write_bytes(b"x" * 4096)
    return BuildOutcome(result=BuildResult(returncode=0, iso_path=iso, duration_seconds=1.0))


def test_a_finished_build_shows_path_size_and_checksum(page, tmp_path) -> None:
    job = vorbereiten(page, tmp_path)
    page._bau_starten()
    job.busy = False
    job.finished.emit(ergebnis(tmp_path), "abc123")

    from archcustomiser.gui.pages.build import SEITE_ERGEBNIS

    assert page.stapel.currentIndex() == SEITE_ERGEBNIS
    assert "arch.iso" in page.ergebnis_pfad.text()
    assert page.sha_block.text() == "abc123"
    assert page.zeile_groesse._wert.text()


def test_a_finished_build_reports_itself_as_done(page, tmp_path) -> None:
    job = vorbereiten(page, tmp_path)
    page._bau_starten()
    job.busy = False
    gemeldet: list[int] = []
    page.fertig.connect(lambda: gemeldet.append(1))
    job.finished.emit(ergebnis(tmp_path), "abc")
    assert gemeldet


def test_the_checksum_can_be_saved_next_to_the_iso(page, tmp_path) -> None:
    """``sha256sum -c`` prueft sie damit ohne weiteres Zutun."""
    job = vorbereiten(page, tmp_path)
    page._bau_starten()
    job.busy = False
    ausgang = ergebnis(tmp_path)
    job.finished.emit(ausgang, "deadbeef")

    page._sha_speichern()
    datei = ausgang.iso_path.with_suffix(ausgang.iso_path.suffix + ".sha256")
    assert datei.read_text(encoding="utf-8") == "deadbeef  arch.iso\n"


def test_a_failed_build_names_possible_causes(page, tmp_path) -> None:
    from archcustomiser.core.build.errors import BuildFailed

    job = vorbereiten(page, tmp_path)
    page._bau_starten()
    job.busy = False
    job.failed.emit(BuildFailed("mkarchiso ist ausgestiegen"))
    assert "FEHLER" in page.log.toPlainText()
    assert "Ursachen" in page.log.toPlainText()


def test_scrolling_up_pauses_the_autoscroll(page, tmp_path) -> None:
    """Ohne diese Pause riss der Autoscroll die gesuchte Stelle sofort weg."""
    job = vorbereiten(page, tmp_path)
    page._bau_starten()
    job.linesReceived.emit([f"Zeile {n}" for n in range(500)])

    balken = page.log.verticalScrollBar()
    balken.setValue(0)
    assert not page._folgen
    # ``isVisible`` ist auf einer nie gezeigten Seite immer False und taugt
    # deshalb nicht als Probe. ``isHidden`` beantwortet die Frage, um die es
    # geht: wurde der Knopf ausdruecklich versteckt oder nicht.
    assert not page.ans_ende.isHidden(), "der Sprung-Knopf fehlt"

    page._ans_ende_springen()
    assert page._folgen
    assert page.ans_ende.isHidden()


def test_starting_over_clears_the_dynamic_phases(page, tmp_path) -> None:
    job = vorbereiten(page, tmp_path)
    page._bau_starten()
    job.progressChanged.emit(0.5, "squashfs wird erzeugt", "")
    assert any(k.startswith("mkarchiso:") for k in page._phasen)

    page._zuruecksetzen()
    assert not any(k.startswith("mkarchiso:") for k in page._phasen)


# ---------------------------------------------------------------------------
# Was nach dem Bau passiert
# ---------------------------------------------------------------------------


def test_a_finished_build_is_checked_for_plausibility(page, tmp_path) -> None:
    """Rueckgabewert 0 von mkarchiso ist kein Beweis fuer eine brauchbare ISO."""
    from .test_verify import baue_iso

    job = vorbereiten(page, tmp_path)
    page._bau_starten()
    job.busy = False

    ausgang = ergebnis(tmp_path)
    baue_iso(ausgang.iso_path)
    job.finished.emit(ausgang, "abc")
    assert page.zeile_pruefung._wert.text() == "plausibel"


def test_an_implausible_image_says_so(page, tmp_path) -> None:
    job = vorbereiten(page, tmp_path)
    page._bau_starten()
    job.busy = False

    ausgang = ergebnis(tmp_path)          # nur 4096 Byte -- viel zu klein
    job.finished.emit(ausgang, "abc")
    assert page.zeile_pruefung._wert.text() == "auffaellig"
    assert "auffaellig" in page.ergebnis_titel.text()
    assert page.ergebnis_hinweise.text()


def test_a_finished_build_lands_in_the_history(page, tmp_path, monkeypatch) -> None:
    from archcustomiser.core import history

    monkeypatch.setattr(history, "state_dir", lambda: tmp_path / "zustand")

    job = vorbereiten(page, tmp_path)
    page._bau_starten()
    job.busy = False
    job.finished.emit(ergebnis(tmp_path), "deadbeef")

    eintraege = history.lies()
    assert len(eintraege) == 1
    assert eintraege[0].iso_name == "arch.iso"
    assert eintraege[0].sha256 == "deadbeef"
    assert eintraege[0].bauweg == "Testweg"


def test_the_history_entry_holds_no_secret(page, tmp_path, monkeypatch) -> None:
    """Passwoerter verlassen den SecretStore nicht -- auch nicht hierhin."""
    import json

    from archcustomiser.core import history

    monkeypatch.setattr(history, "state_dir", lambda: tmp_path / "zustand")
    page.store.set_secret("user.password", "hunter2-geheim")

    job = vorbereiten(page, tmp_path)
    page._bau_starten()
    job.busy = False
    job.finished.emit(ergebnis(tmp_path), "abc")

    datei = next((tmp_path / "zustand" / "builds").glob("*.json"))
    assert "hunter2" not in datei.read_text(encoding="utf-8")
    assert "password" not in json.loads(datei.read_text(encoding="utf-8"))


def test_a_changed_configuration_invalidates_the_preflight(page, tmp_path) -> None:
    """Der Befund gilt fuer die Zusammenstellung, zu der er gehoert.

    Wer nach der Vorabpruefung noch ein Paket abwaehlt, sah sonst weiter den
    alten Befund -- und haette ihn losgeschickt.
    """
    from archcustomiser.gui.pages.build import SEITE_LEER, SEITE_PRUEFUNG

    vorbereiten(page, tmp_path)
    assert page.stapel.currentIndex() == SEITE_PRUEFUNG

    page.store.toggle("apps.firefox", True)
    assert page.stapel.currentIndex() == SEITE_LEER
    assert page.job is None


def test_a_running_build_is_not_disturbed_by_the_store(page, tmp_path) -> None:
    from archcustomiser.gui.pages.build import SEITE_BAU

    vorbereiten(page, tmp_path)
    page._bau_starten()
    page.store.toggle("apps.git", True)
    assert page.stapel.currentIndex() == SEITE_BAU


def test_a_finished_result_stays_visible(page, tmp_path) -> None:
    from archcustomiser.gui.pages.build import SEITE_ERGEBNIS

    job = vorbereiten(page, tmp_path)
    page._bau_starten()
    job.busy = False
    job.finished.emit(ergebnis(tmp_path), "abc")

    page.store.toggle("apps.git", True)
    assert page.stapel.currentIndex() == SEITE_ERGEBNIS
    assert page.job is None, "ohne neue Pruefung darf nicht wieder gebaut werden"


def test_a_failed_cancel_is_reported_and_can_be_retried(
    page, tmp_path, monkeypatch
) -> None:
    """Das lokale Ziel wirft, wenn terminate() an EPERM scheitert.

    Vorher stand die Oberflaeche danach dauerhaft auf "Wird abgebrochen ..."
    mit gesperrtem Knopf, waehrend der Bau in Ruhe zu Ende lief.
    """
    from archcustomiser.core.build.errors import BuildError

    gewarnt: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: gewarnt.append(a[2] if len(a) > 2 else "")
    )
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )

    job = vorbereiten(page, tmp_path)
    page._bau_starten()
    page._abbrechen_geklickt()
    assert not page.cancel_button.isEnabled()

    job.cancelFailed.emit(BuildError("kein Recht, den Prozess zu beenden"))
    assert gewarnt, "der gescheiterte Abbruch blieb unsichtbar"
    assert page.cancel_button.isEnabled(), "kein zweiter Versuch moeglich"


def test_a_failed_build_offers_a_retry(page, tmp_path) -> None:
    """Die Protokollansicht war ohne Knopf eine Sackgasse."""
    from archcustomiser.core.build.errors import BuildFailed

    job = vorbereiten(page, tmp_path)
    page._bau_starten()
    job.busy = False
    job.failed.emit(BuildFailed("mkarchiso ist ausgestiegen"))
    assert not page.retry_button.isHidden()
    assert "FEHLER" in page.log.toPlainText(), "das Protokoll wurde weggeraeumt"


def test_a_cancelled_build_offers_a_retry(page, tmp_path) -> None:
    job = vorbereiten(page, tmp_path)
    page._bau_starten()
    job.busy = False
    job.cancelled.emit()
    assert not page.retry_button.isHidden()


def test_a_successful_build_has_no_retry_button(page, tmp_path) -> None:
    """Dort heisst der Knopf "Neue ISO" und steht in der Ergebnisansicht."""
    job = vorbereiten(page, tmp_path)
    page._bau_starten()
    job.busy = False
    job.finished.emit(ergebnis(tmp_path), "abc")
    assert page.retry_button.isHidden()


def test_the_keep_work_dir_checkbox_reaches_the_store(page, tmp_path) -> None:
    """Der Haken gilt fuers Profil, nicht nur fuer diesen einen Bau."""
    vorbereiten(page, tmp_path)
    page.keep_work.setChecked(True)
    assert page.store.config.field_bool("build.keep_work_dir") is True


def test_the_keep_work_dir_checkbox_does_not_invalidate_the_preflight(
    page, tmp_path
) -> None:
    """Ob das Arbeitsverzeichnis stehen bleibt, aendert an der ISO nichts."""
    from archcustomiser.gui.pages.build import SEITE_PRUEFUNG

    vorbereiten(page, tmp_path)
    page.keep_work.setChecked(True)
    assert page.stapel.currentIndex() == SEITE_PRUEFUNG
    assert page.job is not None
