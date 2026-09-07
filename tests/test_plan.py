"""Tests des Bauplans und der archinstall-Konfiguration.

Die Konfiguration fuer das installierte System ist der Grund, warum das
Datenmodell die *semantische* Auswahl fuehrt und die Paketliste daraus
ableitet. Aus einer flachen Paketliste liesse sich ``profile.details =
["KDE Plasma"]`` nicht zurueckgewinnen.
"""

from __future__ import annotations

import pytest

from archcustomiser.core.config import BuildConfig
from archcustomiser.core.plan import build_archinstall_config, build_plan, plan_as_text


@pytest.fixture
def desktop_config() -> BuildConfig:
    config = BuildConfig()
    for ref in (
        "desktop.kde",
        "kernel.linux",
        "audio.pipewire",
        "network.networkmanager",
        "apps.firefox",
        "apps.steam",
        "drivers.mesa",
    ):
        config.add(ref)
    config.set_field("basics.hostname", "flos-desktop")
    config.set_field("basics.locale", "de_DE.UTF-8")
    config.set_field("basics.keymap", "de-latin1")
    config.set_field("basics.timezone", "Europe/Berlin")
    config.set_field("branding.distro_name", "FLOS")
    config.set_field("branding.version", "1.0")
    config.set_field("user.create", True)
    config.set_field("user.username", "jason")
    return config


# ---------------------------------------------------------------------------
# Abgeleitete Namen
# ---------------------------------------------------------------------------


def test_iso_filename_matches_the_mkarchiso_pattern(desktop_config) -> None:
    assert desktop_config.iso_filename == "flos-1.0-x86_64.iso"


def test_iso_label_follows_iso9660_rules(desktop_config) -> None:
    desktop_config.set_field("branding.distro_name", "FLOS Desktop")
    label = desktop_config.iso_label
    assert label == "FLOS_DESKTOP_1_0"
    assert len(label) <= 32
    assert all(char.isupper() or char.isdigit() or char == "_" for char in label)


def test_install_dir_obeys_the_mkarchiso_limits(desktop_config) -> None:
    desktop_config.set_field("branding.distro_name", "FLOS Super Distribution 2026")
    install_dir = desktop_config.install_dir
    assert install_dir.isalnum() and install_dir.islower()
    assert len(install_dir) <= 30


def test_explicit_values_win_over_derivation(desktop_config) -> None:
    desktop_config.set_field("branding.iso_label", "EIGEN_1")
    desktop_config.set_field("branding.install_dir", "eigen")
    assert desktop_config.iso_label == "EIGEN_1"
    assert desktop_config.install_dir == "eigen"


# ---------------------------------------------------------------------------
# archinstall
# ---------------------------------------------------------------------------


def test_archinstall_gets_semantics_not_package_names(catalog, resolver, desktop_config) -> None:
    document = build_archinstall_config(desktop_config, resolver.resolve(desktop_config))
    assert document["profile_config"]["profile"]["details"] == ["KDE Plasma"]
    assert document["audio_config"]["audio"] == "pipewire"
    assert document["profile_config"]["greeter"] == "sddm"
    assert document["kernels"] == ["linux"]
    assert document["network_config"]["type"] == "nm"


def test_multilib_reaches_the_installer_configuration(catalog, resolver, desktop_config) -> None:
    """Ohne diesen Eintrag findet die Installation Steam nicht."""
    document = build_archinstall_config(desktop_config, resolver.resolve(desktop_config))
    assert document["mirror_config"]["optional_repositories"] == ["multilib"]


def test_no_disk_configuration_is_written(catalog, resolver, desktop_config) -> None:
    """Eine vorgegebene Platte wuerde mit --silent ohne Rueckfrage geloescht."""
    document = build_archinstall_config(desktop_config, resolver.resolve(desktop_config))
    assert "disk_config" not in document


def test_no_credentials_are_written(catalog, resolver, desktop_config) -> None:
    document = build_archinstall_config(desktop_config, resolver.resolve(desktop_config))
    text = repr(document).lower()
    assert "password" not in text
    assert "enc_password" not in text


def test_internal_keys_do_not_leak_into_the_json(catalog, resolver, desktop_config) -> None:
    """kernel_suffix steuert Dateinamen, ist aber keine archinstall-Option."""
    document = build_archinstall_config(desktop_config, resolver.resolve(desktop_config))
    assert "kernel_suffix" not in document


def test_live_only_services_are_absent(catalog, resolver, desktop_config) -> None:
    document = build_archinstall_config(desktop_config, resolver.resolve(desktop_config))
    assert "graphical.target" not in document.get("services", [])
    assert "sddm.service" in document["services"]


def test_bootloader_follows_the_selection(catalog, resolver, desktop_config) -> None:
    desktop_config.set_field("build.uefi_boot", "grub")
    document = build_archinstall_config(desktop_config, resolver.resolve(desktop_config))
    assert document["bootloader_config"]["bootloader"] == "Grub"


# ---------------------------------------------------------------------------
# Bauplan
# ---------------------------------------------------------------------------


def test_plan_has_all_the_sections_the_spec_asks_for(catalog, resolver, desktop_config) -> None:
    plan = build_plan(catalog, desktop_config, resolver.resolve(desktop_config))
    titles = " ".join(section.title for section in plan.sections)
    for expected in ("System", "Desktop", "Kernel", "Pakete", "Dienste", "Branding"):
        assert expected in titles


def test_plan_marks_automatic_additions(catalog, resolver, desktop_config) -> None:
    plan = build_plan(catalog, desktop_config, resolver.resolve(desktop_config))
    lines = [line for section in plan.sections for line in section.lines]
    assert any("automatisch ergaenzt" in line for line in lines)


def test_plan_warns_when_no_installer_is_included(catalog, resolver, desktop_config) -> None:
    """Ohne Installer ist das Ergebnis nach dem Neustart weg."""
    desktop_config.set_field("build.include_installer", False)
    plan = build_plan(catalog, desktop_config, resolver.resolve(desktop_config))
    assert any("Live-System" in warning for warning in plan.warnings)


def test_plan_warns_when_packages_could_not_be_checked(catalog, resolver, desktop_config) -> None:
    from archcustomiser.core.packages.validator import validate_all

    report = validate_all(["firefox"], None, degraded=True)
    plan = build_plan(catalog, desktop_config, resolver.resolve(desktop_config), report)
    assert any("nicht geprueft" in warning for warning in plan.warnings)


def test_plan_text_contains_no_secrets(catalog, resolver, desktop_config) -> None:
    """Der Bauplan darf kein Passwort enthalten -- geprueft am echten Wert.

    Die frueher hier stehende Oder-Bedingung war bereits erfuellt, sobald der
    Text das Wort 'passwort' klein geschrieben enthielt. Sie prueft ein Wort,
    kein Geheimnis. Hier wird stattdessen ein echtes Passwort gesetzt und der
    Klartext gesucht.
    """
    from archcustomiser.core.secrets import SecretStore

    geheim = "streng-geheim-77"
    secrets = SecretStore()
    secrets.set("user.password", geheim)
    desktop_config.set_field("user.username", "jason")

    plan = build_plan(catalog, desktop_config, resolver.resolve(desktop_config))
    text = plan_as_text(plan)

    assert geheim not in text
    assert geheim not in repr(plan)
    assert "flos-1.0-x86_64.iso" in text
