"""Das Hauptfenster -- Kopfzeile, Schrittliste, Seitenstapel, ISO-Panel.

Statt eines ``QWizard`` traegt die Navigation jetzt ein eigenes Modell
(``navigation.py``), und dieses Fenster ist der Teil davon, der Widgets kennt.
Die Trennung hat einen praktischen Grund: die Regeln, welcher Schritt wann
erreichbar ist, lassen sich ohne Bildschirm pruefen.

Drei Dinge, die der Wizard nicht konnte und die hier selbstverstaendlich sind:

* **Vorwaertsspringen.** Die Seiten holen ihren Inhalt ohnehin aus dem Store;
  es gibt keinen Grund, jemanden durch vierzehn Schritte zu fuehren, der weiss,
  was er will.
* **Schritte duerfen zur Laufzeit erscheinen und verschwinden.** Wer keine
  grafische Sitzung waehlt, sieht "Grafiktreiber" als uebersprungen -- nicht
  als offenen Schritt, auf den er vergeblich wartet.
* **Der Bau ist ein Schritt, kein Dialog.** Waehrend er laeuft, ist die
  Navigation gesperrt; danach geht es weiter.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.catalog import Catalog
from ..core.environment import Environment
from ..core.profiles import ProfileService
from . import motion
from .actions import ProfileActions
from .build_flow import BuildFlow
from .design import ThemeManager, tokens
from .navigation import BUILD_ID, WELCOME_ID, Art, NavigationModel
from .packages_worker import PackageController
from .pages.base import PageBase
from .pages.build import BuildPage
from .pages.factory import PageFactory
from .pages.summary import SummaryPage
from .pages.welcome import WelcomePage
from .settings import Settings
from .store import SelectionStore
from .widgets.common import passende_mindestgroesse
from .widgets.iso_panel import IsoPanel
from .widgets.page_stack import AnimatedStack
from .widgets.sidebar import Kopfzeile, StepSidebar
from .widgets.toast import Art as ToastArt
from .widgets.toast import ToastHost

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Der Rahmen um alles."""

    def __init__(
        self,
        catalog: Catalog,
        store: SelectionStore,
        controller: PackageController,
        profiles: ProfileService,
        settings: Settings,
        theme: ThemeManager,
        environment: Environment | None = None,
    ) -> None:
        super().__init__()
        self.catalog = catalog
        self.store = store
        self.controller = controller
        self.profiles = profiles
        self.settings = settings
        self.theme = theme

        self.setWindowTitle("Arch Linux ISO Builder")
        # Aus dem tatsaechlich verfuegbaren Bildschirm ableiten statt fest
        # vorzugeben: bei 1920x1080 und 150 % Skalierung bleiben logisch nur
        # 1280x720 -- eine feste Mindesthoehe von 720 waere dann genau die
        # volle Bildschirmhoehe, ohne Platz fuer die Taskleiste.
        self.setMinimumSize(*passende_mindestgroesse(1000, 720))

        self.model = NavigationModel.aus_katalog(catalog.ordered_categories())
        self.model.ist_anwendbar = self._ist_anwendbar
        self.model.hat_fehler = self._hat_fehler
        self.model.ist_baubereit = self._ist_baubereit

        self.actions_ = ProfileActions(catalog, store, profiles, settings, self)
        self.flow = BuildFlow(catalog, store, self)

        self._pages: dict[str, PageBase] = {}
        self._aufbauen(environment)
        self._verdrahten()
        self._kuerzel()

        self.toasts = ToastHost(self)
        self.theme.bind(self)
        self._gehe_zu(WELCOME_ID, animiert=False)
        self._aktualisieren()

    # -- Aufbau ---------------------------------------------------------------
    def _aufbauen(self, environment: Environment | None) -> None:
        werte = tokens()
        zentral = QWidget()
        aussen = QVBoxLayout(zentral)
        aussen.setContentsMargins(0, 0, 0, 0)
        aussen.setSpacing(0)

        aussen.addLayout(self._kopfleiste())

        koerper = QHBoxLayout()
        koerper.setContentsMargins(
            werte.space.lg, werte.space.sm, werte.space.lg, werte.space.sm
        )
        koerper.setSpacing(werte.space.lg)

        self.sidebar = StepSidebar(self.model)
        self.sidebar.stepClicked.connect(self._sprung)
        koerper.addWidget(self.sidebar, 0)

        mitte = QVBoxLayout()
        mitte.setContentsMargins(0, 0, 0, 0)
        mitte.setSpacing(werte.space.sm)
        self.kopfzeile = Kopfzeile()
        mitte.addWidget(self.kopfzeile)

        self.stack = AnimatedStack()
        mitte.addWidget(self.stack, 1)
        koerper.addLayout(mitte, 1)

        self.iso_panel = IsoPanel(self.store)
        self.iso_panel.setVisible(self.settings.iso_panel_visible)
        koerper.addWidget(self.iso_panel, 0)

        aussen.addLayout(koerper, 1)
        aussen.addLayout(self._fussleiste())
        self.setCentralWidget(zentral)

        self._seiten_erzeugen(environment)

    def _kopfleiste(self) -> QHBoxLayout:
        werte = tokens()
        leiste = QHBoxLayout()
        leiste.setContentsMargins(
            werte.space.lg, werte.space.md, werte.space.lg, werte.space.sm
        )
        leiste.setSpacing(werte.space.sm)

        self.btn_laden = _knopf("Profil &laden", "ghost")
        self.btn_speichern = _knopf("Profil &speichern", "ghost")
        self.btn_export = _knopf("Profil e&xportieren", "ghost")
        for knopf in (self.btn_laden, self.btn_speichern, self.btn_export):
            leiste.addWidget(knopf)
        leiste.addStretch(1)

        self.btn_panel = _knopf("ISO-Uebersicht", "ghost")
        self.btn_panel.setCheckable(True)
        self.btn_panel.setChecked(self.settings.iso_panel_visible)
        leiste.addWidget(self.btn_panel)

        self.btn_theme = _knopf("Hell / Dunkel", "ghost")
        leiste.addWidget(self.btn_theme)
        return leiste

    def _fussleiste(self) -> QHBoxLayout:
        werte = tokens()
        leiste = QHBoxLayout()
        leiste.setContentsMargins(
            werte.space.lg, werte.space.sm, werte.space.lg, werte.space.md
        )
        leiste.setSpacing(werte.space.sm)

        self.btn_beenden = _knopf("&Beenden", "ghost")
        leiste.addWidget(self.btn_beenden)
        leiste.addStretch(1)

        self.btn_zurueck = _knopf("< &Zurueck", "")
        self.btn_weiter = _knopf("&Weiter >", "primary")
        leiste.addWidget(self.btn_zurueck)
        leiste.addWidget(self.btn_weiter)
        return leiste

    def _seiten_erzeugen(self, environment: Environment | None) -> None:
        factory = PageFactory(self.store, self.controller)

        self.welcome = WelcomePage(self.store, self.profiles, environment)
        self.welcome.profileLoaded.connect(self._profil_geladen)
        self._seite_hinzu(WELCOME_ID, self.welcome)

        for schritt in self.model.steps:
            if schritt.art is not Art.CATEGORY or schritt.category is None:
                continue
            seite = factory.create(schritt.category)
            if seite is None:
                continue
            self._seite_hinzu(schritt.id, seite)

        self.build_page = BuildPage(self.store, self.flow)
        self.build_page.laufendGeaendert.connect(self._sperre_setzen)
        self.build_page.fertig.connect(self._bau_fertig)
        self._seite_hinzu(BUILD_ID, self.build_page)

    def _seite_hinzu(self, step_id: str, seite: PageBase) -> None:
        self._pages[step_id] = seite
        self.stack.addWidget(seite)
        seite.completeChanged.connect(self._aktualisieren)

    # -- Verdrahtung ----------------------------------------------------------
    def _verdrahten(self) -> None:
        self.btn_laden.clicked.connect(self._laden)
        self.btn_speichern.clicked.connect(self.actions_.speichern)
        self.btn_export.clicked.connect(lambda: self.actions_.exportieren())
        self.btn_theme.clicked.connect(self.theme.toggle)
        self.btn_panel.toggled.connect(self._panel_umschalten)
        self.btn_beenden.clicked.connect(self.close)
        self.btn_zurueck.clicked.connect(self._zurueck)
        self.btn_weiter.clicked.connect(self._weiter)

        self.actions_.profileSaved.connect(
            lambda pfad: self.toasts.zeige(
                f"Profil gespeichert: {pfad.name}", ToastArt.ERFOLG
            )
        )
        self.flow.exportGewuenscht.connect(lambda: self.actions_.exportieren())

        self.store.issuesChanged.connect(self._aktualisieren)
        self.store.resolutionChanged.connect(self._aktualisieren)
        # Diese beiden Signale gab es seit jeher, verbunden war keines: schlug
        # das Laden der Paketdaten fehl, erfuhr man es nur als Randnotiz in der
        # Fusszeile einer einzigen Seite.
        self.controller.failed.connect(self._pakete_fehlgeschlagen)
        self.controller.statusChanged.connect(self._paket_status)

    def _kuerzel(self) -> None:
        """Tastenkuerzel -- vorher gab es im ganzen Programm keinen einzigen."""
        for folge, ziel in (
            (QKeySequence.StandardKey.Open, self._laden),
            (QKeySequence.StandardKey.Save, self.actions_.speichern),
            (QKeySequence.StandardKey.Find, self._suche_fokussieren),
            (QKeySequence("Ctrl+Return"), self._weiter),
            (QKeySequence("Ctrl+D"), self.theme.toggle),
        ):
            QShortcut(QKeySequence(folge), self, activated=ziel)

    # -- Navigation -----------------------------------------------------------
    def _ist_anwendbar(self, category) -> bool:
        return category.visible_when.evaluate(self.store.context())

    def _hat_fehler(self, step_id: str) -> bool:
        seite = self._pages.get(step_id)
        if seite is None:
            return False
        if any(problem.blocking for problem in self.store.issues(step_id)):
            return True
        return any(problem.blocking for problem in seite.local_issues())

    def _ist_baubereit(self) -> bool:
        if not self.store.resolution().is_valid:
            return False
        zusammenfassung = next(
            (
                seite
                for seite in self._pages.values()
                if isinstance(seite, SummaryPage)
            ),
            None,
        )
        return zusammenfassung is None or zusammenfassung.is_complete()

    def _sprung(self, step_id: str) -> None:
        schritt = self.model.step(step_id)
        if schritt is None or not self.model.anklickbar(schritt):
            return
        self._gehe_zu(step_id)

    def _weiter(self) -> None:
        if not self.btn_weiter.isEnabled():
            return
        ziel = self.model.naechster()
        if ziel is not None:
            self._gehe_zu(ziel)

    def _zurueck(self) -> None:
        ziel = self.model.voriger()
        if ziel is not None:
            self._gehe_zu(ziel)

    def _gehe_zu(self, step_id: str, *, animiert: bool = True) -> None:
        aktuell = self._pages.get(self.model.current_id)
        if aktuell is not None and step_id != self.model.current_id:
            if not aktuell.leave():
                return          # die Seite haelt fest, etwa ein Dateidialog

        richtung = self.model.richtung(step_id)
        self.model.betreten(step_id)

        seite = self._pages.get(step_id)
        if seite is None:
            log.error("Kein Widget fuer Schritt %r", step_id)
            return
        seite.enter()
        index = self.stack.indexOf(seite)
        if animiert:
            self.stack.set_current(index, richtung=richtung)
        else:
            self.stack.setCurrentIndex(index)
        self.kopfzeile.setze(seite.titel(), seite.untertitel())
        self._aktualisieren()

    def _aktualisieren(self) -> None:
        self.sidebar.aktualisieren()
        seite = self._pages.get(self.model.current_id)
        vollstaendig = seite.is_complete() if seite is not None else True
        letzter = self.model.naechster() is None

        self.btn_weiter.setEnabled(
            vollstaendig and not letzter and not self.model.locked
        )
        self.btn_weiter.setVisible(not letzter)
        self.btn_zurueck.setEnabled(
            self.model.voriger() is not None and not self.model.locked
        )
        for knopf in (self.btn_laden, self.btn_speichern, self.btn_export):
            knopf.setEnabled(not self.model.locked)

    # -- Ereignisse -----------------------------------------------------------
    def _laden(self) -> None:
        if self.actions_.laden():
            self._profil_geladen()

    def _profil_geladen(self) -> None:
        """Ein geladenes Profil ist vollstaendig -- das darf man auch sehen.

        Alle Schritte gelten damit als besucht und sind anklickbar. Wer nur
        eine Kleinigkeit aendern will, springt direkt dorthin; wer gleich bauen
        will, ans Ende. Frueher warf das Laden zurueck auf Schritt eins -- man
        musste sich durch alles durchklicken, obwohl schon alles eingestellt
        war.
        """
        self.model.alles_besucht()
        self.welcome.markiere_extern_geladen()
        for seite in self._pages.values():
            seite.sync_from_store()
        self._aktualisieren()
        self.toasts.zeige(
            "Profil geladen -- alle Schritte sind eingestellt.",
            ToastArt.ERFOLG,
            aktion="Zur Zusammenfassung",
            bei_aktion=self._zur_zusammenfassung,
        )

    def _zur_zusammenfassung(self) -> None:
        for step_id, seite in self._pages.items():
            if isinstance(seite, SummaryPage):
                self._gehe_zu(step_id)
                return

    def _panel_umschalten(self, an: bool) -> None:
        self.iso_panel.setVisible(an)
        self.settings.iso_panel_visible = an

    def _suche_fokussieren(self) -> None:
        """Strg+F springt ins Suchfeld der aktuellen Seite, falls es eines gibt."""
        seite = self._pages.get(self.model.current_id)
        fokus = getattr(seite, "fokus_auf_suche", None)
        if fokus is not None and fokus():
            return
        self.toasts.zeige("Diese Seite hat keine Suche.", ToastArt.INFO)

    def _sperre_setzen(self, gesperrt: bool) -> None:
        self.model.locked = gesperrt
        self._aktualisieren()

    def _bau_fertig(self) -> None:
        self.model.build_done = True
        self._aktualisieren()
        self.toasts.zeige("Die ISO ist fertig.", ToastArt.ERFOLG)

    def _pakete_fehlgeschlagen(self, meldung: str) -> None:
        log.warning("Paketdaten nicht ladbar: %s", meldung)
        self.sidebar.set_notice(
            "Paketdaten nicht verfuegbar -- Paketnamen lassen sich nicht "
            "pruefen. Der Bau funktioniert trotzdem."
        )

    def _paket_status(self, text: str) -> None:
        self.sidebar.set_notice(text if "nicht" in text.lower() else "")

    # -- Beenden --------------------------------------------------------------
    def closeEvent(self, event) -> None:
        """Ein laufender Bau haelt das Fenster fest; sonst wird nachgefragt."""
        if not self.build_page.darf_schliessen():
            from PySide6.QtWidgets import QMessageBox

            antwort = QMessageBox.question(
                self,
                "Bau laeuft noch",
                "Es wird gerade eine ISO gebaut. Das Programm zu beenden bricht "
                "den Bau ab.\n\nTrotzdem beenden?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if antwort != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            job = self.build_page.job
            if job is not None:
                job.cancel()
                # Auf die Faeden warten, statt sie von Qt unter laufendem
                # pkill wegraeumen zu lassen ("QThread: Destroyed while thread
                # is still running").
                job.wait(30000)

        if not self.actions_.darf_beenden():
            event.ignore()
            return

        self.controller.cancel()
        self.toasts.schliesse_alle()
        motion.stop_all()
        event.accept()


def _knopf(text: str, variante: str) -> QPushButton:
    knopf = QPushButton(text)
    if variante:
        knopf.setProperty("variant", variante)
    knopf.setCursor(Qt.CursorShape.PointingHandCursor)
    return knopf


__all__ = ["MainWindow"]
