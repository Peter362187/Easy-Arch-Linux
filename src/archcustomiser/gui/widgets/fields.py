"""Aus einer ``FieldSpec`` wird ein Eingabefeld.

Ein neues Feld ist ein YAML-Eintrag, kein Python-Code -- das gilt weiterhin.
Hier steht nur, wie aus den acht Widget-Arten des Katalogs ein bedienbares
Formular wird.

Zwei Dinge, die in der alten Fassung falsch waren und hier richtig sind:

* **Verzeichnisfelder bekommen einen Verzeichnisdialog.** Fuer Ausgabe- und
  Arbeitsverzeichnis erschien ein Datei-Oeffnen-Dialog; ein Verzeichnis liess
  sich damit gar nicht waehlen.
* **Passwortfelder haben einen Anzeigen-Schalter.** Wer sein Passwort nicht
  sehen kann, tippt es zweimal falsch -- und die Wiederholung sagt ihm nur,
  dass etwas nicht stimmt, nicht was.

Unveraendert bleibt die Geheimnis-Regel: der Wert eines ``secret``-Feldes
wandert erst bei ``editingFinished`` in den SecretStore. Bei jedem Tastendruck
zu speichern erzeugte fuer jeden Zwischenstand ein eigenes ``Secret``, das sich
beim Log-Filter anmeldete -- aus "Installiere archiso" wurde dann "Installiere
***".
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...core import choices as choice_registry
from ...core.catalog import FieldSpec
from ..design import tokens
from ..design.typo import BODY, CAPTION, schrift
from .common import setze_rolle

log = logging.getLogger(__name__)

# Validatoren, hinter denen ein Verzeichnis steht und keine Datei.
VERZEICHNIS_VALIDATOREN = frozenset({"writable_dir"})


class FieldRow(QWidget):
    """Ein Feld mit Beschriftung, Hilfetext und Meldungszeile."""

    def __init__(self, spec: FieldSpec, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.spec = spec
        werte = tokens()

        aussen = QVBoxLayout(self)
        aussen.setContentsMargins(0, 0, 0, 0)
        aussen.setSpacing(werte.space.xs)

        self.label = QLabel(spec.label + (" *" if spec.required else ""))
        self.label.setFont(schrift(BODY, fett=False))
        if spec.required:
            self.label.setToolTip("Pflichtfeld")
        aussen.addWidget(self.label)

        self.widget, self.browse = _erzeuge_widget(spec)
        if self.browse is not None:
            zeile = QHBoxLayout()
            zeile.setContentsMargins(0, 0, 0, 0)
            zeile.setSpacing(werte.space.sm)
            zeile.addWidget(self.widget, 1)
            zeile.addWidget(self.browse, 0)
            aussen.addLayout(zeile)
        else:
            aussen.addWidget(self.widget)

        self.hilfe: QLabel | None = None
        if spec.help:
            self.hilfe = QLabel(spec.help)
            self.hilfe.setWordWrap(True)
            self.hilfe.setFont(schrift(CAPTION))
            self.hilfe.setProperty("rolle", "gedaempft")
            aussen.addWidget(self.hilfe)

        self.meldung = QLabel("")
        self.meldung.setWordWrap(True)
        self.meldung.setFont(schrift(CAPTION))
        self.meldung.hide()
        aussen.addWidget(self.meldung)

        # Der Stern erreicht Vorlesewerkzeuge sonst nicht: er steht an der
        # Beschriftung, und die ist mit dem Eingabefeld nicht verknuepft.
        self.widget.setAccessibleName(
            spec.label + (" (Pflichtfeld)" if spec.required else "")
        )
        teile = [t for t in ("Pflichtfeld" if spec.required else "", spec.help) if t]
        if teile:
            self.widget.setAccessibleDescription(". ".join(teile))
        if spec.required:
            self.widget.setToolTip("Pflichtfeld")

    # -- Anzeige --------------------------------------------------------------
    def zeige_meldung(self, text: str, *, warnung: bool) -> None:
        self.meldung.setText(text)
        setze_rolle(self.meldung, "warnung" if warnung else "fehler")
        self.meldung.show()

    def verstecke_meldung(self) -> None:
        self.meldung.hide()

    def setze_sichtbar(self, sichtbar: bool) -> None:
        self.setVisible(sichtbar)

    def angezeigter_text(self) -> str:
        """Was gerade im Feld steht -- unabhaengig vom Store."""
        if isinstance(self.widget, QLineEdit):
            return self.widget.text()
        if isinstance(self.widget, QTextEdit):
            return self.widget.toPlainText()
        if isinstance(self.widget, QComboBox):
            return _combo_wert(self.widget)
        return ""

    def setze_wert(self, wert: Any) -> None:
        """Uebernimmt einen Wert aus dem Store, ohne ein Signal auszuloesen."""
        blockiert = self.widget.blockSignals(True)
        try:
            if isinstance(self.widget, QCheckBox):
                self.widget.setChecked(bool(wert))
            elif isinstance(self.widget, QSpinBox):
                self.widget.setValue(_als_zahl(wert))
            elif isinstance(self.widget, QComboBox):
                _setze_combo(self.widget, wert)
            elif isinstance(self.widget, QTextEdit):
                self.widget.setPlainText(str(wert or ""))
            elif isinstance(self.widget, QLineEdit):
                self.widget.setText(str(wert or ""))
        finally:
            self.widget.blockSignals(blockiert)

    def leeren(self) -> None:
        blockiert = self.widget.blockSignals(True)
        try:
            if isinstance(self.widget, QLineEdit):
                self.widget.clear()
                # Auch den Anzeigen-Schalter zuruecksetzen: nach dem
                # Laden eines Profils blieb sonst ein Feld offen stehen,
                # in das gleich wieder ein Passwort getippt wird.
                for aktion in self.widget.actions():
                    if aktion.isCheckable() and aktion.isChecked():
                        aktion.setChecked(False)
        finally:
            self.widget.blockSignals(blockiert)


def _erzeuge_widget(spec: FieldSpec) -> tuple[QWidget, QPushButton | None]:
    """Das Eingabewidget zur Feldbeschreibung."""
    if spec.widget == "bool":
        return QCheckBox(), None

    if spec.widget == "int":
        spin = QSpinBox()
        spin.setRange(
            spec.minimum if isinstance(spec.minimum, int) else 0,
            spec.maximum if isinstance(spec.maximum, int) else 9999,
        )
        return spin, None

    if spec.widget in ("combo", "editable_combo"):
        combo = QComboBox()
        combo.setEditable(spec.widget == "editable_combo")
        if spec.choices:
            for wahl in spec.choices:
                combo.addItem(wahl.display, wahl.value)
        elif spec.choices_from:
            for wert in choice_registry.get_choices(spec.choices_from):
                combo.addItem(wert, wert)
        if combo.isEditable():
            combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        return combo, None

    if spec.widget == "textarea":
        bereich = QTextEdit()
        bereich.setAcceptRichText(False)
        # Mindest- statt Festhoehe: bei groesserer Systemschrift schnitt die
        # feste Hoehe den Text ab.
        bereich.setMinimumHeight(80)
        bereich.setMaximumHeight(200)
        return bereich, None

    edit = QLineEdit()
    if spec.placeholder:
        edit.setPlaceholderText(spec.placeholder)
    if spec.secret or spec.widget == "password":
        edit.setEchoMode(QLineEdit.EchoMode.Password)
        _anzeigeschalter(edit)

    browse = None
    if spec.widget == "path":
        browse = QPushButton("Durchsuchen ...")
        browse.setProperty("variant", "ghost")
    return edit, browse


def _anzeigeschalter(edit: QLineEdit) -> None:
    """Ein Auge im Feld, mit dem sich das Passwort kurz zeigen laesst.

    ``QLineEdit`` zeichnet fuer eine Aktion **nur** deren Symbol -- der Text
    einer QAction erscheint dort nie. Ohne Symbol war der Schalter also eine
    leere, unsichtbare Flaeche am rechten Feldrand, die niemand findet.
    """
    from PySide6.QtGui import QAction

    from .icons import load_icon

    aktion = QAction(edit)
    aktion.setText("Anzeigen")
    aktion.setToolTip("Passwort anzeigen")
    aktion.setCheckable(True)
    symbol = load_icon("eye", groesse=16)
    if symbol is not None:
        # ``setIcon(None)`` wirft -- ein fehlendes Symbol darf die Seite
        # nicht mitreissen.
        aktion.setIcon(symbol)

    def umschalten(sichtbar: bool) -> None:
        edit.setEchoMode(
            QLineEdit.EchoMode.Normal if sichtbar else QLineEdit.EchoMode.Password
        )
        aktion.setToolTip("Passwort verbergen" if sichtbar else "Passwort anzeigen")

    aktion.toggled.connect(umschalten)
    edit.addAction(aktion, QLineEdit.ActionPosition.TrailingPosition)


def waehle_pfad(spec: FieldSpec, start: str, eltern: QWidget) -> str:
    """Oeffnet den Dialog, der zum Feld passt."""
    if spec.validator in VERZEICHNIS_VALIDATOREN:
        return QFileDialog.getExistingDirectory(eltern, spec.label, start)
    gewaehlt, _filter = QFileDialog.getOpenFileName(
        eltern, spec.label, start, spec.file_filter or "Alle Dateien (*)"
    )
    return gewaehlt


def _als_zahl(wert: Any) -> int:
    """Ein Profil kann fuer ein int-Feld etwas anderes enthalten."""
    try:
        return int(wert or 0)
    except (TypeError, ValueError):
        log.debug("Kein Zahlenwert fuer ein int-Feld: %r", wert)
        return 0


def _combo_wert(combo: QComboBox) -> str:
    daten = combo.currentData()
    return str(daten) if daten is not None else combo.currentText()


def _setze_combo(combo: QComboBox, wert: Any) -> None:
    text = "" if wert is None else str(wert)
    index = combo.findData(text)
    if index < 0:
        index = combo.findText(text)
    if index >= 0:
        combo.setCurrentIndex(index)
    elif combo.isEditable():
        combo.setCurrentText(text)


__all__ = ["VERZEICHNIS_VALIDATOREN", "FieldRow", "waehle_pfad"]
