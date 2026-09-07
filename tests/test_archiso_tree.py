"""Tests des Profilbaums und der Ausgabewege.

Alles laeuft im Speicher bzw. in einem tar-Archiv -- damit sind Symlinks und
Dateirechte auch auf NTFS pruefbar.
"""

from __future__ import annotations

import gzip
import io
import tarfile
from pathlib import Path

import pytest

from archcustomiser.core.archiso.errors import (
    DuplicateEntryError,
    TargetNotEmptyError,
    UnsafePathError,
)
from archcustomiser.core.archiso.sinks import MARKER_NAME, DirectorySink, TarSink
from archcustomiser.core.archiso.tree import ProfileTree, normalise_path

# ---------------------------------------------------------------------------
# Pfadsicherheit
# ---------------------------------------------------------------------------

UNSAFE = [
    "../../etc/passwd",
    "..",
    "a/../../b",
    "/absolut/pfad",
    "C:/windows/system32",
    "c:\\windows",
    "a\\b",
    "",
    "   ",
    "./",
    "a/../..",
]


@pytest.mark.parametrize("path", UNSAFE)
def test_unsafe_paths_rejected(path: str) -> None:
    tree = ProfileTree()
    with pytest.raises(UnsafePathError):
        tree.add_file(path, "x")
    assert tree.file_count == 0, "der Baum darf nach einem Fehlschlag unveraendert sein"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("airootfs/etc/passwd", "airootfs/etc/passwd"),
        ("./airootfs/etc/hostname", "airootfs/etc/hostname"),
        ("airootfs//etc///motd", "airootfs/etc/motd"),
        ("profiledef.sh", "profiledef.sh"),
    ],
)
def test_paths_are_normalised(raw: str, expected: str) -> None:
    assert normalise_path(raw) == expected


def test_symlink_target_is_checked() -> None:
    tree = ProfileTree()
    with pytest.raises(UnsafePathError):
        tree.add_symlink("airootfs/etc/x", "")
    with pytest.raises(UnsafePathError):
        tree.add_symlink("airootfs/etc/x", "C:\\windows")


def test_permission_path_must_be_absolute_in_the_image() -> None:
    tree = ProfileTree()
    tree.add_permission("/etc/shadow", mode="0400")
    assert tree.file_permissions() == {"/etc/shadow": "0:0:0400"}
    with pytest.raises(UnsafePathError):
        tree.add_permission("etc/shadow")
    with pytest.raises(UnsafePathError):
        tree.add_permission("/etc/../../x")


# ---------------------------------------------------------------------------
# Doppelte Eintraege
# ---------------------------------------------------------------------------


def test_same_content_twice_is_fine() -> None:
    tree = ProfileTree()
    tree.add_file("a", "gleich", origin="eins")
    tree.add_file("a", "gleich", origin="zwei")
    assert tree.file_count == 1


def test_conflicting_content_is_reported() -> None:
    """Still zu entscheiden waere schlimmer als zu melden."""
    tree = ProfileTree()
    tree.add_file("a", "erste Fassung", origin="option.eins")
    with pytest.raises(DuplicateEntryError) as info:
        tree.add_file("a", "zweite Fassung", origin="option.zwei")
    assert "option.eins" in str(info.value)
    assert "option.zwei" in str(info.value)


def test_file_and_symlink_cannot_share_a_path() -> None:
    tree = ProfileTree()
    tree.add_file("a", "x", origin="eins")
    with pytest.raises(DuplicateEntryError):
        tree.add_symlink("a", "/ziel", origin="zwei")


def test_append_to_file() -> None:
    tree = ProfileTree()
    tree.append_to_file("etc/passwd", "root:x:0:0::/root:/bin/bash")
    tree.append_to_file("etc/passwd", "jason:x:1000:1000::/home/jason:/bin/bash")
    assert tree.text("etc/passwd").splitlines() == [
        "root:x:0:0::/root:/bin/bash",
        "jason:x:1000:1000::/home/jason:/bin/bash",
    ]


# ---------------------------------------------------------------------------
# tar-Ausgabe
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_tree() -> ProfileTree:
    tree = ProfileTree()
    tree.add_file("profiledef.sh", "#!/usr/bin/env bash\n", origin="generator")
    tree.add_file("airootfs/etc/hostname", "flos\n", origin="generator")
    tree.add_symlink(
        "airootfs/etc/systemd/system/display-manager.service",
        "/usr/lib/systemd/system/sddm.service",
        origin="display_manager.sddm",
    )
    tree.add_permission("/etc/shadow", mode="0400")
    return tree


