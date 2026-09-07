"""Eine Arbeit im Hintergrund erledigen, ohne dass das Fenster einfriert.

Gebraucht fuer alles, was WSL fragt: ``wsl.exe`` antwortet je nach Zustand der
Verteilung sofort oder erst nach einer Minute -- und muss dafuer die Verteilung
unter Umstaenden erst starten. Im GUI-Faden aufgerufen steht das Fenster
solange still, reagiert auf nichts und wird von Windows als "keine Rueckmeldung"
markiert. Der Benutzer haelt das fuer einen Absturz und beendet das Programm.

Bewusst kein Fortschrittsbalken: die Dauer laesst sich nicht abschaetzen. Was
zaehlt, ist die Auskunft "es passiert etwas" und die Moeglichkeit, aufzugeben.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot
from PySide6.QtWidgets import QDialog, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

log = logging.getLogger(__name__)


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(object)


class _Task(QRunnable):
    def __init__(self, arbeit: Callable[[], Any]) -> None:
        super().__init__()
        self._arbeit = arbeit
        self.signals = _Signals()

    @Slot()
    def run(self) -> None:
        try:
            self.signals.done.emit(self._arbeit())
        except Exception as exc:            # ein Worker darf nie mitreissen
            log.exception("Hintergrundarbeit fehlgeschlagen")
            self.signals.failed.emit(exc)


class WaitDialog(QDialog):
    """Fuehrt ``arbeit`` im Hintergrund aus und wartet sichtbar darauf."""

    def __init__(
        self,
        arbeit: Callable[[], Any],
        text: str,
        *,
        parent: QWidget | None = None,
        cancellable: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Bitte warten")
        self.setModal(True)
        # Kein Schliessknopf: das Ergebnis wird erwartet. Wer abbrechen darf,
        # bekommt dafuer einen beschrifteten Knopf.
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)

        self.result_value: Any = None
        self.error: BaseException | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        label = QLabel(text)
        label.setWordWrap(True)
        layout.addWidget(label)

        balken = QProgressBar()
        balken.setRange(0, 0)              # unbestimmt -- die Dauer ist unbekannt
        balken.setTextVisible(False)
        layout.addWidget(balken)

        self._cancellable = cancellable
        if cancellable:
            abbrechen = QPushButton("Abbrechen")
            abbrechen.clicked.connect(self.reject)
            layout.addWidget(abbrechen, 0, Qt.AlignmentFlag.AlignRight)

        self.setMinimumWidth(380)

        task = _Task(arbeit)
        task.signals.done.connect(self._fertig)
        task.signals.failed.connect(self._gescheitert)
        QThreadPool.globalInstance().start(task)

    def reject(self) -> None:
        """Ohne Abbrechen-Knopf gibt es auch kein Escape.

        Der Schliessknopf war entfernt, ``reject()`` aber nicht ueberschrieben:
        Escape schloss den Dialog trotzdem. Bei der archiso-Installation lief
        pacman danach unsichtbar weiter, waehrend die Oberflaeche nichts mehr
        davon wusste.
        """
        if not self._cancellable:
            return
        super().reject()

    def _fertig(self, wert: object) -> None:
        self.result_value = wert
        self.accept()

    def _gescheitert(self, exc: object) -> None:
        assert isinstance(exc, BaseException)
        self.error = exc
        self.reject()


def run_with_wait(
    arbeit: Callable[[], Any],
    text: str,
    *,
    parent: QWidget | None = None,
    cancellable: bool = True,
) -> tuple[Any, BaseException | None]:
    """Bequemer Aufruf: ``(ergebnis, fehler)``.

    Bei Abbruch durch den Benutzer sind beide ``None``. Die Hintergrundarbeit
    laeuft dann noch zu Ende -- unterbrechen laesst sich ein ``wsl.exe``-Aufruf
    nicht sauber --, ihr Ergebnis wird aber verworfen.
    """
    dialog = WaitDialog(arbeit, text, parent=parent, cancellable=cancellable)
    dialog.exec()
    return dialog.result_value, dialog.error
