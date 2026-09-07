"""Erzeugt die ``pacman.conf`` des Profils.

Wichtig: In der ausgelieferten archiso-Vorlage ist ``[multilib]``
**auskommentiert**. Wer Steam auswaehlt, bekaeme ohne Zutun einen Build, der an
einem nicht gefundenen Paket scheitert. Der Resolver meldet ueber
``Resolution.repositories``, welche zusaetzlichen Repositories noetig sind; hier
werden sie aktiviert.

Dieselbe Angabe geht spaeter in ``mirror_config.optional_repositories`` der
archinstall-Konfiguration -- damit auch das installierte System Steam findet.
"""

from __future__ import annotations

from collections.abc import Sequence

HEADER = """#
# Erzeugt von ArchCustomiser -- nicht von Hand bearbeiten.
#
# Diese Datei gilt fuer den Bau der ISO. mkarchiso reicht sie an pacstrap
# weiter und setzt HookDir selbst.
#

[options]
HoldPkg      = pacman glibc
Architecture = auto

CheckSpace
ParallelDownloads = 5

# Keine Fortschrittsbalken: die Ausgabe wird zeilenweise ausgewertet, um den
# Baufortschritt anzuzeigen. Farbcodes und Balken stoeren dabei.
NoProgressBar

SigLevel    = Required DatabaseOptional
LocalFileSigLevel = Optional
"""

# Reihenfolge zaehlt: bei Namensgleichheit gewinnt das zuerst genannte
# Repository -- genau wie in der Standardkonfiguration von Arch.
BASE_REPOSITORIES: tuple[str, ...] = ("core", "extra")
OPTIONAL_ORDER: tuple[str, ...] = ("core-testing", "extra-testing", "multilib", "multilib-testing")


def render_pacman_conf(extra_repositories: Sequence[str] = ()) -> str:
    """Baut die pacman.conf mit allen benoetigten Repositories."""
    wanted = list(BASE_REPOSITORIES)
    for name in OPTIONAL_ORDER:
        if name in extra_repositories and name not in wanted:
            wanted.append(name)
    # Alles, was der Katalog sonst noch nennt, hinten anhaengen.
    for name in extra_repositories:
        if name not in wanted:
            wanted.append(name)

    lines = [HEADER]
    for name in wanted:
        if name == "multilib":
            lines.append(
                "# multilib wird fuer 32-Bit-Anwendungen benoetigt (Steam, Wine).\n"
                "# In der archiso-Vorlage ist dieser Abschnitt auskommentiert; hier\n"
                "# ist er aktiv, weil eine Auswahl ihn verlangt."
            )
        lines.append(f"[{name}]")
        lines.append("Include = /etc/pacman.d/mirrorlist")
        lines.append("")
    return "\n".join(lines)
