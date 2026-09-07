"""Der Build-Dialog (Spec Abschnitt 9).

Zeigt Fortschrittsbalken, Schrittliste mit Haken und das laufende Protokoll --
genau die Darstellung, die in der Spezifikation skizziert ist.

Drei Entscheidungen, die aus der Laufzeit folgen (ein Build dauert zwanzig
Minuten bis eine Stunde):

* **Die Ausgabe wird gebuendelt angehaengt.** ``QPlainTextEdit`` mit
  ``setMaximumBlockCount`` begrenzt den Speicher; das vollstaendige Protokoll
  liegt ohnehin in einer Datei.
* **Abbrechen fragt nach.** Nach vierzig Minuten versehentlich abzubrechen
  waere aergerlich.
* **Der Dialog laesst sich nicht einfach schliessen**, solange gebaut wird --
  sonst liefe der Prozess unsichtbar weiter.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTime, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.build import BuildOutcome, Step
from ...core.build.errors import BuildFailed, PreflightError
from .. import theme
from ..build_worker import BuildJob
from .common import brush, open_path, passende_mindestgroesse
from .step_sidebar import MARK_CURRENT, MARK_DONE, MARK_OPEN

log = logging.getLogger(__name__)

MAX_LOG_BLOCKS = 20000

STEP_LABELS: dict[Step, str] = {
    Step.PREFLIGHT: "Umgebung geprueft",
    Step.GENERATE: "Profil erzeugt",
    Step.WRITE: "Profil geschrieben",
    Step.MKARCHISO: "ISO gebaut",
    Step.CLEANUP: "Aufgeraeumt",
}


class BuildDialog(QDialog):
    """Begleitet einen laufenden ISO-Build."""

    def __init__(
        self,
        job: BuildJob,
        work_dir: Path,
        out_dir: Path,
        *,
        keep_work_dir: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.job = job
        self.work_dir = work_dir
        self.out_dir = out_dir
        self.keep_work_dir = keep_work_dir
        self.outcome: BuildOutcome | None = None
        self._done = False
        self._elapsed = QTime(0, 0)

        self.setWindowTitle("ISO wird erstellt")
        # Aus dem tatsaechlich verfuegbaren Bildschirm ableiten statt fest
        # vorzugeben: bei 1920x1080 und 150 % Skalierung bleiben logisch
        # nur 1280x720 -- eine feste Mindesthoehe von 720 waere dann genau
        # die volle Bildschirmhoehe, ohne Platz fuer die Taskleiste.
        self.setMinimumSize(*passende_mindestgroesse(880, 640))
        self._build_ui()

        job.stepChanged.connect(self._on_step)
        job.progressChanged.connect(self._on_progress)
        job.linesReceived.connect(self._on_lines)
        job.finished.connect(self._on_finished)
        job.failed.connect(self._on_failed)
        job.cancelled.connect(self._on_cancelled)

        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._tick)

    # -- Aufbau ---------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self.headline = QLabel("Der Build wird vorbereitet ...")
        font = self.headline.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 1)
        self.headline.setFont(font)
        layout.addWidget(self.headline)

        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(True)
        self.bar.setFormat("%p%")
        layout.addWidget(self.bar)

        status = QHBoxLayout()
        self.detail = QLabel("")
        self.detail.setStyleSheet(f"color: {theme.muted()};")
        status.addWidget(self.detail, 1)
        self.timer_label = QLabel("00:00")
        self.timer_label.setStyleSheet(f"color: {theme.muted()};")
        status.addWidget(self.timer_label)
        layout.addLayout(status)

        body = QHBoxLayout()

        self.steps = QListWidget()
        self.steps.setMinimumWidth(230)
        self.steps.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        for step in Step:
            item = QListWidgetItem(f"{MARK_OPEN}  {STEP_LABELS[step]}")
            item.setData(Qt.ItemDataRole.UserRole, step.value)
            self.steps.addItem(item)
        body.addWidget(self.steps)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(theme.mono_font())
        # Ohne Obergrenze waechst der Puffer bei einem langen Build unbegrenzt.
        self.log.setMaximumBlockCount(MAX_LOG_BLOCKS)
        self.log.setPlaceholderText("Die Ausgabe von mkarchiso erscheint hier.")
        body.addWidget(self.log, 1)

        layout.addLayout(body, 1)

        buttons = QHBoxLayout()
        self.log_button = QPushButton("Protokoll oeffnen")
        self.log_button.setEnabled(False)
        self.log_button.clicked.connect(self._open_log)
        buttons.addWidget(self.log_button)

        self.folder_button = QPushButton("Ordner oeffnen")
        self.folder_button.setEnabled(False)
        self.folder_button.clicked.connect(self._open_folder)
        buttons.addWidget(self.folder_button)

        buttons.addStretch(1)
        self.cancel_button = QPushButton("Abbrechen")
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        buttons.addWidget(self.cancel_button)

        self.close_button = QPushButton("Schliessen")
        self.close_button.setEnabled(False)
        self.close_button.setDefault(True)
        self.close_button.clicked.connect(self.accept)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

    # -- Start ----------------------------------------------------------------
    def start(self) -> None:
        self._elapsed = QTime(0, 0)
        self._clock.start()
        self.job.start(self.work_dir, self.out_dir, keep_work_dir=self.keep_work_dir)

    # -- Ereignisse -----------------------------------------------------------
    def _on_step(self, step: object, label: str) -> None:
        self.headline.setText(label)
        marked = False
        for index in range(self.steps.count()):
            item = self.steps.item(index)
            value = item.data(Qt.ItemDataRole.UserRole)
            if value == getattr(step, "value", step):
                item.setText(f"{MARK_CURRENT}  {STEP_LABELS[Step(value)]}")
                item.setForeground(brush(theme.accent()))
                marked = True
            elif not marked:
                item.setText(f"{MARK_DONE}  {STEP_LABELS[Step(value)]}")
                item.setForeground(brush(theme.success()))

    def _on_progress(self, fraction: float, label: str, detail: str) -> None:
        self.bar.setValue(int(max(0.0, min(1.0, fraction)) * 1000))
        if label:
            self.headline.setText(label)
        self.detail.setText(detail)

    def _on_lines(self, lines: list) -> None:
        # Gebuendelt anhaengen: einzeln waere die Oberflaeche bei der
        # Paketinstallation sichtbar traege.
        self.log.appendPlainText("\n".join(str(line) for line in lines))

    def _tick(self) -> None:
        self._elapsed = self._elapsed.addSecs(1)
        self.timer_label.setText(self._elapsed.toString("mm:ss"))

    def _on_finished(self, outcome: object) -> None:
        assert isinstance(outcome, BuildOutcome)
        self.outcome = outcome
        self._finish()
        for index in range(self.steps.count()):
            item = self.steps.item(index)
            item.setText(
                f"{MARK_DONE}  "
                f"{STEP_LABELS[Step(item.data(Qt.ItemDataRole.UserRole))]}"
            )
            item.setForeground(brush(theme.success()))

        self.bar.setValue(1000)
        size_bytes = (
            outcome.iso_path.stat().st_size
            if outcome.iso_path is not None and outcome.iso_path.is_file()
            else 0
        )
        self.headline.setText(f"Fertig: {outcome.iso_path.name if outcome.iso_path else ''}")
        self.headline.setStyleSheet(f"color: {theme.success()};")
        self.detail.setText(
            f"{theme.format_size(size_bytes) or 'unbekannte Groesse'} "
            f"in {self._elapsed.toString('mm:ss')}  ·  {outcome.iso_path}"
        )
        # Der Pfad ist das Wertvollste am Ergebnis -- er muss sich markieren
        # und kopieren lassen.
        self.detail.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.folder_button.setEnabled(outcome.iso_path is not None)
        if outcome.warnings:
            self.log.appendPlainText(
                "\n\nHinweise:\n" + "\n".join(f"  - {w}" for w in outcome.warnings)
            )

    def _on_failed(self, error: object) -> None:
        self._finish()
        self.headline.setText("Der Build ist fehlgeschlagen")
        self.headline.setStyleSheet(f"color: {theme.danger()};")

        message = getattr(error, "user_message", str(error))
        self.detail.setText(message.splitlines()[0] if message else "")
        self.log.appendPlainText(f"\n\n{'=' * 60}\nFEHLER\n{message}")

        causes: tuple[str, ...] = ()
        if isinstance(error, PreflightError):
            causes = error.remedies
        elif isinstance(error, BuildFailed):
            causes = (
                "Die genaue Ursache steht in den ERROR-Zeilen des Protokolls.",
                "Bei fehlenden Paketen: Namen im Schritt 'Zusaetzliche Pakete' pruefen.",
            )
        if causes:
            self.log.appendPlainText("\nMoegliche Ursachen:\n" + "\n".join(f"  - {c}" for c in causes))

    def _on_cancelled(self) -> None:
        self._finish()
        self.headline.setText("Abgebrochen")
        self.headline.setStyleSheet(f"color: {theme.warning()};")
        self.detail.setText(
            "Das Arbeitsverzeichnis kann unvollstaendige Dateien enthalten."
        )

    def _finish(self) -> None:
        self._done = True
        self._clock.stop()
        self.cancel_button.setEnabled(False)
        self.close_button.setEnabled(True)
        outcome = self.outcome
        log_path = outcome.log_path if outcome else None
        if log_path is None:
            from ...core.logging_setup import build_log_dir

            # Nach Aenderungszeit, nicht lexikografisch: bei abweichender
            # Namensgebung oeffnete "Protokoll oeffnen" sonst die falsche Datei.
            found = (
                sorted(build_log_dir().glob("*.log"), key=lambda p: p.stat().st_mtime)
                if build_log_dir().is_dir()
                else []
            )
            log_path = found[-1] if found else None
        self._log_path = log_path
        self.log_button.setEnabled(log_path is not None)

    # -- Schaltflaechen -------------------------------------------------------
    def _on_cancel_clicked(self) -> None:
        if self._done:
            self.reject()
            return
        if self.job.cancelling:
            # Laeuft schon. Nicht ein zweites Mal fragen -- der Abbruch
            # braucht seine Zeit, und ein zweiter Dialog liesse den Benutzer
            # glauben, der erste sei wirkungslos gewesen.
            return
        answer = QMessageBox.question(
            self,
            "Build abbrechen?",
            "Der laufende Build wird abgebrochen. Bereits heruntergeladene "
            "Pakete bleiben im Zwischenspeicher erhalten, der Rest geht "
            "verloren.\n\nWirklich abbrechen?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self._done:
            # Die Rueckfrage ist modal, die Signale des Jobs laufen weiter:
            # der Bau kann waehrend der Frage fertig geworden sein. Ohne diese
            # Pruefung wurde die Ueberschrift "Fertig" durch "Wird abgebrochen"
            # ersetzt und der Controller merkte sich einen Abbruch, obwohl die
            # ISO fertig war.
            return
        self.cancel_button.setEnabled(False)
        self.headline.setText("Wird abgebrochen ...")
        self.job.cancel()

    def _open_log(self) -> None:
        path = getattr(self, "_log_path", None)
        if path is None:
            return
        _open(path)

    def _open_folder(self) -> None:
        if self.outcome is not None and self.outcome.iso_path is not None:
            _open(self.outcome.iso_path.parent)

    def reject(self) -> None:
        """Escape darf einen laufenden Bau nicht aus den Augen verlieren.

        ``QDialog`` ruft bei Escape direkt ``reject()`` auf, nicht ``close()``
        -- ``closeEvent`` wurde also uebergangen. ``exec()`` kehrte zurueck,
        der Bau-Faden lief weiter, ein zweiter Bau war startbar (in dasselbe
        Arbeitsverzeichnis), und beim Beenden des Programms zerstoerte Qt einen
        noch laufenden QThread.
        """
        if self._done or not self.job.running:
            super().reject()
            return
        self._on_cancel_clicked()

    def closeEvent(self, event) -> None:
        """Solange gebaut wird, bleibt der Dialog offen.

        Ihn zu schliessen wuerde den Prozess nicht beenden -- er liefe
        unsichtbar weiter und belegte weiter Platte und Rechenzeit.
        """
        if self._done or not self.job.running:
            event.accept()
            return
        event.ignore()
        self._on_cancel_clicked()



def _open(path: Path) -> None:
    open_path(path)
