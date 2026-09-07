"""Welcher Schritt gerade dran ist, und welche es ueberhaupt gibt.

Die alte Oberflaeche ueberliess das ``QWizard``. Das trug lange, hatte aber
drei Einschraenkungen, die sich nicht wegkonfigurieren liessen:

* Seiten durften zur Laufzeit nicht hinzukommen oder verschwinden, sonst
  zerfiel der interne Seitenstapel.
* Ein Sprung musste sich durch alle Zwischenseiten klicken -- mit deren
  Pruefungen und Dateidialogen. Blieb er unterwegs stecken, landete der
  Benutzer irgendwo, ohne Erklaerung.
* Vorwaertsspringen war gar nicht moeglich: nur besuchte und fehlerhafte
  Schritte waren anklickbar.

Hier ist die Navigation ein eigenes Modell -- **ohne Qt-Widgets**, damit sie
sich ohne Bildschirm pruefen laesst. Es kennt die Seiten nur ueber Rueckrufe:
"ist dieser Schritt anwendbar", "hat er blockierende Meldungen". Das haelt die
Regeln an einer Stelle und testbar.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum

from ..core.catalog import Category

WELCOME_ID = "welcome"
BUILD_ID = "iso"


class Art(Enum):
    WELCOME = "welcome"
    CATEGORY = "category"
    BUILD = "build"


class Status(Enum):
    OFFEN = "offen"
    AKTUELL = "aktuell"
    ERLEDIGT = "erledigt"
    UEBERSPRUNGEN = "uebersprungen"
    FEHLER = "fehler"
    GESPERRT = "gesperrt"


@dataclass(frozen=True, slots=True)
class Step:
    id: str
    art: Art
    titel: str
    icon: str = ""
    category: Category | None = None


@dataclass
class NavigationModel:
    """Der Zustand der Navigation -- reines Python.

    Die Rueckrufe werden von aussen gesetzt; ohne sie verhaelt sich das Modell
    so, als waere alles anwendbar und nichts fehlerhaft. Das macht es in Tests
    brauchbar, ohne dass ein Store existieren muss.
    """

    steps: list[Step] = field(default_factory=list)
    current_id: str = WELCOME_ID
    visited: set[str] = field(default_factory=set)
    locked: bool = False
    build_done: bool = False

    ist_anwendbar: Callable[[Category], bool] = lambda _c: True
    hat_fehler: Callable[[str], bool] = lambda _i: False
    ist_baubereit: Callable[[], bool] = lambda: True

    # -- Aufbau ---------------------------------------------------------------
    @classmethod
    def aus_katalog(cls, kategorien: Iterable[Category]) -> NavigationModel:
        schritte = [Step(WELCOME_ID, Art.WELCOME, "Start", "settings")]
        for kategorie in kategorien:
            if not kategorie.visible:
                continue
            schritte.append(
                Step(
                    kategorie.id,
                    Art.CATEGORY,
                    kategorie.title,
                    kategorie.icon,
                    kategorie,
                )
            )
        schritte.append(Step(BUILD_ID, Art.BUILD, "ISO erstellen", "disc"))
        return cls(steps=schritte)

    # -- Nachschlagen ---------------------------------------------------------
    def step(self, step_id: str) -> Step | None:
        for schritt in self.steps:
            if schritt.id == step_id:
                return schritt
        return None

    def index_of(self, step_id: str) -> int:
        for position, schritt in enumerate(self.steps):
            if schritt.id == step_id:
                return position
        return -1

    def anwendbar(self, schritt: Step) -> bool:
        """Ob dieser Schritt unter der aktuellen Auswahl ueberhaupt vorkommt."""
        if schritt.art is not Art.CATEGORY or schritt.category is None:
            return True
        return self.ist_anwendbar(schritt.category)

    # -- Zustaende ------------------------------------------------------------
    def status(self, schritt: Step) -> Status:
        if schritt.id == self.current_id:
            return Status.AKTUELL
        if not self.anwendbar(schritt):
            return Status.UEBERSPRUNGEN
        if schritt.art is Art.BUILD:
            if self.build_done:
                return Status.ERLEDIGT
            if not self.ist_baubereit():
                return Status.GESPERRT
            return Status.OFFEN
        if schritt.art is Art.CATEGORY and self.hat_fehler(schritt.id):
            return Status.FEHLER
        if schritt.id in self.visited:
            return Status.ERLEDIGT
        return Status.OFFEN

    def statuses(self) -> dict[str, Status]:
        return {schritt.id: self.status(schritt) for schritt in self.steps}

    def anklickbar(self, schritt: Step) -> bool:
        """Waehrend eines Baus bleibt nur der Bauschritt erreichbar."""
        if self.locked:
            return schritt.id == BUILD_ID
        zustand = self.status(schritt)
        return zustand not in (Status.UEBERSPRUNGEN, Status.GESPERRT)

    def progress(self) -> tuple[int, int]:
        """(erledigt, insgesamt) -- ohne Start-, Bau- und uebersprungene Schritte."""
        zutreffend = [
            schritt
            for schritt in self.steps
            if schritt.art is Art.CATEGORY and self.anwendbar(schritt)
        ]
        erledigt = sum(
            1 for schritt in zutreffend if self.status(schritt) is Status.ERLEDIGT
        )
        return erledigt, len(zutreffend)

    # -- Bewegen --------------------------------------------------------------
    def naechster(self, ab: str | None = None) -> str | None:
        """Der naechste anwendbare Schritt -- oder None am Ende."""
        start = self.index_of(ab if ab is not None else self.current_id)
        if start < 0:
            return None
        for schritt in self.steps[start + 1 :]:
            if self.anwendbar(schritt):
                return schritt.id
        return None

    def voriger(self, ab: str | None = None) -> str | None:
        start = self.index_of(ab if ab is not None else self.current_id)
        if start <= 0:
            return None
        for schritt in reversed(self.steps[:start]):
            if self.anwendbar(schritt):
                return schritt.id
        return None

    def richtung(self, ziel: str) -> int:
        """1 vorwaerts, -1 rueckwaerts -- fuer die Richtung des Uebergangs."""
        return 1 if self.index_of(ziel) >= self.index_of(self.current_id) else -1

    def betreten(self, step_id: str) -> None:
        """Merkt den neuen Schritt und den vorigen als besucht."""
        vorher = self.step(self.current_id)
        if vorher is not None and vorher.art is Art.CATEGORY:
            self.visited.add(vorher.id)
        self.current_id = step_id
        schritt = self.step(step_id)
        if schritt is not None and schritt.art is Art.CATEGORY:
            self.visited.add(step_id)

    def alles_besucht(self) -> None:
        """Nach dem Laden eines Profils ist alles eingestellt.

        Wer nur eine Kleinigkeit aendern will, springt direkt dorthin; wer
        gleich bauen will, springt ans Ende. Frueher warf das Laden zurueck auf
        Schritt eins.
        """
        self.visited = {
            schritt.id for schritt in self.steps if schritt.art is Art.CATEGORY
        }
