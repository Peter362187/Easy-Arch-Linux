"""Tests der Aufloesungslogik gegen den echten mitgelieferten Katalog."""

from __future__ import annotations

from archcustomiser.core.config import BuildConfig, SelectionSource


def make_config(*refs: str) -> BuildConfig:
    config = BuildConfig()
    for ref in refs:
        config.add(ref)
    return config


# ---------------------------------------------------------------------------
# implies
# ---------------------------------------------------------------------------


def test_implies_is_transitive(resolver) -> None:
    """KDE zieht SDDM, SDDM zieht das grafische Start-Target."""
    result = resolver.resolve(make_config("desktop.kde"))
    assert "display_manager.sddm" in result.effective_refs
    assert "foundation.graphical-target" in result.effective_refs


def test_implied_options_are_marked_automatic(resolver) -> None:
    result = resolver.resolve(make_config("desktop.kde"))
    assert "display_manager.sddm" in result.auto_refs
    assert "desktop.kde" not in result.auto_refs


def test_switching_desktop_drops_the_old_automatic_entries(resolver) -> None:
    """Wechsel KDE -> GNOME: SDDM muss verschwinden, GDM erscheinen."""
    config = make_config("desktop.kde")
    assert "display_manager.sddm" in resolver.resolve(config).effective_refs

    config.remove("desktop.kde")
    config.add("desktop.gnome")
    result = resolver.resolve(config)

    assert "display_manager.gdm" in result.effective_refs
    assert "display_manager.sddm" not in result.effective_refs


def test_user_confirmed_option_survives_when_its_cause_disappears(resolver) -> None:
    """Was der Benutzer selbst angeklickt hat, bleibt.

    Das unterscheidet ``SelectionSource.USER`` von ``AUTO`` -- sonst wuerde eine
    bewusst gesetzte Option beim Desktopwechsel verschwinden.
    """
    config = make_config("desktop.kde")
    config.add("display_manager.sddm", SelectionSource.USER)

    config.remove("desktop.kde")
    config.add("desktop.xfce")
    result = resolver.resolve(config)

    assert "display_manager.sddm" in result.effective_refs
    assert "display_manager.sddm" not in result.auto_refs


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


def test_display_manager_is_added_when_a_session_exists(resolver) -> None:
    """Ohne Login-Screen startet eine grafische Sitzung nicht."""
    result = resolver.resolve(make_config("windowmanager.hyprland"))
    assert result.capabilities.get("display-manager")
    assert any(issue.code == "capability_autofill" for issue in result.issues)


def test_no_display_manager_without_a_graphical_session(resolver) -> None:
    result = resolver.resolve(make_config("desktop.none", "kernel.linux"))
    assert not result.capabilities.get("display-manager")


def test_two_audio_servers_conflict_with_a_fix(resolver) -> None:
    config = make_config("audio.pipewire", "audio.pulseaudio")
    result = resolver.resolve(config)
    conflicts = [issue for issue in result.issues if issue.code == "capability_arity"]
    assert conflicts
    assert conflicts[0].severity == "error"
    assert conflicts[0].fix is not None
    assert conflicts[0].fix.deselect


def test_two_network_stacks_only_warn(resolver) -> None:
    """Technisch moeglich, aber selten gewollt -- deshalb keine Blockade."""
    result = resolver.resolve(
        make_config(
            "desktop.none",
            "kernel.linux",
            "audio.none",
            "network.networkmanager",
            "network.systemd-networkd",
        )
    )
    arity = [issue for issue in result.issues if issue.code == "capability_arity"]
    assert arity and arity[0].severity == "warning"
    # Die Warnung darf den Weiter-Knopf nicht sperren.
    assert result.is_valid


def test_duplicate_message_is_suppressed(resolver) -> None:
    """Capability-Meldung und Kategorieregel sagen dasselbe -- eine reicht."""
    result = resolver.resolve(make_config("audio.pipewire", "audio.pulseaudio"))
    codes = [issue.code for issue in result.issues]
    assert "capability_arity" in codes
    assert "single_selection" not in codes


# ---------------------------------------------------------------------------
# Beitraege
# ---------------------------------------------------------------------------


def test_steam_enables_multilib(resolver) -> None:
    """Ohne multilib findet der Build Steam nicht."""
    result = resolver.resolve(make_config("apps.steam"))
    assert "multilib" in result.repositories


