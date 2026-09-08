"""Gemeinsame Testbausteine.

Alle Doubles hier sind so gebaut, dass sie *aufzeichnen*, was mit ihnen
geschieht. Das ist die Grundlage der Sicherheitstests: sie pruefen nicht nur,
dass eine ungueltige Eingabe abgelehnt wird, sondern auch, dass dabei
tatsaechlich kein Prozess gestartet und keine Verbindung geoeffnet wurde.
"""

from __future__ import annotations

import io
import tarfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from archcustomiser.core.packages.runner import CommandResult
from archcustomiser.core.packages.transport import HttpResponse

# ---------------------------------------------------------------------------
# Echte ALPM-Datenbanken erzeugen
# ---------------------------------------------------------------------------


def build_desc(**fields: Any) -> str:
    """Erzeugt eine ``desc``-Datei im echten ALPM-Abschnittsformat."""
    blocks: list[str] = []
    for key, value in fields.items():
        marker = f"%{key.upper()}%"
        values = value if isinstance(value, (list, tuple)) else [value]
        blocks.append(marker + "\n" + "\n".join(str(item) for item in values))
    return "\n\n".join(blocks) + "\n"


def build_fake_syncdb(entries: Sequence[Mapping[str, Any]]) -> bytes:
    """Baut ein echtes ``.db``-Archiv, kein Mock.

    Damit wird gegen das tatsaechliche Format getestet -- ein Mock wuerde
    Formataenderungen bei archiso oder pacman nicht bemerken.
    """
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for entry in entries:
            name = entry["name"]
            version = entry.get("version", "1.0-1")
            payload = build_desc(**entry).encode("utf-8")
            info = tarfile.TarInfo(f"{name}-{version}/desc")
            info.size = len(payload)
            info.mtime = 0
            archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


SAMPLE_ENTRIES: tuple[dict[str, Any], ...] = (
    {"name": "firefox", "version": "154.0-1", "arch": "x86_64", "desc": "Browser", "isize": "309215874"},
    {"name": "neovim", "version": "0.12.5-1", "arch": "x86_64", "desc": "Editor", "isize": "32000000"},
    {"name": "htop", "version": "3.5.3-1", "arch": "x86_64", "desc": "Monitor", "isize": "400000"},
    {"name": "base-devel", "version": "1-2", "arch": "any", "desc": "Meta-Paket", "depends": ["gcc", "make"]},
    {"name": "plasma-desktop", "version": "6.0-1", "arch": "x86_64", "groups": ["plasma"]},
    {"name": "plasma-workspace", "version": "6.0-1", "arch": "x86_64", "groups": ["plasma"]},
    {"name": "noto-fonts", "version": "1-1", "arch": "any", "provides": ["ttf-font"]},
    {"name": "ttf-dejavu", "version": "2-1", "arch": "any", "provides": ["ttf-font"]},
    {"name": "sddm", "version": "0.21-1", "arch": "x86_64", "provides": ["display-manager=0.21"]},
)


@pytest.fixture
def sample_db_bytes() -> bytes:
    return build_fake_syncdb(SAMPLE_ENTRIES)


@pytest.fixture
def sample_index(sample_db_bytes: bytes):
    from archcustomiser.core.packages.index import build_index
    from archcustomiser.core.packages.models import IndexMetadata, RepoMeta
    from archcustomiser.core.packages.syncdb import parse_syncdb

    packages = parse_syncdb(sample_db_bytes, "extra")
    meta = IndexMetadata(
        backend="test",
        arch="x86_64",
        repos=(
            RepoMeta(
                name="extra",
                source="test",
                fetched_at=datetime.now(UTC),
                last_modified=datetime.now(UTC),
                package_count=len(packages),
            ),
        ),
    )
    return build_index([("extra", packages)], meta)


# ---------------------------------------------------------------------------
# Doubles fuer I/O
# ---------------------------------------------------------------------------


