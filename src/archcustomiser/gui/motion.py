"""Die eine Stelle, an der Bewegung entsteht.

Drei Zusicherungen, die jede Animation im Programm einhaelt:

**Ein Schalter reicht.** Ob eine Bewegung laufen darf, entscheidet nicht das
Widget, sondern ``duration()``. Ist die Dauer null, setzt ``run()`` den Endwert
sofort und startet gar nichts erst -- der Zustand am Ende ist derselbe, nur
ohne Weg dorthin. Damit gilt "Animationen reduzieren" wirklich ueberall, auch
in Widgets, die niemand daraufhin durchgesehen hat.

**Offscreen wird nicht animiert.** Unter ``QT_QPA_PLATFORM=offscreen`` gibt es
keinen Bildschirm, der etwas anzeigen koennte; die Tests pruefen Endzustaende.
Ohne diese Regel warteten sie auf Animationen, die niemand sieht.

**Nichts laeuft im Leerlauf.** Jede laufende Animation traegt sich hier ein;
``active_count()`` gibt die Zahl zurueck. Ein Test kann damit belegen, dass die
Oberflaeche nach dem Aufbau tatsaechlich still steht -- die Anforderung "keine
CPU-Last im Leerlauf" ist damit pruefbar und nicht nur behauptet.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QObject,
    QVariantAnimation,
)

__all__ = [
    "ERFOLG",
    "INTRO",
    "LANGSAM",
    "NORMAL",
    "SCHNELL",
    "active_count",
    "animate",
    "duration",
    "is_reduced",
    "run",
    "set_reduced",
]

# Dauern in Millisekunden. Alles unter 300 ms wirkt als Reaktion, alles
# darueber als eigener Moment -- deshalb tragen nur Intro und Erfolg laengere
# Zeiten, und auch die nur einmal je Programmlauf.
SCHNELL = 120
NORMAL = 200
LANGSAM = 280
INTRO = 900
ERFOLG = 700

_reduziert: bool | None = None
_laufend: set[QAbstractAnimation] = set()


def _offscreen() -> bool:
    return os.environ.get("QT_QPA_PLATFORM", "") == "offscreen"


def set_reduced(wert: bool | None) -> None:
    """Setzt die Antwort auf "soll Bewegung laufen?" fuer die ganze Anwendung.

    ``None`` heisst: wieder selbst entscheiden (Umgebung und Bildschirm).
    """
    global _reduziert
    _reduziert = wert


def is_reduced() -> bool:
    if _reduziert is not None:
        return _reduziert
    if _offscreen():
        return True
    return os.environ.get("ARCHCUSTOMISER_MOTION", "") == "off"


def duration(dauer: int) -> int:
    """Die tatsaechliche Dauer -- null, wenn nicht animiert werden soll."""
    return 0 if is_reduced() else max(0, int(dauer))


def active_count() -> int:
    """Wie viele Animationen gerade laufen.

    Fuer den Leerlauf-Test: nach dem Aufbau des Fensters muss das null sein.
    """
    return len(_laufend)


def animate(
    ziel: QObject,
    *,
    von: Any,
    bis: Any,
    dauer: int = NORMAL,
    kurve: QEasingCurve.Type = QEasingCurve.Type.OutCubic,
    setzen: Callable[[Any], None],
    fertig: Callable[[], None] | None = None,
) -> QVariantAnimation | None:
    """Animiert einen Wert und uebergibt ihn fortlaufend an ``setzen``.

    Bewusst ueber einen Rueckruf statt ueber eine Qt-Eigenschaft: die Widgets
    dieses Programms zeichnen sich selbst und brauchen dafuer keine
    registrierten Properties -- nur einen Wert und ein ``update()``.

    Liefert die laufende Animation, oder ``None``, wenn nicht animiert wurde
    (dann steht der Endwert bereits).
    """
    laufzeit = duration(dauer)
    if laufzeit <= 0:
        setzen(bis)
        if fertig is not None:
            fertig()
        return None

    animation = QVariantAnimation(ziel)
    animation.setStartValue(von)
    animation.setEndValue(bis)
    animation.setDuration(laufzeit)
    animation.setEasingCurve(kurve)
    animation.valueChanged.connect(setzen)

    def abschluss() -> None:
        _laufend.discard(animation)
        if fertig is not None:
            fertig()

    animation.finished.connect(abschluss)
    # Auch austragen, wenn die Animation gar nicht zu Ende kommt. Qt sendet
    # ``finished`` nur beim regulaeren Ende -- nicht beim Anhalten und nicht
    # beim Zerstoeren. Die Animation ist aber ein Kind ihres Ziels: verschwindet
    # das Widget mitten in der Bewegung, war ihr Eintrag hier unsterblich.
    # Zwei Folgen hatte das, und beide sind unangenehm: ``active_count()``
    # kehrte nie mehr auf null zurueck (womit die Leerlaufzusicherung
    # unpruefbar wurde), und ``stop_all()`` fasste beim Schliessen ein
    # geloeschtes C++-Objekt an -- mitten in ``closeEvent``.
    animation.destroyed.connect(lambda *_: _laufend.discard(animation))
    _laufend.add(animation)
    animation.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
    return animation


def run(animation: QAbstractAnimation | None, fertig: Callable[[], None] | None = None) -> None:
    """Startet eine fertig aufgebaute Animation und zaehlt sie mit."""
    if animation is None:
        if fertig is not None:
            fertig()
        return
    if is_reduced():
        # Sofort ans Ende springen: der Endzustand zaehlt, nicht der Weg.
        animation.setCurrentTime(animation.duration())
        animation.stop()
        if fertig is not None:
            fertig()
        return

    def abschluss() -> None:
        _laufend.discard(animation)
        if fertig is not None:
            fertig()

    animation.finished.connect(abschluss)
    animation.destroyed.connect(lambda *_: _laufend.discard(animation))
    _laufend.add(animation)
    animation.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)


def stop_all() -> None:
    """Beendet alles Laufende -- beim Schliessen eines Fensters.

    Wehrhaft gegen Objekte, die es nicht mehr gibt: ``stop_all`` laeuft mitten
    im Abbau eines Fensters, und ein Fehler hier wuerde ``closeEvent`` auf
    halbem Weg abbrechen.
    """
    for animation in list(_laufend):
        try:
            animation.stop()
        except RuntimeError:      # das C++-Objekt ist schon weg
            pass
    _laufend.clear()
