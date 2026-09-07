"""Fuehrt einen echten ISO-Bau ueber die App-Schnittstelle aus.

Kein Testdouble: derselbe ``BuildController``, den auch der Knopf
„ISO erstellen" benutzt, mit einem echten WSL-Ziel.

Der Fortschritt wird laufend in eine Datei geschrieben, damit sich ein
langlaufender Bau von aussen verfolgen laesst, ohne den Prozess zu blockieren.

Aufruf::

    python tools/echtbau.py <profil.yaml> <fortschrittsdatei>
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
from archcustomiser.core.build.targets import WslExecutionTarget
from archcustomiser.core.build.wsl import WslTarget, detect
from archcustomiser.core.catalog import load_catalog
from archcustomiser.core.profiles import ProfileService
from archcustomiser.core.resolver import Resolver
from archcustomiser.core.secrets import SecretStore


def main(profil: Path, fortschritt: Path) -> int:
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

    status = detect()
    if not status.usable:
        schreibe(phase="Fehler", fehler="Keine Arch-Verteilung in WSL gefunden")
        return 2
    assert status.preferred is not None
    ziel = WslExecutionTarget(WslTarget(status.preferred.name))

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

    arbeit = Path.home() / "archcustomiser" / "work"
    ausgabe = Path.home() / "archcustomiser" / "out"
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
    raise SystemExit(main(Path(sys.argv[1]), Path(sys.argv[2])))
