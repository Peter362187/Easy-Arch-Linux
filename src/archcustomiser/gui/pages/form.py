"""Formularseite -- Textfelder, Auswahllisten, Passwortfelder, Vorschau.

Erzeugt Grundkonfiguration, Benutzerkonto, Branding und ISO-Einstellungen aus
demselben Code. Ein neues Feld ist ein YAML-Eintrag.

Passwoerter: Felder mit ``secret: true`` schreiben ausschliesslich in den
SecretStore. Ihr Wert erreicht ``BuildConfig`` nie und kann damit strukturell
nicht in einem Profil landen. Die Live-Pruefung liest den **angezeigten Text**
und nicht den Store -- dorthin wandert der Wert erst beim Verlassen des Feldes,
und die Wiederholung meldete sonst waehrend des Tippens dauerhaft "stimmen
nicht ueberein".

Neu ist die Vorschau rechts neben dem Formular. Welche es ist, sagt der Katalog
ueber ``preview``; die Seite kennt keine Kategorie namentlich.
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLineEdit,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTextEdit,
    QWidget,
)

from ...core import validation
from ...core.catalog import Category, FieldSpec
from ...core.resolver import Issue
from ..design import tokens
from ..previews import PreviewContext
from ..previews import create as vorschau_erzeugen
from ..previews.registry import rollen_aus_katalog
from ..store import SelectionStore
from ..widgets.common import HintLabel
from ..widgets.fields import FieldRow, waehle_pfad
from .base import PageBase

log = logging.getLogger(__name__)

VALIDATION_DELAY_MS = 250
# Unter dieser Fensterbreite steht die Vorschau ueber statt neben dem Formular.
SCHMAL_AB = 1100


class CatalogFormPage(PageBase):
    def __init__(self, category: Category, store: SelectionStore) -> None:
        super().__init__(category, store)
        self._rows: dict[str, FieldRow] = {}
        self._valid: dict[str, bool] = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(VALIDATION_DELAY_MS)
        self._timer.timeout.connect(self._alles_pruefen)

        self._aufbauen()
        self._pflichtlegende()
        self.add_help_link()
        self.store.fieldChanged.connect(self._feld_geaendert)

    def _feld_geaendert(self, _binding: str) -> None:
        """Sichtbarkeit UND Gueltigkeit neu bestimmen.

        Frueher lief nur die Sichtbarkeit. Wurde ein ungueltiges Pflichtfeld
        durch eine andere Eingabe unsichtbar, blieb sein Eintrag bis zum
        naechsten Timerlauf auf "ungueltig" und sperrte den Weiter-Knopf.
        """
        self._sichtbarkeit()
        self._timer.start()

    # -- Aufbau ---------------------------------------------------------------
    def _aufbauen(self) -> None:
        werte = tokens()
        behaelter = QWidget()
        form = QFormLayout(behaelter)
        form.setSpacing(werte.space.md)
        form.setContentsMargins(0, 0, werte.space.sm, 0)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        # Beschriftung ueber dem Feld statt daneben: bei laengeren deutschen
        # Woertern ("Hintergrundbild des Bootmenues") blieb rechts sonst kaum
        # Platz fuer das Eingabefeld.
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        for spec in self.category.fields:
            zeile = FieldRow(spec)
            self._verdrahten(zeile)
            form.addRow(zeile)
            self._rows[spec.id] = zeile

        rolle = QScrollArea()
        rolle.setWidgetResizable(True)
        rolle.setFrameShape(QScrollArea.Shape.NoFrame)
        rolle.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rolle.setWidget(behaelter)

        self.vorschau = self._vorschau_erzeugen()
        if self.vorschau is None:
            self._root.addWidget(rolle, 1)
            return

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.addWidget(rolle)
        self._splitter.addWidget(self.vorschau)
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 2)
        self._splitter.setChildrenCollapsible(False)
        self._root.addWidget(self._splitter, 1)

    def _vorschau_erzeugen(self) -> QWidget | None:
        if not self.category.preview:
            return None
        kontext = PreviewContext(self.store, rollen_aus_katalog(self.store.catalog))
        return vorschau_erzeugen(self.category.preview, kontext)

    def _verdrahten(self, zeile: FieldRow) -> None:
        spec = zeile.spec
        widget = zeile.widget

        if isinstance(widget, QCheckBox):
            widget.toggled.connect(lambda wert, s=spec: self._geaendert(s, wert))
        elif isinstance(widget, QSpinBox):
            widget.valueChanged.connect(lambda wert, s=spec: self._geaendert(s, wert))
        elif isinstance(widget, QComboBox):
            widget.currentIndexChanged.connect(
                lambda _i, s=spec, z=zeile: self._geaendert(s, z.angezeigter_text())
            )
            if widget.isEditable():
                widget.lineEdit().textEdited.connect(
                    lambda text, s=spec: self._geaendert(s, text)
                )
        elif isinstance(widget, QTextEdit):
            widget.textChanged.connect(
                lambda s=spec, w=widget: self._geaendert(s, w.toPlainText())
            )
        elif isinstance(widget, QLineEdit):
            if spec.secret:
                # Bei geheimen Feldern bewusst NICHT je Tastendruck: jeder
                # Zwischenstand erzeugte ein eigenes ``Secret`` und meldete
                # sich beim Log-Filter an. Ein Passwort "archiso" hinterliess
                # so die Literale "arc", "arch", "archi" -- und jedes davon
                # wurde fortan in jeder Logzeile ersetzt, auch mitten im Wort.
                widget.editingFinished.connect(
                    lambda s=spec, w=widget: self._geaendert(s, w.text())
                )
                widget.textEdited.connect(lambda _t: self._timer.start())
            else:
                widget.textEdited.connect(lambda text, s=spec: self._geaendert(s, text))

        if zeile.browse is not None:
            zeile.browse.clicked.connect(
                lambda _c=False, s=spec: self._durchsuchen(s)
            )

    # -- Ereignisse -----------------------------------------------------------
    def _geaendert(self, spec: FieldSpec, wert: Any) -> None:
        if spec.secret:
            self.store.set_secret(spec.binding, str(wert))
        else:
            self.store.set_field(spec.binding, wert)
        self._timer.start()

    def _durchsuchen(self, spec: FieldSpec) -> None:
        start = str(self.store.field(spec.binding) or "")
        gewaehlt = waehle_pfad(spec, start, self)
        if not gewaehlt:
            return
        zeile = self._rows[spec.id]
        zeile.setze_wert(gewaehlt)
        self._geaendert(spec, gewaehlt)

    # -- Anzeige --------------------------------------------------------------
    def sync_from_store(self) -> None:
        for zeile in self._rows.values():
            spec = zeile.spec
            if spec.secret:
                # Geheimnisse werden nie zurueckgeschrieben -- aber ein leerer
                # SecretStore muss auch ein leeres Feld bedeuten. Nach dem
                # Laden eines Profils standen sonst weiter Punkte im Feld,
                # waehrend die Meldung "wird benoetigt" erschien.
                if not self.store.has_secret(spec.binding):
                    zeile.leeren()
                continue
            zeile.setze_wert(self.store.field(spec.binding, spec.default))
        self._sichtbarkeit()
        self._alles_pruefen()

    def _sichtbarkeit(self) -> None:
        kontext = self.store.context()
        for zeile in self._rows.values():
            sichtbar = zeile.spec.visible_when.evaluate(kontext)
            zeile.setze_sichtbar(sichtbar)
            zeile.setEnabled(sichtbar and zeile.spec.enabled_when.evaluate(kontext))

    def _pflichtlegende(self) -> None:
        """Erklaeren, was der Stern bedeutet.

        Er stand bisher an den Beschriftungen, ohne dass irgendwo erklaert war,
        wofuer -- ein Zeichen, das nur weiss, wer es schon kennt.
        """
        if not any(spec.required for spec in self.category.fields):
            return
        self._root.addWidget(HintLabel("* Pflichtfeld"))

    # -- Pruefung -------------------------------------------------------------
    def _alles_pruefen(self) -> None:
        kontext = self.store.context()
        for zeile in self._rows.values():
            spec = zeile.spec
            aktiv = spec.visible_when.evaluate(kontext) and spec.enabled_when.evaluate(
                kontext
            )
            if not aktiv:
                self._valid[spec.id] = True
                zeile.verstecke_meldung()
                continue

            if spec.secret:
                text = zeile.angezeigter_text()
            else:
                text = self.store.field(spec.binding, spec.default)

            if spec.required and not str(text or "").strip():
                zeile.zeige_meldung(f"{spec.label} wird benoetigt.", warnung=False)
                self._valid[spec.id] = False
                continue

            if spec.confirm_field:
                zweitwert = self._wert_von(spec.confirm_field, secret=spec.secret)
                erster = str(text or "")
                if erster and erster != zweitwert:
                    ziel = self._rows.get(spec.confirm_field, zeile)
                    ziel.zeige_meldung(
                        "Die beiden Eingaben stimmen nicht ueberein.", warnung=False
                    )
                    self._valid[spec.id] = False
                    continue
                # Stimmen sie ueberein, muss die Meldung am Wiederholungsfeld
                # auch wieder verschwinden.
                if spec.confirm_field in self._rows:
                    self._rows[spec.confirm_field].verstecke_meldung()

            if spec.validator:
                ergebnis = validation.validate(spec.validator, text)
                if not ergebnis.ok:
                    zeile.zeige_meldung(ergebnis.message, warnung=ergebnis.is_warning)
                    self._valid[spec.id] = ergebnis.is_warning
                    continue

            zeile.verstecke_meldung()
            self._valid[spec.id] = True

        self._feldfehler_melden()
        self.completeChanged.emit()

    def _wert_von(self, field_id: str, *, secret: bool) -> str:
        """Der Wert eines anderen Feldes -- aus dem passenden Speicher.

        Geheime Felder liegen im ``SecretStore``, alle anderen in der
        Konfiguration. Vorher wurde nur der erste Fall bedacht -- bei einem
        nicht geheimen Feld war der Vergleichswert per Konstruktion leer, und
        jede nichtleere Eingabe meldete dauerhaft "stimmen nicht ueberein".
        """
        zeile = self._rows.get(field_id)
        if secret:
            return zeile.angezeigter_text() if zeile is not None else ""
        spec = self.category.field(field_id)
        binding = spec.binding if spec else f"{self.category.id}.{field_id}"
        return str(self.store.field(binding) or "")

    def _hashing_warnung(self) -> Issue | None:
        """Warnen, wenn sich ein Passwort hier gar nicht setzen laesst.

        Ohne eine Moeglichkeit zu hashen wird das Konto gesperrt angelegt; das
        Feld anzubieten und stillschweigend nichts damit zu tun waere die
        schlechteste Auskunft. Das Feld bleibt trotzdem stehen: es
        auszublenden wuerde die Frage aufwerfen, warum es fehlt.
        """
        if not any(spec.secret for spec in self.category.fields):
            return None
        from ...core.archiso.users import hashing_available

        if hashing_available():
            return None
        return Issue(
            severity="warning",
            code="hashing_unavailable",
            category_id=self.category.id,
            message=(
                "Auf diesem Rechner laesst sich kein Passwort-Hash erzeugen. "
                "Das Konto wird gesperrt angelegt und das Passwort spaeter im "
                "laufenden System mit 'passwd' gesetzt."
            ),
        )

    def _feldfehler_melden(self) -> None:
        """Die Feldfehler zusaetzlich nach oben geben.

        Ist der Weiter-Knopf gesperrt, weil ein Pflichtfeld weiter oben leer
        ist, sah man die Begruendung im Scrollbereich womoeglich gar nicht.
        """
        meldungen = []
        for spec_id, gueltig in self._valid.items():
            if gueltig:
                continue
            zeile = self._rows.get(spec_id)
            if zeile is None:
                continue
            meldungen.append(
                Issue(
                    severity="error",
                    code="field_invalid",
                    category_id=self.category.id,
                    message=f"{zeile.spec.label}: {zeile.meldung.text()}",
                )
            )
        hinweis = self._hashing_warnung()
        if hinweis is not None:
            meldungen.append(hinweis)
        self.set_local_issues(tuple(meldungen))

    def is_complete(self) -> bool:
        return all(self._valid.values()) and super().is_complete()


__all__ = ["CatalogFormPage"]
