"""Tests der Profil-Aktionen.

``ProfileActions`` traegt die Frage, die beim Beenden gestellt wird -- und
damit den Fehler, der auf diesem Zweig am teuersten war: nach dem ersten
Speichern galt jede weitere Aenderung als gesichert, und beim Beenden
verschwand sie ohne Rueckfrage.

Getestet wird ohne Fenster: die Dateidialoge sind ersetzt, und der
Fingerabdruck ist ohnehin reines Python.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("ARCHCUSTOMISER_MOTION", "off")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QWidget


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def keine_modalen_dialoge(monkeypatch):
    """Kein Test darf auf einen Klick warten.

    ``QMessageBox.information`` blockiert bis zum Wegklicken -- ein geladenes
    Profil mit Hinweisen liess den Lauf sonst stehen.
    """
    monkeypatch.setattr(
        QMessageBox, "information", lambda *a, **k: QMessageBox.StandardButton.Ok
    )


@pytest.fixture
def aktionen(qapp, catalog, tmp_path):
    from archcustomiser.core.profiles import ProfileService
    from archcustomiser.gui.actions import ProfileActions
    from archcustomiser.gui.settings import Settings
    from archcustomiser.gui.store import SelectionStore

    fenster = QWidget()
    einstellungen = Settings(
        QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    )
    store = SelectionStore(catalog)
    handlung = ProfileActions(
        catalog, store, ProfileService(catalog), einstellungen, fenster
    )
    yield handlung
    fenster.deleteLater()


# ---------------------------------------------------------------------------
# Der Fingerabdruck
# ---------------------------------------------------------------------------


def test_a_fresh_configuration_has_nothing_to_lose(aktionen) -> None:
    """Wer nur oeffnet und wieder schliesst, soll nicht gefragt werden.

    Verglichen wird gegen den Ausgangszustand, nicht gegen "leer": der Store
    ist beim Start schon mit den Vorgaben des Katalogs gefuellt.
    """
    assert not aktionen.hat_ungesicherte_arbeit()


def test_a_selection_counts_as_unsaved_work(aktionen) -> None:
    aktionen.store.toggle("desktop.kde", True)
    assert aktionen.hat_ungesicherte_arbeit()


def test_a_field_counts_as_unsaved_work(aktionen) -> None:
    aktionen.store.set_field("basics.hostname", "mein-arch")
    assert aktionen.hat_ungesicherte_arbeit()


def test_an_extra_package_counts_as_unsaved_work(aktionen) -> None:
    aktionen.store.set_extra_packages(["neovim"])
    assert aktionen.hat_ungesicherte_arbeit()


def test_a_password_counts_as_unsaved_work(aktionen) -> None:
    aktionen.store.set_secret("user.password", "geheim123")
    assert aktionen.hat_ungesicherte_arbeit()


def test_saving_moves_the_comparison_point(aktionen, tmp_path, monkeypatch) -> None:
    """Der Fehler, um den es hier geht.

    Frueher stand hier ein Merker "einmal gespeichert", der nie zurueckgesetzt
    wurde: alles, was danach geaendert wurde, ging beim Beenden wortlos
    verloren.
    """
    ziel = tmp_path / "meins.yaml"
    monkeypatch.setattr(
        QFileDialog, "getSaveFileName", lambda *a, **k: (str(ziel), "")
    )

    aktionen.store.toggle("desktop.kde", True)
    assert aktionen.hat_ungesicherte_arbeit()

    assert aktionen.speichern()
    assert ziel.is_file()
    assert not aktionen.hat_ungesicherte_arbeit()

    # Und jetzt der eigentliche Punkt: eine Aenderung DANACH zaehlt wieder.
    aktionen.store.toggle("apps.firefox", True)
    assert aktionen.hat_ungesicherte_arbeit()


def test_loading_also_moves_the_comparison_point(aktionen) -> None:
    """Ein frisch geladenes Profil ist gesichert -- es liegt ja als Datei vor."""
    vorlage = Path("src/archcustomiser/profiles/minimal.yaml")
    assert aktionen.lade_datei(vorlage)
    assert not aktionen.hat_ungesicherte_arbeit()


# ---------------------------------------------------------------------------
# Die Frage beim Beenden
# ---------------------------------------------------------------------------


def test_without_unsaved_work_no_question_is_asked(aktionen, monkeypatch) -> None:
    gefragt: list[int] = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *a, **k: gefragt.append(1) or QMessageBox.StandardButton.Discard,
    )
    assert aktionen.darf_beenden()
    assert not gefragt


def test_cancel_in_the_question_prevents_quitting(aktionen, monkeypatch) -> None:
    aktionen.store.toggle("desktop.kde", True)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Cancel
    )
    assert not aktionen.darf_beenden()


def test_discard_allows_quitting(aktionen, monkeypatch) -> None:
    aktionen.store.toggle("desktop.kde", True)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Discard
    )
    assert aktionen.darf_beenden()


def test_save_that_is_cancelled_prevents_quitting(
    aktionen, monkeypatch
) -> None:
    """Wer "Speichern" waehlt und den Dateidialog abbricht, will bleiben."""
    aktionen.store.toggle("desktop.kde", True)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Save
    )
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: ("", ""))
    assert not aktionen.darf_beenden()


def test_save_that_succeeds_allows_quitting(aktionen, tmp_path, monkeypatch) -> None:
    aktionen.store.toggle("desktop.kde", True)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Save
    )
    monkeypatch.setattr(
        QFileDialog,
        "getSaveFileName",
        lambda *a, **k: (str(tmp_path / "beim-beenden.yaml"), ""),
    )
    assert aktionen.darf_beenden()
    assert (tmp_path / "beim-beenden.yaml").is_file()


# ---------------------------------------------------------------------------
# Laden
# ---------------------------------------------------------------------------


def test_a_broken_profile_is_reported_and_changes_nothing(
    aktionen, tmp_path, monkeypatch
) -> None:
    kaputt = tmp_path / "kaputt.yaml"
    kaputt.write_text("schema_version: [nicht, eine, zahl]\n", encoding="utf-8")

    gewarnt: list[int] = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *a, **k: gewarnt.append(1)
    )
    vorher = aktionen.fingerprint()
    assert not aktionen.lade_datei(kaputt)
    assert gewarnt
    assert aktionen.fingerprint() == vorher


def test_loading_remembers_the_folder(aktionen) -> None:
    vorlage = Path("src/archcustomiser/profiles/gaming.yaml")
    aktionen.lade_datei(vorlage)
    assert aktionen.settings.letzter_profilordner == str(vorlage.parent)


def test_the_password_hint_names_the_step_from_the_catalog(catalog) -> None:
    """Der Schrittname stand an zwei Stellen als Literal im Code."""
    from archcustomiser.gui.actions import passwort_hinweis

    text = passwort_hinweis(catalog, ["user.password"])
    kategorie = catalog.category("user")
    assert kategorie is not None
    assert kategorie.title in text


def test_the_password_hint_survives_an_unknown_field(catalog) -> None:
    from archcustomiser.gui.actions import passwort_hinweis

    text = passwort_hinweis(catalog, ["gibt.es.nicht"])
    assert "Passwoerter" in text