def test_tar_preserves_symlinks(sample_tree: ProfileTree, tmp_path: Path) -> None:
    """Der Grund fuer den tar-Weg: auf NTFS gaebe es sonst keine Symlinks."""
    payload = TarSink(tmp_path / "x.tar.gz", root_name="flos").to_bytes(sample_tree)
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        links = {m.name: m.linkname for m in archive.getmembers() if m.issym()}
    assert links == {
        "flos/airootfs/etc/systemd/system/display-manager.service": (
            "/usr/lib/systemd/system/sddm.service"
        )
    }


def test_tar_is_reproducible(sample_tree: ProfileTree, tmp_path: Path) -> None:
    """Zweimal erzeugen muss dieselben Bytes ergeben.

    Sonst laesst sich nicht erkennen, was sich zwischen zwei Laeufen wirklich
    geaendert hat.
    """
    sink = TarSink(tmp_path / "x.tar.gz", root_name="flos")
    assert sink.to_bytes(sample_tree) == sink.to_bytes(sample_tree)


def test_tar_contains_all_entries(sample_tree: ProfileTree, tmp_path: Path) -> None:
    payload = TarSink(tmp_path / "x.tar.gz", root_name="flos").to_bytes(sample_tree)
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        names = {m.name for m in archive.getmembers() if not m.isdir()}
    assert names == {
        "flos/profiledef.sh",
        "flos/airootfs/etc/hostname",
        "flos/airootfs/etc/systemd/system/display-manager.service",
    }


def test_tar_creates_parent_directories(sample_tree: ProfileTree, tmp_path: Path) -> None:
    payload = TarSink(tmp_path / "x.tar.gz", root_name="flos").to_bytes(sample_tree)
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        directories = {m.name for m in archive.getmembers() if m.isdir()}
    assert "flos/airootfs/etc" in directories
    assert "flos/airootfs/etc/systemd/system" in directories


def test_tar_round_trip_content(sample_tree: ProfileTree, tmp_path: Path) -> None:
    target = TarSink(tmp_path / "x.tar.gz", root_name="flos").write(sample_tree)
    with tarfile.open(target) as archive:
        handle = archive.extractfile("flos/airootfs/etc/hostname")
        assert handle is not None
        assert handle.read().decode() == "flos\n"


# ---------------------------------------------------------------------------
# Verzeichnis-Ausgabe
# ---------------------------------------------------------------------------


def test_directory_sink_refuses_foreign_content(sample_tree: ProfileTree, tmp_path: Path) -> None:
    """Ein Zielverzeichnis mit fremden Dateien wird nicht angetastet."""
    target = tmp_path / "dokumente"
    target.mkdir()
    (target / "wichtig.txt").write_text("bitte nicht loeschen", encoding="utf-8")

    with pytest.raises(TargetNotEmptyError):
        DirectorySink(target, iso_name="flos").write(sample_tree)

    assert (target / "wichtig.txt").read_text(encoding="utf-8") == "bitte nicht loeschen"


def test_directory_sink_accepts_empty_target(braucht_symlinks, sample_tree: ProfileTree, tmp_path: Path) -> None:
    target = tmp_path / "leer"
    target.mkdir()
    DirectorySink(target, iso_name="flos").write(sample_tree)
    assert (target / "profiledef.sh").is_file()
    assert (target / MARKER_NAME).is_file()


def test_directory_sink_overwrites_its_own_profile(braucht_symlinks, sample_tree: ProfileTree, tmp_path: Path) -> None:
    target = tmp_path / "profil"
    DirectorySink(target, iso_name="flos").write(sample_tree)
    # Zweiter Lauf muss ohne Rueckfrage durchgehen -- es ist unser eigenes Profil.
    DirectorySink(target, iso_name="flos").write(sample_tree)
    assert (target / "profiledef.sh").is_file()


def test_directory_sink_leaves_nothing_behind_on_failure(tmp_path: Path, monkeypatch) -> None:
    """Ein Abbruch darf kein halbes Profil hinterlassen."""
    tree = ProfileTree()
    tree.add_file("profiledef.sh", "x", origin="generator")

    target = tmp_path / "profil"
    import archcustomiser.core.archiso.sinks as sinks

    def explode(*_args, **_kwargs):
        raise OSError("Platte voll")

    monkeypatch.setattr(sinks.os, "replace", explode)
    with pytest.raises(OSError):
        DirectorySink(target, iso_name="flos").write(tree)

    assert not target.exists()
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".")]
    assert leftovers == [], f"Zwischenverzeichnis blieb liegen: {leftovers}"


def test_gzip_header_has_no_timestamp(sample_tree: ProfileTree, tmp_path: Path) -> None:
    """Sonst waere das Archiv nie zweimal bytegleich."""
    payload = TarSink(tmp_path / "x.tar.gz", root_name="flos").to_bytes(sample_tree)
    # Bytes 4-8 des gzip-Kopfes sind der Zeitstempel.
    assert payload[4:8] == b"\x00\x00\x00\x00"
    gzip.decompress(payload)   # muss weiterhin lesbar sein
