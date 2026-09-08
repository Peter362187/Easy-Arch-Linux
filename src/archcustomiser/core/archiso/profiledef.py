"""Erzeugt ``profiledef.sh``.

Diese Datei wird von mkarchiso **ausgefuehrt**, nicht gelesen -- siehe
``quoting.py``. Deshalb geht hier jeder Wert durch ``bash_assignment`` und
Konsorten; im ganzen Modul steht kein einziges direktes f-String-Einsetzen in
Bash-Syntax.
"""

from __future__ import annotations

from collections.abc import Mapping

from .quoting import bash_array, bash_assignment, bash_assoc
from .settings import ArchisoSettings

HEADER = """#!/usr/bin/env bash
# shellcheck disable=SC2034
#
# Erzeugt von ArchCustomiser -- nicht von Hand bearbeiten.
# Aenderungen gehen beim naechsten Erzeugen verloren.
#
# Diese Datei wird von mkarchiso mit '.' eingelesen und damit ausgefuehrt.
"""


def render_profiledef(
    settings: ArchisoSettings, file_permissions: Mapping[str, str]
) -> str:
    """Baut den Inhalt von profiledef.sh."""
    lines: list[str] = [HEADER]

    lines.append(bash_assignment("iso_name", settings.iso_name, field="ISO-Name"))
    lines.append(bash_assignment("iso_label", settings.iso_label, field="ISO-Label"))
    lines.append(
        bash_assignment("iso_publisher", settings.iso_publisher, field="Herausgeber")
    )
    lines.append(
        bash_assignment(
            "iso_application", settings.iso_application, field="Anwendungsbezeichnung"
        )
    )
    lines.append(bash_assignment("iso_version", settings.iso_version, field="Version"))
    lines.append(
        bash_assignment("install_dir", settings.install_dir, field="Verzeichnis auf der ISO")
    )
    lines.append("")

    lines.append(bash_array("buildmodes", ("iso",)))
    lines.append(bash_array("bootmodes", settings.bootmodes))
    lines.append(bash_assignment("arch", settings.arch, field="Architektur"))
    lines.append(bash_assignment("pacman_conf", "pacman.conf"))
    lines.append("")

    lines.append(bash_assignment("airootfs_image_type", "squashfs"))
    lines.append(
        bash_array("airootfs_image_tool_options", settings.airootfs_image_tool_options)
    )
    lines.append("")

    lines.append(bash_assoc("file_permissions", file_permissions))
    lines.append("")

    return "\n".join(lines)
