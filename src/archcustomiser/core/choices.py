"""Auswahllisten fuer Formularfelder (Locales, Tastaturbelegungen, Zeitzonen).

Ein Katalogfeld sagt ``choices_from: timezones``; die Liste kommt von hier.

Auf Arch werden die echten Systemlisten gelesen. Auf Windows -- wo entwickelt
wird -- gibt es weder ``/usr/share/i18n/SUPPORTED`` noch ``localectl``, deshalb
liegt ein kuratierter Ersatzdatensatz bei. Er muss nicht vollstaendig sein: die
Felder sind editierbare Comboboxen, und der Validator prueft die Syntax.
Zeitzonen kommen ohnehin aus ``zoneinfo`` und sind damit ueberall vollstaendig.
"""

from __future__ import annotations

import functools
import logging
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

_SUPPORTED_LOCALES = Path("/usr/share/i18n/SUPPORTED")
_SUBPROCESS_TIMEOUT = 5.0

# Ersatzliste fuer Systeme ohne /usr/share/i18n/SUPPORTED.
_FALLBACK_LOCALES: tuple[str, ...] = (
    "de_DE.UTF-8", "de_AT.UTF-8", "de_CH.UTF-8",
    "en_US.UTF-8", "en_GB.UTF-8", "en_AU.UTF-8", "en_CA.UTF-8",
    "fr_FR.UTF-8", "fr_CA.UTF-8", "es_ES.UTF-8", "it_IT.UTF-8",
    "nl_NL.UTF-8", "pl_PL.UTF-8", "pt_BR.UTF-8", "pt_PT.UTF-8",
    "ru_RU.UTF-8", "cs_CZ.UTF-8", "da_DK.UTF-8", "fi_FI.UTF-8",
    "sv_SE.UTF-8", "nb_NO.UTF-8", "tr_TR.UTF-8", "hu_HU.UTF-8",
    "ro_RO.UTF-8", "el_GR.UTF-8", "uk_UA.UTF-8", "ja_JP.UTF-8",
    "ko_KR.UTF-8", "zh_CN.UTF-8", "zh_TW.UTF-8", "C.UTF-8",
)

_FALLBACK_KEYMAPS: tuple[str, ...] = (
    "de-latin1", "de-latin1-nodeadkeys", "de_CH-latin1", "de",
    "us", "us-acentos", "uk", "fr", "fr-latin1", "es", "it",
    "dvorak", "colemak", "neo", "pt-latin1", "br-abnt2",
    "nl", "pl", "cz-lat2", "dk-latin1", "fi", "sv-latin1",
    "no-latin1", "hu", "ro", "ru", "tr_q-latin5", "jp106",
)


@functools.lru_cache(maxsize=1)
def locales() -> tuple[str, ...]:
    """Verfuegbare Locales. UTF-8 zuerst, alles andere danach."""
    if _SUPPORTED_LOCALES.is_file():
        try:
            found: list[str] = []
            for line in _SUPPORTED_LOCALES.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                # Format: "de_DE.UTF-8 UTF-8"
                name = line.split()[0]
                if name:
                    found.append(name)
            if found:
                return _sort_preferring_utf8(found)
        except OSError as exc:
            log.debug("SUPPORTED nicht lesbar (%s), verwende Ersatzliste", exc)
    return _FALLBACK_LOCALES


def _sort_preferring_utf8(names: list[str]) -> tuple[str, ...]:
    unique = sorted(set(names))
    utf8 = [name for name in unique if "UTF-8" in name.upper()]
    rest = [name for name in unique if "UTF-8" not in name.upper()]
    return tuple(utf8 + rest)


@functools.lru_cache(maxsize=1)
def keymaps() -> tuple[str, ...]:
    """Konsolen-Tastaturbelegungen.

    ``localectl list-keymaps`` ist die dokumentierte Abfrage. Der Aufruf laeuft
    ohne Shell, mit fester Argumentliste und Zeitlimit -- er kann nichts
    ausfuehren, was nicht hier steht.
    """
    executable = shutil.which("localectl")
    if executable:
        try:
            result = subprocess.run(
                [executable, "list-keymaps", "--no-pager"],
                capture_output=True,
                text=True,
                timeout=_SUBPROCESS_TIMEOUT,
                check=False,
                env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"},
            )
            if result.returncode == 0:
                found = [line.strip() for line in result.stdout.splitlines() if line.strip()]
                if found:
                    return tuple(sorted(set(found)))
        except (OSError, subprocess.SubprocessError) as exc:
            log.debug("localectl fehlgeschlagen (%s), verwende Ersatzliste", exc)

    # Zweiter Weg ohne Subprozess: die Kartendateien direkt auflisten.
    root = Path("/usr/share/kbd/keymaps")
    if root.is_dir():
        try:
            found = sorted(
                {
                    path.name.split(".")[0]
                    for path in root.rglob("*.map.gz")
                    if path.is_file()
                }
            )
            if found:
                return tuple(found)
        except OSError:
            pass
    return _FALLBACK_KEYMAPS


@functools.lru_cache(maxsize=1)
def timezones() -> tuple[str, ...]:
    """Zeitzonen aus der Standardbibliothek -- auf jeder Plattform vollstaendig."""
    try:
        from zoneinfo import available_timezones

        found = sorted(available_timezones())
        if found:
            return tuple(found)
    except Exception as exc:  # tzdata fehlt in manchen schlanken Installationen
        log.warning("Zeitzonendatenbank nicht verfuegbar (%s)", exc)
    return (
        "Europe/Berlin", "Europe/Vienna", "Europe/Zurich", "Europe/London",
        "Europe/Paris", "Europe/Madrid", "Europe/Rome", "Europe/Warsaw",
        "UTC", "America/New_York", "America/Chicago", "America/Los_Angeles",
        "Asia/Tokyo", "Asia/Shanghai", "Australia/Sydney",
    )


_REGISTRY: dict[str, Callable[[], tuple[str, ...]]] = {
    "locales": locales,
    "keymaps": keymaps,
    "timezones": timezones,
}


def get_choices(name: str) -> tuple[str, ...]:
    provider = _REGISTRY.get(name)
    if provider is None:
        log.warning("Unbekannte Auswahlliste %r im Katalog", name)
        return ()
    try:
        return provider()
    except Exception:
        log.exception("Auswahlliste %r konnte nicht geladen werden", name)
        return ()
