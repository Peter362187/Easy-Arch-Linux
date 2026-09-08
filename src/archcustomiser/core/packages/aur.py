"""AUR-Abfrage ueber die RPC-Schnittstelle v5.

Standardmaessig abgeschaltet -- die Spezifikation nennt AUR ausdruecklich
optional (Abschnitt 2).

Zwei Dinge, die man beim Einschalten wissen muss:

* Die RPC-Schnittstelle kann echtes Batching (``arg[]`` mehrfach), im Gegensatz
  zur Paket-Web-API. Fehlende Namen fehlen einfach im Ergebnis.
* **pacstrap kann AUR-Pakete nicht installieren.** Diese Schicht prueft und
  kennzeichnet sie nur; sie werden getrennt gefuehrt, damit die
  Profilerzeugung sie nicht versehentlich in ``packages.x86_64`` schreibt und
  der Build daran scheitert.

Aus den Antwortdaten wird nichts nachgeladen und nichts ausgefuehrt -- kein
PKGBUILD, keine URL aus dem Ergebnis wird aufgerufen.
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from .errors import BackendUnavailable
from .models import PackageInfo
from .names import InvalidPackageName, validate_name
from .transport import Transport, UrllibTransport

log = logging.getLogger(__name__)

AUR_RPC = "https://aur.archlinux.org/rpc/v5"
CHUNK_SIZE = 150   # begrenzt durch die URL-Laenge
REQUEST_TIMEOUT = 20.0


class AurInfo:
    """Zusatzangaben, die es nur im AUR gibt."""

    __slots__ = ("orphaned", "out_of_date", "package", "popularity", "votes")

    def __init__(
        self,
        package: PackageInfo,
        out_of_date: datetime | None,
        orphaned: bool,
        votes: int,
        popularity: float,
    ) -> None:
        self.package = package
        self.out_of_date = out_of_date
        self.orphaned = orphaned
        self.votes = votes
        self.popularity = popularity

    @property
    def warnings(self) -> tuple[str, ...]:
        found: list[str] = []
        if self.out_of_date:
            found.append(
                f"seit {self.out_of_date:%d.%m.%Y} als veraltet gemeldet"
            )
        if self.orphaned:
            found.append("verwaist -- niemand pflegt das Paket")
        return tuple(found)


def _chunks(items: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


class AurClient:
    def __init__(self, transport: Transport | None = None) -> None:
        self.transport = transport or UrllibTransport()

    def info(self, names: Sequence[str]) -> dict[str, AurInfo]:
        """Sammelabfrage. Nicht gefundene Namen fehlen im Ergebnis."""
        valid: list[str] = []
        for name in names:
            try:
                valid.append(validate_name(name))
            except InvalidPackageName:
                continue
        if not valid:
            return {}

        found: dict[str, AurInfo] = {}
        for chunk in _chunks(valid, CHUNK_SIZE):
            query = urllib.parse.urlencode([("arg[]", name) for name in chunk])
            url = f"{AUR_RPC}/info?{query}"
            try:
                response = self.transport.get(url, timeout=REQUEST_TIMEOUT)
            except BackendUnavailable as exc:
                log.info("AUR nicht erreichbar: %s", exc.technical)
                return found
            if response.status != 200:
                log.info("AUR antwortet mit HTTP %s", response.status)
                return found
            try:
                payload = json.loads(response.body.decode("utf-8", errors="replace"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                log.warning("AUR-Antwort nicht lesbar: %s", exc)
                return found
            for entry in payload.get("results") or ():
                parsed = self._parse(entry)
                if parsed is not None:
                    found[parsed.package.name] = parsed
        return found

    @staticmethod
    def _parse(entry: object) -> AurInfo | None:
        if not isinstance(entry, dict):
            return None
        raw_name = entry.get("Name")
        if not isinstance(raw_name, str):
            return None
        try:
            # Auch der Name aus der Antwort wird geprueft, bevor er
            # weiterverwendet wird -- die Antwort ist fremde Eingabe.
            name = validate_name(raw_name)
        except InvalidPackageName:
            log.warning("AUR liefert einen unzulaessigen Paketnamen: %r", raw_name)
            return None

        out_of_date = None
        stamp = entry.get("OutOfDate")
        if isinstance(stamp, int):
            try:
                out_of_date = datetime.fromtimestamp(stamp, tz=UTC)
            except (OSError, OverflowError, ValueError):
                out_of_date = None

        return AurInfo(
            package=PackageInfo(
                name=name,
                version=str(entry.get("Version") or ""),
                repo="aur",
                description=str(entry.get("Description") or ""),
                depends=tuple(str(item) for item in entry.get("Depends") or ()),
            ),
            out_of_date=out_of_date,
            orphaned=entry.get("Maintainer") is None,
            votes=int(entry.get("NumVotes") or 0),
            popularity=float(entry.get("Popularity") or 0.0),
        )
