"""Profile laden, speichern, exportieren -- und die Frage beim Beenden.

Das stand vorher in ``wizard.py`` zwischen Navigation und Bau. Hier steht es
fuer sich: das Hauptfenster ruft nur noch Methoden auf, und die Tests koennen
diesen Teil ohne Fenster pruefen.

Der Fingerabdruck ist der Kern. Er beantwortet die Frage "gibt es hier
ueberhaupt etwas zu verlieren?" -- und er **wandert nach jedem Speichern mit**.
Vorher stand dort ein Merker "einmal gespeichert", der nie zurueckgesetzt
wurde: alles, was nach dem ersten Speichern noch geaendert wurde, ging beim
Beenden wortlos verloren.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from ..core.catalog import Catalog
from ..core.logging_setup import log_file_path
from ..core.paths import ensure_dir, user_profiles_dir
from ..core.profiles import ProfileError, ProfileService
from .profile_worker import ProfileExporter
from .settings import Settings
from .store import SelectionStore
from .widgets.export_dialog import ErrorDialog, ExportResultDialog

log = logging.getLogger(__name__)


class ProfileActions(QObject):
    """Alles, was mit Profildateien zu tun hat."""

    profileLoaded = Signal()
    profileSaved = Signal(object)        # Path
    exportFinished = Signal(object)      # Path
    meldung = Signal(str)                # kurze Rueckmeldung fuers Fenster

    def __init__(
        self,
        catalog: Catalog,
        store: SelectionStore,
        profiles: ProfileService,
        settings: Settings,
        fenster: QWidget,
    ) -> None:
        super().__init__(fenster)
        self.catalog = catalog
        self.store = store
        self.profiles = profiles
        self.settings = settings
        self.fenster = fenster

        self._exporter = ProfileExporter(catalog, self)
        self._exporter.finished.connect(self._export_fertig)
        self._exporter.failed.connect(self._export_fehler)
        self._gesicherter_stand = self.fingerprint()

    # -- Laden ----------------------------------------------------------------
    def laden(self) -> bool:
        start = self.settings.letzter_profilordner or str(self.profiles.builtin_dir)
        gewaehlt, _filter = QFileDialog.getOpenFileName(
            self.fenster, "Profil laden", start, "Profile (*.yaml *.yml)"
        )
        if not gewaehlt:
            return False
        return self.lade_datei(Path(gewaehlt))

    def lade_datei(self, pfad: Path) -> bool:
        try:
            ergebnis = self.profiles.load(pfad)
        except ProfileError as exc:
            QMessageBox.warning(
                self.fenster, "Profil konnte nicht geladen werden", str(exc)
            )
            return False

        if ergebnis.issues:
            details = "\n".join(
                f"- {issue.message}"
                + (f"\n  ({issue.action_taken})" if issue.action_taken else "")
                for issue in ergebnis.issues
            )
            QMessageBox.information(
                self.fenster,
                "Hinweise zum Profil",
                f"Das Profil wurde geladen. Dabei ist Folgendes aufgefallen:\n\n{details}",
            )

        self.store.replace_config(ergebnis.config)
        self.settings.letzter_profilordner = str(pfad.parent)
        if ergebnis.secret_fields:
            QMessageBox.information(
                self.fenster,
                "Passwort erneut eingeben",
                "Profile enthalten keine Passwoerter. Bitte das Passwort im "
                "Schritt 'Benutzerkonto' neu eingeben.",
            )
        self._gesicherter_stand = self.fingerprint()
        self.profileLoaded.emit()
        return True

    # -- Speichern ------------------------------------------------------------
    def speichern(self) -> bool:
        ensure_dir(user_profiles_dir(), mode=0o755)
        vorschlag = self.profiles.default_path(
            self.store.config.profile_name or self.store.config.distro_name
        )
        gewaehlt, _filter = QFileDialog.getSaveFileName(
            self.fenster, "Profil speichern", str(vorschlag), "Profile (*.yaml)"
        )
        if not gewaehlt:
            return False
        pfad = Path(gewaehlt)
        try:
            self.profiles.save(
                self.store.config, pfad, resolution=self.store.resolution()
            )
        except OSError as exc:
            QMessageBox.warning(self.fenster, "Speichern fehlgeschlagen", str(exc))
            return False

        # Der Vergleichspunkt wandert mit: sonst galt jede spaetere Aenderung
        # als gesichert, und beim Beenden verschwand sie ohne Rueckfrage.
        self._gesicherter_stand = self.fingerprint()
        self.settings.letzter_profilordner = str(pfad.parent)
        self.profileSaved.emit(pfad)
        self.meldung.emit(f"Profil gespeichert: {pfad.name}")
        return True

    # -- Exportieren ----------------------------------------------------------
    def exportieren(self, *, als_archiv: bool = True) -> bool:
        """Erzeugt das archiso-Profil und schreibt es.

        Was hier entsteht, ist alles, was mkarchiso auf einem Arch-System
        braucht -- der Bau selbst folgt dort.
        """
        config = self.store.config
        if als_archiv:
            vorschlag = str(Path.home() / f"{config.iso_name}-profil.tar.gz")
            gewaehlt, _filter = QFileDialog.getSaveFileName(
                self.fenster,
                "Profil als Archiv speichern",
                vorschlag,
                "tar-Archive (*.tar.gz)",
            )
        else:
            gewaehlt = QFileDialog.getExistingDirectory(
                self.fenster,
                "Zielverzeichnis fuer das Profil waehlen",
                str(Path.home()),
            )
            if gewaehlt:
                # In ein leeres Unterverzeichnis schreiben statt direkt in das
                # gewaehlte -- sonst landet ein ganzes Profil mitten in einem
                # Ordner, in dem der Benutzer etwas anderes erwartet.
                gewaehlt = str(Path(gewaehlt) / f"{config.iso_name}-profil")
        if not gewaehlt:
            return False

        self._exporter.export(
            config,
            self.store.resolution(),
            Path(gewaehlt),
            secrets=self.store.secrets,
            as_archive=als_archiv,
        )
        self.fenster.setEnabled(False)
        return True

    def _export_fertig(self, profil: object, pfad: object) -> None:
        self.fenster.setEnabled(True)
        assert isinstance(pfad, Path)
        dialog = ExportResultDialog(
            profil,          # type: ignore[arg-type]
            pfad,
            as_archive=pfad.suffix in (".gz", ".tgz"),
            parent=self.fenster,
        )
        dialog.exec()
        self.exportFinished.emit(pfad)

    def _export_fehler(self, fehler: object) -> None:
        self.fenster.setEnabled(True)
        from ..core.archiso.errors import SymlinksUnsupportedError, TargetNotEmptyError

        ursachen: tuple[str, ...] = ()
        if isinstance(fehler, SymlinksUnsupportedError):
            ursachen = (
                "Windows erlaubt symbolische Verknuepfungen nur mit "
                "Entwicklermodus oder Administratorrechten.",
                "Der Weg ueber ein Archiv umgeht das vollstaendig.",
            )
        elif isinstance(fehler, TargetNotEmptyError):
            ursachen = (
                "Im Zielverzeichnis liegen Dateien, die nicht von diesem "
                "Programm stammen.",
                "Vorhandene Dateien werden grundsaetzlich nicht ueberschrieben.",
            )

        ErrorDialog(
            "Profil konnte nicht erzeugt werden",
            getattr(fehler, "user_message", str(fehler)),
            causes=ursachen,
            technical=getattr(fehler, "technical", ""),
            log_path=log_file_path(),
            parent=self.fenster,
        ).exec()

    # -- Ungesicherte Arbeit --------------------------------------------------
    def fingerprint(self) -> tuple:
        config = self.store.config
        return (
            tuple(sorted(config.all_refs())),
            tuple(sorted((k, repr(v)) for k, v in config.fields.items())),
            tuple(config.extra_packages),
            bool(self.store.secrets.keys()),
        )

    def hat_ungesicherte_arbeit(self) -> bool:
        """Ob ueberhaupt etwas zu verlieren ist.

        Verglichen wird gegen den letzten gesicherten Stand -- nach dem
        Speichern der gespeicherte, davor der Ausgangszustand. Gegen "leer" zu
        vergleichen waere falsch: der Store ist schon beim Start mit den
        Vorgaben des Katalogs gefuellt (Rechnername, Sprache, Tastatur,
        Zeitzone). Wer nur oeffnet und wieder schliesst, soll nicht gefragt
        werden.
        """
        return self.fingerprint() != self._gesicherter_stand

    def darf_beenden(self) -> bool:
        """Rueckfrage statt kommentarlosem Verwerfen."""
        if not self.hat_ungesicherte_arbeit():
            return True
        antwort = QMessageBox.question(
            self.fenster,
            "ArchCustomiser beenden",
            "Die Zusammenstellung ist noch nicht gespeichert.\n\n"
            "Als Profil speichern, um sie spaeter weiterzuverwenden?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if antwort == QMessageBox.StandardButton.Cancel:
            return False
        if antwort == QMessageBox.StandardButton.Save:
            self.speichern()
            # Speichern abgebrochen -- also auch nicht beenden.
            return not self.hat_ungesicherte_arbeit()
        return True


__all__ = ["ProfileActions"]
