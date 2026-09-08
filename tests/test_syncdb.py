"""Tests des ALPM-Parsers gegen echte Archivdaten."""

from __future__ import annotations

import io
import tarfile

import pytest

from archcustomiser.core.packages.errors import RepositoryDataError
from archcustomiser.core.packages.syncdb import parse_desc, parse_syncdb

from .conftest import build_desc, build_fake_syncdb


def test_parse_desc_sections() -> None:
    text = build_desc(name="firefox", version="1-1", groups=["a", "b"])
    fields = parse_desc(text)
    assert fields["%NAME%"] == ["firefox"]
    assert fields["%GROUPS%"] == ["a", "b"]


def test_parse_syncdb_reads_all_fields(sample_db_bytes: bytes) -> None:
    packages = parse_syncdb(sample_db_bytes, "extra")
    by_name = {package.name: package for package in packages}

    assert by_name["firefox"].version == "154.0-1"
    assert by_name["firefox"].repo == "extra"
    assert by_name["firefox"].installed_size == 309215874
    assert by_name["base-devel"].depends == ("gcc", "make")
    assert by_name["plasma-desktop"].groups == ("plasma",)
    assert by_name["noto-fonts"].provides[0].name == "ttf-font"
    # Versionierte provides werden zerlegt, nicht als Ganzes gespeichert.
    assert by_name["sddm"].provides[0].name == "display-manager"
    assert by_name["sddm"].provides[0].version == "0.21"


def test_empty_data_is_rejected() -> None:
    with pytest.raises(RepositoryDataError):
        parse_syncdb(b"", "core")


def test_truncated_gzip_is_rejected(sample_db_bytes: bytes) -> None:
    with pytest.raises(RepositoryDataError):
        parse_syncdb(sample_db_bytes[: len(sample_db_bytes) // 2], "core")


def test_garbage_is_rejected() -> None:
    with pytest.raises(RepositoryDataError):
        parse_syncdb(b"das ist kein tar-archiv" * 100, "core")


def test_entry_without_name_is_skipped_not_fatal() -> None:
    """Ein defekter Eintrag darf nicht die ganze Datenbank unbrauchbar machen."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for filename, text in (
            ("kaputt-1/desc", "%VERSION%\n1-1\n"),
            ("firefox-1/desc", build_desc(name="firefox", version="1-1")),
        ):
            payload = text.encode("utf-8")
            info = tarfile.TarInfo(filename)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))

    packages = parse_syncdb(buffer.getvalue(), "extra")
    assert [package.name for package in packages] == ["firefox"]


def test_archive_with_traversal_paths_is_skipped() -> None:
    """Ein Archiv aus dem Netz darf keine Pfade ausserhalb vorschlagen."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for filename in ("../../evil/desc", "/absolut/desc"):
            payload = build_desc(name="boese", version="1-1").encode("utf-8")
            info = tarfile.TarInfo(filename)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        payload = build_desc(name="gut", version="1-1").encode("utf-8")
        info = tarfile.TarInfo("gut-1/desc")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))

    packages = parse_syncdb(buffer.getvalue(), "extra")
    assert [package.name for package in packages] == ["gut"]


def test_oversized_member_is_skipped() -> None:
    """Schutz vor Dekompressionsbomben."""
    from archcustomiser.core.packages import syncdb

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        huge = ("x" * 1024).encode("utf-8")
        info = tarfile.TarInfo("riesig-1/desc")
        info.size = len(huge)
        archive.addfile(info, io.BytesIO(huge))
        payload = build_desc(name="klein", version="1-1").encode("utf-8")
        info = tarfile.TarInfo("klein-1/desc")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))

    original = syncdb.MAX_MEMBER_SIZE
    syncdb.MAX_MEMBER_SIZE = 512
    try:
        packages = parse_syncdb(buffer.getvalue(), "extra")
    finally:
        syncdb.MAX_MEMBER_SIZE = original
    assert [package.name for package in packages] == ["klein"]


def test_non_desc_members_are_ignored() -> None:
    data = build_fake_syncdb([{"name": "firefox", "version": "1-1"}])
    packages = parse_syncdb(data, "extra")
    assert len(packages) == 1
