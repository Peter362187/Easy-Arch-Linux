"""Tests der Einrichtung, die beide Startskripte teilen.

Die Abfolge -- Python pruefen, venv anlegen, Abhaengigkeiten installieren,
Importprobe, starten -- stand vorher zweimal getrennt da: einmal in cmd, einmal
in sh. Die beiden liefen auseinander, und keine Zeile davon war getestet.

Hier wird der gemeinsame Kern geprueft. Kein Test legt eine echte
Programmumgebung an: ``venv`` und ``pip`` sind Unterprozesse und werden
aufgezeichnet statt ausgefuehrt.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WURZEL / "tools"))

import bootstrap  # noqa: E402


class FakeLauf:
    """Zeichnet jeden Unterprozessaufruf auf und antwortet nach Vorgabe."""

    def __init__(self, antworten: dict[str, int] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.antworten = antworten or {}

    def __call__(self, argv, **kwargs):
        argv = [str(teil) for teil in argv]
        self.calls.append(argv)
        text = " ".join(argv)
        for schluessel, code in self.antworten.items():
            if schluessel in text:
                return subprocess.CompletedProcess(argv, code, b"", b"")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    def saw(self, *teile: str) -> bool:
        return any(all(t in " ".join(argv) for t in teile) for argv in self.calls)


@pytest.fixture
def lauf(monkeypatch) -> FakeLauf:
    fake = FakeLauf()
    monkeypatch.setattr(bootstrap.subprocess, "run", fake)
    return fake


def _venv_anlegen(wurzel: Path) -> Path:
    """Legt die Dateien an, die eine fertige Umgebung ausmachen."""
    python = bootstrap.venv_python(wurzel)
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("", encoding="utf-8")
    return python


# ---------------------------------------------------------------------------
# Eine defekte Umgebung wird erkannt
# ---------------------------------------------------------------------------


def test_a_broken_environment_is_rebuilt(tmp_path, lauf) -> None:
    """Zeigt pyvenv.cfg auf ein entferntes Python, startet der Interpreter nicht.

    Die frueheren Skripte pruefen nur, ob die Datei existiert, deuteten den
    Fehlschlag als fehlende Abhaengigkeit und rieten zu einer
    Internetverbindung.
    """
    _venv_anlegen(tmp_path)

    def fake(argv, **kwargs):
        argv = [str(teil) for teil in argv]
        lauf.calls.append(argv)
        text = " ".join(argv)
        if "-c pass" in text:
            return subprocess.CompletedProcess(argv, 1, b"", b"")
        if "-m venv" in text:
            _venv_anlegen(tmp_path)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(bootstrap.subprocess, "run", fake)
    try:
        bootstrap.sicherstellen(tmp_path, ausgabe=lambda *_: None)
    finally:
        monkeypatch.undo()

    assert lauf.saw("-m venv"), "die Umgebung wurde nicht neu angelegt"


def test_a_healthy_environment_is_reused(tmp_path, lauf) -> None:
    _venv_anlegen(tmp_path)
    bootstrap.sicherstellen(tmp_path, ausgabe=lambda *_: None)
    assert not lauf.saw("-m venv"), "eine gesunde Umgebung wurde neu angelegt"
    assert not lauf.saw("pip install"), "ohne Not wurde installiert"


def test_a_missing_environment_is_created_and_filled(tmp_path, monkeypatch) -> None:
    aufrufe: list[list[str]] = []

    def fake(argv, **kwargs):
        argv = [str(teil) for teil in argv]
        aufrufe.append(argv)
        text = " ".join(argv)
        if "-m venv" in text:
            # Die Umgebung entsteht -- ab jetzt gilt sie als vorhanden.
            _venv_anlegen(tmp_path)
        if "import archcustomiser" in text and not any(
            "pip" in " ".join(frueher) for frueher in aufrufe
        ):
            return subprocess.CompletedProcess(argv, 1, b"", b"")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(bootstrap.subprocess, "run", fake)
    bootstrap.sicherstellen(tmp_path, ausgabe=lambda *_: None)

    texte = [" ".join(argv) for argv in aufrufe]
    assert any("-m venv" in t for t in texte)
    assert any("pip install" in t for t in texte)


def test_the_user_does_not_get_the_development_extras(tmp_path, monkeypatch) -> None:
    """pytest und pytest-qt gehoeren nicht in eine Benutzerinstallation.

    Beide Startskripte installierten frueher '.[dev]'.
    """
    assert "-e" in bootstrap.PAKETE
    assert not any("[dev]" in teil for teil in bootstrap.PAKETE)


def test_a_failed_installation_says_what_happened(tmp_path, monkeypatch) -> None:
    _venv_anlegen(tmp_path)

    def fake(argv, **kwargs):
        text = " ".join(str(teil) for teil in argv)
        if "pip install" in text:
            return subprocess.CompletedProcess(argv, 1, b"", b"")
        if "import archcustomiser" in text:
            return subprocess.CompletedProcess(argv, 1, b"", b"")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(bootstrap.subprocess, "run", fake)
    with pytest.raises(RuntimeError) as info:
        bootstrap.sicherstellen(tmp_path, ausgabe=lambda *_: None)
    assert "Internetverbindung" in str(info.value)


# ---------------------------------------------------------------------------
# Was die Skripte selbst zusichern
# ---------------------------------------------------------------------------


def test_the_shell_script_survives_an_empty_argument_list() -> None:
    """bash 3.2 -- also /bin/sh auf macOS -- bricht bei "$@" unter set -u ab.

    Das Skript verspricht im Kopf ausdruecklich, dort unveraendert zu laufen;
    der Start ohne Argumente ist der Normalfall.
    """
    text = (WURZEL / "archcustomiser.sh").read_text(encoding="utf-8")
    assert "set -eu" in text
    assert '${1+"$@"}' in text, "der Start ohne Argumente bricht auf macOS ab"


def test_the_batch_file_passes_arguments_through() -> None:
    """--check-env und --dry-run waren unter Windows nicht erreichbar."""
    text = (WURZEL / "ArchCustomiser.bat").read_text(encoding="utf-8")
    assert "%*" in text


def test_the_batch_file_refuses_a_unc_path() -> None:
    """cmd.exe faellt bei einem UNC-Pfad kommentarlos auf C:\\Windows zurueck."""
    text = (WURZEL / "ArchCustomiser.bat").read_text(encoding="utf-8")
    assert "pushd" in text


def test_both_scripts_share_one_bootstrap() -> None:
    """Die Abfolge stand zweimal getrennt da und lief auseinander."""
    for name in ("ArchCustomiser.bat", "archcustomiser.sh"):
        text = (WURZEL / name).read_text(encoding="utf-8")
        assert "bootstrap.py" in text, f"{name} geht nicht ueber den gemeinsamen Kern"


def test_the_shell_script_is_executable_in_git() -> None:
    """Die README erklaerte das fehlende Recht als Verlust beim Herunterladen.

    Tatsaechlich war es nie im Index gesetzt: nach einem frischen Klon
    scheiterte './archcustomiser.sh' mit Permission denied.
    """
    ergebnis = subprocess.run(
        ["git", "ls-files", "-s", "archcustomiser.sh"],
        cwd=str(WURZEL),
        capture_output=True,
        text=True,
        check=False,
    )
    if ergebnis.returncode != 0:
        pytest.skip("kein Git-Repository")
    assert ergebnis.stdout.startswith("100755"), ergebnis.stdout.strip()
