"""Der Weg von "ISO erstellen" bis zur startbereiten Vorabpruefung.

Vier Fragen sind zu beantworten, bevor ein Bau anlaufen kann:

1. Wo laesst sich hier ueberhaupt bauen -- direkt, in WSL, im Container?
2. Wenn nirgends: wie kommt der Benutzer trotzdem zu seiner ISO?
3. Ist das Arbeitsverzeichnis gross genug, sind die Werkzeuge da?
4. Sind die Befunde behebbar oder grundsaetzlicher Natur?

Alle vier brauchen Zeit. ``wsl.exe`` antwortet je nach Zustand der Verteilung
sofort oder erst nach einer Minute, ``podman info`` braucht einen Moment, und
die Vorabpruefung sind beim WSL-Ziel rund acht weitere Aufrufe mit je 60 s
Zeitlimit. Synchron gerufen stand das Fenster solange still und wurde von
Windows als "keine Rueckmeldung" markiert -- deshalb laeuft hier alles ueber
``run_with_wait``.

Der wichtigste Punkt ist der zweite. Frueher entschied ``sys.platform``: alles
ausser Linux ging in den WSL-Dialog, und ein Mac-Benutzer las daraufhin zwei
Bildschirme lang, er solle "wsl --install archlinux" ausfuehren und Windows neu
starten. Die Frage ist nicht "welches Betriebssystem", sondern "was liegt hier
vor".
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QDialog, QMessageBox, QWidget

from ..core.build.preflight import NOT_BUILDABLE_HERE
from ..core.catalog import Catalog
from ..core.plan import plan_as_text
from .build_worker import BuildJob
from .store import SelectionStore
from .widgets.wait_dialog import run_with_wait
from .widgets.wsl_dialog import WslSetupDialog

log = logging.getLogger(__name__)

# None bedeutet 'lokal bauen' -- fuer 'der Benutzer hat abgebrochen' braucht es
# deshalb ein eigenes Zeichen.
_ABGEBROCHEN = object()


class BuildFlow(QObject):
    """Sucht den Bauweg, prueft die Umgebung und uebergibt den Auftrag."""

    preflightReady = Signal(object, object, object, object)  # job, report, work, out
    abgebrochen = Signal()
    exportGewuenscht = Signal()

    def __init__(
        self,
        catalog: Catalog,
        store: SelectionStore,
        fenster: QWidget,
    ) -> None:
        super().__init__(fenster)
        self.catalog = catalog
        self.store = store
        self.fenster = fenster
        self.bauweg = ""

    # -- Ablauf ---------------------------------------------------------------
    def start(self, plan=None) -> None:
        if plan is not None:
            log.info("Bauplan:\n%s", plan_as_text(plan))

        ziel = self._ziel_waehlen()
        if ziel is _ABGEBROCHEN:
            self.abgebrochen.emit()
            return
        self._vorabpruefung(ziel)

    # -- Zielwahl -------------------------------------------------------------
    def _ziel_waehlen(self):
        """Sucht den besten Bauweg fuer diesen Rechner."""
        from ..core.build.targets import available_targets

        optionen, fehler = run_with_wait(
            available_targets,
            "Bauumgebung wird geprueft ...\n\n"
            "Das kann einen Moment dauern, wenn ein Linux-Untersystem "
            "oder eine Container-Umgebung erst starten muss.",
            parent=self.fenster,
        )
        if fehler is not None:
            QMessageBox.warning(
                self.fenster,
                "Bauumgebung nicht pruefbar",
                "Die Pruefung ist fehlgeschlagen:\n\n" + str(fehler),
            )
            return _ABGEBROCHEN
        if optionen is None:
            return _ABGEBROCHEN          # vom Benutzer abgebrochen

        brauchbar = [option for option in optionen if option.usable]
        if brauchbar:
            gewaehlt = brauchbar[0]
            log.info("Bauweg: %s -- %s", gewaehlt.kind, gewaehlt.label)
            self.bauweg = gewaehlt.label
            # Der lokale Weg braucht kein Ziel-Objekt: der Controller nimmt
            # dann seinen Vorgabewert.
            return None if gewaehlt.kind == "lokal" else gewaehlt.target

        return self._einrichtung_anbieten(optionen)

    def _einrichtung_anbieten(self, optionen) -> object:
        """Kein Weg vorhanden -- fuehren statt stehenlassen."""
        import sys as _sys

        if _sys.platform == "win32":
            return self._wsl_ziel()

        zeilen = []
        for option in optionen:
            zeilen.append(f"- {option.label}")
            if option.problem:
                zeilen.append(f"    {option.problem}")
            if option.remedy:
                zeilen.append(f"    Abhilfe: {option.remedy}")

        antwort = QMessageBox.question(
            self.fenster,
            "Hier kann nicht gebaut werden",
            "Auf diesem Rechner gibt es keinen Weg, die ISO zu bauen:\n\n"
            + "\n".join(zeilen)
            + "\n\nDas fertige archiso-Profil laesst sich aber jetzt schon "
            "speichern und auf einem Arch-System bauen.\n\nProfil exportieren?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if antwort == QMessageBox.StandardButton.Yes:
            self.exportGewuenscht.emit()
        return _ABGEBROCHEN

    def _wsl_ziel(self):
        """Sucht eine Arch-Verteilung in WSL oder fuehrt zur Einrichtung."""
        from ..core.build import wsl
        from ..core.build.targets import WslExecutionTarget

        status, fehler = run_with_wait(
            wsl.detect,
            "Linux-Untersystem wird geprueft ...\n\n"
            "Das kann einen Moment dauern, wenn die Verteilung erst "
            "starten muss.",
            parent=self.fenster,
        )
        if fehler is not None:
            QMessageBox.warning(
                self.fenster,
                "Linux-Untersystem nicht erreichbar",
                "Die Pruefung ist fehlgeschlagen:\n\n" + str(fehler),
            )
            # None hiesse fuer den Aufrufer 'lokal bauen'. Wer den WSL-Dialog
            # abbricht, loeste damit einen lokalen Bauversuch unter Windows aus
            # -- und bekam die irrefuehrende Frage 'ISO-Build hier nicht
            # moeglich, Profil exportieren?'.
            return _ABGEBROCHEN
        if status is None:
            return _ABGEBROCHEN          # vom Benutzer abgebrochen

        gefunden = status.find_arch(probe=_ist_arch)
        if status.installed and gefunden is not None:
            self.bauweg = f"WSL-Verteilung {gefunden.name}"
            return WslExecutionTarget(wsl.WslTarget(gefunden.name))

        dialog = WslSetupDialog(status, self.fenster)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return _ABGEBROCHEN
        if dialog.export_requested:
            self.exportGewuenscht.emit()
            return _ABGEBROCHEN
        name = dialog.distribution
        if not name:
            return _ABGEBROCHEN
        self.bauweg = f"WSL-Verteilung {name}"
        return WslExecutionTarget(wsl.WslTarget(name))

    # -- Vorabpruefung --------------------------------------------------------
    def _vorabpruefung(self, ziel) -> None:
        config = self.store.config
        work_dir = Path(
            config.field_str("build.work_dir")
            or str(Path.home() / "archcustomiser" / "work")
        )
        out_dir = Path(
            config.field_str("build.output_dir")
            or str(Path.home() / "archcustomiser" / "out")
        )

        # Eine eigene Kopie: waehrend der Bau laeuft, darf der Benutzer die
        # Konfiguration weiter ansehen, ohne dass sich der laufende Bau
        # darunter veraendert.
        job = BuildJob(
            self.catalog,
            config.copy(),
            self.store.resolution(),
            self.store.secrets,
            self,
        )
        if ziel is not None:
            job.controller.target = ziel

        report, fehler = run_with_wait(
            lambda: job.preflight(work_dir, out_dir),
            "Bauumgebung wird geprueft ...\n\n"
            "Das kann einen Moment dauern, wenn ein Linux-Untersystem "
            "erst starten muss.",
            parent=self.fenster,
        )
        if fehler is not None:
            QMessageBox.warning(
                self.fenster,
                "Vorabpruefung fehlgeschlagen",
                "Die Bauumgebung liess sich nicht pruefen:\n\n" + str(fehler),
            )
            self.abgebrochen.emit()
            return
        if report is None:
            self.abgebrochen.emit()
            return          # vom Benutzer abgebrochen

        if not report.ok and not can_build_here(report):
            self._export_anbieten(report)
            self.abgebrochen.emit()
            return

        self.preflightReady.emit(job, report, work_dir, out_dir)

    def _export_anbieten(self, report) -> None:
        """Wo grundsaetzlich nicht gebaut werden kann, den sinnvollen Weg anbieten.

        Einen Fehler zu melden und den Benutzer stehen zu lassen waere unnoetig:
        das Profil ist fertig, es fehlt nur die Maschine, die daraus baut.
        """
        detail = "\n".join(f"  - {check.detail}" for check in report.blocking)
        antwort = QMessageBox.question(
            self.fenster,
            "ISO-Build hier nicht moeglich",
            f"{detail}\n\n"
            "Stattdessen kann das fertige archiso-Profil als Archiv gespeichert "
            "werden. Auf einem Arch-System entpacken und dort mit einem Befehl "
            "die ISO bauen.\n\nProfil jetzt exportieren?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if antwort == QMessageBox.StandardButton.Yes:
            self.exportGewuenscht.emit()


def can_build_here(report) -> bool:
    """Ob die Beanstandungen behebbar sind oder grundsaetzlicher Natur.

    Frueher wurde auf den Prueftext "Betriebssystem" verglichen, den es nur bei
    einem Nicht-Linux gab. Auf Ubuntu ist die Plattform aber "linux", der
    Vergleich schlug also nie an -- und der freundliche Vorschlag "Profil
    stattdessen exportieren" wurde ausgerechnet dort nie ausgeloest, wo er
    gebraucht wird. Die Vorabpruefung kennzeichnet solche Faelle jetzt selbst.
    """
    return not any(check.name == NOT_BUILDABLE_HERE for check in report.blocking)


def _ist_arch(name: str) -> bool:
    """Fragt eine Verteilung, ob sie Arch ist -- ueber /etc/os-release.

    Wird nur befragt, wenn der Name nichts verraet. Eine Verteilung darf
    beliebig heissen; wer seine Installation "meinlinux" nennt, wurde frueher
    nie gefunden.
    """
    from ..core.build import wsl

    try:
        return wsl.WslTarget(name).is_arch()
    except Exception:          # eine nicht startbare Verteilung ist kein Fehler
        log.debug("Verteilung %r nicht befragbar", name, exc_info=True)
        return False


__all__ = ["BuildFlow", "can_build_here"]
