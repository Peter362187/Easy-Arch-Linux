"""Einrichtung des Linux-Untersystems.

Erscheint, wenn auf einem Windows-Rechner eine ISO gebaut werden soll. Zeigt in
einfachen Worten, was fehlt, und liefert die noetigen Befehle zum Kopieren.

``wsl --install`` fuehrt das Programm bewusst **nicht** selbst aus: der Befehl
braucht Administratorrechte und einen Neustart des Rechners. Das ist eine
Entscheidung des Benutzers, keine, die ein Programm nebenbei trifft.

``pacman -Syu --needed archiso`` dagegen schon -- auf Wunsch und per Knopf. Es
laeuft innerhalb der bereits eingerichteten Verteilung, braucht keine
Windows-Rechte und keinen Neustart. Den Benutzer dafuer eine Zeile abtippen zu
lassen waere Selbstzweck.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.build import wsl
from ..design import tokens
from ..design.typo import mono
from .common import copy_to_clipboard

log = logging.getLogger(__name__)

# pacman laedt bei einem frischen System einige hundert MB.
INSTALL_TIMEOUT = 900.0


def _entleeren(layout) -> None:
    while layout.count():
        eintrag = layout.takeAt(0)
        widget = eintrag.widget()
        if widget is not None:
            widget.deleteLater()
        elif eintrag.layout() is not None:
            _entleeren(eintrag.layout())


class WslSetupDialog(QDialog):
    """Fuehrt durch die Einrichtung und laesst eine Verteilung auswaehlen."""

    def __init__(self, status: wsl.WslStatus, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.status = status
        self.setWindowTitle("Linux fuer den ISO-Bau")
        self.setMinimumWidth(720)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        headline = QLabel("Zum Bauen einer Arch-ISO wird Linux gebraucht")
        font = headline.font()
        font.setBold(True)
        font.setPointSize(font.pointSize() + 2)
        headline.setFont(font)
        layout.addWidget(headline)

        explanation = QLabel(
            "Eine Arch-ISO wird von den Programmen <b>archiso</b> und <b>pacman</b> "
            "zusammengebaut, und die gibt es nur für Linux. Windows kann das nicht "
            "leisten.<br><br>"
            "Das Windows-Subsystem für Linux (WSL) löst das: ein Linux "
            "<i>innerhalb</i> von Windows. Kein zweiter Rechner, keine zweite "
            "Festplattenpartition, kein Neustart in ein anderes System — und "
            "jederzeit wieder entfernbar."
        )
        explanation.setWordWrap(True)
        explanation.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(explanation)

        self._steps_widget = QWidget()
        self._steps = QVBoxLayout(self._steps_widget)
        self._steps.setContentsMargins(0, 0, 0, 0)
        self._steps.setSpacing(8)
        layout.addWidget(self._steps_widget)

        self.chooser = QComboBox()
        self.chooser_label = QLabel("Verteilung:")
        chooser_row = QHBoxLayout()
        chooser_row.addWidget(self.chooser_label)
        chooser_row.addWidget(self.chooser, 1)
        self._chooser_row = chooser_row
        layout.addLayout(chooser_row)

        buttons = QDialogButtonBox()
        self.accept_button = buttons.addButton(
            "Weiter", QDialogButtonBox.ButtonRole.AcceptRole
        )
        # Ohne diesen Knopf musste der Benutzer den Dialog schliessen und
        # "ISO erstellen" erneut druecken, nachdem er die Schritte erledigt
        # hatte -- die Pruefung lief nur einmal, im Konstruktor.
        self.recheck_button = buttons.addButton(
            "Erneut pruefen", QDialogButtonBox.ButtonRole.ResetRole
        )
        self.recheck_button.clicked.connect(self._recheck)
        self.install_button = buttons.addButton(
            "archiso jetzt installieren", QDialogButtonBox.ButtonRole.ActionRole
        )
        self.install_button.clicked.connect(self._install_archiso)
        self.install_button.hide()
        self.export_button = buttons.addButton(
            "Stattdessen Profil exportieren", QDialogButtonBox.ButtonRole.ActionRole
        )
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel).setText("Abbrechen")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.export_button.clicked.connect(self._choose_export)
        layout.addWidget(buttons)

        self._export_requested = False
        self._populate()

    # -- Inhalt ---------------------------------------------------------------
    def _clear_steps(self) -> None:
        """Leert die Schrittliste, damit _populate wiederholbar ist."""
        while self._steps.count():
            eintrag = self._steps.takeAt(0)
            widget = eintrag.widget()
            if widget is not None:
                widget.deleteLater()
            elif eintrag.layout() is not None:
                _entleeren(eintrag.layout())

    def _recheck(self) -> None:
        """Noch einmal nachsehen, nachdem der Benutzer etwas erledigt hat."""
        from .wait_dialog import run_with_wait

        status, fehler = run_with_wait(
            wsl.detect,
            "Linux-Untersystem wird geprueft ...",
            parent=self,
        )
        if fehler is not None:
            QMessageBox.warning(
                self, "Pruefung fehlgeschlagen", str(fehler)
            )
            return
        if status is None:
            return
        self.status = status
        self._clear_steps()
        self.chooser.clear()
        self.install_button.hide()
        self.accept_button.setEnabled(True)
        self._populate()

    def _install_archiso(self) -> None:
        """Installiert archiso in der gewaehlten Verteilung.

        Laeuft als root *innerhalb* von WSL -- keine Windows-Adminrechte, kein
        Neustart. Der Vorgang laedt einige hundert MB und dauert entsprechend,
        deshalb der Wartedialog.
        """
        from .wait_dialog import run_with_wait

        ziel = self._first_distribution()
        if not ziel:
            return

        ergebnis, fehler = run_with_wait(
            lambda: wsl.WslTarget(ziel).run(
                ["pacman", "-Syu", "--needed", "--noconfirm", "archiso"],
                as_root=True,
                timeout=INSTALL_TIMEOUT,
            ),
            f"archiso wird in {ziel} installiert ...\n\n"
            "Es werden einige hundert MB geladen; das dauert ein paar "
            "Minuten.",
            parent=self,
            cancellable=False,
        )
        if fehler is not None:
            QMessageBox.warning(self, "Installation fehlgeschlagen", str(fehler))
            return
        if ergebnis is None:
            return
        if not ergebnis.ok:
            QMessageBox.warning(
                self,
                "Installation fehlgeschlagen",
                "pacman meldet einen Fehler. Die Schritte lassen sich auch von "
                "Hand ausfuehren -- der Befehl steht oben zum Kopieren bereit."
                + ("\n\n" + ergebnis.stderr.strip()[:600]
                   if ergebnis.stderr else ""),
            )
            return
        self._recheck()

    def _first_distribution(self) -> str:
        """Die Verteilung, in der installiert werden soll."""
        if self.chooser.count():
            return str(self.chooser.currentData() or "")
        arch = self.status.arch_distributions
        if arch:
            return arch[0].name
        return self.status.distributions[0].name if self.status.distributions else ""

    def _populate(self) -> None:
        arch = self.status.arch_distributions

        if not self.status.installed:
            self._add_step(
                1,
                "WSL einrichten",
                "In der <b>PowerShell als Administrator</b> ausführen. Danach "
                "startet Windows einmal neu.",
                "wsl --install archlinux",
            )
            self._add_step(
                2,
                "archiso installieren",
                "Nach dem Neustart öffnet sich Arch Linux und fragt nach Benutzername "
                "und Passwort. Dann dort eingeben:",
                "sudo pacman -Syu --needed archiso",
            )
            self._add_step(
                3,
                "Zurück hierher",
                "Danach dieses Fenster erneut öffnen — der ISO-Bau läuft dann "
                "vollständig automatisch.",
                "",
            )
            self._set_chooser_visible(False)
            self.accept_button.setEnabled(False)
            self.accept_button.setText("Weiter")
            return

        if not arch:
            others = ", ".join(d.name for d in self.status.distributions) or "keine"
            self._add_step(
                1,
                "Arch Linux hinzufügen",
                f"WSL ist eingerichtet, aber es fehlt Arch Linux "
                f"(vorhanden: {others}). In der PowerShell:",
                "wsl --install archlinux",
            )
            self._add_step(
                2,
                "archiso installieren",
                "Anschließend in Arch:",
                "sudo pacman -Syu --needed archiso",
            )
            self._set_chooser_visible(False)
            self.accept_button.setEnabled(False)
            return

        # Alles da -- nur noch auswaehlen.
        self._add_step(
            0,
            "Bereit",
            "Arch Linux ist in WSL vorhanden. Der Bau läuft dort und die fertige "
            "ISO landet anschließend wieder in deinem Windows-Ordner.",
            "",
        )
        for distribution in arch:
            marker = "  (Standard)" if distribution.default else ""
            self.chooser.addItem(f"{distribution.name}{marker}", distribution.name)
        self._set_chooser_visible(len(arch) > 1)
        self.accept_button.setText("ISO erstellen")
        self.accept_button.setDefault(True)
        # Ob archiso darin schon liegt, weiss erst die Vorabpruefung -- dafuer
        # muesste die Verteilung starten, was hier Sekunden kosten wuerde. Der
        # Knopf steht deshalb bereit, statt danach zu fragen. Ein erneuter Lauf
        # mit --needed ist ohnehin folgenlos, wenn das Paket schon da ist.
        self.install_button.setText("archiso installieren oder aktualisieren")
        self.install_button.show()

    def _add_step(self, number: int, title: str, text: str, command: str) -> None:
        heading = QLabel(f"<b>{f'{number}. ' if number else ''}{title}</b>")
        heading.setTextFormat(Qt.TextFormat.RichText)
        self._steps.addWidget(heading)

        body = QLabel(text)
        body.setWordWrap(True)
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setProperty("rolle", "gedaempft")
        self._steps.addWidget(body)

        if command:
            row = QHBoxLayout()
            field = QPlainTextEdit(command)
            field.setReadOnly(True)
            field.setFont(mono())
            # Eine Zeile Monospace -- gemessen statt geraten: bei 125 %
            # Schriftskalierung brach die feste Hoehe von 34 px um.
            field.setMinimumHeight(
                field.fontMetrics().lineSpacing() + tokens().space.lg
            )
            field.setMaximumHeight(
                field.fontMetrics().lineSpacing() * 3 + tokens().space.lg
            )
            row.addWidget(field, 1)

            copy = QPushButton("Kopieren")
            copy.clicked.connect(lambda _c=False, value=command: self._copy(value))
            row.addWidget(copy)
            self._steps.addLayout(row)

    def _set_chooser_visible(self, visible: bool) -> None:
        self.chooser.setVisible(visible)
        self.chooser_label.setVisible(visible)

    # -- Ergebnis -------------------------------------------------------------
    @property
    def distribution(self) -> str:
        data = self.chooser.currentData()
        if data:
            return str(data)
        preferred = self.status.preferred
        return preferred.name if preferred else ""

    @property
    def export_requested(self) -> bool:
        return self._export_requested

    def _choose_export(self) -> None:
        self._export_requested = True
        self.accept()

    def _copy(self, value: str) -> None:

        copy_to_clipboard(value)
