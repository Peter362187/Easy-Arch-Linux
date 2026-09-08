"""Die Bauseite -- Vorabpruefung, Bau, Ergebnis an einer Stelle.

Frueher waren das zwei modale Dialoge uebereinander. Das hatte drei Folgen, die
alle drei am selben Punkt haengen: ein Dialog ist etwas, das man wegklickt.

* Escape schloss den Baudialog. ``QDialog`` ruft dabei ``reject()`` und nicht
  ``close()`` -- der Bau lief unsichtbar weiter, ein zweiter war startbar, und
  beim Beenden zerstoerte Qt einen laufenden Faden.
* Die Vorabpruefung erschien als Liste ausgeschriebener Ergebnisse in einem
  eigenen Fenster, das man erst wegklicken musste, um zu bauen.
* Der Ergebnispfad stand in einer Statuszeile, aus der er sich schwer
  herauskopieren liess.

Hier ist der Bau ein Schritt wie jeder andere: die letzte Station der
Navigation. Waehrend er laeuft, sind die anderen Schritte gesperrt -- man kann
die Konfiguration eines laufenden Baus nicht mehr aendern, und das soll man
auch sehen.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QTime, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.build import BuildOutcome, PreflightReport, Step
from ...core.build.errors import BuildFailed, PreflightError
from ..build_worker import BuildJob
from ..design import tokens
from ..design.typo import CAPTION, SUBTITLE, TITLE, format_size, mono, schrift
from ..store import SelectionStore
from ..widgets.checkmark import AnimatedCheck, Zustand
from ..widgets.common import CodeBlock, HintLabel, Wertzeile, open_path
from ..widgets.progress import SmoothProgressBar
from .base import PageBase

log = logging.getLogger(__name__)

MAX_LOG_BLOCKS = 20000
# Die Haken der Vorabpruefung erscheinen nacheinander statt alle zugleich.
STAFFELUNG_MS = 60

STEP_LABELS: dict[Step, str] = {
    Step.PREFLIGHT: "Umgebung geprueft",
    Step.GENERATE: "Profil erzeugt",
    Step.WRITE: "Profil geschrieben",
    Step.MKARCHISO: "ISO gebaut",
    Step.CLEANUP: "Aufgeraeumt",
}

SEITE_LEER = 0
SEITE_PRUEFUNG = 1
SEITE_BAU = 2
SEITE_ERGEBNIS = 3


class _Pruefzeile(QWidget):
    """Ein Befund der Vorabpruefung mit animiertem Haken."""

    def __init__(self, name: str, detail: str, zustand: Zustand) -> None:
        super().__init__()
        werte = tokens()
        self._ziel = zustand

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(werte.space.sm)

        self.haken = AnimatedCheck(18)
        layout.addWidget(self.haken, 0, Qt.AlignmentFlag.AlignTop)

        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(0)
        titel = QLabel(name)
        titel.setFont(schrift())
        text.addWidget(titel)
        if detail:
            unter = QLabel(detail)
            unter.setWordWrap(True)
            unter.setFont(schrift(CAPTION))
            unter.setProperty("rolle", "gedaempft")
            text.addWidget(unter)
        layout.addLayout(text, 1)

    def aufdecken(self) -> None:
        self.haken.set_zustand(self._ziel)


class _Phasenzeile(QWidget):
    """Ein Bauschritt in der Phasenliste."""

    def __init__(self, text: str) -> None:
        super().__init__()
        werte = tokens()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 1, 0, 1)
        layout.setSpacing(werte.space.sm)
        self.haken = AnimatedCheck(16)
        layout.addWidget(self.haken, 0)
        self.label = QLabel(text)
        self.label.setFont(schrift(CAPTION))
        layout.addWidget(self.label, 1)


class BuildPage(PageBase):
    """Der letzte Schritt: aus der Konfiguration wird eine Datei."""

    laufendGeaendert = Signal(bool)      # Navigation sperren / freigeben
    fertig = Signal()                    # ein Bau ist erfolgreich beendet

    def __init__(self, store: SelectionStore, flow) -> None:
        super().__init__(None, store)
        self.flow = flow
        self.job: BuildJob | None = None
        self.outcome: BuildOutcome | None = None
        self.sha256 = ""
        self._work_dir: Path | None = None
        self._out_dir: Path | None = None
        self._done = False
        self._verstrichen = QTime(0, 0)
        self._phasen: dict[str, _Phasenzeile] = {}
        self._folgen = True
        self._log_path: Path | None = None

        self._aufbauen()
        flow.preflightReady.connect(self._pruefung_zeigen)
        flow.abgebrochen.connect(self._pruefung_zuruecksetzen)

    # -- Aufbau ---------------------------------------------------------------
    def _aufbauen(self) -> None:
        werte = tokens()

        kopf = QHBoxLayout()
        kopf.setSpacing(werte.space.md)
        self.headline = QLabel("Bereit zum Bauen")
        self.headline.setFont(schrift(SUBTITLE, fett=True))
        self.headline.setWordWrap(True)
        kopf.addWidget(self.headline, 1)

        self.uhr = QLabel("")
        self.uhr.setFont(mono())
        self.uhr.setProperty("rolle", "gedaempft")
        kopf.addWidget(self.uhr, 0)

        # Der Abbrechen-Knopf bleibt sichtbar, auch wenn die Protokollansicht
        # scrollt. Ihn ans untere Ende zu setzen hiesse, ihn zu verstecken.
        self.cancel_button = QPushButton("Abbrechen")
        self.cancel_button.setProperty("variant", "danger")
        self.cancel_button.setVisible(False)
        self.cancel_button.clicked.connect(self._abbrechen_geklickt)
        kopf.addWidget(self.cancel_button, 0)
        self._root.addLayout(kopf)

        self.balken = SmoothProgressBar()
        self.balken.setVisible(False)
        self._root.addWidget(self.balken)

        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        self.detail.setFont(schrift(CAPTION))
        self.detail.setProperty("rolle", "gedaempft")
        self._root.addWidget(self.detail)

        self.stapel = QStackedWidget()
        self.stapel.addWidget(self._leerseite())
        self.stapel.addWidget(self._pruefseite())
        self.stapel.addWidget(self._bauseite())
        self.stapel.addWidget(self._ergebnisseite())
        self._root.addWidget(self.stapel, 1)

    def _leerseite(self) -> QWidget:
        werte = tokens()
        seite = QWidget()
        layout = QVBoxLayout(seite)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(werte.space.md)
        layout.addStretch(1)

        text = QLabel(
            "Alles steht bereit. Der Bau laeuft auf dem Weg, den dieser Rechner "
            "hergibt: direkt auf einem Arch-System, in einer Arch-Verteilung "
            "unter WSL oder in einem Container. Welcher es ist, wird beim Start "
            "geprueft."
        )
        text.setWordWrap(True)
        layout.addWidget(text)

        self.start_button = QPushButton("Bauumgebung pruefen")
        self.start_button.setProperty("variant", "primary")
        self.start_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self.start_button.clicked.connect(self._pruefung_starten)
        layout.addWidget(self.start_button, 0, Qt.AlignmentFlag.AlignLeft)

        self.exporthinweis = HintLabel(
            "Laesst sich hier nicht bauen, wird stattdessen der Profil-Export "
            "angeboten -- das Ergebnis baut dann ein Arch-System."
        )
        layout.addWidget(self.exporthinweis)
        layout.addStretch(2)
        return seite

    def _pruefseite(self) -> QWidget:
        werte = tokens()
        seite = QWidget()
        layout = QVBoxLayout(seite)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(werte.space.sm)

        self.pruef_bereich = QWidget()
        self._pruef_layout = QVBoxLayout(self.pruef_bereich)
        self._pruef_layout.setContentsMargins(0, 0, 0, 0)
        self._pruef_layout.setSpacing(2)

        rolle = QScrollArea()
        rolle.setWidgetResizable(True)
        rolle.setFrameShape(QScrollArea.Shape.NoFrame)
        rolle.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rolle.setWidget(self.pruef_bereich)
        layout.addWidget(rolle, 1)

        self.pruef_fuss = HintLabel("")
        layout.addWidget(self.pruef_fuss)

        self.keep_work = QCheckBox(
            "Arbeitsverzeichnis nach dem Bau behalten (zur Fehlersuche)"
        )
        layout.addWidget(self.keep_work)

        zeile = QHBoxLayout()
        self.los_button = QPushButton("ISO erstellen")
        self.los_button.setProperty("variant", "primary")
        self.los_button.clicked.connect(self._bau_starten)
        zeile.addWidget(self.los_button)

        self.erneut_button = QPushButton("Erneut pruefen")
        self.erneut_button.setProperty("variant", "ghost")
        self.erneut_button.clicked.connect(self._pruefung_starten)
        zeile.addWidget(self.erneut_button)
        zeile.addStretch(1)
        layout.addLayout(zeile)
        return seite

    def _bauseite(self) -> QWidget:
        werte = tokens()
        seite = QWidget()
        layout = QHBoxLayout(seite)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(werte.space.md)

        links = QWidget()
        links.setMaximumWidth(260)
        self._phasen_layout = QVBoxLayout(links)
        self._phasen_layout.setContentsMargins(0, 0, 0, 0)
        self._phasen_layout.setSpacing(2)
        for step in Step:
            zeile = _Phasenzeile(STEP_LABELS[step])
            self._phasen[step.value] = zeile
            self._phasen_layout.addWidget(zeile)
        self._phasen_layout.addStretch(1)
        layout.addWidget(links, 0)

        rechts = QVBoxLayout()
        rechts.setContentsMargins(0, 0, 0, 0)
        rechts.setSpacing(werte.space.xs)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(mono())
        # Ohne Obergrenze waechst der Puffer bei einem langen Bau unbegrenzt.
        self.log.setMaximumBlockCount(MAX_LOG_BLOCKS)
        self.log.setPlaceholderText("Die Ausgabe von mkarchiso erscheint hier.")
        self.log.verticalScrollBar().valueChanged.connect(self._scroll_geaendert)
        rechts.addWidget(self.log, 1)

        self.ans_ende = QPushButton("Zum Ende springen")
        self.ans_ende.setProperty("variant", "ghost")
        self.ans_ende.setVisible(False)
        self.ans_ende.clicked.connect(self._ans_ende_springen)
        rechts.addWidget(self.ans_ende, 0, Qt.AlignmentFlag.AlignRight)
        layout.addLayout(rechts, 1)
        return seite

    def _ergebnisseite(self) -> QWidget:
        werte = tokens()
        seite = QWidget()
        aussen = QVBoxLayout(seite)
        aussen.setContentsMargins(0, 0, 0, 0)
        aussen.setSpacing(werte.space.md)

        self.erfolgshaken = AnimatedCheck(56)
        aussen.addWidget(self.erfolgshaken, 0, Qt.AlignmentFlag.AlignHCenter)

        self.ergebnis_titel = QLabel("")
        self.ergebnis_titel.setFont(schrift(TITLE, fett=True))
        self.ergebnis_titel.setWordWrap(True)
        self.ergebnis_titel.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        aussen.addWidget(self.ergebnis_titel)

        self.ergebnis_pfad = CodeBlock("")
        aussen.addWidget(self.ergebnis_pfad)

        self.zeile_groesse = Wertzeile("Groesse", "")
        self.zeile_dauer = Wertzeile("Dauer", "")
        aussen.addWidget(self.zeile_groesse)
        aussen.addWidget(self.zeile_dauer)

        self.sha_block = CodeBlock("")
        self.sha_titel = HintLabel("SHA-256")
        aussen.addWidget(self.sha_titel)
        aussen.addWidget(self.sha_block)

        self.ergebnis_hinweise = QLabel("")
        self.ergebnis_hinweise.setWordWrap(True)
        self.ergebnis_hinweise.setFont(schrift(CAPTION))
        aussen.addWidget(self.ergebnis_hinweise)

        knoepfe = QHBoxLayout()
        self.ordner_button = QPushButton("Ordner oeffnen")
        self.ordner_button.clicked.connect(self._ordner_oeffnen)
        knoepfe.addWidget(self.ordner_button)

        self.protokoll_button = QPushButton("Protokoll oeffnen")
        self.protokoll_button.setProperty("variant", "ghost")
        self.protokoll_button.clicked.connect(self._protokoll_oeffnen)
        knoepfe.addWidget(self.protokoll_button)

        self.sha_speichern = QPushButton("Pruefsumme speichern")
        self.sha_speichern.setProperty("variant", "ghost")
        self.sha_speichern.clicked.connect(self._sha_speichern)
        knoepfe.addWidget(self.sha_speichern)

        knoepfe.addStretch(1)
        self.neu_button = QPushButton("Neue ISO")
        self.neu_button.clicked.connect(self._zuruecksetzen)
        knoepfe.addWidget(self.neu_button)
        aussen.addLayout(knoepfe)
        aussen.addStretch(1)
        return seite

    # -- Kennzeichnung --------------------------------------------------------
    def titel(self) -> str:
        return "ISO erstellen"

    def untertitel(self) -> str:
        return "Aus der Zusammenstellung wird jetzt eine startfaehige Datei."

    def is_complete(self) -> bool:
        return True

    # -- Vorabpruefung --------------------------------------------------------
    def _pruefung_starten(self) -> None:
        self.headline.setText("Bauumgebung wird geprueft ...")
        self.detail.setText("")
        self.start_button.setEnabled(False)
        self.flow.start()

    def _pruefung_zuruecksetzen(self) -> None:
        self.start_button.setEnabled(True)
        self.headline.setText("Bereit zum Bauen")
        self.stapel.setCurrentIndex(SEITE_LEER)

    def _pruefung_zeigen(
        self,
        job: object,
        report: object,
        work_dir: object,
        out_dir: object,
    ) -> None:
        assert isinstance(report, PreflightReport)
        self.job = job          # type: ignore[assignment]
        self._work_dir = Path(str(work_dir))
        self._out_dir = Path(str(out_dir))
        self.start_button.setEnabled(True)

        while self._pruef_layout.count():
            eintrag = self._pruef_layout.takeAt(0)
            widget = eintrag.widget()
            if widget is not None:
                widget.deleteLater()

        zeilen: list[_Pruefzeile] = []
        for check in report.checks:
            zustand = (
                Zustand.OK
                if check.ok
                else (Zustand.FEHLER if check.fatal else Zustand.WARNUNG)
            )
            zeile = _Pruefzeile(check.name, check.detail, zustand)
            self._pruef_layout.addWidget(zeile)
            zeilen.append(zeile)
        self._pruef_layout.addStretch(1)

        # Gestaffelt aufdecken: eine Liste, die auf einmal erscheint, liest
        # niemand; eine, die sich Zeile fuer Zeile fuellt, schon.
        for nummer, zeile in enumerate(zeilen):
            QTimer.singleShot(nummer * STAFFELUNG_MS, zeile.aufdecken)

        self.headline.setText(f"Es wird gebaut: {self.store.config.iso_filename}")
        weg = getattr(self.flow, "bauweg", "")
        self.detail.setText(
            f"Bauweg: {weg}\nArbeitsverzeichnis: {self._work_dir}\n"
            f"Ausgabeverzeichnis: {self._out_dir}"
        )
        self.pruef_fuss.setText(
            f"Der Bau dauert je nach Auswahl 20 Minuten bis ueber eine Stunde "
            f"und braucht rund {report.estimated_work_gb:.0f} GB im "
            f"Arbeitsverzeichnis."
        )
        self.keep_work.setChecked(self.store.config.field_bool("build.keep_work_dir"))
        self.los_button.setEnabled(report.ok)
        if not report.ok:
            self.pruef_fuss.setText(
                "Der Bau kann so nicht starten. Die rot markierten Punkte "
                "muessen zuerst behoben werden."
            )
        self.stapel.setCurrentIndex(SEITE_PRUEFUNG)

    # -- Bau ------------------------------------------------------------------
    def _bau_starten(self) -> None:
        if self.job is None or self._work_dir is None or self._out_dir is None:
            return
        job = self.job
        job.stepChanged.connect(self._schritt)
        job.progressChanged.connect(self._fortschritt)
        job.linesReceived.connect(self._zeilen)
        job.finished.connect(self._beendet)
        job.failed.connect(self._fehlgeschlagen)
        job.cancelled.connect(self._abgebrochen)

        self._done = False
        self._verstrichen = QTime(0, 0)
        self.log.clear()
        self._folgen = True
        self.ans_ende.setVisible(False)
        for zeile in self._phasen.values():
            zeile.haken.set_zustand(Zustand.OFFEN, animiert=False)

        self.balken.reset()
        self.balken.setVisible(True)
        self.cancel_button.setVisible(True)
        self.cancel_button.setEnabled(True)
        self.stapel.setCurrentIndex(SEITE_BAU)
        self.laufendGeaendert.emit(True)

        self._uhr = QTimer(self)
        self._uhr.setInterval(1000)
        self._uhr.timeout.connect(self._takt)
        self._uhr.start()

        job.start(
            self._work_dir, self._out_dir, keep_work_dir=self.keep_work.isChecked()
        )

    def _takt(self) -> None:
        self._verstrichen = self._verstrichen.addSecs(1)
        self.uhr.setText(self._verstrichen.toString("mm:ss"))

    def _schritt(self, step: object, label: str) -> None:
        self.headline.setText(label)
        wert = getattr(step, "value", step)
        erreicht = False
        for schluessel, zeile in self._phasen.items():
            if schluessel == wert:
                zeile.haken.set_zustand(Zustand.LAEUFT)
                erreicht = True
            elif not erreicht:
                zeile.haken.set_zustand(Zustand.OK)

    def _fortschritt(self, anteil: float, label: str, detail: str) -> None:
        self.balken.set_target(anteil)
        if label:
            self.headline.setText(label)
        self.detail.setText(detail)
        self._phase_ergaenzen(label)

    def _phase_ergaenzen(self, label: str) -> None:
        """Die Unterschritte von mkarchiso kommen erst zur Laufzeit.

        Eine feste Liste waere falsch: je nach Auswahl laeuft systemd-boot oder
        GRUB, squashfs oder erofs. Was tatsaechlich passiert, sagt der Parser.
        """
        if not label or label in STEP_LABELS.values():
            return
        schluessel = f"mkarchiso:{label}"
        if schluessel in self._phasen:
            return
        zeile = _Phasenzeile(f"   {label}")
        self._phasen[schluessel] = zeile
        # Vor dem Abstandhalter einfuegen, damit die Liste oben bleibt.
        self._phasen_layout.insertWidget(self._phasen_layout.count() - 1, zeile)
        zeile.haken.set_zustand(Zustand.LAEUFT)
        for anderer, andere_zeile in self._phasen.items():
            if anderer.startswith("mkarchiso:") and anderer != schluessel:
                if andere_zeile.haken.zustand() is Zustand.LAEUFT:
                    andere_zeile.haken.set_zustand(Zustand.OK)

    def _zeilen(self, zeilen: list) -> None:
        # Gebuendelt anhaengen: einzeln waere die Oberflaeche bei der
        # Paketinstallation sichtbar traege.
        balken = self.log.verticalScrollBar()
        self.log.appendPlainText("\n".join(str(zeile) for zeile in zeilen))
        if self._folgen:
            balken.setValue(balken.maximum())

    def _scroll_geaendert(self, wert: int) -> None:
        """Wer nach oben scrollt, will dort etwas lesen.

        Ohne diese Pause riss der Autoscroll die Stelle sofort wieder weg --
        ausgerechnet dann, wenn man eine Fehlermeldung sucht.
        """
        balken = self.log.verticalScrollBar()
        am_ende = wert >= balken.maximum() - 4
        if am_ende == self._folgen:
            return
        self._folgen = am_ende
        self.ans_ende.setVisible(not am_ende and not self._done)

    def _ans_ende_springen(self) -> None:
        balken = self.log.verticalScrollBar()
        balken.setValue(balken.maximum())
        self._folgen = True
        self.ans_ende.setVisible(False)

    # -- Abschluss ------------------------------------------------------------
    def _beendet(self, outcome: object, sha256: str) -> None:
        assert isinstance(outcome, BuildOutcome)
        self.outcome = outcome
        self.sha256 = sha256
        self._abschliessen()
        for zeile in self._phasen.values():
            zeile.haken.set_zustand(Zustand.OK)
        self.balken.set_target(1.0)

        pfad = outcome.iso_path
        groesse = pfad.stat().st_size if pfad is not None and pfad.is_file() else 0
        self.headline.setText("Fertig")
        self.ergebnis_titel.setText(
            f"{pfad.name if pfad else 'ISO'} ist fertig"
        )
        self.ergebnis_pfad.set_text(str(pfad) if pfad else "")
        self.zeile_groesse.setze_wert(format_size(groesse) or "unbekannt")
        self.zeile_dauer.setze_wert(self._verstrichen.toString("mm:ss"))
        self.sha_block.set_text(sha256 or "nicht berechnet")
        self.sha_titel.setVisible(bool(sha256))
        self.sha_block.setVisible(bool(sha256))
        self.sha_speichern.setEnabled(bool(sha256) and pfad is not None)
        self.ordner_button.setEnabled(pfad is not None)
        self.ergebnis_hinweise.setText(
            "\n".join(f"- {hinweis}" for hinweis in outcome.warnings)
        )
        self.ergebnis_hinweise.setProperty("rolle", "warnung")
        self.stapel.setCurrentIndex(SEITE_ERGEBNIS)
        self.erfolgshaken.set_zustand(Zustand.OFFEN, animiert=False)
        QTimer.singleShot(80, lambda: self.erfolgshaken.set_zustand(Zustand.OK))
        self.fertig.emit()

    def _fehlgeschlagen(self, fehler: object) -> None:
        self._abschliessen()
        for zeile in self._phasen.values():
            if zeile.haken.zustand() is Zustand.LAEUFT:
                zeile.haken.set_zustand(Zustand.FEHLER)
        self.headline.setText("Der Bau ist fehlgeschlagen")

        meldung = getattr(fehler, "user_message", str(fehler))
        self.detail.setText(meldung.splitlines()[0] if meldung else "")
        self.log.appendPlainText(f"\n\n{'=' * 60}\nFEHLER\n{meldung}")

        ursachen: tuple[str, ...] = ()
        if isinstance(fehler, PreflightError):
            ursachen = fehler.remedies
        elif isinstance(fehler, BuildFailed):
            ursachen = (
                "Die genaue Ursache steht in den ERROR-Zeilen des Protokolls.",
                "Bei fehlenden Paketen: Namen im Schritt 'Zusaetzliche Pakete' pruefen.",
            )
        if ursachen:
            self.log.appendPlainText(
                "\nMoegliche Ursachen:\n" + "\n".join(f"  - {u}" for u in ursachen)
            )
        self._ans_ende_springen()

    def _abgebrochen(self) -> None:
        self._abschliessen()
        for zeile in self._phasen.values():
            if zeile.haken.zustand() is Zustand.LAEUFT:
                zeile.haken.set_zustand(Zustand.WARNUNG)
        self.headline.setText("Abgebrochen")
        self.detail.setText(
            "Das Arbeitsverzeichnis kann unvollstaendige Dateien enthalten."
        )

    def _abschliessen(self) -> None:
        self._done = True
        uhr = getattr(self, "_uhr", None)
        if uhr is not None:
            uhr.stop()
        self.cancel_button.setVisible(False)
        self.ans_ende.setVisible(False)
        self._log_path = self._protokollpfad()
        self.protokoll_button.setEnabled(self._log_path is not None)
        # Erst wenn auch der Abbruch-Faden durch ist, darf wieder navigiert
        # werden: sonst laeuft im Hintergrund noch ein pkill, waehrend der
        # Benutzer schon die Konfiguration aendert.
        self._freigeben_wenn_ruhig()

    def _freigeben_wenn_ruhig(self) -> None:
        job = self.job
        if job is not None and job.busy:
            QTimer.singleShot(200, self._freigeben_wenn_ruhig)
            return
        self.laufendGeaendert.emit(False)

    def _protokollpfad(self) -> Path | None:
        if self.outcome is not None and self.outcome.log_path is not None:
            return self.outcome.log_path
        from ...core.logging_setup import build_log_dir

        verzeichnis = build_log_dir()
        if not verzeichnis.is_dir():
            return None
        # Nach Aenderungszeit, nicht lexikografisch: bei abweichender
        # Namensgebung oeffnete "Protokoll oeffnen" sonst die falsche Datei.
        gefunden = sorted(verzeichnis.glob("*.log"), key=lambda p: p.stat().st_mtime)
        return gefunden[-1] if gefunden else None

    # -- Bedienung ------------------------------------------------------------
    @property
    def laeuft(self) -> bool:
        return self.job is not None and self.job.running

    def _abbrechen_geklickt(self) -> None:
        job = self.job
        if job is None or self._done:
            return
        if job.cancelling:
            # Laeuft schon. Nicht ein zweites Mal fragen -- der Abbruch braucht
            # seine Zeit, und ein zweiter Dialog liesse den Benutzer glauben,
            # der erste sei wirkungslos gewesen.
            return
        antwort = QMessageBox.question(
            self,
            "Bau abbrechen?",
            "Der laufende Bau wird abgebrochen. Bereits heruntergeladene "
            "Pakete bleiben im Zwischenspeicher erhalten, der Rest geht "
            "verloren.\n\nWirklich abbrechen?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if antwort != QMessageBox.StandardButton.Yes:
            return
        if self._done:
            # Die Rueckfrage ist modal, die Signale des Auftrags laufen weiter:
            # der Bau kann waehrend der Frage fertig geworden sein.
            return
        self.cancel_button.setEnabled(False)
        self.headline.setText("Wird abgebrochen ...")
        job.cancel()

    def _ordner_oeffnen(self) -> None:
        if self.outcome is not None and self.outcome.iso_path is not None:
            open_path(self.outcome.iso_path.parent)

    def _protokoll_oeffnen(self) -> None:
        if self._log_path is not None:
            open_path(self._log_path)

    def _sha_speichern(self) -> None:
        """Die Pruefsumme im ueblichen Format neben die ISO legen.

        ``sha256sum -c datei.iso.sha256`` prueft sie damit ohne weiteres
        Zutun -- deshalb genau dieses Format und dieser Name.
        """
        if not self.sha256 or self.outcome is None or self.outcome.iso_path is None:
            return
        iso = self.outcome.iso_path
        ziel = iso.with_suffix(iso.suffix + ".sha256")
        try:
            ziel.write_text(f"{self.sha256}  {iso.name}\n", encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Nicht gespeichert", str(exc))
            return
        self.sha_speichern.setText("Gespeichert")
        QTimer.singleShot(
            1500, lambda: self.sha_speichern.setText("Pruefsumme speichern")
        )

    def _zuruecksetzen(self) -> None:
        """Nach einem Bau wieder von vorn -- ohne Neustart des Programms."""
        self.job = None
        self.outcome = None
        self.sha256 = ""
        self._done = False
        self.uhr.setText("")
        self.balken.setVisible(False)
        self.detail.setText("")
        self.headline.setText("Bereit zum Bauen")
        for schluessel in list(self._phasen):
            if schluessel.startswith("mkarchiso:"):
                self._phasen.pop(schluessel).deleteLater()
        self.stapel.setCurrentIndex(SEITE_LEER)

    def darf_schliessen(self) -> bool:
        """Ob das Fenster jetzt zugehen darf."""
        job = self.job
        return job is None or not job.busy


__all__ = ["STEP_LABELS", "BuildPage"]
