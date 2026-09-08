"""Tests des Baus ohne Oberflaeche.

Es wird nie wirklich gebaut: der Controller ist eine Attrappe. Geprueft wird
der Rahmen darum -- Rueckgabewerte, Zielwahl, Passwortweg, Abbruch, und dass
der Fortschritt nach stderr geht und nur das Ergebnis nach stdout.

Der letzte Punkt ist kein Schoenheitsfehler: ``--build p.yaml > ergebnis.txt``
soll die Zusammenfassung enthalten und nicht dreitausend mkarchiso-Zeilen.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from archcustomiser import cli_build
from archcustomiser.core.build.errors import BuildCancelled, BuildFailed

from .test_verify import baue_iso

PROFIL = Path("src/archcustomiser/profiles/minimal.yaml")


class FakeTarget:
    kind = "lokal"
    label = "Dieser Rechner (Arch Linux)"
    target = None
    problem = ""
    remedy = ""
    usable = True


class FakeOption(FakeTarget):
    def __init__(self, kind="lokal", usable=True, label="", problem="", remedy=""):
        self.kind = kind
        self.label = label or f"Bauweg {kind}"
        self.target = object() if usable else None
        self.problem = problem
        self.remedy = remedy

    @property
    def usable(self) -> bool:
        return self.target is not None


class FakeCheck:
    def __init__(self, name: str, ok: bool = True, fatal: bool = False) -> None:
        self.name = name
        self.ok = ok
        self.fatal = fatal
        self.detail = "Detail"


class FakeReport:
    def __init__(self, *checks: FakeCheck) -> None:
        self.checks = list(checks) or [FakeCheck("Platz")]
        self.estimated_work_gb = 30.0

    @property
    def blocking(self):
        return [c for c in self.checks if not c.ok and c.fatal]

    @property
    def ok(self) -> bool:
        return not self.blocking


class FakeResult:
    def __init__(self, iso: Path | None) -> None:
        self.iso_path = iso
        self.warnings: list[str] = []


class FakeController:
    """Ein Controller, der nichts tut -- ausser sich zu merken, was gefragt wurde."""

    letzte: FakeController | None = None

    def __init__(self, catalog, config, resolution, secrets=None, **rest) -> None:
        self.catalog = catalog
        self.config = config
        self.resolution = resolution
        self.secrets = secrets
        self.target = None
        self.bericht = FakeReport()
        self.ergebnis: FakeResult | None = None
        self.fehler: Exception | None = None
        self.cancel_calls = 0
        self.run_kwargs: dict = {}
        FakeController.letzte = self

    def preflight(self, work_dir, out_dir):
        return self.bericht

    def run(self, work_dir, out_dir, **kwargs):
        self.run_kwargs = dict(kwargs, work_dir=work_dir, out_dir=out_dir)
        if self.fehler is not None:
            raise self.fehler
        schritt = kwargs.get("on_step")
        if schritt is not None:
            schritt(None, "Profil wird erzeugt")
        fortschritt = kwargs.get("on_progress")
        if fortschritt is not None:
            fortschritt(0.5, "mkarchiso laeuft", "squashfs")
        zeile = kwargs.get("on_line")
        if zeile is not None:
            zeile("eine Ausgabezeile von mkarchiso")
        assert self.ergebnis is not None
        return self.ergebnis

    def cancel(self) -> None:
        self.cancel_calls += 1


@pytest.fixture
def umgebung(tmp_path, monkeypatch):
    """Attrappen fuer Controller und Zielwahl, plus eigene Historie."""
    from archcustomiser.core import build as build_paket
    from archcustomiser.core import history
    from archcustomiser.core.build import targets

    monkeypatch.setattr(build_paket, "BuildController", FakeController)
    monkeypatch.setattr(targets, "available_targets", lambda: [FakeOption()])
    monkeypatch.setattr(history, "state_dir", lambda: tmp_path / "zustand")

    (tmp_path / "out").mkdir(exist_ok=True)
    iso = baue_iso(tmp_path / "out" / "arch.iso")
    FakeController.letzte = None
    return {"tmp": tmp_path, "iso": iso}


def starte(umgebung, **rest) -> int:
    return cli_build.build(
        PROFIL,
        out_dir=umgebung["tmp"] / "out",
        work_dir=umgebung["tmp"] / "work",
        **rest,
    )


def ergebnis_setzen(iso) -> None:
    assert FakeController.letzte is not None
    FakeController.letzte.ergebnis = FakeResult(iso)


# ---------------------------------------------------------------------------
# Der gute Fall
# ---------------------------------------------------------------------------


def test_a_successful_build_returns_zero(umgebung, monkeypatch, capsys) -> None:
    ausgang = {}

    class Vorbereitet(FakeController):
        def run(self, work_dir, out_dir, **kwargs):
            self.ergebnis = FakeResult(umgebung["iso"])
            ausgang["kwargs"] = kwargs
            return super().run(work_dir, out_dir, **kwargs)

    from archcustomiser.core import build as build_paket

    monkeypatch.setattr(build_paket, "BuildController", Vorbereitet)
    assert starte(umgebung) == cli_build.FERTIG
    assert ausgang["kwargs"]["skip_preflight"] is True


def test_the_summary_goes_to_stdout_and_progress_to_stderr(
    umgebung, monkeypatch, capsys
) -> None:
    class Vorbereitet(FakeController):
        def run(self, work_dir, out_dir, **kwargs):
            self.ergebnis = FakeResult(umgebung["iso"])
            return super().run(work_dir, out_dir, **kwargs)

    from archcustomiser.core import build as build_paket

    monkeypatch.setattr(build_paket, "BuildController", Vorbereitet)
    starte(umgebung, ausfuehrlich=True)
    gefangen = capsys.readouterr()

    assert "SHA-256" in gefangen.out
    assert "ISO-9660" in gefangen.out
    assert "mkarchiso laeuft" in gefangen.err
    assert "mkarchiso laeuft" not in gefangen.out
    assert "eine Ausgabezeile von mkarchiso" in gefangen.err


def test_a_successful_build_writes_a_checksum_file(umgebung, monkeypatch) -> None:
    class Vorbereitet(FakeController):
        def run(self, work_dir, out_dir, **kwargs):
            self.ergebnis = FakeResult(umgebung["iso"])
            return super().run(work_dir, out_dir, **kwargs)

    from archcustomiser.core import build as build_paket

    monkeypatch.setattr(build_paket, "BuildController", Vorbereitet)
    starte(umgebung)
    neben = umgebung["iso"].with_name(umgebung["iso"].name + ".sha256")
    assert neben.is_file()


def test_a_successful_build_lands_in_the_history(umgebung, monkeypatch) -> None:
    from archcustomiser.core import build as build_paket
    from archcustomiser.core import history

    class Vorbereitet(FakeController):
        def run(self, work_dir, out_dir, **kwargs):
            self.ergebnis = FakeResult(umgebung["iso"])
            return super().run(work_dir, out_dir, **kwargs)

    monkeypatch.setattr(build_paket, "BuildController", Vorbereitet)
    starte(umgebung)
    eintraege = history.lies()
    assert len(eintraege) == 1
    assert eintraege[0].iso_name == "arch.iso"
    assert eintraege[0].bauweg


# ---------------------------------------------------------------------------
# Die Faelle, die schiefgehen
# ---------------------------------------------------------------------------


def test_an_unknown_profile_is_an_input_error(umgebung) -> None:
    assert cli_build.build(Path("gibt-es-nicht.yaml")) == cli_build.EINGABEFEHLER


def test_a_blocking_preflight_stops_the_build(umgebung, monkeypatch) -> None:
    class Blockiert(FakeController):
        def preflight(self, work_dir, out_dir):
            return FakeReport(FakeCheck("Platz", ok=False, fatal=True))

    from archcustomiser.core import build as build_paket

    monkeypatch.setattr(build_paket, "BuildController", Blockiert)
    assert starte(umgebung) == cli_build.BLOCKIERT


def test_a_cancelled_build_has_its_own_exit_code(umgebung, monkeypatch) -> None:
    class Abgebrochen(FakeController):
        def run(self, work_dir, out_dir, **kwargs):
            raise BuildCancelled()

    from archcustomiser.core import build as build_paket

    monkeypatch.setattr(build_paket, "BuildController", Abgebrochen)
    assert starte(umgebung) == cli_build.ABGEBROCHEN


def test_a_failed_build_returns_one(umgebung, monkeypatch) -> None:
    class Gescheitert(FakeController):
        def run(self, work_dir, out_dir, **kwargs):
            raise BuildFailed("mkarchiso ist ausgestiegen")

    from archcustomiser.core import build as build_paket

    monkeypatch.setattr(build_paket, "BuildController", Gescheitert)
    assert starte(umgebung) == cli_build.FEHLGESCHLAGEN


def test_a_build_without_an_image_is_a_failure(umgebung, monkeypatch) -> None:
    class Ohne(FakeController):
        def run(self, work_dir, out_dir, **kwargs):
            self.ergebnis = FakeResult(None)
            return super().run(work_dir, out_dir, **kwargs)

    from archcustomiser.core import build as build_paket

    monkeypatch.setattr(build_paket, "BuildController", Ohne)
    assert starte(umgebung) == cli_build.FEHLGESCHLAGEN


def test_an_implausible_image_is_not_reported_as_success(
    umgebung, monkeypatch, tmp_path
) -> None:
    """Rueckgabewert 0 von mkarchiso ist kein Beweis fuer eine brauchbare ISO."""
    kaputt = tmp_path / "out" / "halb.iso"
    kaputt.write_bytes(b"\x00" * 4096)

    class Halb(FakeController):
        def run(self, work_dir, out_dir, **kwargs):
            self.ergebnis = FakeResult(kaputt)
            return super().run(work_dir, out_dir, **kwargs)

    from archcustomiser.core import build as build_paket

    monkeypatch.setattr(build_paket, "BuildController", Halb)
    assert starte(umgebung) == cli_build.FEHLGESCHLAGEN


# ---------------------------------------------------------------------------
# Zielwahl
# ---------------------------------------------------------------------------


def test_an_unknown_target_is_refused(umgebung, monkeypatch) -> None:
    assert starte(umgebung, ziel="container") == cli_build.EINGABEFEHLER


def test_an_unusable_target_is_refused_with_its_remedy(
    umgebung, monkeypatch, capsys
) -> None:
    from archcustomiser.core.build import targets

    monkeypatch.setattr(
        targets,
        "available_targets",
        lambda: [FakeOption("wsl", usable=False, problem="keine Verteilung", remedy="wsl --install")],
    )
    assert starte(umgebung, ziel="wsl") == cli_build.EINGABEFEHLER
    assert "wsl --install" in capsys.readouterr().err


def test_without_any_usable_target_the_export_is_suggested(
    umgebung, monkeypatch, capsys
) -> None:
    from archcustomiser.core.build import targets

    monkeypatch.setattr(
        targets, "available_targets", lambda: [FakeOption("lokal", usable=False)]
    )
    assert starte(umgebung) == cli_build.EINGABEFEHLER
    assert "--export-profile" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Passwort
# ---------------------------------------------------------------------------


def test_the_password_comes_from_stdin_and_reaches_the_secret_store(
    umgebung, monkeypatch
) -> None:
    """Nie als Argument: das stuende unter Linux in /proc fuer jeden lesbar."""
    import io

    class Vorbereitet(FakeController):
        def run(self, work_dir, out_dir, **kwargs):
            self.ergebnis = FakeResult(umgebung["iso"])
            return super().run(work_dir, out_dir, **kwargs)

    from archcustomiser.core import build as build_paket

    monkeypatch.setattr(build_paket, "BuildController", Vorbereitet)
    monkeypatch.setattr("sys.stdin", io.StringIO("geheim123\n"))

    starte(umgebung, password_stdin=True)
    controller = FakeController.letzte
    assert controller is not None
    assert controller.secrets.keys(), "das Passwort kam nie im SecretStore an"


def test_an_empty_password_is_an_input_error(umgebung, monkeypatch) -> None:
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO("\n"))
    assert starte(umgebung, password_stdin=True) == cli_build.EINGABEFEHLER


def test_the_password_never_reaches_the_configuration(umgebung, monkeypatch) -> None:
    import io

    class Vorbereitet(FakeController):
        def run(self, work_dir, out_dir, **kwargs):
            self.ergebnis = FakeResult(umgebung["iso"])
            return super().run(work_dir, out_dir, **kwargs)

    from archcustomiser.core import build as build_paket

    monkeypatch.setattr(build_paket, "BuildController", Vorbereitet)
    monkeypatch.setattr("sys.stdin", io.StringIO("hunter2-geheim\n"))

    starte(umgebung, password_stdin=True)
    controller = FakeController.letzte
    assert controller is not None
    assert "hunter2" not in repr(controller.config)


# ---------------------------------------------------------------------------
# Die kleinen Unterbefehle
# ---------------------------------------------------------------------------


def test_verify_reports_a_good_image(tmp_path, capsys) -> None:
    assert cli_build.verify(baue_iso(tmp_path / "arch.iso")) == cli_build.FERTIG
    assert "SHA-256" in capsys.readouterr().out


def test_verify_reports_a_broken_image(tmp_path) -> None:
    kaputt = tmp_path / "kaputt.iso"
    kaputt.write_bytes(b"\x00" * 100)
    assert cli_build.verify(kaputt) == cli_build.FEHLGESCHLAGEN


def test_an_empty_history_says_so(umgebung, capsys) -> None:
    assert cli_build.historie() == cli_build.FERTIG
    assert "Noch nichts" in capsys.readouterr().out


def test_the_history_can_be_cleared_from_the_command_line(umgebung, capsys) -> None:
    from archcustomiser.core import history

    history.merke(history.Bau(iso_name="a.iso", zeitpunkt="2026-01-01 01:00"))
    assert cli_build.historie(leeren=True) == cli_build.FERTIG
    assert "1 Eintraege" in capsys.readouterr().out
