"""Tests der Bewegungsschicht.

Die Zusicherung des Projekts lautet: eine Oberflaeche im Leerlauf verbraucht
keine Rechenzeit. Gemessen wird sie an ``motion.active_count()``.

Diese Tests schalten die Bewegung deshalb **an**. Der uebrige Lauf setzt
``ARCHCUSTOMISER_MOTION=off``; dort ist jede Dauer null, ``animate()`` kehrt
sofort zurueck und die Buchfuehrung wird gar nicht erst betreten -- ein Test
darueber koennte nur immer null sehen und waere damit wertlos.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QWidget

from archcustomiser.gui import motion


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def bewegt(qapp):
    """Bewegung an -- und danach wieder in den Zustand des uebrigen Laufs."""
    vorher = motion.is_reduced()
    motion.stop_all()
    motion.set_reduced(False)
    yield
    motion.stop_all()
    motion.set_reduced(vorher)


def warte(millisekunden: int) -> None:
    schleife = QEventLoop()
    QTimer.singleShot(millisekunden, schleife.quit)
    schleife.exec()


# ---------------------------------------------------------------------------
# Buchfuehrung
# ---------------------------------------------------------------------------


def test_a_running_animation_is_counted(bewegt) -> None:
    ziel = QWidget()
    werte: list[float] = []
    motion.animate(ziel, von=0.0, bis=1.0, dauer=200, setzen=werte.append)
    assert motion.active_count() == 1
    ziel.deleteLater()


def test_a_finished_animation_is_released(bewegt) -> None:
    ziel = QWidget()
    motion.animate(ziel, von=0.0, bis=1.0, dauer=60, setzen=lambda _w: None)
    assert motion.active_count() == 1
    warte(300)
    assert motion.active_count() == 0
    ziel.deleteLater()


def test_a_destroyed_target_releases_its_animation(bewegt) -> None:
    """Der Fall, an dem die Buchfuehrung frueher zerbrach.

    Die Animation ist ein Kind ihres Ziels. Verschwindet das Widget mitten in
    der Bewegung, sendet Qt kein ``finished`` -- der Eintrag blieb ewig stehen,
    ``active_count()`` kehrte nie auf null zurueck, und ``stop_all()`` fasste
    beim Schliessen des Fensters ein geloeschtes C++-Objekt an.
    """
    ziel = QWidget()
    motion.animate(ziel, von=0.0, bis=1.0, dauer=5000, setzen=lambda _w: None)
    assert motion.active_count() == 1

    ziel.deleteLater()
    warte(100)
    assert motion.active_count() == 0


def test_stop_all_survives_a_destroyed_target(bewegt) -> None:
    """``stop_all`` laeuft mitten im Abbau eines Fensters."""
    ziel = QWidget()
    motion.animate(ziel, von=0.0, bis=1.0, dauer=5000, setzen=lambda _w: None)
    ziel.deleteLater()
    warte(100)
    motion.stop_all()          # darf nicht werfen
    assert motion.active_count() == 0


def test_stopping_all_empties_the_registry(bewegt) -> None:
    ziele = [QWidget() for _ in range(3)]
    for widget in ziele:
        motion.animate(widget, von=0.0, bis=1.0, dauer=5000, setzen=lambda _w: None)
    assert motion.active_count() == 3
    motion.stop_all()
    assert motion.active_count() == 0
    for widget in ziele:
        widget.deleteLater()


# ---------------------------------------------------------------------------
# Reduzierte Bewegung
# ---------------------------------------------------------------------------


def test_with_reduced_motion_the_end_value_is_set_at_once(qapp) -> None:
    vorher = motion.is_reduced()
    motion.set_reduced(True)
    try:
        werte: list[float] = []
        fertig: list[int] = []
        ergebnis = motion.animate(
            QWidget(),
            von=0.0,
            bis=1.0,
            dauer=500,
            setzen=werte.append,
            fertig=lambda: fertig.append(1),
        )
        assert ergebnis is None
        assert werte == [1.0]
        assert fertig == [1]
        assert motion.active_count() == 0
    finally:
        motion.set_reduced(vorher)


def test_duration_is_zero_when_motion_is_reduced(qapp) -> None:
    vorher = motion.is_reduced()
    motion.set_reduced(True)
    try:
        assert motion.duration(motion.NORMAL) == 0
    finally:
        motion.set_reduced(vorher)


def test_duration_is_the_requested_value_otherwise(bewegt) -> None:
    assert motion.duration(motion.NORMAL) == motion.NORMAL


# ---------------------------------------------------------------------------
# Der Seitenstapel raeumt seinen Uebergang ab
# ---------------------------------------------------------------------------


def test_an_interrupted_page_transition_leaves_nothing_behind(bewegt, qapp) -> None:
    """Zweimal schnell "Weiter" strandete frueher je eine Animation."""
    from archcustomiser.gui.widgets.page_stack import AnimatedStack

    stapel = AnimatedStack()
    for _ in range(3):
        stapel.addWidget(QWidget())
    stapel.resize(400, 300)
    stapel.show()
    warte(50)

    stapel.set_current(1, richtung=1)
    stapel.set_current(2, richtung=1)
    warte(500)

    assert motion.active_count() == 0
    stapel.hide()
    stapel.deleteLater()
