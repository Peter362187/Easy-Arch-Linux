"""HTTP-Transport -- die einzige Stelle mit Netzwerkzugriff.

Als Protokoll formuliert, damit Tests einen ``FakeTransport`` einsetzen koennen
und die gesamte Fehlerbehandlung (Timeout, 503, 304, abgeschnittener Body)
deterministisch pruefbar wird, ohne je ins Netz zu gehen.
"""

from __future__ import annotations

import logging
import socket
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import formatdate, parsedate_to_datetime
from typing import Protocol

from .errors import MirrorError, NetworkUnavailable

log = logging.getLogger(__name__)

USER_AGENT = "ArchCustomiser/0.4 (+https://example.invalid)"
DEFAULT_CONNECT_TIMEOUT = 20.0
DEFAULT_READ_TIMEOUT = 120.0
MAX_RESPONSE_BYTES = 128 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""

    @property
    def not_modified(self) -> bool:
        return self.status == 304

    def header(self, name: str) -> str | None:
        lowered = name.lower()
        for key, value in self.headers.items():
            if key.lower() == lowered:
                return value
        return None

    @property
    def last_modified(self) -> datetime | None:
        raw = self.header("Last-Modified")
        if not raw:
            return None
        try:
            parsed = parsedate_to_datetime(raw)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed

    @property
    def etag(self) -> str | None:
        return self.header("ETag")


class Transport(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = DEFAULT_READ_TIMEOUT,
    ) -> HttpResponse: ...


def http_date(moment: datetime) -> str:
    return formatdate(moment.timestamp(), usegmt=True)


class UrllibTransport:
    """Umsetzung mit der Standardbibliothek -- keine externe Abhaengigkeit."""

    def __init__(self, user_agent: str = USER_AGENT) -> None:
        self.user_agent = user_agent

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = DEFAULT_READ_TIMEOUT,
    ) -> HttpResponse:
        if not url.startswith(("http://", "https://")):
            raise MirrorError(f"nicht unterstuetztes Schema: {url!r}")

        request = urllib.request.Request(url, method="GET")
        request.add_header("User-Agent", self.user_agent)
        request.add_header("Accept-Encoding", "identity")
        for key, value in (headers or {}).items():
            request.add_header(key, value)

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise MirrorError(f"Antwort groesser als {MAX_RESPONSE_BYTES} Bytes: {url}")
                # http.client liefert bei vorzeitigem Verbindungsende
                # stillschweigend einen kuerzeren Rumpf. Ohne diesen Vergleich
                # landete das Bruchstueck im Zwischenspeicher, der Parser warf
                # danach RepositoryDataError -- und der Spiegelwechsel unterblieb,
                # weil der Abruf ja "erfolgreich" war.
                erwartet = response.headers.get("Content-Length")
                if erwartet is not None:
                    try:
                        soll = int(erwartet)
                    except ValueError:
                        soll = -1
                    if soll >= 0 and len(body) != soll:
                        raise MirrorError(
                            f"Unvollstaendige Antwort von {url}: "
                            f"{len(body)} statt {soll} Bytes"
                        )
                return HttpResponse(
                    status=response.status,
                    headers=dict(response.headers.items()),
                    body=body,
                )
        except urllib.error.HTTPError as exc:
            if exc.code == 304:
                # Kein Fehler, sondern die erwuenschte Antwort auf einen
                # bedingten Abruf: die zwischengespeicherte Datei ist aktuell.
                return HttpResponse(status=304, headers=dict(exc.headers.items()))
            raise MirrorError(f"HTTP {exc.code} fuer {url}", status=exc.code, tried=(url,)) from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, (socket.gaierror, socket.timeout, TimeoutError, ConnectionError)):
                raise NetworkUnavailable(f"{url}: {reason}") from exc
            raise NetworkUnavailable(f"{url}: {reason}") from exc
        except TimeoutError as exc:
            raise NetworkUnavailable(f"{url}: Zeitueberschreitung") from exc
        except OSError as exc:
            raise NetworkUnavailable(f"{url}: {exc}") from exc
