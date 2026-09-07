"""Tests des Zielprotokolls.

Der Controller fragte frueher an drei Stellen per ``isinstance`` nach dem
Zieltyp. Bei zwei Zielen waren das drei Sonderfaelle, bei einem dritten waeren
es neun geworden. Seit die zielabhaengigen Schritte im Protokoll stehen, laesst
sich der ganze Ablauf gegen ein nachgebildetes Ziel pruefen -- und genau das
war vorher unmoeglich.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from archcustomiser.core.build.targets import BuildPaths, LocalTarget

sys.path.insert(0, str(Path(__file__).parent))
from fake_target import FakeTarget   # noqa: E402


# ---------------------------------------------------------------------------
# Der Controller darf den Zieltyp nicht mehr kennen
# ---------------------------------------------------------------------------


def test_the_controller_no_longer_asks_for_the_target_type() -> None:
    """Die Zusicherung, um die es beim Umbau ging.

    Als Test formuliert, damit ein spaeteres viertes Ziel nicht wieder mit
    einer isinstance-Abfrage nachgeruestet wird.
    """
    # Ueber das Modul selbst, nicht ueber einen relativen Pfad: sonst haengt
    # der Test am Arbeitsverzeichnis des pytest-Aufrufs und faellt anderswo
    # mit FileNotFoundError aus einem Grund, der nichts mit der Zusicherung
    # zu tun hat.
    import inspect

    from archcustomiser.core.build import controller as controller_modul

    quelle = inspect.getsource(controller_modul)
    assert "isinstance(self.target" not in quelle
    assert "self.target.wsl" not in quelle, "auch der Durchgriff muss weg sein"


def test_every_target_answers_the_whole_protocol() -> None:
    """Ein Ziel, dem eine Methode fehlt, faellt sonst erst zur Laufzeit auf."""
    from archcustomiser.core.build.targets import ExecutionTarget

    noetig = [
        name
        for name in dir(ExecutionTarget)
        if not name.startswith("_") and callable(getattr(ExecutionTarget, name, None))
    ]
    for ziel in (LocalTarget(), FakeTarget()):
        fehlend = [name for name in noetig if not hasattr(ziel, name)]
        assert not fehlend, f"{type(ziel).__name__} fehlt: {fehlend}"


# ---------------------------------------------------------------------------
# Der Ablauf loest die richtigen Schritte am Ziel aus
# ---------------------------------------------------------------------------


@pytest.fixture
def controller_mit_ziel(catalog, resolver, tmp_path):
    """Ein Controller, dessen Ziel jeden Aufruf mitschreibt."""
    from archcustomiser.core.build.controller import BuildController
    from archcustomiser.core.build.runner import BuildResult
    from archcustomiser.core.config import BuildConfig
    from archcustomiser.core.secrets import SecretStore

    config = BuildConfig(catalog_version=catalog.catalog_version)
    for kategorie in catalog.categories:
        if kategorie.default_selection:
            config.set_selection(kategorie.id, kategorie.default_selection)
        for spec in kategorie.fields:
            if spec.default is not None and not spec.secret:
                config.set_field(spec.binding, spec.default)

    ziel = FakeTarget(iso="/fake/x/out/test.iso")

    class FakeRunner:
        def __init__(self, *args, **kwargs) -> None:
            self.target = kwargs.get("target")

        def run(self, **kwargs):
            return BuildResult(
                returncode=0,
                iso_path=None,
                duration_seconds=1.0,
                iso_location="/fake/x/out/test.iso",
            )

        def cancel(self) -> None:
            pass

    controller = BuildController(
        catalog,
        config,
        resolver.resolve(config),
        SecretStore(),
        runner_factory=FakeRunner,
        target=ziel,
    )
    return controller, ziel, tmp_path


def test_the_build_walks_through_the_target(controller_mit_ziel) -> None:
    controller, ziel, tmp_path = controller_mit_ziel
    controller.run(tmp_path / "work", tmp_path / "out", skip_preflight=True)

    reihenfolge = ziel.order()
    for schritt in ("prepare", "deliver_profile", "fetch_iso", "discard"):
        assert schritt in reihenfolge, f"{schritt} wurde nie ausgeloest"
    assert reihenfolge.index("prepare") < reihenfolge.index("deliver_profile")
    assert reihenfolge.index("deliver_profile") < reihenfolge.index("discard")


def test_the_bootmodes_reach_the_preflight(controller_mit_ziel) -> None:
    """Die WSL-Fassung kannte die Bootmodi frueher gar nicht.

    Ein Bau mit uefi.grub ohne grub-mkstandalone lief deshalb an, statt vorher
    zu blockieren.
    """
    controller, ziel, tmp_path = controller_mit_ziel
    controller.preflight(tmp_path / "work", tmp_path / "out")
    _args, kwargs = ziel.call("preflight")
    assert kwargs["bootmodes"], "ohne Bootmodi kann die Pruefung sie nicht beachten"


def test_cleanup_happens_even_when_the_build_is_cancelled(controller_mit_ziel) -> None:
    """Frueher wurde Schritt 5 bei einem Abbruch uebersprungen.

    Ausgerechnet in dem Fall also, in dem am meisten liegenbleibt: ein halbes
    Arbeitsverzeichnis von mehreren Gigabyte.
    """
    from archcustomiser.core.build.errors import BuildCancelled

    controller, ziel, tmp_path = controller_mit_ziel

    class AbbrechenderRunner:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def run(self, **kwargs):
            raise BuildCancelled()

        def cancel(self) -> None:
            pass

    controller.runner_factory = AbbrechenderRunner
    with pytest.raises(BuildCancelled):
        controller.run(tmp_path / "work", tmp_path / "out", skip_preflight=True)

    assert ziel.called("discard"), "nach dem Abbruch wurde nicht aufgeraeumt"
    _args, kwargs = ziel.call("discard")
    assert kwargs["remove_output"] is False, "ohne geholte ISO darf sie nicht weg"


# ---------------------------------------------------------------------------
# Abbruch erreicht das Ziel
# ---------------------------------------------------------------------------


def test_cancel_goes_through_the_target() -> None:
    """``runner.cancel()`` beendete frueher den lokalen Prozess -- immer.

    Bei WSL war das wirkungslos: wsl.exe leitet keine Signale weiter, mkarchiso
    lief drueben ungestoert weiter, waehrend die Oberflaeche "abgebrochen"
    meldete.
    """
    from archcustomiser.core.build.runner import MkarchisoRunner

    ziel = FakeTarget()
    runner = MkarchisoRunner("/p", "/w", "/o", target=ziel)
    runner.cancel()
    assert ziel.called("cancel_run")


def test_the_local_target_still_terminates_its_process() -> None:
    """Lokal bleibt es beim bewaehrten Weg: erst freundlich, dann hart."""
    gerufen: list[str] = []

    class FakeProcess:
        def __init__(self) -> None:
            self._tot = False

        def poll(self):
            return 0 if self._tot else None

        def terminate(self) -> None:
            gerufen.append("terminate")
            self._tot = True

        def wait(self, timeout=None) -> int:
            gerufen.append("wait")
            return 0

        def kill(self) -> None:
            gerufen.append("kill")

    LocalTarget().cancel_run(FakeProcess(), grace_seconds=1.0)
    assert gerufen[0] == "terminate"
    assert "kill" not in gerufen, "ein reagierender Prozess wird nicht hart beendet"


def test_a_stubborn_process_is_killed() -> None:
    class Sturkopf:
        getoetet = False

        def poll(self):
            return None

        def terminate(self) -> None:
            pass

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("mkarchiso", timeout or 0)

        def kill(self) -> None:
            self.getoetet = True

    prozess = Sturkopf()
    LocalTarget().cancel_run(prozess, grace_seconds=0.01)
    assert prozess.getoetet


def test_the_wsl_target_kills_the_build_over_there() -> None:
    """Der eigentliche Befund: den Client zu beenden reicht nicht."""
    from archcustomiser.core.build.targets import WslExecutionTarget

    aufrufe: list[list[str]] = []

    class FakeWslTarget:
        distribution = "archlinux"

        def run(self, argv, **kwargs):
            aufrufe.append(list(argv))

            class Ergebnis:
                ok = True
                returncode = 0
                stdout = ""
                stderr = ""

            return Ergebnis()

    ziel = WslExecutionTarget(FakeWslTarget())
    ziel.cancel_run(None, grace_seconds=0.01)

    assert aufrufe, "in der Verteilung wurde gar nichts unternommen"
    assert any("pkill" in argv for argv in aufrufe)
    assert any("mkarchiso" in argv for argv in aufrufe)


# ---------------------------------------------------------------------------
# BuildPaths
# ---------------------------------------------------------------------------


def test_build_paths_stay_strings() -> None:
    """Sie duerfen nicht durch pathlib laufen.

    Bei einem Bau in WSL oder im Container sind das Linux-Pfade; unter Windows
    machte ``Path`` daraus einen Pfad mit Rueckwaertsschraegstrichen -- und die
    fertige ISO waere unauffindbar. Genau dieser Fehler ist schon einmal
    passiert.
    """
    paths = BuildPaths(profile="/home/x/profil", work="/home/x/work", out="/home/x/out")
    assert all(isinstance(wert, str) for wert in paths.as_tuple())
    assert paths.as_tuple() == ("/home/x/profil", "/home/x/work", "/home/x/out")
