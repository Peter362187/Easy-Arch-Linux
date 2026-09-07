"""Tests des Zwischenspeichers.

Schwerpunkt: Ein abgebrochener oder beschaedigter Schreibvorgang darf nie zu
stillen Falschdaten fuehren. Lieber neu laden als etwas Halbes verwenden.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from archcustomiser.core.packages.cache import PackageCache

from .conftest import build_fake_syncdb

DATA = build_fake_syncdb([{"name": "firefox", "version": "1-1"}])


@pytest.fixture
def cache(tmp_path) -> PackageCache:
    return PackageCache(root=tmp_path)


def store(cache: PackageCache, data: bytes = DATA):
    return cache.store(
        "core",
        data,
        url="https://example.invalid/core.db",
        etag='"abc"',
        last_modified=datetime(2026, 8, 28, 20, 4, tzinfo=UTC),
        package_count=1,
    )


def test_round_trip(cache) -> None:
    entry = store(cache)
    loaded = cache.load("core")
    assert loaded is not None
    assert loaded.read() == DATA
    assert loaded.etag == '"abc"'
    assert loaded.last_modified == entry.last_modified


def test_missing_entry_returns_none(cache) -> None:
    assert cache.load("core") is None


def test_checksum_mismatch_discards_the_entry(cache) -> None:
    """Eine halb geschriebene Datei darf nicht als gueltig durchgehen."""
    store(cache)
    cache.db_path("core").write_bytes(b"veraendert")
    assert cache.load("core") is None
    assert not cache.db_path("core").exists()


def test_unreadable_metadata_discards_the_entry(cache) -> None:
    store(cache)
    cache.meta_path("core").write_text("kein json {", encoding="utf-8")
    assert cache.load("core") is None


def test_old_schema_version_is_discarded(cache) -> None:
    store(cache)
    meta = json.loads(cache.meta_path("core").read_text(encoding="utf-8"))
    meta["schema_version"] = 0
    cache.meta_path("core").write_text(json.dumps(meta), encoding="utf-8")
    assert cache.load("core") is None


def test_touch_keeps_the_repo_timestamp_but_updates_the_check_time(cache) -> None:
    """Nach 304 aendert sich nur, wann wir nachgesehen haben."""
    entry = store(cache)
    refreshed = cache.touch(entry)
    assert refreshed.last_modified == entry.last_modified
    assert refreshed.fetched_at >= entry.fetched_at


def test_clear_removes_everything(cache) -> None:
    store(cache)
    assert cache.clear() >= 2
    assert cache.load("core") is None


def test_clear_on_an_empty_cache_is_harmless(cache) -> None:
    assert cache.clear() == 0


def test_stored_file_is_byte_identical(cache) -> None:
    """Die rohe Datenbank wird unveraendert abgelegt, nicht umgewandelt."""
    store(cache)
    assert cache.db_path("core").read_bytes() == DATA


def test_no_temporary_files_are_left_behind(cache, tmp_path) -> None:
    store(cache)
    leftovers = [path.name for path in cache.root.glob("*.tmp")]
    assert leftovers == []


def test_lock_detects_a_stale_lockfile(cache) -> None:
    import os
    import time

    lock_path = cache.root / ".lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("999999\n0\n", encoding="utf-8")
    old = time.time() - 3600
    os.utime(lock_path, (old, old))

    with cache.lock() as lock:
        assert lock.acquired


def test_metadata_is_json_not_a_binary_blob(cache) -> None:
    store(cache)
    document = json.loads(cache.meta_path("core").read_text(encoding="utf-8"))
    assert document["repo"] == "core"
    assert document["package_count"] == 1
