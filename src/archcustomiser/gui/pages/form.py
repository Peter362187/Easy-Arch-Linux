"""Formularseite -- rendert Textfelder, Auswahllisten und Passwortfelder.

Erzeugt Grundkonfiguration, Benutzerkonto, Branding und ISO-Einstellungen aus
demselben Code. Ein neues Feld ist ein YAML-Eintrag.

Passwoerter: Felder mit ``secret: true`` schreiben ausschliesslich in den
SecretStore. Ihr Wert erreicht ``BuildConfig`` nie und kann damit strukturell
nicht in einem Profil landen. Beim Verlassen der Seite wird zusaetzlich
geprueft, ob Passwort und Wiederholung uebereinstimmen.
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...core import choices as choice_registry
from ...core import validation
from ...core.catalog import Category, FieldSpec
from ...core.resolver import Issue
from .. import theme
from ..store import SelectionStore
from ..widgets.common import HintLabel
from .base import CatalogPageBase

log = logging.getLogger(__name__)

VALIDATION_DELAY_MS = 250


class _FieldRow:
    """Ein Feld samt Eingabewidget und Meldungszeile."""

    __slots__ = ("spec", "widget", "message", "label", "container", "browse")

    def __init__(
        self,
        spec: FieldSpec,
        widget: QWidget,
        message: QLabel,
        label: QLabel,
        container: QWidget,
        browse: QPushButton | None = None,
    ) -> None:
        self.spec = spec
        self.widget = widget
        self.message = message
        self.label = label
        self.container = container
        self.browse = browse


class CatalogFormPage(CatalogPageBase):
    def __init__(self, category: Category, store: SelectionStore) -> None:
        super().__init__(category, store)
        self._rows: dict[str, _FieldRow] = {}
        self._valid: dict[str, bool] = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(VALIDATION_DELAY_MS)
        self._timer.timeout.connect(self._validate_all)
        self._build_ui()
        self._add_required_legend()
        self.add_help_link()
        self.store.fieldChanged.connect(self._on_field_changed)

    def _on_field_changed(self, _binding: str) -> None:
        """Sichtbarkeit UND Gueltigkeit neu bestimmen.

        Frueher lief nur die Sichtbarkeit. Wurde ein ungueltiges
        Pflichtfeld durch eine andere Eingabe unsichtbar, blieb sein
        Eintrag in ``_valid`` bis zum naechsten Timerlauf auf False und
        sperrte den Weiter-Knopf.
        """
        self._update_visibility()
        self._timer.start()

    # -- Aufbau ---------------------------------------------------------------
    def _build_ui(self) -> None:
        container = QWidget()
        form = QFormLayout(container)
        form.setSpacing(8)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        for spec in self.category.fields:
            widget, browse = self._make_widget(spec)
            message = QLabel("")
            message.setWordWrap(True)
            message.setFont(theme.small_font())
            message.hide()

            cell = QWidget()
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(2)

            if browse is not None:
                row = QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.addWidget(widget, 1)
                row.addWidget(browse, 0)
                cell_layout.addLayout(row)
            else:
                cell_layout.addWidget(widget)

            if spec.help:
                hint = QLabel(spec.help)
                hint.setWordWrap(True)
                hint.setFont(theme.small_font())
                hint.setStyleSheet(f"color: {theme.muted()};")
                cell_layout.addWidget(hint)
            cell_layout.addWidget(message)

            label = QLabel(spec.label + (" *" if spec.required else ""))
            if spec.required:
                label.setToolTip("Pflichtfeld")
            form.addRow(label, cell)
            self._rows[spec.id] = _FieldRow(spec, widget, message, label, cell, browse)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(container)
        self._root.addWidget(scroll, 1)

    def _make_widget(self, spec: FieldSpec) -> tuple[QWidget, QPushButton | None]:
        if spec.widget == "bool":
            widget = QCheckBox()
            widget.toggled.connect(lambda value, s=spec: self._on_changed(s, value))
            return widget, None

        if spec.widget == "int":
            spin = QSpinBox()
            spin.setRange(
                spec.minimum if spec.minimum is not None else 0,
                spec.maximum if spec.maximum is not None else 9999,
            )
            spin.valueChanged.connect(lambda value, s=spec: self._on_changed(s, value))
            return spin, None

        if spec.widget in ("combo", "editable_combo"):
            combo = QComboBox()
            combo.setEditable(spec.widget == "editable_combo")
            if spec.choices:
                for choice in spec.choices:
                    combo.addItem(choice.display, choice.value)
            elif spec.choices_from:
                for value in choice_registry.get_choices(spec.choices_from):
                    combo.addItem(value, value)
            if combo.isEditable():
                combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
                combo.lineEdit().textEdited.connect(
                    lambda text, s=spec: self._on_changed(s, text)
                )
            combo.currentIndexChanged.connect(
                lambda _index, s=spec, c=combo: self._on_changed(s, _combo_value(c))
            )
            return combo, None

        if spec.widget == "textarea":
            area = QTextEdit()
            area.setAcceptRichText(False)
            # Mindest- statt Festhoehe: bei groesserer Systemschrift
            # schnitt die feste Hoehe den Text ab.
            area.setMinimumHeight(80)
            area.setMaximumHeight(200)
            area.textChanged.connect(
                lambda s=spec, a=area: self._on_changed(s, a.toPlainText())
            )
            return area, None

        edit = QLineEdit()
        if spec.placeholder:
            edit.setPlaceholderText(spec.placeholder)
        if spec.secret:
            edit.setEchoMode(QLineEdit.EchoMode.Password)
            # Bei geheimen Feldern bewusst NICHT je Tastendruck: jeder
            # Zwischenstand erzeugte ein eigenes ``Secret`` und meldete sich beim
            # Log-Filter an. Ein Passwort "archiso" hinterliess so die Literale
            # "arc", "arch", "archi", "archis" -- und jedes davon wurde fortan in
            # jeder Logzeile ersetzt, auch mitten im Wort. Aus "Installiere
            # archiso" wurde "Installiere ***".
            #
            # editingFinished feuert beim Verlassen des Feldes und bei Eingabe,
            # also einmal je vollstaendigem Wert. Die Live-Pruefung des Formulars
            # laeuft weiter ueber textEdited, nur ohne den Wert zu speichern.
            edit.editingFinished.connect(
                lambda s=spec, e=edit: self._on_changed(s, e.text())
            )
            edit.textEdited.connect(lambda _text: self._timer.start())
        else:
            if spec.widget == "password":
                edit.setEchoMode(QLineEdit.EchoMode.Password)
            edit.textEdited.connect(lambda text, s=spec: self._on_changed(s, text))

        browse = None
        if spec.widget == "path":
            browse = QPushButton("Durchsuchen ...")
            browse.clicked.connect(lambda _checked=False, s=spec: self._browse(s))
        return edit, browse

    # -- Ereignisse -----------------------------------------------------------
    def _on_changed(self, spec: FieldSpec, value: Any) -> None:
        if spec.secret:
            # Geht ausschliesslich in den SecretStore.
            self.store.set_secret(spec.binding, str(value))
        else:
            self.store.set_field(spec.binding, value)
        self._timer.start()

    # Validatoren, hinter denen ein Verzeichnis steht und keine Datei.
    _VERZEICHNIS_VALIDATOREN = frozenset({"writable_dir"})

    def _browse(self, spec: FieldSpec) -> None:
        """Oeffnet den Dialog, der zum Feld passt.

        Fuer 'Ausgabeverzeichnis' und 'Arbeitsverzeichnis' erschien bisher
        ein Datei-Oeffnen-Dialog; ein Verzeichnis liess sich damit gar
        nicht waehlen, und eine gewaehlte Datei scheiterte anschliessend
        am Validator.
        """
        start = str(self.store.field(spec.binding) or "")
        if spec.validator in self._VERZEICHNIS_VALIDATOREN:
            selected = QFileDialog.getExistingDirectory(self, spec.label, start)
        else:
            selected, _filter = QFileDialog.getOpenFileName(
                self, spec.label, start, spec.file_filter or "Alle Dateien (*)"
            )
        if selected:
            row = self._rows[spec.id]
            if isinstance(row.widget, QLineEdit):
                row.widget.setText(selected)
            self._on_changed(spec, selected)

    # -- Anzeige --------------------------------------------------------------
    def sync_from_store(self) -> None:
        for spec_id, row in self._rows.items():
            spec = row.spec
            if spec.secret:
                # Geheimnisse werden nie zurueckgeschrieben -- aber ein
                # leerer SecretStore muss auch ein leeres Feld bedeuten.
                # Nach dem Laden eines Profils standen sonst weiter Punkte
                # im Feld, waehrend die Meldung "wird benoetigt" erschien.
                if not self.store.has_secret(spec.binding):
                    blocked = row.widget.blockSignals(True)
                    try:
                        if isinstance(row.widget, QLineEdit):
                            row.widget.clear()
                    finally:
                        row.widget.blockSignals(blocked)
                continue
            value = self.store.field(spec.binding, spec.default)
            widget = row.widget
            blocked = widget.blockSignals(True)
            try:
                if isinstance(widget, QCheckBox):
                    widget.setChecked(bool(value))
                elif isinstance(widget, QSpinBox):
                    widget.setValue(int(value or 0))
                elif isinstance(widget, QComboBox):
                    _set_combo_value(widget, value)
                elif isinstance(widget, QTextEdit):
                    widget.setPlainText(str(value or ""))
                elif isinstance(widget, QLineEdit):
                    widget.setText(str(value or ""))
            finally:
                widget.blockSignals(blocked)
        self._update_visibility()
        self._validate_all()

    def _update_visibility(self) -> None:
        context = self.store.context()
        for row in self._rows.values():
            visible = row.spec.visible_when.evaluate(context)
            enabled = visible and row.spec.enabled_when.evaluate(context)
            row.container.setVisible(visible)
            row.label.setVisible(visible)
            row.container.setEnabled(enabled)

    def _add_required_legend(self) -> None:
        """Erklaeren, was der Stern bedeutet.

        Er stand bisher an den Beschriftungen, ohne dass irgendwo erklaert war,
        wofuer -- ein Zeichen, das nur weiss, wer es schon kennt.
        """
        if not any(spec.required for spec in self.category.fields):
            return
        self._root.addWidget(HintLabel("* Pflichtfeld"))

    def _validate_all(self) -> None:
        context = self.store.context()
        for row in self._rows.values():
            spec = row.spec
            active = spec.visible_when.evaluate(context) and spec.enabled_when.evaluate(context)
            if not active:
                self._valid[spec.id] = True
                row.message.hide()
                continue

            if spec.secret:
                # Direkt aus dem Feld lesen, nicht aus dem Store: dorthin
                # wandert der Wert erst bei editingFinished (damit nicht
                # jeder Zwischenstand als Literal beim Log-Filter landet).
                # Die Pruefung sah deshalb waehrend des Tippens immer den
                # alten Wert: die Wiederholung meldete dauerhaft "stimmen
                # nicht ueberein", und der Weiter-Knopf brauchte zwei Klicks.
                text = self._angezeigter_text(row)
            else:
                text = self.store.field(spec.binding, spec.default)

            if spec.required and not str(text or "").strip():
                self._show(row, f"{spec.label} wird benoetigt.", ok=False)
                self._valid[spec.id] = False
                continue

            if spec.confirm_field:
                # Bei einem NICHT geheimen Feld war ``other`` frueher per
                # Konstruktion None und ``second`` damit leer -- jede nichtleere
                # Eingabe meldete dauerhaft "stimmen nicht ueberein". Heute
                # ungenutzt, aber eine Falle fuer den naechsten Katalogeintrag.
                zweitwert = self._value_of(spec.confirm_field, secret=spec.secret)
                first = str(text or "")
                if first and first != zweitwert:
                    ziel = self._rows.get(spec.confirm_field, row)
                    self._show(
                        ziel, "Die beiden Eingaben stimmen nicht ueberein.", ok=False
                    )
                    self._valid[spec.id] = False
                    continue
                # Stimmen sie ueberein, muss die Meldung am Wiederholungsfeld
                # auch wieder verschwinden.
                if spec.confirm_field in self._rows:
                    self._rows[spec.confirm_field].message.hide()

            if spec.validator:
                result = validation.validate(spec.validator, text)
                if not result.ok:
                    self._show(row, result.message, ok=result.is_warning)
                    self._valid[spec.id] = result.is_warning
                    continue

            row.message.hide()
            self._valid[spec.id] = True

        self._publish_field_errors()
        self.completeChanged.emit()

    def _hashing_warning(self) -> Issue | None:
        """Warnen, wenn sich ein Passwort hier gar nicht setzen laesst.

        ``hashing_available()`` gibt es seit jeher, aufgerufen hat es niemand --
        obwohl sein Docstring genau diesen Fall beschreibt. Ohne eine Moeglichkeit
        zu hashen wird das Konto gesperrt angelegt; das Feld anzubieten und
        stillschweigend nichts damit zu tun waere die schlechteste Auskunft.

        Das Feld bleibt trotzdem stehen: es auszublenden wuerde die Frage
        aufwerfen, warum es fehlt.
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

    def _publish_field_errors(self) -> None:
        """Die Feldfehler zusaetzlich nach oben geben.

        Ist der Weiter-Knopf gesperrt, weil ein Pflichtfeld weiter oben leer
        ist, sah man die Begruendung im Scrollbereich womoeglich gar nicht.
        """
        meldungen = []
        for spec_id, gueltig in self._valid.items():
            if gueltig:
                continue
            row = self._rows.get(spec_id)
            if row is None:
                continue
            meldungen.append(
                Issue(
                    severity="error",
                    code="field_invalid",
                    category_id=self.category.id,
                    message=f"{row.spec.label}: {row.message.text()}",
                )
            )
        hinweis = self._hashing_warning()
        if hinweis is not None:
            meldungen.append(hinweis)
        self.set_local_issues(tuple(meldungen))

    def _angezeigter_text(self, row: _FieldRow) -> str:
        """Was gerade im Eingabefeld steht -- unabhaengig vom Store."""
        widget = row.widget
        if isinstance(widget, QLineEdit):
            return widget.text()
        if isinstance(widget, QTextEdit):
            return widget.toPlainText()
        return ""

    def _value_of(self, field_id: str, *, secret: bool) -> str:
        """Der Wert eines anderen Feldes -- aus dem passenden Speicher.

        Geheime Felder liegen im ``SecretStore``, alle anderen in der
        Konfiguration. Vorher wurde nur der erste Fall bedacht.
        """
        if secret:
            # Auch hier der angezeigte Text: der Store hinkt bei geheimen
            # Feldern bis zum Fokuswechsel hinterher.
            row = self._rows.get(field_id)
            if row is not None:
                return self._angezeigter_text(row)
            return ""
        return str(self.store.field(self._binding_of(field_id)) or "")

    def _binding_of(self, field_id: str) -> str:
        spec = self.category.field(field_id)
        return spec.binding if spec else f"{self.category.id}.{field_id}"

    @staticmethod
    def _show(row: _FieldRow, message: str, *, ok: bool) -> None:
        colour = theme.warning() if ok else theme.danger()
        row.message.setText(message)
        row.message.setFont(theme.small_font())
        row.message.setStyleSheet(f"color: {colour};")
        row.message.show()

    def isComplete(self) -> bool:
        return all(self._valid.values()) and super().isComplete()


def _combo_value(combo: QComboBox) -> str:
    data = combo.currentData()
    if data is not None:
        return str(data)
    return combo.currentText()


def _set_combo_value(combo: QComboBox, value: Any) -> None:
    text = "" if value is None else str(value)
    index = combo.findData(text)
    if index < 0:
        index = combo.findText(text)
    if index >= 0:
        combo.setCurrentIndex(index)
    elif combo.isEditable():
        combo.setCurrentText(text)
