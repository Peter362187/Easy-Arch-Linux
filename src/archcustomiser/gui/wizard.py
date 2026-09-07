"""Der Wizard.

``QWizard`` statt eigener Navigation, aus drei Gruenden:

* ``nextId()`` und der interne Seitenverlauf erledigen das Ueberspringen
  unsichtbarer Kategorien und die korrekte Zurueck-Navigation. Ein Eigenbau
  muesste den Verlaufsstapel samt Randfaellen nachbilden.
* ``isComplete()`` und ``validatePage()`` sind zwei bereits vorhandene,
  semantisch verschiedene Pruefebenen -- die eine sperrt den Weiter-Knopf live,
  die andere prueft beim Verlassen.
* Tastaturbedienung, Fokusreihenfolge und Escape-Verhalten sind geschenkt.

Seiten werden zur Laufzeit nie hinzugefuegt oder entfernt -- das wuerde den
Seitenverlauf zerstoeren. Unsichtbare Kategorien werden stattdessen in
``nextId()`` uebersprungen.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QMessageBox,
    QWidget,
    QWizard,
)

from ..core.build.preflight import NOT_BUILDABLE_HERE
from ..core.catalog import Catalog, Category
from ..core.environment import Environment
from ..core.logging_setup import log_file_path
from ..core.paths import ensure_dir, user_profiles_dir
from ..core.plan import plan_as_text
from ..core.profiles import ProfileError, ProfileService
from .build_worker import BuildJob
from .packages_worker import PackageController
from .pages.base import CatalogPageBase
from .pages.factory import PageFactory
from .pages.summary import SummaryPage
from .pages.welcome import WELCOME_STEP, WelcomePage
from .profile_worker import ProfileExporter
from .store import SelectionStore
from .widgets.build_dialog import BuildDialog
from .widgets.common import passende_mindestgroesse
from .widgets.export_dialog import ErrorDialog, ExportResultDialog
from .widgets.preflight_dialog import PreflightDialog
from .widgets.step_sidebar import StepSidebar, StepState
from .widgets.wsl_dialog import WslSetupDialog

log = logging.getLogger(__name__)

# None bedeutet 'lokal bauen' -- fuer 'der Benutzer hat abgebrochen'
# braucht es deshalb ein eigenes Zeichen.
_ABGEBROCHEN = object()


class BuildWizard(QWizard):
    """Der Hauptdialog."""

    def __init__(
        self,
        catalog: Catalog,
        store: SelectionStore,
        controller: PackageController,
        profiles: ProfileService,
        environment: Environment | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.catalog = catalog
        self.store = store
        self.controller = controller
        self.profiles = profiles

        self.setWindowTitle("Arch Linux ISO Builder")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        # Der Store ist die Quelle der Wahrheit, nicht die Seite. Ohne diese
        # Option wuerde Qt beim Zurueckblaettern Eingaben zuruecksetzen.
        self.setOption(QWizard.WizardOption.IndependentPages, True)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self.setOption(QWizard.WizardOption.HaveCustomButton1, True)
        self.setOption(QWizard.WizardOption.HaveCustomButton2, True)
        self.setOption(QWizard.WizardOption.HaveCustomButton3, True)
        # Mit "&"-Mnemonik: ohne sie ueberschreiben eigene Beschriftungen die
        # Akzeleratoren, die Qt sonst selbst vergibt -- die drei Profil-Knoepfe
        # waren dadurch ausschliesslich mit der Maus erreichbar.
        self.setButtonText(QWizard.WizardButton.CustomButton1, "Profil &laden")
        self.setButtonText(QWizard.WizardButton.CustomButton2, "Profil &speichern")
        self.setButtonText(QWizard.WizardButton.CustomButton3, "Profil e&xportieren")
        self.setButtonText(QWizard.WizardButton.NextButton, "&Weiter >")
        self.setButtonText(QWizard.WizardButton.BackButton, "< &Zurueck")
        self.setButtonText(QWizard.WizardButton.CancelButton, "&Beenden")
        self.setButtonText(QWizard.WizardButton.FinishButton, "&ISO erstellen")
        # Aus dem tatsaechlich verfuegbaren Bildschirm ableiten statt fest
        # vorzugeben: bei 1920x1080 und 150 % Skalierung bleiben logisch
        # nur 1280x720 -- eine feste Mindesthoehe von 720 waere dann genau
        # die volle Bildschirmhoehe, ohne Platz fuer die Taskleiste.
        self.setMinimumSize(*passende_mindestgroesse(1000, 720))

        self.customButtonClicked.connect(self._on_custom_button)

        self._exporter = ProfileExporter(catalog, self)
        self._exporter.finished.connect(self._on_export_finished)
        self._exporter.failed.connect(self._on_export_failed)

        self._pages: dict[str, CatalogPageBase] = {}
        self._order: list[Category] = []
        self.welcome = WelcomePage(store, profiles, environment)
        self.welcome.profileLoaded.connect(self._on_profile_loaded)
        self.setPage(WELCOME_STEP, self.welcome)
        self._build_pages()

        self._visited: set[str] = set()
        self._saved_once = False
        self._initial_fingerprint = self._fingerprint()
        self.sidebar = StepSidebar(tuple(self._order))
        self.sidebar.stepClicked.connect(self._jump_to)
        self.setSideWidget(self.sidebar)

        self.currentIdChanged.connect(self._on_page_changed)
        self.store.issuesChanged.connect(self._refresh_sidebar)
        # Diese beiden Signale gab es seit jeher, verbunden war keines: schlug
        # das Laden der Paketdaten fehl, erfuhr man es nur als Randnotiz in der
        # Fusszeile einer einzigen Seite.
        self.controller.failed.connect(self._on_packages_failed)
        self.controller.statusChanged.connect(self._on_package_status)
        self._install_shortcuts()

    def _on_packages_failed(self, message: str) -> None:
        log.warning("Paketdaten nicht ladbar: %s", message)
        self.sidebar.set_notice(
            "Paketdaten nicht verfuegbar -- Paketnamen lassen sich nicht "
            "pruefen. Der Bau funktioniert trotzdem."
        )

    def _on_package_status(self, text: str) -> None:
        self.sidebar.set_notice(text if "nicht" in text.lower() else "")

    def _install_shortcuts(self) -> None:
        """Tastenkuerzel -- vorher gab es im ganzen Programm keinen einzigen."""
        for folge, ziel in (
            (QKeySequence.StandardKey.Open, self._load_profile),
            (QKeySequence.StandardKey.Save, self._save_profile),
            (QKeySequence.StandardKey.Find, self._focus_search),
            (QKeySequence("Ctrl+Return"), self._advance),
        ):
            QShortcut(QKeySequence(folge), self, activated=ziel)

    def _focus_search(self) -> None:
        """Strg+F springt ins Suchfeld der aktuellen Seite, falls es eines gibt."""
        suche = getattr(self.currentPage(), "search", None)
        if suche is not None:
            suche.edit.setFocus()
            suche.edit.selectAll()

    def _advance(self) -> None:
        if self.button(QWizard.WizardButton.NextButton).isEnabled():
            self.next()

    # -- Aufbau ---------------------------------------------------------------
    def _build_pages(self) -> None:
        factory = PageFactory(self.store, self.controller)
        for category in self.catalog.ordered_categories():
            if not category.visible:
                continue
            page = factory.create(category)
            if page is None:
                continue
            # Seiten-IDs kommen aus der Schrittnummer des Katalogs und sind
            # damit stabil, auch wenn eine Kategorie dazukommt.
            self.setPage(category.step, page)
            self._pages[category.id] = page
            self._order.append(category)
            if isinstance(page, CatalogPageBase):
                page.nextId = _make_next_id(self, category)   # type: ignore[method-assign]

    def visible_after(self, category: Category) -> int:
        """Naechste Kategorie, deren Bedingung erfuellt ist."""
        context = self.store.context()
        started = False
        for candidate in self._order:
            if candidate.id == category.id:
                started = True
                continue
            if not started:
                continue
            if candidate.visible_when.evaluate(context):
                return candidate.step
        return -1

    # -- Ereignisse -----------------------------------------------------------
    def _on_page_changed(self, page_id: int) -> None:
        for category in self._order:
            if category.step == page_id:
                self._visited.add(category.id)
                break
        self._refresh_sidebar()

    def _refresh_sidebar(self) -> None:
        """Zeichnet die Schrittliste aus dem tatsaechlichen Zustand.

        Wichtig ist der Fall "uebersprungen": ``nextId()`` ueberspringt
        Kategorien, deren ``visible_when`` nicht erfuellt ist -- die Liste zeigte
        sie aber unveraendert an. Wer keinen Desktop gewaehlt hat, wartete so auf
        die Seite "Grafiktreiber", die nie kommt.
        """
        context = self.store.context()
        aktuell = self.currentId()
        zustaende: dict[str, StepState] = {}
        anklickbar: set[str] = set()

        for category in self._order:
            if category.step == aktuell:
                zustaende[category.id] = StepState.CURRENT
                anklickbar.add(category.id)
                continue
            if not category.visible_when.evaluate(context):
                zustaende[category.id] = StepState.SKIPPED
                continue
            if any(issue.blocking for issue in self.store.issues(category.id)):
                zustaende[category.id] = StepState.ERROR
                anklickbar.add(category.id)
                continue
            if category.id in self._visited:
                zustaende[category.id] = StepState.DONE
                anklickbar.add(category.id)
            else:
                zustaende[category.id] = StepState.OPEN

        self.sidebar.set_states(zustaende)
        self.sidebar.set_clickable(anklickbar)

    def _jump_to(self, category_id: str) -> None:
        """Direkt zu einem Schritt springen.

        ``QWizard`` fuehrt intern einen Seitenstapel; ein Sprung muss ihn
        durchlaufen, sonst ist die Zurueck-Navigation danach falsch. Deshalb
        Schritt fuer Schritt, nicht per setStartId.
        """
        ziel = next((c for c in self._order if c.id == category_id), None)
        if ziel is None or ziel.step == self.currentId():
            return

        vorwaerts = ziel.step > self.currentId()
        for _ in range(len(self._order) + 1):
            if self.currentId() == ziel.step:
                return
            vorher = self.currentId()
            if vorwaerts:
                self.next()
            else:
                self.back()
            if self.currentId() == vorher:
                return          # es geht nicht weiter -- eine Seite blockiert

    def _on_profile_loaded(self) -> None:
        """Ein geladenes Profil ist vollstaendig -- das darf man auch sehen.

        Alle Schritte gelten damit als besucht und sind in der Schrittliste
        anklickbar. Wer nur eine Kleinigkeit aendern will, springt direkt
        dorthin; wer gleich bauen will, springt zur Zusammenfassung. Frueher
        rief das Laden ``restart()`` und warf auf Schritt 1 zurueck -- man musste
        sich durch alles durchklicken, obwohl schon alles eingestellt war.
        """
        self._visited = {category.id for category in self._order}
        self._refresh_sidebar()

    def _on_custom_button(self, which: int) -> None:
        if which == QWizard.WizardButton.CustomButton1:
            self._load_profile()
        elif which == QWizard.WizardButton.CustomButton2:
            self._save_profile()
        elif which == QWizard.WizardButton.CustomButton3:
            self._export(as_archive=True)

    # -- Profile --------------------------------------------------------------
    def _load_profile(self) -> None:
        start = self.profiles.builtin_dir
        selected, _filter = QFileDialog.getOpenFileName(
            self, "Profil laden", str(start), "Profile (*.yaml *.yml)"
        )
        if not selected:
            return
        try:
            result = self.profiles.load(Path(selected))
        except ProfileError as exc:
            QMessageBox.warning(self, "Profil konnte nicht geladen werden", str(exc))
            return

        if result.issues:
            details = "\n".join(
                f"- {issue.message}"
                + (f"\n  ({issue.action_taken})" if issue.action_taken else "")
                for issue in result.issues
            )
            QMessageBox.information(
                self,
                "Hinweise zum Profil",
                f"Das Profil wurde geladen. Dabei ist Folgendes aufgefallen:\n\n{details}",
            )

        self.store.replace_config(result.config)
        if result.secret_fields:
            QMessageBox.information(
                self,
                "Passwort erneut eingeben",
                "Profile enthalten keine Passwoerter. Bitte das Passwort im Schritt "
                "'Benutzerkonto' neu eingeben.",
            )
        self._on_profile_loaded()

    def _save_profile(self) -> None:
        ensure_dir(user_profiles_dir(), mode=0o755)
        suggested = self.profiles.default_path(
            self.store.config.profile_name or self.store.config.distro_name
        )
        selected, _filter = QFileDialog.getSaveFileName(
            self, "Profil speichern", str(suggested), "Profile (*.yaml)"
        )
        if not selected:
            return
        path = Path(selected)
        try:
            self.profiles.save(
                self.store.config,
                path,
                resolution=self.store.resolution(),
            )
        except OSError as exc:
            QMessageBox.warning(self, "Speichern fehlgeschlagen", str(exc))
            return
        self._saved_once = True
        # Der Vergleichspunkt wandert mit: sonst galt jede spaetere
        # Aenderung als gesichert, und beim Beenden verschwand sie ohne
        # Rueckfrage.
        self._initial_fingerprint = self._fingerprint()
        QMessageBox.information(
            self,
            "Profil gespeichert",
            f"Gespeichert unter:\n{path}\n\n"
            "Passwoerter werden bewusst nicht mitgespeichert.",
        )

    # -- Profil erzeugen ------------------------------------------------------
    def _export(self, as_archive: bool) -> None:
        """Erzeugt das archiso-Profil und schreibt es.

        Der eigentliche ISO-Build (mkarchiso starten) folgt spaeter und laeuft
        nur auf einem Arch-System. Was hier entsteht, ist alles, was mkarchiso
        dort braucht.
        """
        page = self.currentPage()
        plan = page.plan() if isinstance(page, SummaryPage) else None
        if plan is not None:
            log.info("Bauplan:\n%s", plan_as_text(plan))

        config = self.store.config
        if as_archive:
            suggested = str(Path.home() / f"{config.iso_name}-profil.tar.gz")
            selected, _filter = QFileDialog.getSaveFileName(
                self, "Profil als Archiv speichern", suggested, "tar-Archive (*.tar.gz)"
            )
        else:
            selected = QFileDialog.getExistingDirectory(
                self, "Zielverzeichnis fuer das Profil waehlen", str(Path.home())
            )
            if selected:
                # In ein leeres Unterverzeichnis schreiben statt direkt in das
                # gewaehlte -- sonst landet ein ganzes Profil mitten in einem
                # Ordner, in dem der Benutzer etwas anderes erwartet.
                selected = str(Path(selected) / f"{config.iso_name}-profil")
        if not selected:
            return

        self._exporter.export(
            config,
            self.store.resolution(),
            Path(selected),
            secrets=self.store.secrets,
            as_archive=as_archive,
        )
        self.setEnabled(False)

    def _on_export_finished(self, profile: object, path: object) -> None:
        self.setEnabled(True)
        assert isinstance(path, Path)
        dialog = ExportResultDialog(
            profile,          # type: ignore[arg-type]
            path,
            as_archive=path.suffix in (".gz", ".tgz"),
            parent=self,
        )
        dialog.exec()

    def _on_export_failed(self, error: object) -> None:
        self.setEnabled(True)
        from ..core.archiso.errors import (
            SymlinksUnsupportedError,
            TargetNotEmptyError,
        )

        causes: tuple[str, ...] = ()
        if isinstance(error, SymlinksUnsupportedError):
            causes = (
                "Windows erlaubt symbolische Verknuepfungen nur mit "
                "Entwicklermodus oder Administratorrechten.",
                "Der Weg ueber ein Archiv umgeht das vollstaendig.",
            )
        elif isinstance(error, TargetNotEmptyError):
            causes = (
                "Im Zielverzeichnis liegen Dateien, die nicht von diesem "
                "Programm stammen.",
                "Vorhandene Dateien werden grundsaetzlich nicht ueberschrieben.",
            )

        dialog = ErrorDialog(
            "Profil konnte nicht erzeugt werden",
            getattr(error, "user_message", str(error)),
            causes=causes,
            technical=getattr(error, "technical", ""),
            log_path=log_file_path(),
            parent=self,
        )
        dialog.exec()

    # -- Beenden --------------------------------------------------------------
    def reject(self) -> None:
        """Rueckfrage statt kommentarlosem Verwerfen.

        Escape und der Beenden-Knopf loeschten bisher wortlos die gesamte
        Zusammenstellung -- nach zwanzig Minuten Arbeit. Der Baudialog fragt in
        derselben Lage nach; der Wizard selbst tat es nicht.
        """
        if not self._has_unsaved_work():
            super().reject()
            return

        antwort = QMessageBox.question(
            self,
            "ArchCustomiser beenden",
            "Die Zusammenstellung ist noch nicht gespeichert.\n\n"
            "Als Profil speichern, um sie spaeter weiterzuverwenden?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if antwort == QMessageBox.StandardButton.Cancel:
            return
        if antwort == QMessageBox.StandardButton.Save:
            self._save_profile()
            if self._has_unsaved_work():
                return          # Speichern abgebrochen -- also auch nicht beenden
        super().reject()

    def _has_unsaved_work(self) -> bool:
        """Ob ueberhaupt etwas zu verlieren ist.

        Wer das Programm nur oeffnet und gleich wieder schliesst, soll nicht
        gefragt werden -- die Vorgabewerte allein zaehlen deshalb nicht.
        """
        # Verglichen wird immer gegen den letzten gesicherten Stand -- nach
        # dem Speichern ist das der gespeicherte, davor der Ausgangszustand.
        # Frueher stand hier ein Merker 'einmal gespeichert', der nie
        # zurueckgesetzt wurde: alles nach dem ersten Speichern ging beim
        # Beenden wortlos verloren.
        # Gegen den Ausgangszustand vergleichen, nicht gegen "leer": der Store
        # ist schon beim Start mit den Vorgaben des Katalogs gefuellt --
        # Rechnername, Sprache, Tastatur, Zeitzone. Wer nur oeffnet und wieder
        # schliesst, soll nicht gefragt werden.
        return self._fingerprint() != self._initial_fingerprint

    def _fingerprint(self) -> tuple:
        config = self.store.config
        return (
            tuple(sorted(config.all_refs())),
            tuple(sorted((k, repr(v)) for k, v in config.fields.items())),
            tuple(config.extra_packages),
            bool(self.store.secrets.keys()),
        )

    # -- ISO bauen ------------------------------------------------------------
    def accept(self) -> None:
        """'ISO erstellen'.

        Gebaut wird auf dem Weg, den dieser Rechner hergibt: direkt auf einem
        Arch-System, in einer Arch-Verteilung unter WSL, oder in einem Container
        mit dem archlinux-Abbild. Welcher es ist, entscheidet
        ``available_targets()`` -- nicht die Plattform, sondern was tatsaechlich
        vorliegt.

        Geht keiner davon, gibt es statt einer Fehlermeldung den Profil-Export:
        das Ergebnis laesst sich dann auf einem Arch-System bauen.
        """

        page = self.currentPage()
        plan = page.plan() if isinstance(page, SummaryPage) else None
        if plan is not None:
            log.info("Bauplan:\n%s", plan_as_text(plan))

        target = self._choose_target()
        if target is _ABGEBROCHEN:
            return
        self._start_build(target)

    def _choose_target(self):
        """Sucht den besten Bauweg fuer diesen Rechner.

        Frueher entschied hier ``sys.platform``: alles ausser Linux ging in den
        WSL-Dialog. Ein Mac-Benutzer las daraufhin zwei Bildschirme lang, er
        solle "wsl --install archlinux" ausfuehren und Windows neu starten.

        Die Frage ist aber nicht "welches Betriebssystem", sondern "was liegt
        hier vor": ein Arch mit archiso, eine WSL-Verteilung, eine
        Container-Umgebung -- oder nichts davon. Der Benutzer waehlt nichts, er
        bekommt den besten Weg und einen Satz dazu.

        Die Suche laeuft im Hintergrund: ``wsl.exe`` antwortet je nach Zustand
        der Verteilung erst nach einer Minute, und auch ``podman info`` braucht
        einen Moment. Frueher stand das Fenster solange still und wurde von
        Windows als "keine Rueckmeldung" markiert.
        """
        from ..core.build.targets import available_targets
        from .widgets.wait_dialog import run_with_wait

        optionen, fehler = run_with_wait(
            available_targets,
            "Bauumgebung wird geprueft ...\n\n"
            "Das kann einen Moment dauern, wenn ein Linux-Untersystem "
            "oder eine Container-Umgebung erst starten muss.",
            parent=self,
        )
        if fehler is not None:
            QMessageBox.warning(
                self,
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
            self._build_note = gewaehlt.label
            # Der lokale Weg braucht kein Ziel-Objekt: der Controller nimmt
            # dann seinen Vorgabewert.
            return None if gewaehlt.kind == "lokal" else gewaehlt.target

        return self._offer_setup(optionen)

    def _offer_setup(self, optionen) -> object:
        """Kein Weg vorhanden -- fuehren statt stehenlassen.

        Unter Windows gibt es den eingerichteten WSL-Dialog. Sonst wird
        aufgezaehlt, was fehlt und was dagegen hilft, mit dem Profil-Export als
        immer moeglichem Ausweg.
        """
        import sys as _sys

        if _sys.platform == "win32":
            return self._choose_wsl_target()

        zeilen = []
        for option in optionen:
            zeilen.append(f"• {option.label}")
            if option.problem:
                zeilen.append(f"    {option.problem}")
            if option.remedy:
                zeilen.append(f"    Abhilfe: {option.remedy}")

        antwort = QMessageBox.question(
            self,
            "Hier kann nicht gebaut werden",
            "Auf diesem Rechner gibt es keinen Weg, die ISO zu bauen:\n\n"
            + "\n".join(zeilen)
            + "\n\nDas fertige archiso-Profil laesst sich aber jetzt schon "
            "speichern und auf einem Arch-System bauen.\n\nProfil exportieren?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if antwort == QMessageBox.StandardButton.Yes:
            self._export(as_archive=True)
        return _ABGEBROCHEN

    def _choose_wsl_target(self):
        """Sucht eine Arch-Verteilung in WSL oder fuehrt zur Einrichtung.

        Die Suche laeuft im Hintergrund: ``wsl.exe`` antwortet je nach Zustand
        der Verteilung sofort oder erst nach einer Minute, weil es sie erst
        starten muss. Frueher stand das Fenster solange still und wurde von
        Windows als "keine Rueckmeldung" markiert.
        """
        from ..core.build import wsl
        from ..core.build.targets import WslExecutionTarget
        from .widgets.wait_dialog import run_with_wait

        status, fehler = run_with_wait(
            self._suche_arch_verteilung,
            "Linux-Untersystem wird geprueft ...\n\n"
            "Das kann einen Moment dauern, wenn die Verteilung erst "
            "starten muss.",
            parent=self,
        )
        if fehler is not None:
            QMessageBox.warning(
                self,
                "Linux-Untersystem nicht erreichbar",
                "Die Pruefung ist fehlgeschlagen:\n\n" + str(fehler),
            )
            # None bedeutet fuer den Aufrufer 'lokal bauen'. Wer den
            # WSL-Dialog abbricht, loeste damit einen lokalen Bauversuch
            # unter Windows aus -- und bekam die irrefuehrende Frage
            # 'ISO-Build hier nicht moeglich, Profil exportieren?'.
            return _ABGEBROCHEN
        if status is None:
            return _ABGEBROCHEN          # vom Benutzer abgebrochen

        gefunden = status.find_arch(probe=_ist_arch)
        if status.installed and gefunden is not None:
            return WslExecutionTarget(wsl.WslTarget(gefunden.name))

        dialog = WslSetupDialog(status, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return _ABGEBROCHEN
        if dialog.export_requested:
            self._export(as_archive=True)
            return _ABGEBROCHEN
        name = dialog.distribution
        if not name:
            return _ABGEBROCHEN
        return WslExecutionTarget(wsl.WslTarget(name))

    @staticmethod
    def _suche_arch_verteilung():
        from ..core.build import wsl

        return wsl.detect()

    def _start_build(self, target) -> None:
        config = self.store.config
        work_dir = Path(
            config.field_str("build.work_dir") or str(Path.home() / "archcustomiser" / "work")
        )
        out_dir = Path(
            config.field_str("build.output_dir") or str(Path.home() / "archcustomiser" / "out")
        )

        job = BuildJob(self.catalog, config, self.store.resolution(), self.store.secrets, self)
        if target is not None:
            job.controller.target = target

        # Im Hintergrund: beim WSL-Ziel sind das rund acht
        # wsl.exe-Aufrufe mit je 60 s Zeitlimit, dazu 'du -sm' ueber das
        # Bauverzeichnis. Synchron aufgerufen stand das Fenster solange
        # still und wurde von Windows als 'keine Rueckmeldung' markiert --
        # genau das, wogegen die Zielsuche davor schon abgesichert ist.
        from .widgets.wait_dialog import run_with_wait

        report, fehler = run_with_wait(
            lambda: job.preflight(work_dir, out_dir),
            "Bauumgebung wird geprueft ..." + '\n\n' +
            "Das kann einen Moment dauern, wenn ein Linux-Untersystem "
            "erst starten muss.",
            parent=self,
        )
        if fehler is not None:
            QMessageBox.warning(
                self,
                "Vorabpruefung fehlgeschlagen",
                "Die Bauumgebung liess sich nicht pruefen:" + '\n\n' + str(fehler),
            )
            return
        if report is None:
            return          # vom Benutzer abgebrochen

        if not report.ok and not self._can_build_here(report):
            self._offer_profile_export(report)
            return

        dialog = PreflightDialog(report, work_dir, out_dir, config.iso_filename, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        self._build_job = job
        build = BuildDialog(
            job, work_dir, out_dir, keep_work_dir=dialog.keep_work_dir, parent=self
        )
        build.start()
        build.exec()

    @staticmethod
    def _can_build_here(report) -> bool:
        """Ob die Beanstandungen behebbar sind oder grundsaetzlicher Natur.

        Frueher wurde hier auf den Prueftext "Betriebssystem" verglichen, den es
        nur bei einem Nicht-Linux gab. Auf Ubuntu ist die Plattform aber "linux",
        der Vergleich schlug also nie an -- und der freundliche Vorschlag "Profil
        stattdessen exportieren" wurde ausgerechnet dort nie ausgeloest, wo er
        gebraucht wird. Die Vorabpruefung kennzeichnet solche Faelle jetzt selbst.
        """
        return not any(
            check.name == NOT_BUILDABLE_HERE for check in report.blocking
        )

    def _offer_profile_export(self, report) -> None:
        """Wo grundsaetzlich nicht gebaut werden kann, den sinnvollen Weg anbieten.

        Einen Fehler zu melden und den Benutzer stehen zu lassen waere unnoetig:
        das Profil ist fertig, es fehlt nur die Maschine, die daraus baut.
        """
        detail = "\n".join(f"  - {check.detail}" for check in report.blocking)
        answer = QMessageBox.question(
            self,
            "ISO-Build hier nicht moeglich",
            f"{detail}\n\n"
            f"Stattdessen kann das fertige archiso-Profil als Archiv gespeichert "
            f"werden. Auf einem Arch-System entpacken und dort mit einem Befehl "
            f"die ISO bauen.\n\nProfil jetzt exportieren?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._export(as_archive=True)


def _make_next_id(wizard: BuildWizard, category: Category):
    """Bindet ``nextId`` an die Sichtbarkeitsbedingungen des Katalogs."""

    def next_id() -> int:
        return wizard.visible_after(category)

    return next_id


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
