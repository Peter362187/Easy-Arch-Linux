"""Tests des Gesamtablaufs vom Wizard zur ISO.

Der Ablauf wird vollstaendig durchgespielt -- mit dem nachgebildeten mkarchiso
aus ``fake_mkarchiso.py``, aber mit echtem Profilgenerator, echtem Schreiben
auf die Platte und echtem Prozessstart.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from archcustomiser.core.build import BuildController, Step
from archcustomiser.core.build.errors import BuildCancelled, BuildFailed, PreflightError
from archcustomiser.core.build.runner import MkarchisoRunner
from archcustomiser.core.config import BuildConfig
from archcustomiser.core.secrets import SecretStore

FAKE = Path(__file__).parent / "fake_mkarchiso.py"


def make_config() -> BuildConfig:
    config = BuildConfig()
    for ref in ("desktop.none", "kernel.linux", "audio.none", "network.networkmanager"):
        config.add(ref)
    config.set_field("branding.distro_name", "FLOS")
    config.set_field("branding.version", "1.0")
    config.set_field("basics.hostname", "flos")
    return config


def fake_runner_factory(**overrides):
    """Erzeugt Runner, die statt mkarchiso das nachgebildete Programm starten."""

    def factory(profile_dir, work_dir, out_dir, **kwargs) -> MkarchisoRunner:
        runner = MkarchisoRunner(profile_dir, work_dir, out_dir, **{**kwargs, **overrides})
        original = runner.build_argv
        runner.executable = sys.executable
        runner.build_argv = lambda: [sys.executable, str(FAKE), *original()[1:]]  # type: ignore[method-assign]
        return runner

    return factory


@pytest.fixture
def controller(catalog, resolver):
    config = make_config()
    secrets = SecretStore()
    secrets.set("user.password", "geheim123")
    return BuildController(
        catalog,
        config,
        resolver.resolve(config),
        secrets,
        runner_factory=fake_runner_factory(),
    )


# ---------------------------------------------------------------------------
# Erfolgreicher Durchlauf
# ---------------------------------------------------------------------------


def test_full_run_produces_an_iso(braucht_symlinks, controller, tmp_path) -> None:
    steps: list[Step] = []
    fractions: list[float] = []

    outcome = controller.run(
        tmp_path / "work",
        tmp_path / "out",
        on_step=lambda step, _label: steps.append(step),
        on_progress=lambda fraction, _l, _d: fractions.append(fraction),
        skip_preflight=True,     # laeuft auf jedem Betriebssystem
    )

    assert outcome.succeeded
    assert outcome.iso_path is not None and outcome.iso_path.is_file()
    assert steps == [Step.PREFLIGHT, Step.GENERATE, Step.WRITE, Step.MKARCHISO, Step.CLEANUP]
    assert fractions[-1] == 1.0
    assert fractions == sorted(fractions), "der Fortschritt ist zurueckgesprungen"


def test_profile_is_written_before_the_build(braucht_symlinks, controller, tmp_path) -> None:
    """mkarchiso bekommt ein echtes Verzeichnis, keinen Baum im Speicher."""
    seen: dict[str, Path] = {}

    def factory(profile_dir, work_dir, out_dir, **kwargs):
        seen["profile"] = Path(profile_dir)
        assert (Path(profile_dir) / "profiledef.sh").is_file()
        assert (Path(profile_dir) / "packages.x86_64").is_file()
        return fake_runner_factory()(profile_dir, work_dir, out_dir, **kwargs)

    controller.runner_factory = factory
    controller.run(tmp_path / "work", tmp_path / "out", skip_preflight=True)
    assert seen["profile"].name == "profile"


def test_work_directory_is_cleaned_up(braucht_symlinks, controller, tmp_path) -> None:
    work = tmp_path / "work"
    controller.run(work, tmp_path / "out", skip_preflight=True)
    assert not (work / "profile").exists()
    assert not (work / "work").exists()


def test_work_directory_can_be_kept(braucht_symlinks, controller, tmp_path) -> None:
    """Zur Fehlersuche muss sich das Aufraeumen abschalten lassen."""
    work = tmp_path / "work"
    controller.run(work, tmp_path / "out", keep_work_dir=True, skip_preflight=True)
    assert (work / "profile" / "profiledef.sh").is_file()


def test_build_log_is_written(braucht_symlinks, controller, tmp_path) -> None:
    outcome = controller.run(tmp_path / "work", tmp_path / "out", skip_preflight=True)
    assert outcome.log_path is not None and outcome.log_path.is_file()
    text = outcome.log_path.read_text(encoding="utf-8")
    assert "Erfolgreich" in text
    assert "profiledef.sh" in text
    assert "Ausgabe von mkarchiso" in text


def test_build_log_contains_no_password(braucht_symlinks, controller, tmp_path) -> None:
    outcome = controller.run(tmp_path / "work", tmp_path / "out", skip_preflight=True)
    assert outcome.log_path is not None
    assert "geheim123" not in outcome.log_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Fehlschlaege
# ---------------------------------------------------------------------------


def test_failure_is_reported_with_the_cause(braucht_symlinks, controller, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_FAIL_AT", "Creating SquashFS image")
    with pytest.raises(BuildFailed) as info:
        controller.run(tmp_path / "work", tmp_path / "out", skip_preflight=True)
    assert info.value.returncode != 0


def test_log_is_written_even_when_the_build_fails(braucht_symlinks, controller, tmp_path, monkeypatch) -> None:
    """Gerade dann ist das Protokoll das Einzige, was noch hilft."""
    monkeypatch.setenv("FAKE_FAIL_AT", "Creating ISO image")
    from archcustomiser.core.logging_setup import build_log_dir

    before = set(build_log_dir().glob("*.log")) if build_log_dir().is_dir() else set()
    with pytest.raises(BuildFailed):
        controller.run(tmp_path / "work", tmp_path / "out", skip_preflight=True)
    after = set(build_log_dir().glob("*.log"))
    assert after - before, "es wurde kein Protokoll geschrieben"


def test_preflight_blocks_before_anything_happens(catalog, resolver, tmp_path) -> None:
    """Auf Windows blockiert die Pruefung -- und zwar bevor etwas geschrieben wird."""
    config = make_config()
    controller = BuildController(
        catalog, config, resolver.resolve(config), runner_factory=fake_runner_factory()
    )
    work = tmp_path / "work"
    if sys.platform == "linux":
        pytest.skip("Auf Linux kann die Vorabpruefung bestehen")
    with pytest.raises(PreflightError):
        controller.run(work, tmp_path / "out")
    assert not (work / "profile").exists(), "es wurde trotz Abbruch geschrieben"


def test_invalid_configuration_never_reaches_mkarchiso(catalog, resolver, tmp_path) -> None:
    from archcustomiser.core.archiso.errors import ProfileError

    started = []

    def factory(*args, **kwargs):
        started.append(True)
        return fake_runner_factory()(*args, **kwargs)

    config = BuildConfig()      # nichts ausgewaehlt
    controller = BuildController(
        catalog, config, resolver.resolve(config), runner_factory=factory
    )
    with pytest.raises(ProfileError):
        controller.run(tmp_path / "work", tmp_path / "out", skip_preflight=True)
    assert not started, "mkarchiso wurde trotz unvollstaendiger Konfiguration gestartet"


# ---------------------------------------------------------------------------
# Abbruch
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_cancel_during_the_build(braucht_symlinks, controller, tmp_path, monkeypatch) -> None:
    import threading
    import time

    monkeypatch.setenv("FAKE_SLOW", "0.05")

    def cancel_soon() -> None:
        time.sleep(0.8)
        controller.cancel()

    threading.Thread(target=cancel_soon, daemon=True).start()
    with pytest.raises(BuildCancelled):
        controller.run(tmp_path / "work", tmp_path / "out", skip_preflight=True)
    assert controller.cancelled


def test_cancel_before_the_build_stops_early(controller, tmp_path) -> None:
    started = []
    controller.runner_factory = lambda *a, **k: started.append(True)  # type: ignore[assignment]
    controller.cancel()
    with pytest.raises(BuildCancelled):
        controller.run(tmp_path / "work", tmp_path / "out", skip_preflight=True)
    assert not started


# ---------------------------------------------------------------------------
# Vorabpruefung
# ---------------------------------------------------------------------------


def test_space_estimate_grows_with_the_selection() -> None:
    from archcustomiser.core.build import estimate_work_space_gb

    small = estimate_work_space_gb(500)
    large = estimate_work_space_gb(4000)
    assert large > small
    # Ein KDE-System mit Spielen braucht realistisch deutlich mehr als 20 GB.
    assert large > 20


def test_preflight_collects_every_finding(tmp_path) -> None:
    """Der Benutzer soll alles auf einmal sehen, nicht Problem fuer Problem."""
    from archcustomiser.core.build import run_preflight

    report = run_preflight(tmp_path / "work", tmp_path / "out", installed_mb=3000)
    assert report.checks
    assert isinstance(report.ok, bool)


# ---------------------------------------------------------------------------
# Bedingt noetige Werkzeuge -- Befund der Durchsicht vom 02.09.2026
# ---------------------------------------------------------------------------


def _umgebung_ohne(*fehlende: str):
    """Eine Linux-Umgebung, in der bestimmte Werkzeuge fehlen."""
    from archcustomiser.core.environment import (
        _OPTIONAL_TOOLS,
        _REQUIRED_TOOLS,
        CONDITIONAL_TOOLS,
        Environment,
        Tool,
    )

    tools = []
    for name, paket, zweck in _REQUIRED_TOOLS:
        tools.append(Tool(name, paket, zweck, True, None if name in fehlende else f"/usr/bin/{name}"))
    for name, paket, zweck in _OPTIONAL_TOOLS:
        tools.append(Tool(name, paket, zweck, False, None if name in fehlende else f"/usr/bin/{name}"))
    for name, paket, zweck in CONDITIONAL_TOOLS.values():
        tools.append(Tool(name, paket, zweck, False, None if name in fehlende else f"/usr/bin/{name}"))
    return Environment(
        platform="linux",
        can_build=True,
        tools=tuple(tools),
        privilege_mode="rootless",
        # Ohne pacman gilt das System als "hier grundsaetzlich nicht baubar" und
        # die Vorabpruefung bricht vorher ab -- diese Umgebung soll aber ein
        # Arch-System nachbilden, dem einzelne Werkzeuge fehlen.
        pacman_available="pacman" not in fehlende,
    )


def test_missing_grub_does_not_block_a_systemd_boot_build(tmp_path, monkeypatch) -> None:
    """grub-mkstandalone stand bei den unbedingt noetigen Werkzeugen.

    In mkarchiso ruft es ausschliesslich ``_make_bootmode_uefi.grub`` auf;
    grubenv und loopback.cfg entstehen per printf und sed. Wer systemd-boot
    gewaehlt hatte, wurde also ohne jeden Grund am Bauen gehindert.
    """
    from archcustomiser.core.build.preflight import run_preflight

    monkeypatch.setattr("sys.platform", "linux")
    report = run_preflight(
        tmp_path / "work",
        tmp_path / "out",
        installed_mb=1000,
        bootmodes=("bios.syslinux", "uefi.systemd-boot"),
        environment=_umgebung_ohne("grub-mkstandalone"),
    )
    namen = [check.name for check in report.blocking]
    assert "Bootloader-Werkzeuge" not in namen
    assert "Werkzeuge" not in namen, f"unerwartet blockiert: {report.blocking}"


def test_missing_grub_does_block_a_grub_build(tmp_path, monkeypatch) -> None:
    """Bei uefi.grub wird es dagegen tatsaechlich gebraucht."""
    from archcustomiser.core.build.preflight import run_preflight

    monkeypatch.setattr("sys.platform", "linux")
    report = run_preflight(
        tmp_path / "work",
        tmp_path / "out",
        installed_mb=1000,
        bootmodes=("bios.syslinux", "uefi.grub"),
        environment=_umgebung_ohne("grub-mkstandalone"),
    )
    blockierend = [check for check in report.blocking if check.name == "Bootloader-Werkzeuge"]
    assert blockierend, "der fehlende Bootloader faellt nicht auf"
    assert "grub" in blockierend[0].detail
    assert "pacman -S" in blockierend[0].detail, "ohne Abhilfe ist die Meldung nutzlos"
