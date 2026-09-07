"""Die erste Seite: womit soll begonnen werden?

Vorher landete man ohne Vorrede in "Grundkonfiguration" -- einem Formular. Die
vier mitgelieferten Vorlagen (minimal, desktop, gaming, development) existierten
zwar, waren aber nur ueber einen Knopf in der Fussleiste erreichbar und wurden
darum praktisch nie gefunden. Wer eine nimmt, muss anschliessend nur noch
anpassen, was ihm nicht passt, statt vierzehn Schritte durchzuklicken.

Unter Windows erschien ausserdem beim Start eine Infobox "Hinweis zur
Bauumgebung" -- der erste Eindruck war ein Dialog mit dem Wort "Hinweis", was
nach einem Fehler aussieht. Dieselbe Auskunft steht jetzt ruhig auf dieser
Seite.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QWizardPage,
)

from ...core.environment import Environment
from ...core.profiles import ProfileError, ProfileInfo, ProfileService
from .. import theme
from ..store import SelectionStore
from ..widgets.common import HeadlineLabel, HintLabel

log = logging.getLogger(__name__)

WELCOME_STEP = 1        # vor der ersten Katalogkategorie (kleinster step: 5)


class _Choice(QFrame):
    """Eine anklickbare Flaeche mit Titel und Erklaerung."""

    def __init__(
        self,
        title: str,
        description: str,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("optionCard")     # nutzt das zentrale Stylesheet
        self.setFrameShape(QFrame.Shape.StyledPanel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            theme.SPACE_MD, theme.SPACE_SM, theme.SPACE_MD, theme.SPACE_SM
        )
        layout.setSpacing(theme.SPACE_XS)

        self.button = QRadioButton(title)
        font = self.button.font()
        font.setBold(True)
        self.button.setFont(font)
        layout.addWidget(self.button)

        if description:
            layout.addWidget(HintLabel(description))

    def mousePressEvent(self, event) -> None:      # noqa: N802 -- Qt
        # Die ganze Karte anklickbar machen, nicht nur den kleinen Knopf.
        self.button.setChecked(True)
        super().mousePressEvent(event)


class WelcomePage(QWizardPage):
    """Vorlage waehlen, Profil laden oder von vorn beginnen."""

    profileLoaded = Signal()

    def __init__(
        self,
        store: SelectionStore,
        profiles: ProfileService,
        environment: Environment | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.profiles = profiles
        self.environment = environment
        self._loaded_from: Path | None = None
        # Was zuletzt tatsaechlich angewendet wurde. Ohne diesen Merker
        # verwarf jedes erneute Weitergehen von dieser Seite die gesamte
        # Zusammenstellung: die Startseite ist ueber 'Zurueck' von der
        # ersten Katalogseite erreichbar, und 'Von vorn beginnen' ist
        # vorgehakt. Die einzige Stelle im Programm, die Arbeit ohne
        # Warnung vernichtete -- waehrend der Wizard beim Beenden
        # ausdruecklich nachfragt.
        self._angewendet: object = None

        self.setTitle("Willkommen")
        self.setSubTitle(
            "Womit soll begonnen werden? Alles laesst sich danach noch aendern."
        )

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._choices: list[tuple[_Choice, ProfileInfo | None]] = []

        root = QVBoxLayout(self)
        root.setSpacing(theme.SPACE_MD)

        inner = QWidget()
        self._list = QVBoxLayout(inner)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(theme.SPACE_SM)

        self._add_templates()
        self._add_fixed_choices()
        self._list.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidget(inner)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(scroll, 1)

        self.status = HintLabel(self._environment_text())
        root.addWidget(self.status)

    # -- Aufbau ---------------------------------------------------------------
    def _add_templates(self) -> None:
        vorlagen = [info for info in self.profiles.list() if info.builtin]
        if not vorlagen:
            return
        ueberschrift = HeadlineLabel("Mit einer Vorlage beginnen", level=2)
        self._list.addWidget(ueberschrift)

        for info in vorlagen:
            karte = _Choice(info.display_name, info.description)
            self._group.addButton(karte.button)
            self._list.addWidget(karte)
            self._choices.append((karte, info))
            karte.button.toggled.connect(self._changed)

    def _add_fixed_choices(self) -> None:
        self._list.addSpacing(theme.SPACE_SM)
        self._list.addWidget(HeadlineLabel("Oder", level=2))

        self._eigenes = _Choice(
            "Eigenes Profil laden ...",
            "Eine gespeicherte Konfiguration von der Festplatte oeffnen.",
        )
        self._leer = _Choice(
            "Von vorn beginnen",
            "Alles selbst zusammenstellen, ohne Vorgaben.",
        )
        for karte in (self._eigenes, self._leer):
            self._group.addButton(karte.button)
            self._list.addWidget(karte)
            self._choices.append((karte, None))
            karte.button.toggled.connect(self._changed)

        self._leer.button.setChecked(True)

    def _environment_text(self) -> str:
        if self.environment is None or self.environment.can_build:
            return ""
        # Frueher eine modale Infobox beim Start. Hier ist dieselbe Auskunft,
        # ohne dass sie sich vor die Anwendung schiebt.
        return self.environment.summary()

    # -- Ereignisse -----------------------------------------------------------
    def _changed(self, checked: bool) -> None:
        if checked:
            self.completeChanged.emit()

    def isComplete(self) -> bool:
        return self._group.checkedButton() is not None

    def selected_profile(self) -> ProfileInfo | None:
        """Die gewaehlte Vorlage, falls eine gewaehlt wurde."""
        for karte, info in self._choices:
            if karte.button.isChecked():
                return info
        return None

    def wants_file_dialog(self) -> bool:
        return self._eigenes.button.isChecked()

    def wants_empty(self) -> bool:
        return self._leer.button.isChecked()

    # -- Uebergang ------------------------------------------------------------
    def validatePage(self) -> bool:
        """Setzt die Auswahl um, bevor weitergegangen wird.

        Erst hier und nicht beim Anklicken: wer eine Vorlage nur ansieht und
        sich anders entscheidet, soll den Store nicht schon veraendert haben.
        """
        if self.wants_empty():
            if self._angewendet == ("leer",):
                return True          # nichts hat sich geaendert
            if not self._darf_verwerfen():
                return False
            self.store.reset()
            self._angewendet = ("leer",)
            return True

        if self.wants_file_dialog():
            pfad, _filter = QFileDialog.getOpenFileName(
                self, "Profil laden", str(Path.home()), "Profile (*.yaml *.yml)"
            )
            if not pfad:
                return False          # abgebrochen -- auf der Seite bleiben
            if not self._load(Path(pfad)):
                return False
            self._angewendet = ("datei", pfad)
            return True

        info = self.selected_profile()
        if info is None:
            return False
        if self._angewendet == ("vorlage", str(info.path)):
            return True          # dieselbe Vorlage, nichts zu tun
        if not self._darf_verwerfen():
            return False
        if not self._load(info.path):
            return False
        self._angewendet = ("vorlage", str(info.path))
        return True

    def _darf_verwerfen(self) -> bool:
        """Fragt nach, bevor eine begonnene Zusammenstellung verworfen wird.

        Beim ersten Durchgang gibt es nichts zu verlieren; dann erscheint
        auch keine Frage.
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

    def _load(self, pfad: Path) -> bool:
        try:
            ergebnis = self.profiles.load(pfad)
        except ProfileError as exc:
            QMessageBox.warning(self, "Profil konnte nicht geladen werden", str(exc))
            return False

        if ergebnis.issues:
            details = "\n".join(
                f"• {issue.message}"
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
