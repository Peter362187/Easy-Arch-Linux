"""Die erste Seite: womit soll begonnen werden?

Vorlagen, ein eigenes Profil oder ein leeres Blatt. Die Auswahl wird erst beim
Verlassen angewendet: wer eine Vorlage nur ansieht und sich anders entscheidet,
soll den Store nicht schon veraendert haben.

Der Fehler, den diese Seite frueher hatte, war teuer. "Von vorn beginnen" ist
vorgehakt, und die Startseite ist ueber Zurueck erreichbar. Zurueck und wieder
vor genuegte, um eine halbe Stunde Zusammenstellung wortlos zu loeschen -- die
einzige Stelle im Programm, die Arbeit ohne Rueckfrage vernichtete, waehrend
das Beenden ausdruecklich nachfragt. Jetzt wird nur angewendet, was sich
tatsaechlich geaendert hat, und auch das nur nach Rueckfrage.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QFileDialog,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...core.environment import Environment
from ...core.profiles import ProfileError, ProfileInfo, ProfileService
from .. import motion
from ..design import mit_alpha, tokens
from ..design.typo import BODY, CAPTION, schrift
from ..store import SelectionStore
from ..widgets.common import HeadlineLabel, HintLabel
from .base import PageBase

log = logging.getLogger(__name__)


class _Auswahlkarte(QWidget):
    """Eine grosse anklickbare Flaeche -- Titel, Erklaerung, Auswahlpunkt."""

    gewaehlt = Signal(str)

    def __init__(self, kennung: str, titel: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kennung = kennung
        self.titel = titel
        self.text = text
        self._aktiv = False
        self._hover = 0.0
        self._fuellung = 0.0

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAccessibleName(titel)
        if text:
            self.setAccessibleDescription(text)
        self.setFixedHeight(self._hoehe())

    def _hoehe(self) -> int:
        werte = tokens()
        zeilen = 2 if self.text else 1
        return int(werte.space.md * 2 + 20 + (zeilen - 1) * 18)

    # -- Zustand --------------------------------------------------------------
    def setze_aktiv(self, wert: bool) -> None:
        if self._aktiv == wert:
            return
        self._aktiv = wert
        motion.animate(
            self,
            von=self._fuellung,
            bis=1.0 if wert else 0.0,
            dauer=motion.SCHNELL,
            setzen=self._setze_fuellung,
        )

    def ist_aktiv(self) -> bool:
        return self._aktiv

    def _setze_fuellung(self, wert) -> None:
        self._fuellung = float(wert)
        self.update()

    def _setze_hover(self, wert) -> None:
        self._hover = float(wert)
        self.update()

    # -- Bedienung ------------------------------------------------------------
    def enterEvent(self, event) -> None:
        motion.animate(
            self, von=self._hover, bis=1.0, dauer=motion.SCHNELL, setzen=self._setze_hover
        )
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        motion.animate(
            self, von=self._hover, bis=0.0, dauer=motion.SCHNELL, setzen=self._setze_hover
        )
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self.gewaehlt.emit(self.kennung)
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.gewaehlt.emit(self.kennung)
            event.accept()
            return
        super().keyPressEvent(event)

    # -- Zeichnen -------------------------------------------------------------
    def paintEvent(self, event) -> None:
        werte = tokens()
        p = werte.palette
        maler = QPainter(self)
        maler.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        flaeche = self.rect().adjusted(1, 1, -1, -1)

        maler.setPen(Qt.PenStyle.NoPen)
        grund = p.surface_alt if self._hover > 0.5 and not self._aktiv else p.surface
        maler.setBrush(QColor(grund))
        maler.drawRoundedRect(flaeche, werte.radius.md, werte.radius.md)
        if self._fuellung > 0.01:
            maler.setBrush(QColor(mit_alpha(p.accent, 0.16 * self._fuellung)))
            maler.drawRoundedRect(flaeche, werte.radius.md, werte.radius.md)

        randfarbe = QColor(p.accent) if self._fuellung > 0.5 else QColor(p.border)
        maler.setBrush(Qt.BrushStyle.NoBrush)
        maler.setPen(QPen(randfarbe, 1.0 + self._fuellung))
        maler.drawRoundedRect(flaeche, werte.radius.md, werte.radius.md)

        if self.hasFocus():
            maler.setPen(QPen(QColor(p.accent), 2.0))
            maler.drawRoundedRect(
                flaeche.adjusted(-1, -1, 1, 1), werte.radius.md + 1, werte.radius.md + 1
            )

        # Auswahlpunkt
        mitte_y = werte.space.md + 9
        punkt_x = werte.space.md + 8
        maler.setBrush(Qt.BrushStyle.NoBrush)
        maler.setPen(QPen(QColor(p.accent if self._fuellung > 0.5 else p.border_strong), 1.5))
        maler.drawEllipse(int(punkt_x - 7), int(mitte_y - 7), 14, 14)
        if self._fuellung > 0.01:
            maler.setPen(Qt.PenStyle.NoPen)
            maler.setBrush(QColor(p.accent))
            radius = 4.5 * self._fuellung
            maler.drawEllipse(
                int(punkt_x - radius), int(mitte_y - radius), int(radius * 2), int(radius * 2)
            )

        links = punkt_x + 16
        maler.setFont(schrift(BODY, fett=True))
        maler.setPen(QColor(p.text))
        maler.drawText(
            int(links),
            int(mitte_y + 5),
            self.titel,
        )
        if self.text:
            maler.setFont(schrift(CAPTION))
            maler.setPen(QColor(p.text_muted))
            maler.drawText(int(links), int(mitte_y + 24), self.text)
        maler.end()


class WelcomePage(PageBase):
    """Vorlage waehlen, Profil laden oder von vorn beginnen."""

    profileLoaded = Signal()

    def __init__(
        self,
        store: SelectionStore,
        profiles: ProfileService,
        environment: Environment | None = None,
    ) -> None:
        super().__init__(None, store)
        self.profiles = profiles
        self.environment = environment
        self._loaded_from: Path | None = None
        self._angewendet: object = None
        self._karten: dict[str, _Auswahlkarte] = {}
        self._vorlagen: dict[str, ProfileInfo] = {}
        self._gewaehlt = "leer"

        werte = tokens()
        inneres = QWidget()
        self._liste = QVBoxLayout(inneres)
        self._liste.setContentsMargins(0, 0, werte.space.sm, 0)
        self._liste.setSpacing(werte.space.sm)

        self._vorlagen_aufbauen()
        self._feste_auswahl()
        self._liste.addStretch(1)

        rolle = QScrollArea()
        rolle.setWidgetResizable(True)
        rolle.setFrameShape(QScrollArea.Shape.NoFrame)
        rolle.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rolle.setWidget(inneres)
        self._root.addWidget(rolle, 1)

        self.status = HintLabel(self._umgebungstext())
        self.status.setVisible(bool(self.status.text()))
        self._root.addWidget(self.status)

        self._setze_gewaehlt("leer")

    # -- Aufbau ---------------------------------------------------------------
    def _vorlagen_aufbauen(self) -> None:
        vorlagen = [info for info in self.profiles.list() if info.builtin]
        if not vorlagen:
            return
        self._liste.addWidget(HeadlineLabel("Mit einer Vorlage beginnen", level=2))
        for info in vorlagen:
            kennung = f"vorlage:{info.path}"
            self._vorlagen[kennung] = info
            self._karte(kennung, info.display_name, info.description)

    def _feste_auswahl(self) -> None:
        werte = tokens()
        self._liste.addSpacing(werte.space.sm)
        self._liste.addWidget(HeadlineLabel("Oder", level=2))
        self._karte(
            "datei",
            "Eigenes Profil laden ...",
            "Eine gespeicherte Konfiguration von der Festplatte oeffnen.",
        )
        self._karte(
            "leer",
            "Von vorn beginnen",
            "Alles selbst zusammenstellen, ohne Vorgaben.",
        )

    def _karte(self, kennung: str, titel: str, text: str) -> None:
        karte = _Auswahlkarte(kennung, titel, text)
        karte.gewaehlt.connect(self._setze_gewaehlt)
        self._karten[kennung] = karte
        self._liste.addWidget(karte)

    def _umgebungstext(self) -> str:
        if self.environment is None or self.environment.can_build:
            return ""
        # Frueher eine modale Infobox beim Start. Dieselbe Auskunft, ohne dass
        # sie sich vor die Anwendung schiebt.
        return self.environment.summary()

    # -- Auswahl --------------------------------------------------------------
    def _setze_gewaehlt(self, kennung: str) -> None:
        self._gewaehlt = kennung
        for schluessel, karte in self._karten.items():
            karte.setze_aktiv(schluessel == kennung)
        self.completeChanged.emit()

    def gewaehlt(self) -> str:
        return self._gewaehlt

    def is_complete(self) -> bool:
        return bool(self._gewaehlt)

    def titel(self) -> str:
        return "Willkommen"

    def untertitel(self) -> str:
        return "Womit soll begonnen werden? Alles laesst sich danach noch aendern."

    # -- Uebergang ------------------------------------------------------------
    def leave(self) -> bool:
        """Setzt die Auswahl um, bevor weitergegangen wird."""
        if self._gewaehlt == "leer":
            if self._angewendet == ("leer",):
                return True          # nichts hat sich geaendert
            if not self._darf_verwerfen():
                return False
            self.store.reset()
            self._angewendet = ("leer",)
            return True

        if self._gewaehlt == "datei":
            start = str(self.profiles.builtin_dir)
            pfad, _filter = QFileDialog.getOpenFileName(
                self, "Profil laden", start, "Profile (*.yaml *.yml)"
            )
            if not pfad:
                return False          # abgebrochen -- auf der Seite bleiben
            if not self._laden(Path(pfad)):
                return False
            self._angewendet = ("datei", pfad)
            return True

        info = self._vorlagen.get(self._gewaehlt)
        if info is None:
            return False
        if self._angewendet == ("vorlage", str(info.path)):
            return True          # dieselbe Vorlage, nichts zu tun
        if not self._darf_verwerfen():
            return False
        if not self._laden(info.path):
            return False
        self._angewendet = ("vorlage", str(info.path))
        return True

    def _darf_verwerfen(self) -> bool:
        """Fragt nach, bevor eine begonnene Zusammenstellung verworfen wird.

        Beim ersten Durchgang gibt es nichts zu verlieren; dann erscheint auch
        keine Frage.
        """
        if self._angewendet is None:
            return True
        antwort = QMessageBox.question(
            self,
            "Zusammenstellung verwerfen?",
            "Die bisherige Auswahl wird dabei zurueckgesetzt.\n\nFortfahren?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return antwort == QMessageBox.StandardButton.Yes

    def _laden(self, pfad: Path) -> bool:
        try:
            ergebnis = self.profiles.load(pfad)
        except ProfileError as exc:
            QMessageBox.warning(self, "Profil konnte nicht geladen werden", str(exc))
            return False

        if ergebnis.issues:
            details = "\n".join(
                f"- {issue.message}"
                + (f"\n   ({issue.action_taken})" if issue.action_taken else "")
                for issue in ergebnis.issues
            )
            QMessageBox.information(
                self,
                "Hinweise zum Profil",
                f"Das Profil wurde geladen. Dabei ist Folgendes aufgefallen:\n\n{details}",
            )

        self.store.replace_config(ergebnis.config)
        self._loaded_from = pfad
        if ergebnis.secret_fields:
            QMessageBox.information(
                self,
                "Passwort erneut eingeben",
                "Profile enthalten keine Passwoerter. Bitte das Passwort im "
                "Schritt 'Benutzerkonto' neu eingeben.",
            )
        self.profileLoaded.emit()
        return True

    def loaded_from(self) -> Path | None:
        return self._loaded_from

    def markiere_extern_geladen(self) -> None:
        """Nach einem Profilwechsel ueber die Kopfzeile.

        Ohne diesen Vermerk haette ein spaeterer Besuch der Startseite das
        geladene Profil beim Weitergehen wieder zurueckgesetzt -- "Von vorn
        beginnen" steht ja weiter vorgehakt.
        """
        self._angewendet = ("extern", object())


__all__ = ["WelcomePage"]
