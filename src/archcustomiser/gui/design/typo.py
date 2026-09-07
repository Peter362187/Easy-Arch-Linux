"""Schriftstufen -- relativ zur Systemschrift, nicht in festen Pixeln.

Der Grund steht in der alten Fassung schon richtig da: wer Windows auf 125 %
gestellt hat, weil er sonst schlecht liest, bekam bei ``font-size: 11px``
weiterhin elf Pixel Text, waehrend die Rahmen darum herum mitwuchsen. Und
``Consolas`` gibt es unter Linux nicht -- ausgerechnet auf der Plattform, auf
der gebaut wird.

Deshalb hier: alles als Abstand zur Systemschrift, und die Monospace-Schrift
ueber ``QFontDatabase``.
"""

from __future__ import annotations

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

CAPTION = -1.0
BODY = 0.0
SUBTITLE = 1.0
TITLE = 3.0
DISPLAY = 8.0


def basis_groesse() -> float:
    """Die Schriftgroesse des Systems -- Bezugspunkt fuer alles andere."""
    app = QApplication.instance()
    if app is None:
        return 9.0
    groesse = app.font().pointSizeF()
    # Auf manchen Systemen ist nur die Pixelgroesse gesetzt; pointSizeF liefert
    # dann -1, und ein daraus gerechnetes setPointSize(1) ergaebe winzigen Text.
    return groesse if groesse > 0 else 9.0


def schrift(stufe: float = BODY, *, fett: bool = False) -> QFont:
    """Eine Schrift der gewuenschten Stufe."""
    app = QApplication.instance()
    font = QFont(app.font()) if app is not None else QFont()
    font.setPointSizeF(max(basis_groesse() + stufe, 6.0))
    font.setBold(fett)
    return font


def mono(stufe: float = CAPTION) -> QFont:
    """Feste Zeichenbreite -- fuer Befehle, Pfade und Protokolle."""
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPointSizeF(max(basis_groesse() + stufe, 6.0))
    return font


def format_size(size_bytes: int | None) -> str:
    """Groessenangabe, die auch bei kleinen Dateien sinnvoll bleibt.

    Ein Paket mit 400 KB als '0 MB' anzuzeigen sieht nach einem Fehler aus --
    genauso wie eine Konfigurationsdatei mit 40 Bytes als '0 KB'.
    """
    if not size_bytes:
        return ""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    megabytes = size_bytes / 1_048_576
    if megabytes < 0.1:
        return f"{size_bytes / 1024:.0f} KB"
    if megabytes < 10:
        return f"{megabytes:.1f} MB"
    if megabytes < 1024:
        return f"{megabytes:.0f} MB"
    return f"{megabytes / 1024:.2f} GB"
