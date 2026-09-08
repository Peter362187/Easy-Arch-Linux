"""Logging mit Passwort-Redaktion (Spec §12/§13).

Der Filter greift an zwei Stellen:
* registrierte Secret-Klartexte (über ``secrets.register_observer``)
* alles, was wie ein crypt(3)-Hash aussieht -- auch Hashes gehören nicht ins Log

Wichtig: gefiltert werden ``record.msg`` *und* ``record.args``, sonst rutscht
``log.info("user=%s pw=%s", name, pw)`` ungefiltert durch.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import re
from collections import Counter
from pathlib import Path

from . import secrets
from .paths import ensure_dir, state_dir

MASK = "***"
LOG_FILENAME = "archcustomiser.log"

# crypt(3)-Hashes: yescrypt ($y$), gost-yescrypt ($gy$), sha512 ($6$), sha256
# ($5$), bcrypt ($2b$), md5 ($1$).
_HASH_RE = re.compile(r"\$(?:y|gy|7|6|5|2[aby]?|1)\$[^\s\"']{4,}")


MIN_LITERAL_LENGTH = 3


class SecretRedactionFilter(logging.Filter):
    """Ersetzt bekannte Geheimnisse in jedem Log-Record.

    Gezaehlt statt gesammelt: mehrere ``Secret``-Objekte koennen denselben Wert
    tragen, und das Loeschen des einen darf den Schutz des anderen nicht
    aufheben. Erst wenn der letzte Traeger verbrannt ist, verschwindet das
    Literal aus der Liste.

    Die Zaehlung ist zugleich der Grund, warum ``Secret.burn()` ueberhaupt etwas
    bewirkt: vorher wanderte jeder Klartext hier hinein und blieb bis zum
    Prozessende liegen.
    """

    def __init__(self) -> None:
        super().__init__()
        self._literals: Counter[str] = Counter()

    def add_literal(self, value: str) -> None:
        # Sehr kurze Werte würden zu viele False Positives maskieren.
        if value and len(value) >= MIN_LITERAL_LENGTH:
            self._literals[value] += 1

    def discard_literal(self, value: str) -> None:
        """Gegenstueck zu ``add_literal`` -- aufgerufen von ``Secret.burn()``."""
        if not value or value not in self._literals:
            return
        self._literals[value] -= 1
        if self._literals[value] <= 0:
            del self._literals[value]

    def known_literals(self) -> tuple[str, ...]:
        """Nur fuer Tests: welche Werte gerade maskiert wuerden."""
        return tuple(self._literals)

    def _scrub(self, text: str) -> str:
        # Laengste zuerst: sonst zerlegt ein kurzes Literal ein langes, bevor
        # dieses ueberhaupt geprueft wird.
        for literal in sorted(self._literals, key=len, reverse=True):
            if literal in text:
                text = text.replace(literal, MASK)
        return _HASH_RE.sub(MASK, text)

    def _scrub_any(self, value: object) -> object:
        if isinstance(value, str):
            return self._scrub(value)
        if isinstance(value, (list, tuple)):
            return type(value)(self._scrub_any(item) for item in value)
        if isinstance(value, dict):
            return {key: self._scrub_any(item) for key, item in value.items()}
        # Alles andere wird erst im Formatter zu Text -- also hier schon. Ein
        # Ausnahme- oder Pfadobjekt blieb sonst unmaskiert: aus
        # ``log.warning("Fehler: %s", OSError(passwort))`` wurde eine
        # Protokollzeile mit dem Klartext darin.
        text = str(value)
        maskiert = self._scrub(text)
        return maskiert if maskiert != text else value

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._scrub(record.msg)
        if record.args:
            record.args = self._scrub_any(record.args)  # type: ignore[assignment]
        # Ein Traceback laeuft am Argumentweg voellig vorbei: er entsteht erst
        # im Formatter aus ``exc_info``. Genau dort steht aber der Text jeder
        # Ausnahme -- und ein ``ValueError("Passwort ... ungueltig")`` waere im
        # Protokoll gelandet, obwohl jede gewoehnliche Zeile maskiert wird.
        if record.exc_info and record.exc_info[1] is not None:
            record.exc_text = self._scrub(self._formatiere(record))
            record.exc_info = None
        if record.stack_info:
            record.stack_info = self._scrub(record.stack_info)
        return True

    @staticmethod
    def _formatiere(record: logging.LogRecord) -> str:
        import traceback

        art, wert, spur = record.exc_info      # type: ignore[misc]
        return "".join(traceback.format_exception(art, wert, spur)).rstrip()


_filter = SecretRedactionFilter()
secrets.register_observer(_filter.add_literal, _filter.discard_literal)


def redaction_filter() -> SecretRedactionFilter:
    return _filter


def log_file_path() -> Path:
    return state_dir() / LOG_FILENAME


def setup_logging(*, verbose: bool = False, to_file: bool = True) -> Path | None:
    """Konfiguriert Root-Logger mit Konsole und rotierender Datei.

    Gibt den Pfad des Logfiles zurück, oder ``None`` wenn keines geschrieben
    werden konnte (z.B. schreibgeschütztes Home). Das ist kein Fehlerfall --
    die Anwendung muss auch ohne Logdatei laufen.
    """
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(levelname)-8s %(name)s: %(message)s"))
    console.addFilter(_filter)
    root.addHandler(console)

    if not to_file:
        return None

    try:
        path = log_file_path()
        ensure_dir(path.parent)
        file_handler = logging.handlers.RotatingFileHandler(
            path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
        )
        file_handler.addFilter(_filter)
        root.addHandler(file_handler)
        return path
    except OSError as exc:
        root.warning("Logdatei konnte nicht angelegt werden: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Protokoll je Erzeugungslauf (Spec Abschnitt 13: "Build-Logs sollen gespeichert
# werden koennen")
# ---------------------------------------------------------------------------

BUILD_LOG_DIR = "builds"
MAX_BUILD_LOGS = 20


def build_log_dir() -> Path:
    return state_dir() / BUILD_LOG_DIR


def write_build_log(iso_name: str, sections: dict[str, str]) -> Path | None:
    """Schreibt ein Protokoll dieses Laufs in eine eigene Datei.

    Getrennt vom laufenden Programmprotokoll, damit sich ein einzelner Lauf
    weitergeben laesst, ohne alles andere mitzuschicken. Beim spaeteren echten
    ISO-Build haengt die mkarchiso-Ausgabe hier einfach hinten an.

    Der Redaktionsfilter greift auch hier: die Abschnitte laufen durch dieselbe
    Maskierung wie jede Logzeile.
    """
    import datetime

    try:
        directory = ensure_dir(build_log_dir())
    except OSError as exc:
        logging.getLogger(__name__).warning("Build-Protokoll nicht anlegbar: %s", exc)
        return None

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_name = "".join(c for c in iso_name if c.isalnum() or c in "-_") or "profil"
    path = directory / f"{stamp}-{safe_name}.log"

    # Der Zeitstempel geht nur bis zur Sekunde. Zwei Laeufe kurz hintereinander
    # -- etwa ein abgebrochener und der sofortige zweite Versuch -- wuerden sich
    # sonst gegenseitig ueberschreiben, und ausgerechnet das Protokoll des
    # Fehlschlags waere weg.
    if path.exists():
        for suffix in range(2, 100):
            candidate = directory / f"{stamp}-{safe_name}-{suffix}.log"
            if not candidate.exists():
                path = candidate
                break
        else:
            # Alle Ausweichnamen belegt -- hundert Laeufe in derselben Sekunde.
            # Sehr unwahrscheinlich, aber der frueher hier fehlende else-Zweig
            # liess ``path`` auf dem Originalnamen stehen und ueberschrieb damit
            # genau das Protokoll, das die Schleife retten sollte.
            path = directory / f"{stamp}-{safe_name}-{os.getpid()}.log"

    lines = [f"ArchCustomiser -- Erzeugungslauf {stamp}", "=" * 60, ""]
    for title, body in sections.items():
        lines.append(title)
        lines.append("-" * len(title))
        lines.append(_filter._scrub(body))
        lines.append("")

    try:
        path.write_text("\n".join(lines), encoding="utf-8")
    except OSError as exc:
        logging.getLogger(__name__).warning("Build-Protokoll nicht schreibbar: %s", exc)
        return None

    _prune_build_logs(directory)
    return path


def _prune_build_logs(directory: Path) -> None:
    """Nur die letzten Laeufe behalten -- sonst waechst das Verzeichnis endlos."""
    try:
        found = sorted(directory.glob("*.log"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    for stale in found[:-MAX_BUILD_LOGS]:
        try:
            stale.unlink()
        except OSError:
            pass
