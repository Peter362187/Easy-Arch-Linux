"""Vorschauen zu Katalogkategorien.

Der Katalog bleibt das Programm: eine Kategorie bekommt ueber den Schluessel
``preview`` einen Namen, und die Oberflaeche sucht darunter eine Vorschau.
Steht dort nichts oder ist nichts registriert, gibt es einfach keine -- kein
Fehler, kein Sonderfall.

Genauso arbeiten ``choices_from`` (``core/choices.py``) und ``validator``
(``core/validation.py``) bereits im Kern.
"""

from __future__ import annotations

# Die Vorschauen registrieren sich beim Import.
from . import branding as _branding  # noqa: F401  -- Nebenwirkung ist der Zweck
from .registry import PreviewContext, create, register

__all__ = ["PreviewContext", "create", "register"]
