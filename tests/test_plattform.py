"""Zusicherungen zur Plattformunabhaengigkeit.

Jeder Test hier steht fuer eine Stelle, an der das Programm auf einem fremden
System etwas Falsches getan oder gesagt hat. Sie sind bewusst in einer eigenen
Datei gesammelt: sie pruefen keine Funktion, sondern eine Eigenschaft --
"verhaelt sich auf jedem System ehrlich".
"""

from __future__ import annotations

import pytest

from archcustomiser.core.build.preflight import NOT_BUILDABLE_HERE, run_preflight
from archcustomiser.core.environment import Environment, Tool


def _umgebung(*, pacman: bool, plattform: str = "linux") -> Environment:
    werkzeuge = (
        Tool("mkarchiso", "archiso", "x", True, None),
        Tool("pacman", "pacman", "x", True, "/usr/bin/pacman" if pacman else None),
        Tool("pacstrap", "arch-install-scripts", "x", True, None),
    )
    return Environment(
        platform=plattform,
        can_build=False,
        tools=werkzeuge,
        pacman_available=pacman,
        privilege_mode="rootless",
    )


# ---------------------------------------------------------------------------
# Keine unmoeglichen Ratschlaege
# ---------------------------------------------------------------------------


def test_no_pacman_command_where_there_is_no_pacman() -> None:
    """Der teuerste Rat ist einer, den man nicht befolgen kann.

    Auf Ubuntu lautete die Empfehlung nachgestellt woertlich
    ``sudo pacman -S --needed arch-install-scripts archiso libarchive pacman``
    -- ein Befehl, der genau daran scheitert, dass pacman fehlt.
    """
    hinweis = _umgebung(pacman=False).install_hint()
    assert hinweis, "gar keine Auskunft waere auch keine Loesung"
    assert "pacman -S" not in hinweis
    assert "Container" in hinweis or "Arch-System" in hinweis


def test_the_pacman_command_stays_where_it_works() -> None:
    hinweis = _umgebung(pacman=True).install_hint()
    assert hinweis.startswith("sudo pacman -S --needed")


# ---------------------------------------------------------------------------
# Der Ausweg muss ueberall greifen
# ---------------------------------------------------------------------------


def test_a_linux_without_pacman_is_marked_as_not_buildable(tmp_path, monkeypatch) -> None:
    """Vorher blieb der Benutzer vor einem ausgegrauten Knopf stehen.

    Die Beanstandung "Betriebssystem" gab es nur bei einem Nicht-Linux; auf
    Ubuntu ist die Plattform aber "linux", und der Vorschlag "Profil
    stattdessen exportieren" wurde nie ausgeloest.
    """
    monkeypatch.setattr("sys.platform", "linux")
    bericht = run_preflight(
        tmp_path / "w", tmp_path / "o",
        installed_mb=1000, environment=_umgebung(pacman=False),
    )
    assert not bericht.ok
    assert any(pruefung.name == NOT_BUILDABLE_HERE for pruefung in bericht.blocking)


