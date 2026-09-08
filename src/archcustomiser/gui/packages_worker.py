"""Qt-Anbindung der Paketschicht.

``PackageService`` ist bewusst synchron und Qt-frei. Hier bekommt er einen
Worker-Thread und Signale.

Wichtig ist nur die Trennung: das Laden des Index dauert einige Sekunden und
darf die Oberflaeche nicht einfrieren. Das *Pruefen* dagegen laeuft gegen den
Index im Speicher und ist so schnell, dass es direkt im GUI-Thread passieren
kann -- ein Thread waere dort nur zusaetzliche Komplexitaet.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

from ..core.packages import PackageService, RefreshPolicy
from ..core.packages.models import ValidationReport

log = logging.getLogger(__name__)


class _LoaderSignals(QObject):
    progress = Signal(str, float)
    finished = Signal(bool)     # erfolgreich?
    failed = Signal(str)


class _LoaderTask(QRunnable):
    """Laedt den Paketindex im Hintergrund."""

    def __init__(self, service: PackageService, policy: RefreshPolicy) -> None:
        super().__init__()
        self.service = service
        self.policy = policy
        self.signals = _LoaderSignals()
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _melde(self, signal, *werte) -> None:
        """Senden, ohne daran zu scheitern.

        Wird das Fenster geschlossen, waehrend die Paketdaten noch laden,
        raeumt Qt den Empfaenger ab -- der Faden laeuft aber weiter und sendet
        ins Leere: "RuntimeError: Signal source has been deleted". Das
        ``except`` unten faengt das Laden ab, nicht das Melden; die Ausnahme
        entkam deshalb aus dem Faden heraus.

        Beim Beenden ist das folgenlos, aber es ist genau die Art Meldung, die
        einen Benutzer glauben laesst, etwas sei kaputtgegangen.
        """
        try:
            signal.emit(*werte)
        except RuntimeError:
            log.debug("Empfaenger ist weg -- Meldung verworfen", exc_info=True)

    @Slot()
    def run(self) -> None:
        try:
            index = self.service.load(
                policy=self.policy,
                progress=lambda stage, value: self._melde(self.signals.progress, stage, value),
                cancel=lambda: self._cancelled,
            )
            self._melde(self.signals.finished, index is not None)
        except Exception as exc:   # ein Worker darf die Anwendung nie mitreissen
            log.exception("Laden der Paketdaten fehlgeschlagen")
            self._melde(self.signals.failed, str(exc))


class _AurSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)


class _AurTask(QRunnable):
    """Fragt das AUR nach Namen, die offiziell nicht gefunden wurden."""

    def __init__(self, service: PackageService, names: list[str], provider_choices: dict) -> None:
        super().__init__()
        self.service = service
        self.names = names
        self.provider_choices = provider_choices
        self.signals = _AurSignals()

    @Slot()
    def run(self) -> None:
        try:
            report = self.service.validate(
                self.names,
                provider_choices=self.provider_choices,
                check_aur=True,
            )
            self.signals.finished.emit(report)
        except Exception as exc:   # ein Worker darf die Anwendung nie mitreissen
            log.exception("AUR-Abfrage fehlgeschlagen")
            self.signals.failed.emit(str(exc))


class PackageController(QObject):
    """Verbindet ``PackageService`` mit der Oberflaeche."""

    progress = Signal(str, float)
    ready = Signal(bool)
    failed = Signal(str)
    statusChanged = Signal(str)
    aurReady = Signal(object)      # ValidationReport
    aurFailed = Signal(str)

    def __init__(self, service: PackageService | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.service = service or PackageService()
        self._task: _LoaderTask | None = None
        self._aur_task: _AurTask | None = None
        self._loading = False

    @property
    def loading(self) -> bool:
        return self._loading

    def start(self, policy: RefreshPolicy = RefreshPolicy.IF_STALE) -> None:
        if self._loading:
            return
        self._loading = True
        self.statusChanged.emit("Paketdaten werden geladen ...")

        task = _LoaderTask(self.service, policy)
        task.signals.progress.connect(self.progress)
        task.signals.finished.connect(self._on_finished)
        task.signals.failed.connect(self._on_failed)
        self._task = task
        QThreadPool.globalInstance().start(task)

    def cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()

    def validate(self, names, provider_choices=None) -> ValidationReport:
        """Laeuft gegen den Index im Speicher -- kein Netzzugriff."""
        return self.service.validate(
            names, provider_choices=provider_choices, check_aur=False
        )

    def pruefe_aur(self, names, provider_choices=None) -> None:
        """Fragt zusaetzlich das AUR -- im Hintergrund, weil es ins Netz geht.

        Bewusst nicht Teil von ``validate``: das laeuft bei jedem Tastendruck
        und gegen den Index im Speicher. Eine Abfrage an aur.archlinux.org
        gehoert dort nicht hinein -- sie dauert, sie kann scheitern, und sie
        verraet dem AUR, was jemand gerade tippt.
        """
        if self._aur_task is not None:
            return
        task = _AurTask(self.service, list(names), provider_choices or {})
        task.signals.finished.connect(self._on_aur_finished)
        task.signals.failed.connect(self._on_aur_failed)
        self._aur_task = task
        QThreadPool.globalInstance().start(task)

    def _on_aur_finished(self, report: object) -> None:
        self._aur_task = None
        self.aurReady.emit(report)

    def _on_aur_failed(self, meldung: str) -> None:
        self._aur_task = None
        log.warning("AUR nicht erreichbar: %s", meldung)
        self.aurFailed.emit(meldung)

    def status_text(self) -> str:
        return self.service.freshness_text()

    def _on_finished(self, success: bool) -> None:
        self._loading = False
        self._task = None
        self.statusChanged.emit(self.service.freshness_text())
        self.ready.emit(success)

    def _on_failed(self, message: str) -> None:
        self._loading = False
        self._task = None
        self.statusChanged.emit("Paketdaten nicht verfuegbar")
        self.failed.emit(message)