def test_packages_are_deduplicated_with_origins(resolver) -> None:
    """KDE und Sway ziehen beide dieselben Basispakete."""
    result = resolver.resolve(make_config("desktop.kde", "windowmanager.sway"))
    names = result.package_names
    assert len(names) == len(set(names))
    fonts = [p for p in result.packages if p.name == "noto-fonts"]
    assert fonts and len(fonts[0].origins) >= 1


def test_services_are_deduplicated_by_owner(resolver) -> None:
    result = resolver.resolve(make_config("desktop.kde", "desktop.lxqt"))
    units = [service.unit for service in result.services]
    assert units.count("sddm.service") == 1


def test_conditional_package_follows_the_kernel(resolver) -> None:
    """Der NVIDIA-Treiber muss zum gewaehlten Kernel passen.

    Seit dem 20.12.2025 heissen die Pakete nvidia-open, nvidia-open-lts und
    nvidia-open-dkms; die alten Namen gibt es in den offiziellen Repositories
    nicht mehr. Die Fallunterscheidung nach Kernel bleibt aber richtig:
    nvidia-open haengt an linux, nvidia-open-lts an linux-lts, und
    nvidia-open-dkms an dkms -- deshalb ist es fuer Zen der einzige Weg.
    """
    standard = resolver.resolve(make_config("kernel.linux", "drivers.nvidia")).package_names
    assert "nvidia-open" in standard and "nvidia-open-dkms" not in standard

    zen = resolver.resolve(make_config("kernel.linux-zen", "drivers.nvidia")).package_names
    assert "nvidia-open-dkms" in zen and "nvidia-open" not in zen

    lts = resolver.resolve(make_config("kernel.linux-lts", "drivers.nvidia")).package_names
    assert "nvidia-open-lts" in lts and "nvidia-open-dkms" not in lts


def test_missing_requirement_is_reported_with_a_fix(resolver) -> None:
    result = resolver.resolve(make_config("services.docker"))
    missing = [issue for issue in result.issues if issue.code == "missing_requirement"]
    assert missing
    assert missing[0].fix is not None
    assert "apps.docker" in missing[0].fix.select


def test_unknown_option_warns_but_does_not_crash(resolver) -> None:
    config = BuildConfig()
    config.add("gibtesnicht.auchnicht")
    result = resolver.resolve(config)
    assert any(issue.code == "unknown_option" for issue in result.issues)


# ---------------------------------------------------------------------------
# systemd-Symlinks
# ---------------------------------------------------------------------------


def test_display_manager_uses_the_alias_not_a_wants_link(resolver) -> None:
    """sddm.service traegt in [Install] nur 'Alias=display-manager.service'.

    Ein Symlink unter graphical.target.wants/ waere wirkungslos -- genau der
    Fehler, den man beim Nachbau von 'systemctl enable' leicht macht.
    """
    result = resolver.resolve(make_config("desktop.kde"))
    links = dict(result.all_symlinks())
    assert "etc/systemd/system/display-manager.service" in links
    assert links["etc/systemd/system/display-manager.service"].endswith("sddm.service")
    assert not any("graphical.target.wants/sddm" in link for link in links)


def test_networkmanager_uses_a_wants_link(resolver) -> None:
    result = resolver.resolve(make_config("network.networkmanager"))
    links = dict(result.all_symlinks())
    assert "etc/systemd/system/multi-user.target.wants/NetworkManager.service" in links


def test_user_services_land_in_the_user_tree(resolver) -> None:
    result = resolver.resolve(make_config("audio.pipewire"))
    links = dict(result.all_symlinks())
    assert any(link.startswith("etc/systemd/user/") for link in links)


def test_live_only_services_stay_out_of_the_target_system(resolver) -> None:
    """graphical.target gehoert in die Live-Sitzung, nicht in die Installation."""
    from archcustomiser.core.catalog import EnableIn

    result = resolver.resolve(make_config("desktop.kde"))
    live = {service.unit for service in result.services_for(EnableIn.LIVE)}
    target = {service.unit for service in result.services_for(EnableIn.TARGET)}
    assert "graphical.target" in live
    assert "graphical.target" not in target


# ---------------------------------------------------------------------------
# Semantik fuer archinstall
# ---------------------------------------------------------------------------


def test_semantics_are_collected_for_archinstall(resolver) -> None:
    result = resolver.resolve(
        make_config("desktop.kde", "audio.pipewire", "kernel.linux-zen")
    )
    assert result.semantics["profile_config.profile.details"] == ["KDE Plasma"]
    assert result.semantics["audio_config.audio"] == "pipewire"
    assert result.semantics["kernels"] == ["linux-zen"]
    assert result.semantics["profile_config.greeter"] == "sddm"
