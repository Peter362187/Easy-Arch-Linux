"""Tests fuer Profile: Rundlauf, Migration und Datenerhalt."""

from __future__ import annotations

import pytest
import yaml

from archcustomiser.core.config import SCHEMA_VERSION, BuildConfig
from archcustomiser.core.profiles import ProfileError, ProfileService


@pytest.fixture
def service(catalog, tmp_path) -> ProfileService:
    return ProfileService(catalog, profiles_dir=tmp_path, builtin_dir=tmp_path)


# ---------------------------------------------------------------------------
# Mitgelieferte Profile
# ---------------------------------------------------------------------------


def test_bundled_profiles_load_and_resolve(catalog, resolver, profiles_dir, tmp_path) -> None:
    service = ProfileService(catalog, profiles_dir=tmp_path, builtin_dir=profiles_dir)
    found = service.list()
    assert len(found) >= 4

    for info in found:
        loaded = service.load(info.path)
        assert not [issue for issue in loaded.issues if issue.severity == "error"]
        result = resolver.resolve(loaded.config)
        blocking = [issue.message for issue in result.issues if issue.blocking]
        assert not blocking, f"{info.display_name}: {blocking}"


# ---------------------------------------------------------------------------
# Rundlauf
# ---------------------------------------------------------------------------


def test_round_trip_preserves_user_selections(service, resolver, tmp_path) -> None:
    config = BuildConfig(profile_name="Test")
    for ref in ("desktop.kde", "kernel.linux", "audio.pipewire", "apps.firefox"):
        config.add(ref)
    config.set_field("basics.hostname", "testhost")
    config.extra_packages = ["neovim", "htop"]

    path = tmp_path / "test.yaml"
    service.save(config, path, resolution=resolver.resolve(config))
    loaded = service.load(path)

    assert loaded.config.user_refs() == config.user_refs()
    assert loaded.config.field("basics.hostname") == "testhost"
    assert loaded.config.extra_packages == ["htop", "neovim"]


def test_automatic_selections_are_not_saved(service, resolver, tmp_path) -> None:
    """Ein altes Profil soll spaetere Katalogaenderungen automatisch mitnehmen.

    Waere SDDM mitgespeichert, wuerde ein Profil den Login-Screen von 2026
    festschreiben, auch wenn KDE spaeter einen anderen mitbringt.
    """
    config = BuildConfig()
    config.add("desktop.kde")
    resolution = resolver.resolve(config)
    assert "display_manager.sddm" in resolution.auto_refs

    path = tmp_path / "p.yaml"
    service.save(config, path, resolution=resolution)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "display_manager" not in document["selections"]


def test_passwords_are_never_written(service, tmp_path) -> None:
    config = BuildConfig()
    config.add("desktop.kde")
    config.set_field("user.username", "jason")
    path = tmp_path / "p.yaml"
    service.save(config, path)

    text = path.read_text(encoding="utf-8")
    assert "password" not in text.lower()


def test_password_in_a_hand_edited_profile_is_dropped(service, tmp_path) -> None:
    path = tmp_path / "p.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": SCHEMA_VERSION,
                "name": "manuell",
                "selections": {"desktop": ["kde"]},
                "fields": {"user.username": "jason", "user.password": "geheim123"},
            }
        ),
        encoding="utf-8",
    )
    loaded = service.load(path)
    assert loaded.config.field("user.password") is None
    assert any(issue.code == "secret_dropped" for issue in loaded.issues)


# ---------------------------------------------------------------------------
# Migration und Datenerhalt
# ---------------------------------------------------------------------------


def test_unknown_option_is_preserved_across_save(service, tmp_path) -> None:
    """Der Kern der Datensicherheit.

    Ein Profil, das mit einer Katalogerweiterung erstellt wurde, darf beim
    Oeffnen und Speichern auf einem Rechner ohne diese Erweiterung nichts
    verlieren.
    """
    path = tmp_path / "p.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": SCHEMA_VERSION,
                "name": "erweitert",
                "selections": {"desktop": ["kde"], "meinkram": ["spezialoption"]},
                "fields": {},
            }
        ),
        encoding="utf-8",
    )

    loaded = service.load(path)
    assert loaded.config.unresolved == {"meinkram": ["spezialoption"]}
    assert any(issue.code == "unknown_option" for issue in loaded.issues)

    service.save(loaded.config, path)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert document["selections"]["meinkram"] == ["spezialoption"]


def test_newer_schema_is_refused_not_guessed(service, tmp_path) -> None:
    """Ein halb verstandenes Profil ist schlimmer als gar keines."""
    path = tmp_path / "p.yaml"
    path.write_text(
        yaml.safe_dump({"schema_version": SCHEMA_VERSION + 5, "selections": {}}),
        encoding="utf-8",
    )
    with pytest.raises(ProfileError) as info:
        service.load(path)
    assert "neueren Programmversion" in str(info.value)


def test_broken_yaml_gives_a_readable_error(service, tmp_path) -> None:
    path = tmp_path / "p.yaml"
    path.write_text("selections: [unvollstaendig\n", encoding="utf-8")
    with pytest.raises(ProfileError) as info:
        service.load(path)
    assert "YAML" in str(info.value)


def test_snapshot_rescues_a_removed_option_as_a_package(service, tmp_path) -> None:
    path = tmp_path / "p.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "schema_version": SCHEMA_VERSION,
                "selections": {"apps": ["firefox", "verschwunden"]},
                "fields": {},
                "resolved_snapshot": {"packages": ["firefox", "verschwunden"]},
            }
        ),
        encoding="utf-8",
    )
    loaded = service.load(path)
    assert "verschwunden" in loaded.config.extra_packages
    assert any(issue.code == "moved_to_extra" for issue in loaded.issues)


def test_save_is_atomic(service, tmp_path, monkeypatch) -> None:
    """Ein Abbruch beim Schreiben darf das alte Profil nicht zerstoeren."""
    path = tmp_path / "p.yaml"
    config = BuildConfig()
    config.add("desktop.kde")
    service.save(config, path)
    original = path.read_text(encoding="utf-8")

    def explode(*_args, **_kwargs):
        raise OSError("Platte voll")

    monkeypatch.setattr("os.replace", explode)
    with pytest.raises(OSError):
        service.save(config, path)

    assert path.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob(".*tmp")), "Temporaerdatei blieb liegen"
