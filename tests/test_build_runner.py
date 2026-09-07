"""Tests des mkarchiso-Runners gegen ein nachgebildetes Programm.

Bewusst ein echter Prozess und kein Mock: die interessanten Fehler stecken
genau dort, wo ein Mock nichts prueft -- im Puffern der Ausgabe, im Trennen an
Wagenruecklaeufen, im Abbruch eines laufenden Prozesses.

``tests/fake_mkarchiso.py`` gibt aufgezeichnete Ausgabe im echten Format aus.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from archcustomiser.core.build.errors import BuildCancelled, BuildFailed
from archcustomiser.core.build.runner import MkarchisoRunner, _take_complete

FAKE = Path(__file__).parent / "fake_mkarchiso.py"


@pytest.fixture
def dirs(tmp_path: Path):
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "profiledef.sh").write_text("iso_name=flos\n", encoding="utf-8")
    return profile, tmp_path / "work", tmp_path / "out"


def make_runner(dirs, **kwargs) -> MkarchisoRunner:
    profile, work, out = dirs
    runner = MkarchisoRunner(profile, work, out, **kwargs)
    # Statt mkarchiso das nachgebildete Programm -- ueber denselben Weg.
    runner.executable = sys.executable
    original = runner.build_argv

    def argv() -> list[str]:
        return [sys.executable, str(FAKE), *original()[1:]]

    runner.build_argv = argv          # type: ignore[method-assign]
    return runner


# ---------------------------------------------------------------------------
# Pufferung
# ---------------------------------------------------------------------------


def test_take_complete_keeps_the_unfinished_rest() -> None:
    """Eine mitten durchgeschnittene Zeile darf nicht zweimal erscheinen."""
    rest, ready = _take_complete(b"eins\nzwei\rdrei-unvoll")
    assert ready == [b"eins", b"zwei"]
    assert rest == b"drei-unvoll"


def test_take_complete_on_a_clean_boundary() -> None:
    rest, ready = _take_complete(b"eins\nzwei\n")
    assert ready == [b"eins", b"zwei"]
    assert rest == b""


# ---------------------------------------------------------------------------
# Aufrufzusammenbau
# ---------------------------------------------------------------------------


def test_argv_always_contains_verbose(dirs) -> None:
    """Ohne -v gibt mkarchiso keine einzige Fortschrittsmeldung aus."""
    profile, work, out = dirs
    runner = MkarchisoRunner(profile, work, out, executable="/usr/bin/mkarchiso")
    argv = runner.build_argv()
    assert "-v" in argv
    assert argv[-1] == str(profile)


def test_argv_does_not_use_remove_work_dir(dirs) -> None:
    """-r raeumt schon waehrend der ISO-Erzeugung auf. Wir raeumen selbst."""
    profile, work, out = dirs
    argv = MkarchisoRunner(profile, work, out, executable="/usr/bin/mkarchiso").build_argv()
    assert "-r" not in argv


def test_pkexec_wraps_the_call(dirs, monkeypatch) -> None:
    profile, work, out = dirs
    # Dort ersetzen, wo es tatsaechlich aufgerufen wird. Frueher stand hier
    # "...build.runner.shutil.which" -- der Runner importierte shutil zwar,
    # benutzte es aber nie; der Test hing an einem zufaelligen Re-Export und
    # waere beim Aufraeumen der Importe stillschweigend wirkungslos geworden.
    monkeypatch.setattr(
        "archcustomiser.core.build.targets.shutil.which",
        lambda name: f"/usr/bin/{name}",
    )
    runner = MkarchisoRunner(profile, work, out, privilege_mode="pkexec")
    argv = runner.build_argv()
    assert any(item.endswith("pkexec") for item in argv)
    assert any(item.endswith("mkarchiso") for item in argv)

    # Seit der Kerngrenze steht pkexec nicht mehr zwingend an Position 0.
    # Die Reihenfolge ist dabei kein Zufall, sondern eine Entscheidung:
    # taskset steht AUSSEN. Andernfalls saehe polkit nicht mehr mkarchiso als
    # das Programm, das ausgefuehrt werden soll, sondern taskset -- und eine
    # Regel, die nur mkarchiso erlaubt, wuerde stillschweigend brechen. Die
    # Kernbindung uebersteht execve und setuid, sie darf deshalb davor.
    if "taskset" in argv:
        assert argv.index("taskset") < next(
            i for i, item in enumerate(argv) if item.endswith("pkexec")
        ), "taskset gehoert vor pkexec, sonst autorisiert polkit das falsche Programm"


def test_environment_forces_a_predictable_language(dirs) -> None:
    """Sonst haengt das Auswerten der Ausgabe von der Spracheinstellung ab."""
    profile, work, out = dirs
    env = MkarchisoRunner(profile, work, out).environment()
    assert env["LC_ALL"] == "C.UTF-8"


def test_source_date_epoch_is_passed_through(dirs) -> None:
    """Ein wiederverwendetes Arbeitsverzeichnis waere sonst in der Zeit eingefroren."""
    profile, work, out = dirs
    env = MkarchisoRunner(profile, work, out, source_date_epoch=1735689600).environment()
    assert env["SOURCE_DATE_EPOCH"] == "1735689600"


# ---------------------------------------------------------------------------
# Vollstaendiger Lauf
# ---------------------------------------------------------------------------


def test_successful_run(dirs) -> None:
    runner = make_runner(dirs)
    states: list[float] = []
    lines: list[str] = []

    result = runner.run(
        on_line=lines.append,
        on_progress=lambda state: states.append(state.fraction),
        expected_iso="flos-1.0-x86_64.iso",
    )

    assert result.succeeded
    assert result.returncode == 0
    # Der Runner meldet nur, WO die ISO liegt -- in der Schreibweise des
    # Zielrechners. Sie hierher zu holen ist Sache des Controllers.
    assert result.iso_location
    assert Path(result.iso_location).is_file()
    assert Path(result.iso_location).name == "flos-1.0-x86_64.iso"
    assert lines, "es wurde keine Ausgabe gelesen"
    assert states[-1] == 1.0
    assert states == sorted(states), "der Fortschritt ist zurueckgesprungen"


def test_carriage_return_progress_reaches_the_parser(dirs) -> None:
    """Der eigentliche Grund fuer den byteweisen Leser.

    Die Fortschrittszeilen von mksquashfs und xorriso enden mit \\r. Ein
    zeilenweiser Leser wuerde sie erst am Ende in einem Block sehen -- also
    genau dann, wenn der Fortschritt nichts mehr nuetzt.
    """
    runner = make_runner(dirs)
    lines: list[str] = []
    runner.run(on_line=lines.append, expected_iso="flos-1.0-x86_64.iso")

    assert any("50%" in line or "43%" in line for line in lines), "mksquashfs-Fortschritt fehlt"
    assert any("xorriso" in line and "%" in line for line in lines), "xorriso-Fortschritt fehlt"
    # Und sie muessen als einzelne Zeilen ankommen, nicht als ein Klumpen.
    assert all(len(line) < 200 for line in lines)


def test_warnings_are_collected(dirs) -> None:
    runner = make_runner(dirs)
    result = runner.run(expected_iso="flos-1.0-x86_64.iso")
    assert any("Cannot change permissions" in w for w in result.warnings)


def test_failure_reports_the_cause_not_just_the_exit_code(dirs, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_FAIL_AT", "Validating options")
    runner = make_runner(dirs)
    with pytest.raises(BuildFailed) as info:
        runner.run(expected_iso="flos-1.0-x86_64.iso")
    assert "syslinux" in str(info.value), "die eigentliche Ursache fehlt"
    assert info.value.returncode == 1


def test_failure_in_the_middle_names_the_stage(dirs, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_FAIL_AT", "Creating SquashFS image")
    runner = make_runner(dirs)
    with pytest.raises(BuildFailed) as info:
        runner.run(expected_iso="flos-1.0-x86_64.iso")
    assert info.value.stage


def test_success_without_an_iso_is_treated_as_failure(dirs, monkeypatch) -> None:
    """mkarchiso koennte melden, fertig zu sein, ohne etwas erzeugt zu haben."""
    monkeypatch.setenv("FAKE_NO_ISO", "1")
    runner = make_runner(dirs)
    with pytest.raises(BuildFailed) as info:
        runner.run(expected_iso="flos-1.0-x86_64.iso")
    assert "keine ISO-Datei" in str(info.value)


def test_iso_is_found_even_under_another_name(dirs) -> None:
    """Falls mkarchiso den Namen anders zusammensetzt als erwartet."""
    runner = make_runner(dirs)
    os.environ["FAKE_ISO_NAME"] = "anders-2.0-x86_64.iso"
    try:
        result = runner.run(expected_iso="flos-1.0-x86_64.iso")
    finally:
        del os.environ["FAKE_ISO_NAME"]
    assert result.iso_location
    assert Path(result.iso_location).name == "anders-2.0-x86_64.iso"


def test_missing_executable_is_reported_clearly(dirs, monkeypatch) -> None:
    from archcustomiser.core.build.errors import MkarchisoMissing

    profile, work, out = dirs
    monkeypatch.setattr("archcustomiser.core.build.targets.shutil.which", lambda _n: None)
    with pytest.raises(MkarchisoMissing):
        MkarchisoRunner(profile, work, out).build_argv()


# ---------------------------------------------------------------------------
# Abbruch
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_cancel_stops_a_running_build(dirs, monkeypatch) -> None:
    """Ein Build laeuft eine halbe Stunde -- Abbrechen muss zuverlaessig gehen."""
    import threading

    monkeypatch.setenv("FAKE_SLOW", "0.05")
    runner = make_runner(dirs)

    def cancel_soon() -> None:
        time.sleep(0.6)
        runner.cancel()

    threading.Thread(target=cancel_soon, daemon=True).start()

    started = time.monotonic()
    with pytest.raises(BuildCancelled):
        runner.run(expected_iso="flos-1.0-x86_64.iso")
    assert time.monotonic() - started < 20, "der Abbruch hat zu lange gedauert"
    assert runner.cancelled


def test_cancel_before_start_is_harmless(dirs) -> None:
    runner = make_runner(dirs)
    runner.cancel()
    assert runner.cancelled


# ---------------------------------------------------------------------------
# Abbruch -- Befunde der Durchsicht vom 02.09.2026
# ---------------------------------------------------------------------------


def test_cancel_before_the_start_prevents_the_process(dirs) -> None:
    """Ein Abbruch vor dem Start liess den Build frueher trotzdem durchlaufen.

    ``cancel()`` setzte nur das Ereignis; ``run()`` startete mkarchiso, las die
    gesamte Ausgabe und warf erst ganz am Ende ``BuildCancelled``. Bei einem
    echten Build sind das vierzig Minuten Rechenzeit fuer ein Ergebnis, das
    verworfen wird.
    """
    runner = make_runner(dirs)
    gestartet: list[str] = []
    original = runner.build_argv

    def aufzeichnen() -> list[str]:
        gestartet.append("los")
        return original()

    runner.build_argv = aufzeichnen        # type: ignore[method-assign]

    runner.cancel()
    with pytest.raises(BuildCancelled):
        runner.run(expected_iso="flos-1.0-x86_64.iso")

    assert runner._process is None, "es blieb ein Prozess zurueck"


def test_output_is_not_kept_without_limit(dirs) -> None:
    """Kern und Oberflaeche hielten die vollstaendige Ausgabe parallel.

    Die Oberflaeche kappt bei MAX_PENDING_LINES, der Kern kappte gar nicht.
    Bei einem haengenden Werkzeug waechst das unbegrenzt.
    """
    from archcustomiser.core.build.runner import MAX_KEPT_LINES

    runner = make_runner(dirs)
    result = runner.run(expected_iso="flos-1.0-x86_64.iso")
    assert len(result.lines) <= MAX_KEPT_LINES
    assert isinstance(result.lines, list)


def test_the_pipe_is_closed_even_when_a_callback_raises(dirs) -> None:
    """Ein on_line-Rueckruf ist ein Qt-Signal und kann werfen.

    Vorher blieb stdout dann offen und ``wait()`` wartete auf einen Prozess,
    der seinerseits auf Platz in der vollen Pipe wartete.
    """
    runner = make_runner(dirs)

    def platzt(_zeile: str) -> None:
        raise RuntimeError("Rueckruf gescheitert")

    with pytest.raises(RuntimeError):
        runner.run(on_line=platzt, expected_iso="flos-1.0-x86_64.iso")

    assert runner._process is None