class FakeTransport:
    """Spielt eine vorher festgelegte Antwortfolge ab und zeichnet Aufrufe auf."""

    def __init__(self, responses: Sequence[Any] | None = None) -> None:
        self.responses = list(responses or ())
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get(self, url: str, *, headers=None, timeout: float = 30.0) -> HttpResponse:
        self.calls.append((url, dict(headers or {})))
        if not self.responses:
            raise AssertionError(f"unerwarteter Abruf: {url}")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeRunner:
    """Ersetzt ``subprocess`` und merkt sich jede Argumentliste."""

    def __init__(self, results: Sequence[CommandResult] | None = None) -> None:
        self.results = list(results or ())
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv, *, timeout: float = 60.0, env=None) -> CommandResult:
        arguments = tuple(str(item) for item in argv)
        self.calls.append(arguments)
        if self.results:
            return self.results.pop(0)
        return CommandResult(argv=arguments, returncode=0, stdout="", stderr="")


@pytest.fixture
def fake_transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def fake_runner() -> FakeRunner:
    return FakeRunner()


# ---------------------------------------------------------------------------
# Katalog
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def catalog():
    from archcustomiser.core.catalog import load_catalog

    # Ohne Benutzer-Overlays: Tests duerfen nicht davon abhaengen, was auf dem
    # Rechner des Entwicklers zufaellig installiert ist.
    return load_catalog(include_user_overlays=False)


@pytest.fixture
def resolver(catalog):
    from archcustomiser.core.resolver import Resolver

    return Resolver(catalog)


@pytest.fixture
def profiles_dir() -> Path:
    """Die mitgelieferten Profile -- ueber die Paketfunktion, nicht ueber
    einen zusammengesetzten Pfad.

    Frueher stand hier ``parent.parent / "profiles"``. Das band den Test an
    das Repo-Layout und waere beim Verschieben der Daten ins Paket
    stillschweigend ins Leere gelaufen.
    """
    from archcustomiser.core.paths import bundled_profiles_dir

    return bundled_profiles_dir()


# ---------------------------------------------------------------------------
# Der Rechner des Entwicklers bleibt unberuehrt
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _eigene_verzeichnisse(tmp_path_factory):
    """Lenkt Konfigurations-, Zustands- und Zwischenspeicherpfade um.

    Ohne diese Umlenkung schrieben die Controller-Tests ihre Bauprotokolle in
    das *echte* Zustandsverzeichnis des Benutzers -- und ``_prune_build_logs``
    loeschte dort bei jedem Lauf die aeltesten echten Protokolle. Genauso
    haetten Overlay-Kataloge unter ``~/.config`` die Ergebnisse verfaelscht.

    Session-weit und ``autouse``: eine einzelne vergessene Markierung reichte
    sonst, um die Zusicherung wieder zu verlieren.
    """
    import os

    wurzel = tmp_path_factory.mktemp("benutzer")
    alt = {}
    variablen = {
        "HOME": str(wurzel),
        "USERPROFILE": str(wurzel),
        "LOCALAPPDATA": str(wurzel / "Local"),
        "APPDATA": str(wurzel / "Roaming"),
        "XDG_CONFIG_HOME": str(wurzel / "config"),
        "XDG_STATE_HOME": str(wurzel / "state"),
        "XDG_CACHE_HOME": str(wurzel / "cache"),
    }
    for name, wert in variablen.items():
        alt[name] = os.environ.get(name)
        os.environ[name] = wert
    try:
        yield wurzel
    finally:
        for name, wert in alt.items():
            if wert is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = wert


@pytest.fixture(scope="session")
def symlinks_moeglich(tmp_path_factory) -> bool:
    """Ob dieses System symbolische Verknuepfungen anlegen kann.

    Windows verlangt dafuer den Entwicklermodus oder Administratorrechte. Ohne
    sie scheitert jeder Test, der ein Profil als Verzeichnis schreibt -- und
    zwar an der Umgebung, nicht am Code. Ein gezielter Skip ist ehrlicher als
    elf rote Tests, die nichts ueber das Programm aussagen.
    """
    import os

    verzeichnis = tmp_path_factory.mktemp("symlinkprobe")
    ziel = verzeichnis / "ziel.txt"
    ziel.write_text("x", encoding="utf-8")
    try:
        os.symlink(ziel, verzeichnis / "verknuepfung")
    except (OSError, NotImplementedError):
        return False
    return True


@pytest.fixture
def braucht_symlinks(symlinks_moeglich: bool) -> None:
    if not symlinks_moeglich:
        pytest.skip(
            "Dieses System kann keine symbolischen Verknuepfungen anlegen "
            "(Windows ohne Entwicklermodus). Der Profilbaum selbst wird von "
            "den TarSink-Tests geprueft."
        )
