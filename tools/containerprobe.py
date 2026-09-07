"""Prueft den Container-Bauweg auf einem echten System -- ohne ISO zu bauen.

Die Sprossen 0 bis 2 aus der Durchsicht vom 07.09.2026, in derselben
Reihenfolge und ueber **den Code des Programms**, nicht ueber nachgebaute
Befehle. Genau darauf kommt es an: dass die Aufrufe, die
``ContainerExecutionTarget`` zusammensetzt, von einer echten Engine
angenommen werden.

Aufruf::

    PYTHONPATH=src python tools/containerprobe.py [--engine podman|docker]

Rueckgabe 0, wenn der Weg trägt; 1, wenn nicht. Beides ist ein brauchbares
Ergebnis -- auf einem Runner mit rootless podman ist der Fehlschlag die
richtige Antwort, und die Vorabpruefung soll ihn benennen.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from archcustomiser.core.build.container import (        # noqa: E402
    ContainerError,
    ContainerTarget,
    detect,
    find_engine,
)


def titel(text: str) -> None:
    print(f"\n{'=' * 70}\n{text}\n{'=' * 70}", flush=True)


def main() -> int:
    zerleger = argparse.ArgumentParser(description=__doc__)
    zerleger.add_argument("--engine", choices=("podman", "docker"), default="")
    zerleger.add_argument(
        "--erwarte-fehlschlag",
        action="store_true",
        help="Rueckgabe 0, wenn der Weg NICHT traegt -- fuer rootless-Nachweise.",
    )
    argumente = zerleger.parse_args()

    titel("Was auf diesem System vorliegt")
    gefunden = find_engine()
    print(f"  find_engine()      : {gefunden}")
    engine = argumente.engine or gefunden
    if engine is None:
        print("  Keine Engine installiert -- nichts zu pruefen.")
        return 1

    status = detect()
    print(f"  detect().engine    : {status.engine}")
    print(f"  detect().version   : {status.version}")
    print(f"  detect().rootless  : {status.rootless}")
    print(f"  detect().image_ready: {status.image_ready}")
    print(f"  detect().usable    : {status.usable}")
    if status.problem:
        print(f"  Beanstandung       : {status.problem}")
        print(f"  Abhilfe            : {status.remedy}")

    ziel = ContainerTarget(engine)

    titel(f"Sprosse 0: Abbild bauen mit {engine}")
    begonnen = time.time()
    try:
        ziel.ensure_image(on_line=lambda text: print(f"  {text}", flush=True))
    except ContainerError as fehler:
        print(f"  FEHLGESCHLAGEN: {fehler.user_message}")
        print(f"  Technisch     : {fehler.technical[:800]}")
        return 0 if argumente.erwarte_fehlschlag else 1
    print(f"  Abbild bereit nach {time.time() - begonnen:.0f} s")

    titel("Sprosse 1: darf der Container einhaengen? (devtmpfs)")
    darf = ziel.can_mount_privileged()
    print(f"  can_mount_privileged() -> {darf}")
    if not darf:
        print("  Das ist der Fall, der frueher erst in pacstrap aufgefallen waere.")
        print("  Meist laeuft die Engine rootless; --privileged wirkt dann nur")
        print("  innerhalb des Benutzer-Namensraums.")
        return 0 if argumente.erwarte_fehlschlag else 1

    titel("Sprosse 2: pacstrap im Kleinen (chroot_setup, acht Mounts)")
    begonnen = time.time()
    ergebnis = ziel._runner(
        [
            engine, "run", "--rm", "--privileged", ziel.image,
            "sh", "-c", "mkdir -p /t && pacstrap -c /t base >/dev/null 2>&1 && echo PACSTRAP-OK",
        ],
        timeout=900.0,
    )
    print(f"  Rueckgabecode {ergebnis.returncode} nach {time.time() - begonnen:.0f} s")
    print(f"  Ausgabe: {ergebnis.stdout.strip()[-200:]}")
    if not ergebnis.ok:
        print(f"  Fehler : {ergebnis.stderr.strip()[-800:]}")
        return 0 if argumente.erwarte_fehlschlag else 1

    titel("Sprosse 3: mkarchiso ist im Abbild erreichbar")
    for werkzeug in ("mkarchiso", "pacstrap", "grub-mkstandalone", "mksquashfs", "xorriso"):
        print(f"  {werkzeug:20} {'vorhanden' if ziel.has_command(werkzeug) else 'FEHLT'}")

    titel("Ergebnis")
    if argumente.erwarte_fehlschlag:
        print("  Der Weg traegt -- erwartet war ein Fehlschlag.")
        return 1
    print("  Der Container-Bauweg traegt auf diesem System.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
