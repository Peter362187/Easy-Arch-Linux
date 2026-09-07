"""Kopiert das Projekt in einen geklonten Repository-Ordner.

Uebernommen wird genau das, was git auch uebernehmen wuerde -- ermittelt ueber
``git ls-files``, nicht ueber eine Handkopie der ``.gitignore``. Die frueher
hier gepflegte Musterliste war naemlich keine: sie liess ``*-profil/``,
``/profiles-lokal/``, ``Thumbs.db``, ``desktop.ini`` und ``*.pyd`` aus, obwohl
der Kopf des Moduls behauptete, die Regeln staemmten aus derselben Datei.

Zweiter Unterschied: das Skript kopierte nur und loeschte nie. Eine in der
Quelle geloeschte oder umbenannte Datei blieb im Ziel bestehen -- bei einer
Umbenennung entstand so unbemerkt ein Duplikat, das beim naechsten Commit als
weiterhin vorhanden gefuehrt wurde. Jetzt werden verwaiste Dateien im Ziel
entfernt.

Aufruf::

    python tools/ins_repo_kopieren.py <zielordner>
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# Das Zielverzeichnis ist ein Klon; sein eigenes .git wird nie angefasst.
NIE_ANFASSEN = {".git"}


def verfolgte_dateien(quelle: Path) -> list[str]:
    """Alles, was git im Quellordner fuehren wuerde.

    ``--cached`` nennt die bereits verfolgten Dateien, ``--others
    --exclude-standard`` die neuen, die nicht ignoriert werden. Zusammen ist
    das genau der Stand, der beim naechsten ``git add -A`` entstuende.
    """
    ergebnis = subprocess.run(
        [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        cwd=str(quelle),
        capture_output=True,
        check=False,
    )
    if ergebnis.returncode != 0:
        raise SystemExit(
            "Der Quellordner ist kein Git-Repository -- ohne git laesst sich "
            "nicht sagen, was hineingehoert.\n"
            + ergebnis.stderr.decode("utf-8", errors="replace").strip()
        )
    roh = ergebnis.stdout.decode("utf-8", errors="replace")
    return [name for name in roh.split(chr(0)) if name]


def main(ziel: Path) -> int:
    quelle = Path(__file__).resolve().parent.parent
    ziel = ziel.resolve()

    if not ziel.is_dir():
        raise SystemExit(f"{ziel} gibt es nicht.")
    if ziel == quelle:
        raise SystemExit("Quelle und Ziel sind derselbe Ordner.")

    gewollt = verfolgte_dateien(quelle)

    kopiert = 0
    for name in gewollt:
        herkunft = quelle / name
        if not herkunft.is_file():
            continue
        zielpfad = ziel / name
        zielpfad.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(herkunft, zielpfad)
        kopiert += 1

    # Verwaiste Dateien im Ziel entfernen -- sonst ueberlebt eine geloeschte
    # oder umbenannte Datei jeden weiteren Lauf.
    behalten = {(ziel / name).resolve() for name in gewollt}
    entfernt = 0
    for vorhanden in sorted(ziel.rglob("*"), reverse=True):
        if any(teil in NIE_ANFASSEN for teil in vorhanden.relative_to(ziel).parts):
            continue
        if vorhanden.is_file() and vorhanden.resolve() not in behalten:
            vorhanden.unlink()
            entfernt += 1
        elif vorhanden.is_dir() and not any(vorhanden.iterdir()):
            vorhanden.rmdir()

    print(f"{kopiert} Dateien kopiert, {entfernt} verwaiste entfernt -> {ziel}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Aufruf: python tools/ins_repo_kopieren.py <zielordner>")
    raise SystemExit(main(Path(sys.argv[1])))
