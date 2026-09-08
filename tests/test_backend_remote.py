"""Tests des Netz-Backends -- vollstaendig ohne Netzwerk.

Deckt die Faelle ab, die im Betrieb tatsaechlich auftreten: bedingter Abruf mit
304, Ausweichen auf einen anderen Spiegelserver, Netzausfall mit vorhandenem
Zwischenspeicher, beschaedigte Datenbank.
"""

from __future__ import annotations

import pytest

from archcustomiser.core.packages.backend import PackageConfig, RefreshPolicy
from archcustomiser.core.packages.backend_remote import RemoteIndexBackend, read_system_mirrors
from archcustomiser.core.packages.cache import PackageCache
from archcustomiser.core.packages.errors import BackendUnavailable, NetworkUnavailable
from archcustomiser.core.packages.transport import HttpResponse

from .conftest import FakeTransport, build_fake_syncdb

ENTRIES = [{"name": "firefox", "version": "1-1", "arch": "x86_64"}]


@pytest.fixture
def config() -> PackageConfig:
    return PackageConfig(repos=("core",), mirrors=("https://mirror.invalid/$repo/os/$arch",))


@pytest.fixture
def cache(tmp_path) -> PackageCache:
    return PackageCache(root=tmp_path)


def ok_response(data: bytes) -> HttpResponse:
    return HttpResponse(
        status=200,
        headers={"Last-Modified": "Fri, 28 Aug 2026 20:04:00 GMT", "ETag": '"abc"'},
        body=data,
    )


def test_download_and_index(config, cache) -> None:
    transport = FakeTransport([ok_response(build_fake_syncdb(ENTRIES))])
    backend = RemoteIndexBackend(config, transport=transport, cache=cache)
    index = backend.load_index(policy=RefreshPolicy.FORCE)
    assert "firefox" in index
    assert index.meta.repo_names == ("core",)


def test_conditional_request_uses_cached_data(config, cache) -> None:
    """Nach einer 304-Antwort wird der gespeicherte Stand weiterverwendet."""
    data = build_fake_syncdb(ENTRIES)
    transport = FakeTransport([ok_response(data), HttpResponse(status=304)])
    backend = RemoteIndexBackend(config, transport=transport, cache=cache)

    backend.load_index(policy=RefreshPolicy.FORCE)
    index = backend.load_index(policy=RefreshPolicy.FORCE)

    assert "firefox" in index
    # Der zweite Abruf muss die Bedingungs-Header gesetzt haben.
    _url, headers = transport.calls[1]
    assert "If-None-Match" in headers or "If-Modified-Since" in headers


def test_304_keeps_the_real_repo_timestamp(config, cache) -> None:
    """'Zuletzt aktualisiert' meint den Repo-Stand, nicht unseren Abrufzeitpunkt."""
    data = build_fake_syncdb(ENTRIES)
    transport = FakeTransport([ok_response(data), HttpResponse(status=304)])
    backend = RemoteIndexBackend(config, transport=transport, cache=cache)

    first = backend.load_index(policy=RefreshPolicy.FORCE)
    stamp_before = first.meta.data_updated_at
    second = backend.load_index(policy=RefreshPolicy.FORCE)
    assert second.meta.data_updated_at == stamp_before


def test_falls_back_to_next_mirror(cache) -> None:
    config = PackageConfig(
        repos=("core",),
        mirrors=("https://kaputt.invalid/$repo/os/$arch", "https://gut.invalid/$repo/os/$arch"),
    )
    transport = FakeTransport(
        [NetworkUnavailable("erster Server nicht erreichbar"), ok_response(build_fake_syncdb(ENTRIES))]
    )
    backend = RemoteIndexBackend(config, transport=transport, cache=cache)
    backend._mirrors = config.mirrors      # Systemliste ausblenden
    index = backend.load_index(policy=RefreshPolicy.FORCE)
    assert "firefox" in index
    assert len(transport.calls) == 2


def test_network_failure_uses_cache(config, cache) -> None:
    """Ohne Netz, aber mit gespeichertem Stand, wird weitergearbeitet."""
    data = build_fake_syncdb(ENTRIES)
    transport = FakeTransport([ok_response(data)])
    backend = RemoteIndexBackend(config, transport=transport, cache=cache)
    backend.load_index(policy=RefreshPolicy.FORCE)

    offline = FakeTransport([NetworkUnavailable("kein Netz")])
    backend2 = RemoteIndexBackend(config, transport=offline, cache=cache)
    index = backend2.load_index(policy=RefreshPolicy.FORCE)
    assert "firefox" in index


def test_no_network_and_no_cache_raises(config, cache) -> None:
    """Ohne beides gibt es einen Fehler -- keinen leeren Index.

    Ein leerer Index waere fatal: jedes Paket erschiene als 'existiert nicht'.
    """
    transport = FakeTransport([NetworkUnavailable("kein Netz")])
    backend = RemoteIndexBackend(config, transport=transport, cache=cache)
    with pytest.raises(BackendUnavailable):
        backend.load_index(policy=RefreshPolicy.FORCE)


def test_corrupt_database_is_discarded(config, cache) -> None:
    transport = FakeTransport([ok_response(b"kein gueltiges archiv" * 50)])
    backend = RemoteIndexBackend(config, transport=transport, cache=cache)
    with pytest.raises(BackendUnavailable):
        backend.load_index(policy=RefreshPolicy.FORCE)
    assert cache.load("core") is None


def test_never_policy_does_not_touch_the_network(config, cache) -> None:
    transport = FakeTransport([])       # jeder Abruf waere ein Fehler
    backend = RemoteIndexBackend(config, transport=transport, cache=cache)
    with pytest.raises(BackendUnavailable):
        backend.load_index(policy=RefreshPolicy.NEVER)
    assert transport.calls == []


def test_mirror_url_substitution() -> None:
    config = PackageConfig(arch="x86_64")
    url = config.mirror_url("https://example.invalid/$repo/os/$arch", "extra")
    assert url == "https://example.invalid/extra/os/x86_64/extra.db"


def test_read_system_mirrors_ignores_everything_but_server_lines(tmp_path) -> None:
    path = tmp_path / "mirrorlist"
    path.write_text(
        "\n".join(
            [
                "## Kommentar",
                "#Server = https://auskommentiert.invalid/$repo/os/$arch",
                "Server = https://echt.invalid/$repo/os/$arch",
                "SomethingElse = https://ignorieren.invalid",
                "Server = ftp://falsches-schema.invalid/x",
            ]
        ),
        encoding="utf-8",
    )
    assert read_system_mirrors(path) == ("https://echt.invalid/$repo/os/$arch",)
