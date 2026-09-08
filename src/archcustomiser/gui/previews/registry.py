"""Die Registry der Vorschauen.

``@register("iso_branding")`` traegt eine Fabrik ein, ``create(name, ctx)``
holt sie wieder heraus. Mehr ist es nicht -- und mehr soll es nicht sein: die
Zuordnung Kategorie zu Vorschau steht im YAML, nicht in einem ``if``-Baum.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtWidgets import QWidget

from ..store import SelectionStore

log = logging.getLogger(__name__)


class PreviewContext:
    """Was eine Vorschau ueber die Aussenwelt wissen darf.

    Nur der Store und die Rollenzuordnung. Eine Vorschau kennt damit keine
    Feldnamen: sie fragt nach der Rolle ``wallpaper`` und bekommt den Wert des
    Feldes, das sich im Katalog dafuer gemeldet hat. Heisst das Feld eines
    Tages anders, aendert sich hier nichts.
    """

    __slots__ = ("roles", "store")

    def __init__(self, store: SelectionStore, roles: dict[str, str]) -> None:
        self.store = store
        self.roles = roles

    def binding(self, rolle: str) -> str:
        return self.roles.get(rolle, "")

    def wert(self, rolle: str, vorgabe=None):
        binding = self.roles.get(rolle)
        if not binding:
            return vorgabe
        wert = self.store.field(binding)
        return vorgabe if wert is None else wert

    def text(self, rolle: str, vorgabe: str = "") -> str:
        wert = self.wert(rolle)
        return vorgabe if wert is None else str(wert)

    def zahl(self, rolle: str, vorgabe: int = 0) -> int:
        try:
            return int(self.wert(rolle, vorgabe))
        except (TypeError, ValueError):
            return vorgabe


Fabrik = Callable[[PreviewContext], QWidget]

_REGISTRY: dict[str, Fabrik] = {}


def register(name: str) -> Callable[[Fabrik], Fabrik]:
    def eintragen(fabrik: Fabrik) -> Fabrik:
        _REGISTRY[name] = fabrik
        return fabrik

    return eintragen


def create(name: str, kontext: PreviewContext) -> QWidget | None:
    """Die Vorschau zu diesem Namen -- oder ``None``.

    Ein unbekannter Name ist kein Absturzgrund: die Kategorie bekommt dann
    einfach keine Vorschau. Er wird aber protokolliert, weil er fast immer ein
    Tippfehler im Katalog ist.
    """
    if not name:
        return None
    fabrik = _REGISTRY.get(name)
    if fabrik is None:
        log.warning("Keine Vorschau namens %r registriert", name)
        return None
    return fabrik(kontext)


def rollen_aus_katalog(catalog) -> dict[str, str]:
    """Sammelt katalogweit alle ``preview_role``-Zuordnungen.

    Katalogweit und nicht je Kategorie: die Vorschau des Brandings zeigt auch
    den ISO-Dateinamen, und der haengt an Feldern aus zwei Kategorien.
    """
    rollen: dict[str, str] = {}
    for category in catalog.categories:
        for spec in category.fields:
            if spec.preview_role:
                rollen.setdefault(spec.preview_role, spec.binding)
    return rollen


__all__ = ["PreviewContext", "create", "register", "rollen_aus_katalog"]
