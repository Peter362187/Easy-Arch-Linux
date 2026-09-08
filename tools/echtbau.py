"""Fuehrt einen echten ISO-Bau ueber die App-Schnittstelle aus.

Kein Testdouble: derselbe ``BuildController``, den auch der Knopf
„ISO erstellen" benutzt, mit einem echten Ziel.

Der Fortschritt wird laufend in eine Datei geschrieben, damit sich ein
langlaufender Bau von aussen verfolgen laesst, ohne den Prozess zu blockieren.

Aufruf::

    python tools/echtbau.py <profil.yaml> <fortschrittsdatei> [--ziel WEG]

``--ziel`` ist ``wsl`` (Vorgabe), ``container`` oder ``lokal``. Das Ziel wird
ausdruecklich gewaehlt und nicht ermittelt: in einer Arch-Verteilung faende
``best_target()`` das lokale archiso und nutzte nie den Container -- womit
sich genau der Weg nicht pruefen liesse, um den es geht.

Mit ``--engine podman|docker`` laesst sich beim Container-Ziel die Engine
festlegen; ohne Angabe gilt die uebliche Reihenfolge (podman vor docker).

Bewusst hier und nicht in ``__main__.py``: ein Bau von der Kommandozeile ist
kein zugesagtes Merkmal des Programms, sondern Werkzeug fuer Pruefungen.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import logging

from archcustomiser.core.build import BuildController
from archcustomiser.core.catalog import load_catalog
from archcustomiser.core.profiles import ProfileService
from archcustomiser.core.resolver import Resolver
from archcustomiser.core.secrets import SecretStore


def waehle_ziel(name: str, engine: str = ""):
    """Das Bauziel, ausdruecklich benannt.

    Gibt ein Paar (ziel, fehlermeldung) zurueck; bei Erfolg ist die Meldung
    leer.
    """
    if name == "lokal":
        from archcustomiser.core.build.targets import LocalTarget

        return LocalTarget(), ""

    if name == "container":
        from archcustomiser.core.build.container import ContainerTarget, detect
        from archcustomiser.core.build.targets import ContainerExecutionTarget

        status = detect()
        if not status.usable:
            return None, f"{status.problem} {status.remedy}".strip()
        gewaehlt = engine or status.engine
        return ContainerExecutionTarget(ContainerTarget(gewaehlt)), ""

    from archcustomiser.core.build.targets import WslExecutionTarget
    from archcustomiser.core.build.wsl import WslTarget, detect

    status = detect()
    if not status.usable:
        return None, "Keine Arch-Verteilung in WSL gefunden"
    assert status.preferred is not None
    return WslExecutionTarget(WslTarget(status.preferred.name)), ""


def main(
    profil: Path,
    fortschritt: Path,
    zielname: str = "wsl",
    engine: str = "",
    arbeit: Path | None = None,
    ausgabe: Path | None = None,
) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")
    zustand: dict[str, object] = {"phase": "start", "anteil": 0.0, "zeilen": 0}
    begonnen = time.time()

    def schreibe(**neu: object) -> None:
        zustand.update(neu)
        zustand["sekunden"] = round(time.time() - begonnen, 1)
        try:
            fortschritt.write_text(json.dumps(zustand, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    schreibe(phase="Katalog wird geladen")
    catalog = load_catalog()
    service = ProfileService(catalog)
    geladen = service.load(profil)
    resolution = Resolver(catalog).resolve(geladen.config)

    ziel, fehler = waehle_ziel(zielname, engine)
    if ziel is None:
        schreibe(phase="Fehler", fehler=fehler)
        return 2
    schreibe(phase="Ziel gewaehlt", ziel=ziel.name)

    # Frueher stand hier ein festes Passwort. Die damit gebaute ISO trug ein
    # Benutzerkonto, dessen Kennwort im Repository nachzulesen war -- und die
    # README nennt zwei so entstandene ISOs. Ohne Vorgabe wird das Konto
    # gesperrt angelegt und laesst sich spaeter mit 'passwd' freischalten.
    secrets = SecretStore()
    kennwort = os.environ.get("ARCHCUSTOMISER_PASSWORD")
    if kennwort:
        secrets.set("user.password", kennwort)
    else:
        print(
            "Hinweis: ohne ARCHCUSTOMISER_PASSWORD wird das Konto gesperrt "
            "angelegt.",
            file=sys.stderr,
        )

    controller = BuildController(catalog, geladen.config, resolution, secrets, target=ziel)

    protokoll = fortschritt.with_suffix(".ausgabe.log")
    handle = protokoll.open("w", encoding="utf-8")
    zaehler = {"n": 0}

    def zeile(text: str) -> None:
        zaehler["n"] += 1
        handle.write(text + "\n")
        if zaehler["n"] % 20 == 0:
            handle.flush()
            schreibe(zeilen=zaehler["n"])

    def anteil(wert: float, titel: str, detail: str) -> None:
        schreibe(anteil=round(wert, 4), phase=titel, detail=detail, zeilen=zaehler["n"])

    # Auf einem Bau-Runner liegt der Platz woanders als im Benutzerordner.
    arbeit = arbeit or Path.home() / "archcustomiser" / "work"
    ausgabe = ausgabe or Path.home() / "archcustomiser" / "out"
    schreibe(phase="Bau startet", arbeitsverzeichnis=str(arbeit), ausgabe=str(ausgabe))

    try:
        ergebnis = controller.run(
            arbeit, ausgabe, on_progress=anteil, on_line=zeile,
        )
    except Exception as exc:
        handle.flush()
        handle.close()
        schreibe(
            phase="Fehlgeschlagen",
            fehler=str(exc),
            technisch=getattr(exc, "technical", ""),
            spur=traceback.format_exc()[-1500:],
        )
        return 1

    handle.flush()
    handle.close()
    iso = ergebnis.iso_path
    schreibe(
        phase="Fertig",
        anteil=1.0,
        iso=str(iso) if iso else "",
        groesse_mb=round(ergebnis.result.size_mb, 1) if ergebnis.result else 0,
        protokoll=str(ergebnis.log_path) if ergebnis.log_path else "",
        hinweise=ergebnis.warnings,
    )
    return 0


if __name__ == "__main__":
    import argparse

    zerleger = argparse.ArgumentParser(description="Echter ISO-Bau ueber die App-Schnittstelle")
    zerleger.add_argument("profil", type=Path)
    zerleger.add_argument("fortschritt", type=Path)
    zerleger.add_argument("--ziel", choices=("wsl", "container", "lokal"), default="wsl")
    zerleger.add_argument("--engine", choices=("podman", "docker"), default="")
    zerleger.add_argument("--arbeit", type=Path, default=None)
    zerleger.add_argument("--ausgabe", type=Path, default=None)
    argumente = zerleger.parse_args()
    raise SystemExit(
        main(
            argumente.profil,
            argumente.fortschritt,
            argumente.ziel,
            argumente.engine,
            argumente.arbeit,
            argumente.ausgabe,
        )
    )
