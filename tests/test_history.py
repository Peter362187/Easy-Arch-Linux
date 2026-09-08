"""Tests der Bauhistorie.

Die Historie ist Zusatznutzen. Sie darf deshalb nie einen Bau gefaehrden -- ein
unbeschreibbares Verzeichnis, eine kaputte Datei oder ein Eintrag aus einer
kuenftigen Fassung muessen folgenlos bleiben.

Der wichtigste Test ist der letzte: in einem Eintrag darf kein Passwort stehen.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from archcustomiser.core import history


@pytest.fixture(autouse=True)
def eigenes_verzeichnis(tmp_path, monkeypatch):
    """Niemals in das echte Zustandsverzeichnis des Benutzers schreiben."""
    monkeypatch.setattr(history, "state_dir", lambda: tmp_path)
    return tmp_path


def bau(name: str = "arch.iso", zeitpunkt: str = "2026-09-08 12:00", **rest) -> history.Bau:
    daten = {
        "iso_name": name,
        "zeitpunkt": zeitpunkt,
        "groesse_bytes": 1311 * 1024 * 1024,
        "dauer_sekunden": 185.0,
        "bauweg": "WSL-Verteilung archlinux",
        "pakete": 412,
        "sha256": "abc",
    }
    daten.update(rest)
    return history.Bau(**daten)


# ---------------------------------------------------------------------------
# Schreiben und Lesen
# ---------------------------------------------------------------------------


def test_a_build_can_be_written_and_read_back() -> None:
    history.merke(bau())
    eintraege = history.lies()
    assert len(eintraege) == 1
    assert eintraege[0].iso_name == "arch.iso"
    assert eintraege[0].pakete == 412


def test_the_newest_entry_comes_first() -> None:
    history.merke(bau("alt.iso", "2026-01-01 08:00"))
    history.merke(bau("neu.iso", "2026-09-08 12:00"))
    assert [e.iso_name for e in history.lies()] == ["neu.iso", "alt.iso"]


def test_an_empty_history_is_an_empty_list() -> None:
    assert history.lies() == []


def test_the_size_is_reported_in_megabytes() -> None:
    assert bau().groesse_mb == 1311


def test_a_line_mentions_name_size_and_duration() -> None:
    zeile = bau().als_zeile()
    assert "arch.iso" in zeile
    assert "1311" in zeile
    assert "3:05" in zeile


def test_a_deleted_image_is_recognisable(tmp_path: Path) -> None:
    """Der Benutzer darf seine ISO loeschen -- die Liste muss das aushalten."""
    vorhanden = tmp_path / "da.iso"
    vorhanden.write_bytes(b"x")
    assert bau(iso_pfad=str(vorhanden)).existiert_noch
    assert not bau(iso_pfad=str(tmp_path / "weg.iso")).existiert_noch
    assert not bau().existiert_noch


# ---------------------------------------------------------------------------
# Robustheit
# ---------------------------------------------------------------------------


def test_a_broken_entry_does_not_break_the_list(eigenes_verzeichnis: Path) -> None:
    history.merke(bau())
    (history.verzeichnis() / "0000-kaputt.json").write_text("{kein json", encoding="utf-8")
    assert len(history.lies()) == 1


def test_an_entry_with_unknown_fields_still_loads() -> None:
    """Ein Eintrag aus einer neueren Fassung darf nicht durchfallen."""
    history.merke(bau())
    datei = next(history.verzeichnis().glob("*.json"))
    daten = json.loads(datei.read_text(encoding="utf-8"))
    daten["kommt_erst_spaeter"] = 42
    datei.write_text(json.dumps(daten), encoding="utf-8")
    assert len(history.lies()) == 1


def test_a_json_list_instead_of_an_object_is_ignored() -> None:
    (history.verzeichnis()).mkdir(parents=True, exist_ok=True)
    (history.verzeichnis() / "0001-liste.json").write_text("[]", encoding="utf-8")
    assert history.lies() == []


def test_an_unwritable_directory_is_not_fatal(monkeypatch) -> None:
    """Eine Historie ist Zusatznutzen, kein Teil des Baus."""

    def kaputt(*args, **kwargs):
        raise OSError("kein Platz")

    monkeypatch.setattr(history, "ensure_dir", kaputt)
    assert history.merke(bau()) is None


def test_old_entries_are_pruned() -> None:
    for nummer in range(history.MAX_EINTRAEGE + 5):
        history.merke(bau(f"iso{nummer:03d}.iso", f"2026-01-{nummer % 28 + 1:02d} {nummer:02d}:00"))
    assert len(list(history.verzeichnis().glob("*.json"))) <= history.MAX_EINTRAEGE


def test_the_history_can_be_cleared() -> None:
    history.merke(bau("a.iso", "2026-01-01 01:00"))
    history.merke(bau("b.iso", "2026-01-02 01:00"))
    assert history.leeren() == 2
    assert history.lies() == []


def test_clearing_an_empty_history_is_harmless() -> None:
    assert history.leeren() == 0


def test_a_strange_iso_name_cannot_escape_the_directory() -> None:
    """Der ISO-Name kommt aus einem Profil -- also aus einer fremden Datei."""
    history.merke(bau("../../boese.iso"))
    dateien = list(history.verzeichnis().glob("*.json"))
    assert len(dateien) == 1
    assert dateien[0].parent == history.verzeichnis()


# ---------------------------------------------------------------------------
# Die Regel des Projekts
# ---------------------------------------------------------------------------


def test_an_entry_contains_no_secret_fields() -> None:
    """Passwoerter verlassen den SecretStore nicht -- auch nicht hierhin."""
    history.merke(bau())
    datei = next(history.verzeichnis().glob("*.json"))
    daten = json.loads(datei.read_text(encoding="utf-8"))
    verboten = {"password", "passwort", "secret", "secrets", "fields", "hash"}
    assert verboten.isdisjoint(daten.keys())
