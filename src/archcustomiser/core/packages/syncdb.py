"""Parser fuer ALPM-Sync-Datenbanken (``core.db`` und Verwandte).

Eine ``.db`` ist ein gzip-komprimiertes tar-Archiv mit einem Verzeichnis je
Paket, darin eine Datei ``desc`` im Abschnittsformat::

    %NAME%
    firefox

    %VERSION%
    154.0.1-1

    %GROUPS%
    xorg
    xorg-apps

Der Weg ueber die Sync-Datenbank statt ueber die Web-API ist eine bewusste
Entscheidung mit drei Gruenden:

1. **Die Web-API kann kein Batching.** ``?name=a&name=b`` liefert nur ``b``
   (Django nimmt bei einem CharField den letzten Wert). Wer darauf baut, meldet
   existierende Pakete still als "nicht gefunden".
2. **Gruppen und virtuelle Pakete sind ueber die API nicht abfragbar.** Hier
   fallen beide Indizes im selben Durchgang mit ab.
3. **Kosten.** Drei Anfragen pro Sitzung statt einer pro Paketname -- und
   danach ist die Pruefung offline und ohne messbare Verzoegerung moeglich.

Der Parser benutzt ausschliesslich die Standardbibliothek. Er ist damit auf
Windows identisch verwendbar wie auf Arch, was die Entwicklung ueberhaupt erst
moeglich macht.

Sicherheit: Das Archiv stammt zwar von einem Spiegelserver, wird aber wie eine
fremde Datei behandelt. Es wird nichts entpackt und nichts geschrieben -- nur
im Speicher gelesen, mit Groessenobergrenzen gegen Dekompressionsbomben.
"""

from __future__ import annotations

import io
import logging
import tarfile
from collections.abc import Iterator
from datetime import UTC, datetime

from .errors import RepositoryDataError
from .models import PackageInfo, Provide
from .names import split_provide

log = logging.getLogger(__name__)

# Grenzen gegen Dekompressionsbomben. extra.db ist derzeit rund 9 MB
# komprimiert und entpackt sich auf gut 60 MB -- 512 MB lassen also reichlich
# Luft und stoppen trotzdem ein praepariertes Archiv.
MAX_TOTAL_UNCOMPRESSED = 512 * 1024 * 1024
MAX_MEMBER_SIZE = 4 * 1024 * 1024

_LIST_KEYS = frozenset(
    {
        "%GROUPS%",
        "%PROVIDES%",
        "%DEPENDS%",
        "%OPTDEPENDS%",
        "%MAKEDEPENDS%",
        "%CHECKDEPENDS%",
        "%CONFLICTS%",
        "%REPLACES%",
        "%LICENSE%",
    }
)


def parse_desc(text: str) -> dict[str, list[str]]:
    """Zerlegt eine ``desc``-Datei in ihr Abschnittsformat."""
    fields: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            current = None
            continue
        if line.startswith("%") and line.endswith("%"):
            current = line
            fields.setdefault(current, [])
            continue
        if current is not None:
            fields[current].append(line)
    return fields


def _first(fields: dict[str, list[str]], key: str) -> str:
    values = fields.get(key)
    return values[0] if values else ""


def _int_or_none(fields: dict[str, list[str]], key: str) -> int | None:
    raw = _first(fields, key)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _date_or_none(fields: dict[str, list[str]], key: str) -> datetime | None:
    raw = _first(fields, key)
    try:
        return datetime.fromtimestamp(int(raw), tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _to_package(fields: dict[str, list[str]], repo: str) -> PackageInfo | None:
    name = _first(fields, "%NAME%")
    if not name:
        return None
    return PackageInfo(
        name=name,
        version=_first(fields, "%VERSION%"),
        repo=repo,
        arch=_first(fields, "%ARCH%"),
        description=_first(fields, "%DESC%"),
        groups=tuple(fields.get("%GROUPS%", ())),
        provides=tuple(
            Provide(*split_provide(entry)) for entry in fields.get("%PROVIDES%", ())
        ),
        depends=tuple(fields.get("%DEPENDS%", ())),
        optdepends=tuple(fields.get("%OPTDEPENDS%", ())),
        replaces=tuple(fields.get("%REPLACES%", ())),
        conflicts=tuple(fields.get("%CONFLICTS%", ())),
        installed_size=_int_or_none(fields, "%ISIZE%"),
        compressed_size=_int_or_none(fields, "%CSIZE%"),
        build_date=_date_or_none(fields, "%BUILDDATE%"),
    )


def _safe_members(archive: tarfile.TarFile, repo: str) -> Iterator[tarfile.TarInfo]:
    """Nur regulaere ``desc``-Dateien mit unauffaelligem Pfad."""
    total = 0
    for member in archive:
        if not member.isfile():
            continue
        name = member.name
        # Absolute Pfade und '..' koennen in einem Archiv vom Netz stehen. Wir
        # entpacken zwar nichts, aber ein solches Archiv ist nicht
        # vertrauenswuerdig und wird uebersprungen.
        if name.startswith("/") or ".." in name.split("/"):
            log.warning("%s: Archiveintrag mit verdaechtigem Pfad uebersprungen: %r", repo, name)
            continue
        if not name.endswith("/desc"):
            continue
        if member.size > MAX_MEMBER_SIZE:
            log.warning("%s: Eintrag %r ist unplausibel gross und wird uebersprungen", repo, name)
            continue
        total += member.size
        if total > MAX_TOTAL_UNCOMPRESSED:
            raise RepositoryDataError(
                repo,
                f"entpackte Groesse ueberschreitet {MAX_TOTAL_UNCOMPRESSED} Bytes",
            )
        yield member


def parse_syncdb(data: bytes, repo: str) -> tuple[PackageInfo, ...]:
    """Liest eine komplette ``.db`` aus dem Speicher.

    Wirft ``RepositoryDataError``, wenn das Archiv unbrauchbar ist -- niemals
    eine rohe ``tarfile``-Exception, damit der Aufrufer nur einen Fehlertyp
    kennen muss.
    """
    if not data:
        raise RepositoryDataError(repo, "leere Datei")

    packages: list[PackageInfo] = []
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
            for member in _safe_members(archive, repo):
                handle = archive.extractfile(member)
                if handle is None:
                    continue
                try:
                    raw = handle.read(MAX_MEMBER_SIZE)
                finally:
                    handle.close()
                fields = parse_desc(raw.decode("utf-8", errors="replace"))
                package = _to_package(fields, repo)
                if package is None:
                    log.debug("%s: %r enthaelt kein %%NAME%% und wird uebersprungen", repo, member.name)
                    continue
                packages.append(package)
    except RepositoryDataError:
        raise
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise RepositoryDataError(repo, f"{type(exc).__name__}: {exc}") from exc
    except Exception as exc:
        # gzip und lzma reichen bei beschaedigten Daten zlib.error bzw.
        # LZMAError durch -- weder OSError noch TarError. Die Ausnahme
        # durchlief bisher die gesamte Schicht bis in den Worker, ohne dass
        # jemand den defekten Zwischenspeicher verwarf: jeder Start scheiterte
        # danach erneut, bis die Frist ablief.
        raise RepositoryDataError(repo, f"{type(exc).__name__}: {exc}") from exc

    if not packages:
        raise RepositoryDataError(repo, "Archiv enthaelt keine lesbaren Paketeintraege")

    log.debug("%s: %d Pakete gelesen", repo, len(packages))
    return tuple(packages)
