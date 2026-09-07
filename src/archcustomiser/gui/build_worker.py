"""Qt-Anbindung des ISO-Baus.

``BuildController`` ist synchron und Qt-frei. Hier bekommt er einen eigenen
Thread -- keinen ``QThreadPool``-Auftrag wie beim Profilexport, denn ein Build
laeuft eine halbe Stunde und wuerde sonst dauerhaft einen Platz im gemeinsamen
Vorrat belegen.

Die Ausgabezeilen werden **gesammelt und gebuendelt** weitergereicht. mkarchiso
schreibt waehrend pacstrap mehrere Zeilen pro Sekunde; jede einzeln durch die
Signalwarteschlange und in ein Textfeld zu schieben laesst die Oberflaeche
sichtbar stocken.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from ..core.archiso.errors import ProfileError
from ..core.build import BuildController, BuildOutcome, Step
from ..core.build.errors import BuildCancelled, BuildError
from ..core.catalog import Catalog
from ..core.config import BuildConfig
from ..core.resolver import Resolution
from ..core.secrets import SecretStore

log = logging.getLogger(__name__)

FLUSH_INTERVAL_MS = 120
MAX_PENDING_LINES = 500


class _BuildThread(QThread):
    """Fuehrt den Build aus. Alles, was hier passiert, laeuft nebenlaeufig."""

    stepChanged = Signal(object, str)        # (Step, Beschriftung)
    progressChanged = Signal(float, str, str)
    lineReceived = Signal(str)
    finishedOk = Signal(object)              # BuildOutcome
    failed = Signal(object)                  # Exception
    cancelledByUser = Signal()

    def __init__(
        self,
        controller: BuildController,
        work_dir: Path,
        out_dir: Path,
        keep_work_dir: bool,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.work_dir = work_dir
        self.out_dir = out_dir
        self.keep_work_dir = keep_work_dir

    def run(self) -> None:
        try:
            outcome = self.controller.run(
                self.work_dir,
                self.out_dir,
                keep_work_dir=self.keep_work_dir,
                on_step=lambda step, label: self.stepChanged.emit(step, label),
                on_progress=lambda f, label, detail: self.progressChanged.emit(f, label, detail),
                on_line=self.lineReceived.emit,
            )
            self.finishedOk.emit(outcome)
        except BuildCancelled:
            self.cancelledByUser.emit()
        except (BuildError, ProfileError) as exc:
            log.warning("Build fehlgeschlagen: %s", getattr(exc, "technical", exc))
            self.failed.emit(exc)
        except Exception as exc:      # darf die Anwendung nie mitreissen
            log.exception("Unerwarteter Fehler im Build")
            self.failed.emit(exc)


class _CancelThread(QThread):
    """Bricht den Bau ab -- neben der Oberflaeche, nicht in ihr.

    Ein Abbruch ist keine schnelle Sache. Beim WSL-Ziel schickt er ein Signal
    in die Verteilung, wartet die Frist ab, kontrolliert nach und greift
    notfalls hart durch: mehrere ``wsl.exe``-Aufrufe mit je 30 s Zeitlimit.

    Bis zum 03.09.2026 lief das alles im Oberflaechenfaden. Das war genau
    verkehrt herum: der Abbrechen-Knopf wird gedrueckt, **weil** der Rechner
    ueberlastet ist -- und legte dann das Fenster fuer bis zu anderthalb
    Minuten vollstaendig still. Der Benutzer sah ein totes Programm und hielt
    den Abbruch fuer wirkungslos.
    """

    def __init__(self, controller: BuildController, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.controller = controller

    def run(self) -> None:
        try:
            self.controller.cancel()
        except Exception:        # ein Abbruch darf nie seinerseits scheitern
            log.exception("Abbruch fehlgeschlagen")


class BuildJob(QObject):
    """Steuert einen Build und buendelt seine Ausgabe fuer die Oberflaeche."""

    stepChanged = Signal(object, str)
    progressChanged = Signal(float, str, str)
    linesReceived = Signal(list)             # gebuendelt, nicht einzeln
    finished = Signal(object)
    failed = Signal(object)
    cancelled = Signal()

    def __init__(
        self,
        catalog: Catalog,
        config: BuildConfig,
        resolution: Resolution,
        secrets: SecretStore | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = BuildController(catalog, config, resolution, secrets)
        self._thread: _BuildThread | None = None
        self._cancel_thread: _CancelThread | None = None
        self._cancel_requested = False
        self._pending: list[str] = []

        # Sammelt die Ausgabe und gibt sie im festen Takt weiter.
        self._flush = QTimer(self)
        self._flush.setInterval(FLUSH_INTERVAL_MS)
        self._flush.timeout.connect(self._emit_pending)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    @property
    def busy(self) -> bool:
        """Ob noch irgendein Faden dieses Auftrags arbeitet.

        Der Abbruch-Faden laeuft weiter, nachdem der Bau-Faden schon
        "abgebrochen" gemeldet hat: beim WSL-Ziel sind das mehrere
        wsl.exe-Aufrufe mit je 30 s Zeitlimit. 'running' allein reicht
        deshalb nicht, um zu entscheiden, ob das Programm beendet werden
        darf.
        """
        if self.running:
            return True
        thread = self._cancel_thread
        return thread is not None and thread.isRunning()

    def preflight(self, work_dir: Path, out_dir: Path):
        return self.controller.preflight(work_dir, out_dir)

    def start(self, work_dir: Path, out_dir: Path, *, keep_work_dir: bool = False) -> None:
        if self.running:
            return
        thread = _BuildThread(self.controller, work_dir, out_dir, keep_work_dir, self)
        thread.stepChanged.connect(self.stepChanged)
        thread.progressChanged.connect(self.progressChanged)
        thread.lineReceived.connect(self._collect)
        thread.finishedOk.connect(self._on_finished)
        thread.failed.connect(self._on_failed)
        thread.cancelledByUser.connect(self._on_cancelled)
        # Erst wenn der QThread wirklich zu Ende ist, gilt der Auftrag als
        # beendet. Frueher setzte _finish() den Verweis schon beim
        # Ergebnis-Signal auf None: 'running' war danach False, wait()
        # wartete auf nichts, und beim Beenden zerstoerte Qt einen noch
        # laufenden Thread ("Destroyed while thread is still running").
        thread.finished.connect(self._on_thread_finished)
        self._thread = thread
        self._flush.start()
        thread.start()

    @property
    def cancelling(self) -> bool:
        return self._cancel_requested

    def cancel(self) -> None:
        """Kehrt sofort zurueck; der Abbruch selbst laeuft nebenher.

        Die Oberflaeche bleibt damit waehrend des Abbruchs bedienbar. Dass der
        Merker im Controller dadurch erst wenige Millisekunden spaeter gesetzt
        wird, ist ohne Folge: der Controller prueft ihn zwischen den Schritten,
        und der Bau dauert Minuten.
        """
        if self._cancel_requested:
            return
        self._cancel_requested = True
        thread = _CancelThread(self.controller, self)
        self._cancel_thread = thread
        thread.start()

    def wait(self, milliseconds: int = 30000) -> bool:
        fertig = True
        if self._thread is not None:
            fertig = self._thread.wait(milliseconds)
        # Auch der Abbruchfaden muss durch sein, sonst raeumt Qt ihn beim
        # Beenden unter einem laufenden pkill weg.
        if self._cancel_thread is not None:
            fertig = self._cancel_thread.wait(milliseconds) and fertig
        return fertig

    # -- intern ---------------------------------------------------------------
    def _collect(self, line: str) -> None:
        self._pending.append(line)
        # Bei einer Flut sofort abgeben, statt unbegrenzt zu wachsen.
        if len(self._pending) >= MAX_PENDING_LINES:
            self._emit_pending()

    def _emit_pending(self) -> None:
        if not self._pending:
            return
        batch, self._pending = self._pending, []
        self.linesReceived.emit(batch)

    def _finish(self) -> None:
        self._flush.stop()
        self._emit_pending()

    def _on_thread_finished(self) -> None:
        """Der Bau-Faden ist beendet -- erst jetzt darf der Verweis weg."""
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.deleteLater()

    def _on_finished(self, outcome: object) -> None:
        self._finish()
        self.finished.emit(outcome)

    def _on_failed(self, error: object) -> None:
        self._finish()
        self.failed.emit(error)

    def _on_cancelled(self) -> None:
        self._finish()
        self.cancelled.emit()
