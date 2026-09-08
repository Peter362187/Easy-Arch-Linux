"""Ist die Datei, die da liegt, ueberhaupt eine startfaehige ISO?

``mkarchiso`` mit Rueckgabewert 0 heisst nicht, dass eine brauchbare ISO
entstanden ist. Ein abgebrochener Kopiervorgang, eine volle Platte oder ein
Netzlaufwerk, das die letzten Bloecke schluckt, hinterlassen eine Datei mit
dem richtigen Namen und dem falschen Inhalt. Das faellt sonst erst auf, wenn
der Rechner mit dem USB-Stick nicht startet -- eine halbe Stunde Bau und
zwanzig Minuten Schreiben spaeter.

Die Pruefungen hier brauchen kein Fremdwerkzeug und lesen nur ein paar
Kilobyte. Sie beweisen nicht, dass die ISO bootet; sie schliessen die Faelle
aus, in denen sie es sicher nicht tut.

Grundlage sind drei Festlegungen aus ECMA-119 (ISO 9660) und der
El-Torito-Spezifikation:

* Ab Byte 32769 steht die Kennung ``CD001`` -- das erste, was ein Leser sucht.
* Ein BIOS-startfaehiges Abbild traegt am Ende des ersten Sektors ``0x55AA``.
* Ein El-Torito-Katalog meldet sich als Boot-Record im Sektor 17.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SEKTOR = 2048
# ECMA-119: der erste Volume Descriptor liegt im 17. Sektor.
DESKRIPTOR_START = 16 * SEKTOR
KENNUNG = b"CD001"
# Ein Abbild unter 16 MiB kann kein Live-System sein -- da fehlt schon der Kernel.
MINDESTGROESSE = 16 * 1024 * 1024
BLOCK = 1024 * 1024


@dataclass(frozen=True, slots=True)
class IsoBefund:
    """Was sich ueber die Datei sagen laesst."""

    pfad: Path
    groesse: int
    iso9660: bool = False
    mbr_signatur: bool = False
    el_torito: bool = False
    datentraeger: str = ""
    probleme: tuple[str, ...] = ()
    hinweise: tuple[str, ...] = ()

    @property
    def plausibel(self) -> bool:
        """Ob nichts dagegen spricht, dass die Datei startfaehig ist."""
        return not self.probleme

    def zusammenfassung(self) -> str:
        zeilen = [
            f"Datei        : {self.pfad.name} ({self.groesse // (1024 * 1024)} MB)",
            f"ISO-9660     : {'gueltig (CD001-Signatur)' if self.iso9660 else 'FEHLT'}",
            f"Datentraeger : {self.datentraeger or 'unbenannt'}",
            f"MBR-Signatur : {'0x55AA vorhanden' if self.mbr_signatur else 'fehlt'}",
            f"El Torito    : {'Bootkatalog vorhanden' if self.el_torito else 'nicht gefunden'}",
        ]
        for problem in self.probleme:
            zeilen.append(f"PROBLEM      : {problem}")
        for hinweis in self.hinweise:
            zeilen.append(f"Hinweis      : {hinweis}")
        return "\n".join(zeilen)


def pruefe_iso(pfad: Path) -> IsoBefund:
    """Liest die Kopfsektoren und beurteilt die Datei.

    Wirft nicht: eine unlesbare Datei ist selbst der Befund.
    """
    pfad = Path(pfad)
    probleme: list[str] = []
    hinweise: list[str] = []

    try:
        groesse = pfad.stat().st_size
    except OSError as exc:
        return IsoBefund(pfad=pfad, groesse=0, probleme=(f"Nicht lesbar: {exc}",))

    if groesse == 0:
        return IsoBefund(pfad=pfad, groesse=0, probleme=("Die Datei ist leer.",))
    if groesse < MINDESTGROESSE:
        probleme.append(
            f"Nur {groesse // 1024} KB gross -- ein Live-System ist das nicht."
        )
    if groesse % SEKTOR:
        # Ein abgebrochener Kopiervorgang endet fast immer mitten im Sektor.
        probleme.append(
            "Die Groesse ist kein Vielfaches von 2048 Byte -- die Datei ist "
            "wahrscheinlich unvollstaendig."
        )

    try:
        kopf, deskriptoren = _lies_kopf(pfad)
    except OSError as exc:
        return IsoBefund(
            pfad=pfad, groesse=groesse, probleme=(f"Nicht lesbar: {exc}",)
        )

    iso9660 = deskriptoren[1:6] == KENNUNG
    if not iso9660:
        probleme.append("Keine CD001-Signatur -- das ist kein ISO-9660-Abbild.")

    mbr = len(kopf) >= 512 and kopf[510:512] == b"\x55\xaa"
    if not mbr:
        hinweise.append(
            "Keine MBR-Signatur. Ein reines UEFI-Abbild ist das nicht "
            "zwangslaeufig ein Fehler, ein BIOS-Start schlaegt aber fehl."
        )

    el_torito = _el_torito(deskriptoren)
    if iso9660 and not el_torito:
        hinweise.append(
            "Kein El-Torito-Bootkatalog gefunden -- das Abbild laesst sich "
            "wahrscheinlich nur einhaengen, nicht starten."
        )

    return IsoBefund(
        pfad=pfad,
        groesse=groesse,
        iso9660=iso9660,
        mbr_signatur=mbr,
        el_torito=el_torito,
        datentraeger=_datentraeger(deskriptoren),
        probleme=tuple(probleme),
        hinweise=tuple(hinweise),
    )


def _lies_kopf(pfad: Path) -> tuple[bytes, bytes]:
    """Die ersten 512 Byte und die Volume Descriptors ab Sektor 16."""
    with pfad.open("rb") as datei:
        kopf = datei.read(512)
        datei.seek(DESKRIPTOR_START)
        # Vier Deskriptoren reichen: primaer, ergaenzend, Boot-Record, Ende.
        deskriptoren = datei.read(4 * SEKTOR)
    return kopf, deskriptoren


def _datentraeger(deskriptoren: bytes) -> str:
    """Die Bezeichnung des Datentraegers aus dem primaeren Deskriptor.

    Sie steht ab Byte 40 des Sektors und ist 32 Byte lang, mit Leerzeichen
    aufgefuellt.
    """
    if len(deskriptoren) < 72 or deskriptoren[1:6] != KENNUNG:
        return ""
    roh = deskriptoren[40:72]
    return roh.decode("ascii", errors="replace").strip()


def _el_torito(deskriptoren: bytes) -> bool:
    """Sucht den Boot-Record unter den Volume Descriptors.

    Ein Boot-Record hat den Typ 0 und traegt die Kennung
    ``EL TORITO SPECIFICATION``.
    """
    for anfang in range(0, len(deskriptoren) - SEKTOR + 1, SEKTOR):
        sektor = deskriptoren[anfang : anfang + SEKTOR]
        if sektor[1:6] != KENNUNG:
            continue
        if sektor[0] == 0 and b"EL TORITO" in sektor[7:71]:
            return True
        if sektor[0] == 255:          # Ende der Deskriptorenfolge
            break
    return False


def sha256_von(pfad: Path, *, abbruch=None) -> str:
    """Die Pruefsumme einer Datei, blockweise gelesen.

    ``abbruch`` ist ein Rueckruf ohne Argumente: gibt er etwas Wahres zurueck,
    bricht die Rechnung ab und liefert eine leere Zeichenkette. Damit laesst
    sich ein laufender Abbruch auch mitten in einer vier Gigabyte grossen
    Datei durchsetzen.
    """
    digest = hashlib.sha256()
    try:
        with Path(pfad).open("rb") as datei:
            while block := datei.read(BLOCK):
                if abbruch is not None and abbruch():
                    return ""
                digest.update(block)
    except OSError as exc:
        log.warning("Pruefsumme nicht berechenbar: %s", exc)
        return ""
    return digest.hexdigest()


def schreibe_pruefsumme(iso: Path, summe: str) -> Path | None:
    """Legt ``<name>.iso.sha256`` neben die ISO.

    Format und Name sind so gewaehlt, dass ``sha256sum -c datei.iso.sha256``
    ohne weiteres Zutun funktioniert.
    """
    if not summe:
        return None
    iso = Path(iso)
    ziel = iso.with_name(iso.name + ".sha256")
    try:
        ziel.write_text(f"{summe}  {iso.name}\n", encoding="utf-8")
    except OSError as exc:
        log.warning("Pruefsumme nicht speicherbar: %s", exc)
        return None
    return ziel


__all__ = [
    "IsoBefund",
    "pruefe_iso",
    "schreibe_pruefsumme",
    "sha256_von",
]