def test_macos_is_marked_the_same_way(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("sys.platform", "darwin")
    bericht = run_preflight(
        tmp_path / "w", tmp_path / "o",
        installed_mb=1000, environment=_umgebung(pacman=False, plattform="darwin"),
    )
    assert any(pruefung.name == NOT_BUILDABLE_HERE for pruefung in bericht.blocking)


def test_the_gui_recognises_that_marker() -> None:
    """Die Oberflaeche hing an einem Zeichenketten-Literal aus dem Kern.

    Frueher verglich sie den Prueftext "Betriebssystem", den es nur bei einem
    Nicht-Linux gab. Auf Ubuntu ist die Plattform aber "linux", der Vergleich
    schlug also nie an -- und der Vorschlag "Profil stattdessen exportieren"
    wurde ausgerechnet dort nie ausgeloest, wo er gebraucht wird.
    """
    from archcustomiser.gui.build_flow import can_build_here

    class FakeCheck:
        def __init__(self, name: str) -> None:
            self.name = name

    class FakeReport:
        def __init__(self, *namen: str) -> None:
            self.blocking = [FakeCheck(n) for n in namen]

    assert not can_build_here(FakeReport(NOT_BUILDABLE_HERE))
    assert can_build_here(FakeReport("Plattenplatz"))


# ---------------------------------------------------------------------------
# Keine Windows-Anweisungen auf fremden Systemen
# ---------------------------------------------------------------------------


def _ohne_systemsonden(monkeypatch) -> None:
    """Ersetzt die drei Sonden durch Antworten ohne Systemzugriff.

    Ohne das startete ``available_targets()`` im Test tatsaechlich ``wsl.exe``
    und ``podman info`` -- mit Laufzeiten von Sekunden und einem Ergebnis, das
    vom Rechner des Entwicklers abhaengt. Geprueft werden soll aber allein,
    *welche Arten* die Zielwahl auf welcher Plattform ueberhaupt anbietet.
    """
    from archcustomiser.core.build import targets

    def sonde(art: str):
        return lambda: targets.TargetOption(art, f"{art} (Attrappe)", problem="Attrappe")

    monkeypatch.setattr(targets, "_probe_local", sonde("lokal"))
    monkeypatch.setattr(targets, "_probe_wsl", sonde("wsl"))
    monkeypatch.setattr(targets, "_probe_container", sonde("container"))


def test_wsl_is_never_offered_outside_windows(monkeypatch) -> None:
    """Auf macOS erschien der WSL-Dialog mit zwei Bildschirmen Windows-Text.

    Geprueft wird das Verhalten, nicht der Quelltext: die Zielwahl darf auf
    einem Nicht-Windows-System gar kein WSL anbieten.
    """
    from archcustomiser.core.build import targets

    _ohne_systemsonden(monkeypatch)
    monkeypatch.setattr("sys.platform", "darwin")
    arten = {option.kind for option in targets.available_targets()}
    assert "wsl" not in arten, "macOS bekam Windows-Anweisungen"


def test_the_container_is_not_offered_on_windows(monkeypatch) -> None:
    """Dort gaebe es keine gueltige Pfadzuordnung -- und WSL ist besser."""
    from archcustomiser.core.build import targets

    _ohne_systemsonden(monkeypatch)
    monkeypatch.setattr("sys.platform", "win32")
    arten = {option.kind for option in targets.available_targets()}
    assert "container" not in arten


def test_a_linux_offers_local_and_container(monkeypatch) -> None:
    """Beides pruefen: ein Arch baut direkt, ein Ubuntu im Container."""
    from archcustomiser.core.build import targets

    _ohne_systemsonden(monkeypatch)
    monkeypatch.setattr("sys.platform", "linux")
    arten = {option.kind for option in targets.available_targets()}
    assert arten == {"lokal", "container"}


def test_usable_targets_come_first(monkeypatch) -> None:
    """Der Wizard nimmt den ersten -- also muss der beste vorne stehen."""
    from archcustomiser.core.build.targets import TargetOption, available_targets

    monkeypatch.setattr(
        "archcustomiser.core.build.targets._probe_local",
        lambda: TargetOption("lokal", "x", problem="geht nicht"),
    )
    monkeypatch.setattr(
        "archcustomiser.core.build.targets._probe_container",
        lambda: TargetOption("container", "y", target=object()),
    )
    monkeypatch.setattr("sys.platform", "linux")
    optionen = available_targets()
    assert optionen[0].usable, "ein brauchbarer Weg stand hinter einem unbrauchbaren"


def test_the_wsl_error_path_does_not_crash() -> None:
    """``WslTarget`` hat kein ``name`` -- der Fehlerpfad warf AttributeError."""
    from archcustomiser.core.build.wsl import WslError, WslResult, WslTarget

    ziel = WslTarget("archlinux")
    ziel.run = lambda *a, **k: WslResult(  # type: ignore[method-assign]
        returncode=1, stdout="", stderr="kaputt"
    )
    with pytest.raises(WslError) as info:
        ziel.home()
    assert "archlinux" in str(info.value)
