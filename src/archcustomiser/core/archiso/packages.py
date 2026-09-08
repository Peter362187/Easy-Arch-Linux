"""Erzeugt ``packages.x86_64``.

Der Katalog enthaelt nur, was der Benutzer bewusst waehlt. Zum Booten fehlen
dann Pakete, die keine Auswahlmoeglichkeit sind, sondern Bau-Infrastruktur --
``base`` etwa, oder ``mkinitcpio-archiso``, ohne das die ISO gar nicht startet.
Die ergaenzt dieses Modul.

Jede Ergaenzung traegt eine Begruendung, die im Dry-Run erscheint. Wer
``syslinux`` in seiner Paketliste findet, soll nicht raten muessen, warum.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ..config import BuildConfig
from .settings import ArchisoSettings


@dataclass(frozen=True, slots=True)
class AddedPackage:
    name: str
    reason: str


def required_packages(
    settings: ArchisoSettings, config: BuildConfig, selected: Iterable[str]
) -> tuple[AddedPackage, ...]:
    """Pakete, die archiso braucht und der Katalog nicht liefert."""
    have = set(selected)
    added: list[AddedPackage] = []

    def add(name: str, reason: str) -> None:
        if name not in have:
            have.add(name)
            added.append(AddedPackage(name, reason))

    # Ohne diese vier bootet gar nichts.
    add("base", "Grundsystem -- ohne dieses Paket gibt es keine Shell und keine Basiswerkzeuge")
    add("mkinitcpio", "erzeugt das Start-Abbild (initramfs)")
    add(
        "mkinitcpio-archiso",
        "die archiso-Starthaken; ohne sie findet das System sein Dateisystem nicht",
    )
    add("linux-firmware", "Firmware fuer Netzwerk-, WLAN- und Grafikhardware")

    # mkarchiso prueft ausdruecklich, ob 'syslinux' in der Paketliste steht, und
    # holt die .c32-Module spaeter aus dem Abbild -- nicht vom Host.
    if settings.has_bios:
        add("syslinux", "wird fuer den BIOS-Start benoetigt und von mkarchiso vorausgesetzt")

    if settings.has_grub:
        add("grub", "wird als UEFI-Bootloader verwendet")

    if settings.include_memtest:
        add("memtest86+", "Speichertest im BIOS-Bootmenue")
        if settings.has_uefi:
            add("memtest86+-efi", "Speichertest im UEFI-Bootmenue")

    if settings.include_installer:
        # archiso erzeugt ein Live-System. Ohne diese beiden Pakete koennte der
        # Benutzer sein System nicht dauerhaft installieren.
        add("archinstall", "Installationsprogramm fuer das Zielsystem")
        add("arch-install-scripts", "wird von archinstall benoetigt (pacstrap, genfstab)")

    # C.UTF-8 ist in glibc eingebaut; jede andere Sprache braucht generierte
    # Locales. Das offizielle Paket bringt sie fertig mit -- die Alternative
    # waere, locale-gen im Chroot laufen zu lassen.
    locale = config.field_str("basics.locale", "C.UTF-8")
    if locale and not locale.startswith("C."):
        add(
            "glibc-locales",
            f"stellt die Sprache {locale} bereit, ohne sie beim Bauen erzeugen zu muessen",
        )

    return tuple(added)


def render_packages(
    selected: Sequence[str], groups: Sequence[str], added: Sequence[AddedPackage]
) -> str:
    """Baut den Inhalt von packages.x86_64.

    Paketgruppen bleiben Gruppennamen -- pacstrap loest sie zur Bauzeit auf dem
    dann aktuellen Stand auf. Eine hier eingefrorene Mitgliederliste waere beim
    naechsten Repo-Update bereits veraltet.
    """
    lines = [
        "# Erzeugt von ArchCustomiser -- nicht von Hand bearbeiten.",
        "#",
        "# Ein Paket je Zeile. Gruppennamen sind erlaubt und werden von pacstrap",
        "# beim Bauen aufgeloest.",
        "#",
        "# WICHTIG: keine Kommentare hinter einem Paketnamen. mkarchiso liest die",
        "# Datei mit  sed 's/#.*//'  -- das schneidet zwar den Kommentar ab, laesst",
        "# aber die Leerzeichen davor stehen. Aus 'base  # Grundsystem' wuerde der",
        "# Paketname 'base  ' und pacstrap meldet 'target not found'.",
        "",
    ]

    if added:
        lines.append("# Von archiso benoetigt (automatisch ergaenzt):")
        for entry in sorted(added, key=lambda item: item.name):
            # Begruendung ueber den Namen, nie dahinter.
            lines.append(f"#   {entry.name}: {entry.reason}")
        lines.append("")
        lines.extend(entry.name for entry in sorted(added, key=lambda item: item.name))
        lines.append("")

    if groups:
        lines.append("# Paketgruppen:")
        lines.extend(sorted(set(groups)))
        lines.append("")

    lines.append("# Ausgewaehlte Pakete:")
    lines.extend(sorted(set(selected)))
    lines.append("")
    return "\n".join(lines)
