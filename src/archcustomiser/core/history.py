"""Was wurde hier schon gebaut?

Ein Bau dauert eine halbe Stunde. Wer ihn zweimal in der Woche macht, will
danach wissen: welche ISO liegt eigentlich noch auf der Platte, wie gross war
sie, wie lange hat sie gebraucht -- und mit welchem Profil? Das Bauprotokoll
beantwortet das nur fuer den einen Lauf und heisst nach einem Zeitstempel.

Hier steht je Bau ein kleiner JSON-Eintrag unter ``state_dir()/builds``.
Bewusst dort und nicht neben der ISO: das Ausgabeverzeichnis gehoert dem
Benutzer, und ein Programm, das ungefragt Verwaltungsdateien danebenlegt, ist
laestig.

**Keine Geheimnisse.** Der Eintrag enthaelt Name, Groesse, Dauer, Bauweg,
Paketzahl und Pruefsumme -- nichts aus dem SecretStore, keine Feldwerte. Das
ist keine Vorsicht, sondern die Regel des Projekts: Passwoerter verlassen den
SecretStore nicht.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .paths import ensure_dir, state_dir

log = logging.getLogger(__name__)

# Mehr als das liest ohnehin niemand; aeltere Eintraege werden verworfen.
MAX_EINTRAEGE = 50


@dataclass(frozen=True, slots=True)
class Bau:
    """Ein abgeschlossener Bau."""

    iso_name: str
    zeitpunkt: str = ""            # ISO-8601, vom Aufrufer gesetzt
    groesse_bytes: int = 0
    dauer_sekunden: float = 0.0
    erfolgreich: bool = True
    bauweg: str = ""
    profil: str = ""
    pakete: int = 0
    sha256: str = ""
    iso_pfad: str = ""
    hinweise: tuple[str, ...] = field(default_factory=tuple)

    @property
    def groesse_mb(self) -> int:
        return self.groesse_bytes // (1024 * 1024)

    @property
    def existiert_noch(self) -> bool:
        """Ob die Datei noch da ist -- sie kann geloescht worden sein."""
        return bool(self.iso_pfad) and Path(self.iso_pfad).is_file()

    def als_zeile(self) -> str:
        zustand = "ok" if self.erfolgreich else "fehlgeschlagen"
        dauer = f"{int(self.dauer_sekunden // 60)}:{int(self.dauer_sekunden % 60):02d}"
        return (
            f"{self.zeitpunkt or '?':<20} {self.iso_name:<34} "
            f"{self.groesse_mb:>6} MB  {dauer:>7}  {zustand}"
        )


def verzeichnis() -> Path:
    return state_dir() / "builds"


def _datei(bau: Bau) -> Path:
    """Ein Dateiname, der sich sortieren laesst und nichts verraet."""
    stempel = (bau.zeitpunkt or "0").replace(":", "-").replace(" ", "_")
    sicher = "".join(
        zeichen for zeichen in bau.iso_name if zeichen.isalnum() or zeichen in "-_."
    )
    return verzeichnis() / f"{stempel}-{sicher or 'bau'}.json"


def merke(bau: Bau) -> Path | None:
    """Schreibt einen Eintrag und raeumt alte weg.

    Scheitert das Schreiben, ist das kein Grund, irgendetwas abzubrechen: eine
    Historie ist Zusatznutzen, kein Teil des Baus.
    """
    try:
        ordner = ensure_dir(verzeichnis(), mode=0o700)
        ziel = _datei(bau)
        ziel.write_text(
            json.dumps(asdict(bau), indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as exc:
        log.warning("Bauhistorie nicht schreibbar: %s", exc)
        return None
    _aufraeumen(ordner)
    return ziel


def lies(grenze: int = MAX_EINTRAEGE) -> list[Bau]:
    """Die juengsten Eintraege zuerst."""
    ordner = verzeichnis()
    if not ordner.is_dir():
        return []
    eintraege: list[Bau] = []
    for datei in sorted(ordner.glob("*.json"), reverse=True)[:grenze]:
        bau = _lade(datei)
        if bau is not None:
            eintraege.append(bau)
    return eintraege


def _lade(datei: Path) -> Bau | None:
    try:
        roh = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # Eine kaputte Datei darf die Liste nicht sprengen.
        log.debug("Historieneintrag %s unlesbar: %s", datei, exc)
        return None
    if not isinstance(roh, dict):
        return None
    bekannt = {feld.name for feld in Bau.__dataclass_fields__.values()}
    daten = {schluessel: wert for schluessel, wert in roh.items() if schluessel in bekannt}
    daten["hinweise"] = tuple(daten.get("hinweise") or ())
    try:
        return Bau(**daten)
    except TypeError as exc:
        log.debug("Historieneintrag %s passt nicht: %s", datei, exc)
        return None


def _aufraeumen(ordner: Path) -> None:
    dateien = sorted(ordner.glob("*.json"), reverse=True)
    for veraltet in dateien[MAX_EINTRAEGE:]:
        try:
            veraltet.unlink()
        except OSError:          # nicht schlimm, beim naechsten Mal wieder
            log.debug("Alter Historieneintrag %s nicht loeschbar", veraltet)


def leeren() -> int:
    """Loescht die gesamte Historie und meldet, wie viele Eintraege es waren."""
    ordner = verzeichnis()
    if not ordner.is_dir():
        return 0
    anzahl = 0
    for datei in ordner.glob("*.json"):
        try:
            datei.unlink()
            anzahl += 1
        except OSError:
            log.debug("Historieneintrag %s nicht loeschbar", datei)
    return anzahl


__all__ = ["MAX_EINTRAEGE", "Bau", "leeren", "lies", "merke", "verzeichnis"]
