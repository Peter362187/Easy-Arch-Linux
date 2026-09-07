"""Tests des Kommandozeilen-Einstiegs.

Der Einstieg hatte keinen einzigen Test, obwohl er laut Modul-Docstring der
Weg fuer Skripte ist: Rueckgabewerte, die Wahl zwischen Archiv und Verzeichnis
beim Export und die Argumentpruefung waren damit ungeschuetzt.

Kein Test hier laedt Paketdaten. ``--dry-run`` bekommt dafuer ``--offline``;
ohne den Schalter oeffnete ein "Trockenlauf" ungefragt Verbindungen zu einem
Spiegelserver oder startete pacman als Unterprozess.
"""

from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from archcustomiser.__main__ import main


@pytest.fixture
def minimal(profiles_dir: Path) -> Path:
    return profiles_dir / "minimal.yaml"


# ---------------------------------------------------------------------------
# Argumentpruefung
# ---------------------------------------------------------------------------


def test_out_without_export_is_refused(capsys) -> None:
    """--out allein wurde stillschweigend ignoriert und die GUI gestartet."""
    assert main(["--out", "irgendwo", "--no-log-file"]) == 2
    assert "export-profile" in capsys.readouterr().err


def test_export_and_dry_run_together_are_refused(minimal, tmp_path, capsys) -> None:
    """argparse schliesst die Kombination nicht aus; --dry-run entfiel stumm."""
    ergebnis = main(
        [
            "--export-profile",
            str(minimal),
            "--out",
            str(tmp_path / "p.tar.gz"),
            "--dry-run",
            str(minimal),
            "--no-log-file",
        ]
    )
    assert ergebnis == 2
    assert "schliessen sich aus" in capsys.readouterr().err


def test_export_without_out_is_refused(minimal, capsys) -> None:
    assert main(["--export-profile", str(minimal), "--no-log-file"]) == 2
    assert "--out" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Trockenlauf
# ---------------------------------------------------------------------------


def test_dry_run_offline_needs_no_network(minimal, capsys) -> None:
    ergebnis = main(["--dry-run", str(minimal), "--offline", "--no-log-file"])
    ausgabe = capsys.readouterr().out
    assert ergebnis == 0
    assert "Bauplan" in ausgabe
    assert "miniarch-1.0-x86_64.iso" in ausgabe


def test_dry_run_reports_a_broken_profile(tmp_path, capsys) -> None:
    kaputt = tmp_path / "kaputt.yaml"
    kaputt.write_text("schema_version: [1, 2]", encoding="utf-8")
    assert main(["--dry-run", str(kaputt), "--offline", "--no-log-file"]) == 2
    assert "Fehler" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Profilexport
# ---------------------------------------------------------------------------


def test_export_writes_an_archive(minimal, tmp_path, capsys) -> None:
    ziel = tmp_path / "profil.tar.gz"
    ergebnis = main(
        ["--export-profile", str(minimal), "--out", str(ziel), "--no-log-file"]
    )
    assert ergebnis == 0
    assert ziel.is_file()

    with tarfile.open(ziel) as archiv:
        namen = archiv.getnames()
    assert any(name.endswith("profiledef.sh") for name in namen)
    assert "miniarch-1.0-x86_64.iso" in capsys.readouterr().out


def test_export_writes_a_directory(braucht_symlinks, minimal, tmp_path) -> None:
    ziel = tmp_path / "profil"
    ergebnis = main(
        ["--export-profile", str(minimal), "--out", str(ziel), "--no-log-file"]
    )
    assert ergebnis == 0
    assert (ziel / "profiledef.sh").is_file()
    assert (ziel / "airootfs").is_dir()


def test_export_of_a_missing_profile_fails_cleanly(tmp_path, capsys) -> None:
    ergebnis = main(
        [
            "--export-profile",
            str(tmp_path / "gibtsnicht.yaml"),
            "--out",
            str(tmp_path / "p.tar.gz"),
            "--no-log-file",
        ]
    )
    assert ergebnis == 2
    assert "Fehler" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Umgebungspruefung
# ---------------------------------------------------------------------------


def test_check_env_never_raises(capsys) -> None:
    """Der Rueckgabewert haengt vom Rechner ab, die Ausgabe nicht."""
    ergebnis = main(["--check-env", "--no-log-file"])
    assert ergebnis in (0, 1)
    ausgabe = capsys.readouterr().out
    assert "Plattform" in ausgabe
    assert "Build moeglich" in ausgabe
