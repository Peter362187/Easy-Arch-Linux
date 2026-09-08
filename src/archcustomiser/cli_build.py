"""``--build`` -- eine ISO ohne Oberflaeche bauen.

Gedacht fuer den Fall, in dem gar kein Bildschirm da ist: ein Server, eine
Sitzung ueber SSH, ein Skript, das nachts baut. Bisher gab es dafuer nur
``tools/echtbau.py``, ein Entwicklerskript mit fest eingebautem Passwort.

Drei Entscheidungen, die den Unterschied zur Oberflaeche ausmachen:

* **Der Fortschritt geht nach stderr, das Ergebnis nach stdout.** Damit laesst
  sich ``archcustomiser --build p.yaml > ergebnis.txt`` schreiben, ohne dass
  die Fortschrittszeilen mit hineinlaufen.
* **Das Passwort kommt ueber die Standardeingabe, nie als Argument.** Ein
  Argument steht unter Linux fuer jeden Benutzer in ``/proc`` und landet in
  der Shell-Historie.
* **Strg+C bricht den Bau ab, statt den Prozess zu erschlagen.** Ein hart
  beendeter Bau laesst Einhaengungen und dreissig Gigabyte Muell zurueck.

Rueckgabewerte: 0 fertig, 1 fehlgeschlagen, 2 falsche Eingabe, 3 abgebrochen,
4 Vorabpruefung blockiert.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
import time
from datetime import UTC
from pathlib import Path

log = logging.getLogger(__name__)

FERTIG = 0
FEHLGESCHLAGEN = 1
EINGABEFEHLER = 2
ABGEBROCHEN = 3
BLOCKIERT = 4


def _melde(text: str) -> None:
    """Fortschritt nach stderr -- stdout gehoert dem Ergebnis."""
    print(text, file=sys.stderr, flush=True)


def _ziel_waehlen(wunsch: str):
    """Den Bauweg bestimmen: entweder den gewuenschten oder den besten."""
    from .core.build.targets import available_targets

    optionen = available_targets()
    if wunsch and wunsch != "auto":
        passend = [option for option in optionen if option.kind == wunsch]
        if not passend:
            _melde(f"Fehler: Es gibt hier keinen Bauweg '{wunsch}'.")
            return None, ""
        gewaehlt = passend[0]
        if not gewaehlt.usable:
            _melde(f"Fehler: {gewaehlt.label} -- {gewaehlt.problem}")
            if gewaehlt.remedy:
                _melde(f"Abhilfe: {gewaehlt.remedy}")
            return None, ""
        return gewaehlt, gewaehlt.label

    brauchbar = [option for option in optionen if option.usable]
    if not brauchbar:
        _melde("Fehler: Auf diesem Rechner laesst sich keine ISO bauen.")
        for option in optionen:
            _melde(f"  - {option.label}: {option.problem}")
            if option.remedy:
                _melde(f"    Abhilfe: {option.remedy}")
        _melde("Mit --export-profile laesst sich das Profil auf einem Arch-System bauen.")
        return None, ""
    return brauchbar[0], brauchbar[0].label


def _passwort_lesen() -> str:
    """Liest genau eine Zeile von der Standardeingabe.

    Kein ``getpass``: der Aufruf soll auch in einer Rohrleitung funktionieren
    (``pass show arch | archcustomiser --build ... --password-stdin``), und
    dort gibt es kein Terminal, das die Eingabe verbergen koennte.
    """
    zeile = sys.stdin.readline()
    return zeile.rstrip("\n").rstrip("\r")


def build(
    profil: Path,
    *,
    out_dir: Path | None = None,
    work_dir: Path | None = None,
    ziel: str = "auto",
    keep_work_dir: bool = False,
    password_stdin: bool = False,
    ausfuehrlich: bool = False,
) -> int:
    from .core.archiso.errors import ProfileError
    from .core.build import BuildController
    from .core.build.errors import BuildCancelled, BuildError
    from .core.build.verify import pruefe_iso, schreibe_pruefsumme, sha256_von
    from .core.catalog import CatalogError, load_catalog
    from .core.profiles import ProfileError as ProfileFileError
    from .core.profiles import ProfileService
    from .core.resolver import Resolver
    from .core.secrets import SecretStore

    try:
        catalog = load_catalog()
    except CatalogError as exc:
        _melde(f"Fehler: Der Optionskatalog ist fehlerhaft: {exc}")
        return EINGABEFEHLER

    try:
        geladen = ProfileService(catalog).load(profil)
    except ProfileFileError as exc:
        _melde(f"Fehler: {exc}")
        return EINGABEFEHLER

    for hinweis in geladen.issues:
        _melde(f"[{hinweis.severity}] {hinweis.message}")

    config = geladen.config
    resolution = Resolver(catalog).resolve(config)
    if not resolution.is_valid:
        _melde("Fehler: Die Konfiguration ist nicht baubar:")
        for problem in resolution.blocking_issues:
            _melde(f"  - {problem.message}")
        return EINGABEFEHLER

    secrets = SecretStore()
    if password_stdin:
        wert = _passwort_lesen()
        if not wert:
            _melde("Fehler: Kein Passwort auf der Standardeingabe.")
            return EINGABEFEHLER
        # Welches Feld gemeint ist, sagt der Katalog, nicht dieser Code:
        # jedes geheime Feld, das nicht selbst die Wiederholung eines anderen
        # ist. Die Wiederholung mitzufuellen waere harmlos, aber verwirrend --
        # sie ist eine Tippkontrolle der Oberflaeche.
        for kategorie in catalog.categories:
            wiederholungen = {
                spec.confirm_field for spec in kategorie.fields if spec.confirm_field
            }
            for spec in kategorie.fields:
                if spec.secret and spec.id not in wiederholungen:
                    secrets.set(spec.binding, wert)
        if not secrets.keys():
            _melde("Hinweis: Der Katalog kennt kein Passwortfeld -- ignoriert.")

    arbeit = Path(
        work_dir or config.field_str("build.work_dir")
        or str(Path.home() / "archcustomiser" / "work")
    )
    ausgabe = Path(
        out_dir or config.field_str("build.output_dir")
        or str(Path.home() / "archcustomiser" / "out")
    )

    option, weg = _ziel_waehlen(ziel)
    if option is None:
        return EINGABEFEHLER
    _melde(f"Bauweg: {weg}")

    controller = BuildController(catalog, config, resolution, secrets)
    if option.kind != "lokal" and option.target is not None:
        controller.target = option.target

    _melde("Vorabpruefung ...")
    bericht = controller.preflight(arbeit, ausgabe)
    for pruefung in bericht.checks:
        zeichen = "ok " if pruefung.ok else ("!! " if pruefung.fatal else " ? ")
        _melde(f"  {zeichen}{pruefung.name}: {pruefung.detail}")
    if not bericht.ok:
        _melde("Der Bau kann so nicht starten.")
        return BLOCKIERT

    abbruch_laeuft = threading.Event()

    def bei_signal(_nummer, _rahmen) -> None:
        # Ein zweites Strg+C darf nicht einen zweiten Abbruch starten -- der
        # erste braucht seine Zeit, und zwei parallele Aufraeumlaeufe raeumen
        # sich gegenseitig weg.
        if abbruch_laeuft.is_set():
            return
        abbruch_laeuft.set()
        _melde("\nAbbruch angefordert -- laufende Vorgaenge werden beendet ...")
        controller.cancel()

    vorher = signal.signal(signal.SIGINT, bei_signal)
    begonnen = time.monotonic()
    try:
        ergebnis = controller.run(
            arbeit,
            ausgabe,
            keep_work_dir=keep_work_dir,
            on_step=lambda _schritt, text: _melde(f"== {text}"),
            on_progress=lambda anteil, text, detail: _melde(
                f"[{anteil * 100:5.1f}%] {text}{('  ' + detail) if detail else ''}"
            ),
            on_line=(lambda zeile: _melde(f"   {zeile}")) if ausfuehrlich else None,
            skip_preflight=True,
        )
    except BuildCancelled:
        _melde("Abgebrochen.")
        return ABGEBROCHEN
    except (BuildError, ProfileError) as exc:
        _melde(f"Fehlgeschlagen: {getattr(exc, 'user_message', exc)}")
        for grund in getattr(exc, "remedies", ()):
            _melde(f"  - {grund}")
        return FEHLGESCHLAGEN
    finally:
        signal.signal(signal.SIGINT, vorher)

    dauer = time.monotonic() - begonnen
    iso = ergebnis.iso_path
    if iso is None:
        _melde("Fehlgeschlagen: Es ist keine ISO entstanden.")
        return FEHLGESCHLAGEN

    _melde("Pruefsumme wird berechnet ...")
    summe = sha256_von(iso)
    schreibe_pruefsumme(iso, summe)
    befund = pruefe_iso(iso)

    print(befund.zusammenfassung())
    print(f"SHA-256      : {summe or 'nicht berechnet'}")
    print(f"Dauer        : {int(dauer // 60)}:{int(dauer % 60):02d}")
    print(f"Pfad         : {iso}")
    for warnung in ergebnis.warnings:
        print(f"Hinweis      : {warnung}")

    _merke_bau(config, resolution, ergebnis, weg, dauer, summe, profil)
    if not befund.plausibel:
        # Der Bau lief durch, das Ergebnis ist trotzdem nicht brauchbar --
        # das darf nicht als Erfolg gemeldet werden.
        return FEHLGESCHLAGEN
    return FERTIG


def _merke_bau(config, resolution, ergebnis, weg, dauer, summe, profil) -> None:
    """Traegt den Bau in die Historie ein -- ohne dass ein Fehler stoert."""
    from datetime import datetime

    from .core import history

    iso = ergebnis.iso_path
    try:
        groesse = iso.stat().st_size if iso is not None else 0
    except OSError:
        groesse = 0
    history.merke(
        history.Bau(
            iso_name=iso.name if iso is not None else config.iso_filename,
            zeitpunkt=datetime.now(UTC).astimezone().strftime("%Y-%m-%d %H:%M"),
            groesse_bytes=groesse,
            dauer_sekunden=dauer,
            erfolgreich=True,
            bauweg=weg,
            profil=str(profil),
            pakete=len(resolution.package_names),
            sha256=summe,
            iso_pfad=str(iso) if iso is not None else "",
            hinweise=tuple(ergebnis.warnings),
        )
    )


def verify(pfad: Path) -> int:
    """``--verify-iso`` -- eine vorhandene Datei beurteilen."""
    from .core.build.verify import pruefe_iso, sha256_von

    befund = pruefe_iso(pfad)
    print(befund.zusammenfassung())
    if befund.iso9660:
        print(f"SHA-256      : {sha256_von(pfad) or 'nicht berechenbar'}")
    return FERTIG if befund.plausibel else FEHLGESCHLAGEN


def historie(leeren: bool = False) -> int:
    """``--history`` -- was hier schon gebaut wurde."""
    from .core import history as speicher

    if leeren:
        anzahl = speicher.leeren()
        print(f"{anzahl} Eintraege geloescht.")
        return FERTIG

    eintraege = speicher.lies()
    if not eintraege:
        print("Noch nichts gebaut.")
        return FERTIG
    for eintrag in eintraege:
        rest = "" if eintrag.existiert_noch else "  (Datei geloescht)"
        print(eintrag.als_zeile() + rest)
    return FERTIG


__all__ = ["build", "historie", "verify"]
