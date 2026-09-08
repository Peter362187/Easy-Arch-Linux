"""Tests des Navigationsmodells -- ohne Qt und ohne Bildschirm.

Genau deshalb liegt dieses Modell separat: die Regeln, welcher Schritt wann
erreichbar ist, sind die Stelle, an der die alte Oberflaeche ihre haesslichsten
Fehler hatte, und sie lassen sich hier ohne ein einziges Widget pruefen.
"""

from __future__ import annotations

import pytest

from archcustomiser.gui.navigation import (
    BUILD_ID,
    WELCOME_ID,
    Art,
    NavigationModel,
    Status,
    Step,
)


class FakeKategorie:
    """Gerade genug Kategorie fuer das Modell."""

    def __init__(self, kennung: str, titel: str = "") -> None:
        self.id = kennung
        self.title = titel or kennung.title()
        self.icon = ""
        self.visible = True


def modell(*namen: str) -> NavigationModel:
    schritte = [Step(WELCOME_ID, Art.WELCOME, "Start")]
    schritte += [
        Step(name, Art.CATEGORY, name.title(), "", FakeKategorie(name))
        for name in namen
    ]
    schritte.append(Step(BUILD_ID, Art.BUILD, "ISO erstellen"))
    return NavigationModel(steps=schritte)


# ---------------------------------------------------------------------------
# Aufbau
# ---------------------------------------------------------------------------


def test_the_welcome_step_comes_first_and_the_build_step_last() -> None:
    m = modell("a", "b")
    assert m.steps[0].id == WELCOME_ID
    assert m.steps[-1].id == BUILD_ID


def test_invisible_categories_are_left_out() -> None:
    sichtbar = FakeKategorie("a")
    versteckt = FakeKategorie("b")
    versteckt.visible = False
    m = NavigationModel.aus_katalog([sichtbar, versteckt])
    assert {schritt.id for schritt in m.steps} == {WELCOME_ID, "a", BUILD_ID}


def test_an_unknown_step_is_simply_absent() -> None:
    m = modell("a")
    assert m.step("gibt-es-nicht") is None
    assert m.index_of("gibt-es-nicht") == -1


# ---------------------------------------------------------------------------
# Ueberspringen
# ---------------------------------------------------------------------------


def test_a_step_that_does_not_apply_is_skipped() -> None:
    m = modell("a", "b", "c")
    m.ist_anwendbar = lambda kategorie: kategorie.id != "b"
    assert m.statuses()["b"] is Status.UEBERSPRUNGEN


def test_next_jumps_over_a_skipped_step() -> None:
    m = modell("a", "b", "c")
    m.ist_anwendbar = lambda kategorie: kategorie.id != "b"
    m.current_id = "a"
    assert m.naechster() == "c"


def test_back_jumps_over_a_skipped_step() -> None:
    m = modell("a", "b", "c")
    m.ist_anwendbar = lambda kategorie: kategorie.id != "b"
    m.current_id = "c"
    assert m.voriger() == "a"


def test_a_skipped_step_is_not_clickable() -> None:
    m = modell("a", "b")
    m.ist_anwendbar = lambda kategorie: kategorie.id != "b"
    schritt = m.step("b")
    assert schritt is not None and not m.anklickbar(schritt)


def test_the_last_step_has_no_successor() -> None:
    m = modell("a")
    m.current_id = BUILD_ID
    assert m.naechster() is None


def test_the_first_step_has_no_predecessor() -> None:
    m = modell("a")
    m.current_id = WELCOME_ID
    assert m.voriger() is None


# ---------------------------------------------------------------------------
# Zustaende
# ---------------------------------------------------------------------------


def test_the_current_step_is_marked_as_such() -> None:
    m = modell("a", "b")
    m.current_id = "b"
    assert m.statuses()["b"] is Status.AKTUELL


def test_a_blocking_issue_marks_a_step_as_faulty() -> None:
    m = modell("a", "b")
    m.hat_fehler = lambda kennung: kennung == "a"
    assert m.statuses()["a"] is Status.FEHLER


def test_a_fixed_step_is_no_longer_faulty() -> None:
    """Ein einmal rot markierter Schritt blieb frueher rot."""
    fehlerhaft = {"a"}
    m = modell("a", "b")
    m.hat_fehler = lambda kennung: kennung in fehlerhaft
    assert m.statuses()["a"] is Status.FEHLER
    fehlerhaft.clear()
    assert m.statuses()["a"] is not Status.FEHLER


def test_a_visited_step_counts_as_done() -> None:
    m = modell("a", "b")
    m.current_id = "b"
    m.visited.add("a")
    assert m.statuses()["a"] is Status.ERLEDIGT


def test_the_build_step_is_locked_until_the_configuration_is_valid() -> None:
    m = modell("a")
    m.ist_baubereit = lambda: False
    assert m.statuses()[BUILD_ID] is Status.GESPERRT

    m.ist_baubereit = lambda: True
    assert m.statuses()[BUILD_ID] is Status.OFFEN


def test_a_finished_build_stays_marked_as_done() -> None:
    m = modell("a")
    m.ist_baubereit = lambda: False
    m.build_done = True
    assert m.statuses()[BUILD_ID] is Status.ERLEDIGT


# ---------------------------------------------------------------------------
# Fortschritt
# ---------------------------------------------------------------------------


def test_progress_counts_only_applicable_categories() -> None:
    m = modell("a", "b", "c")
    m.ist_anwendbar = lambda kategorie: kategorie.id != "c"
    m.current_id = "b"
    m.visited.add("a")
    erledigt, gesamt = m.progress()
    assert (erledigt, gesamt) == (1, 2)


def test_progress_ignores_the_welcome_and_build_steps() -> None:
    m = modell("a")
    _erledigt, gesamt = m.progress()
    assert gesamt == 1


# ---------------------------------------------------------------------------
# Sperre waehrend eines Baus
# ---------------------------------------------------------------------------


def test_while_locked_only_the_build_step_is_reachable() -> None:
    m = modell("a", "b")
    m.locked = True
    erreichbar = {schritt.id for schritt in m.steps if m.anklickbar(schritt)}
    assert erreichbar == {BUILD_ID}


# ---------------------------------------------------------------------------
# Bewegen
# ---------------------------------------------------------------------------


def test_entering_a_step_marks_the_previous_one_as_visited() -> None:
    m = modell("a", "b")
    m.current_id = "a"
    m.betreten("b")
    assert "a" in m.visited
    assert m.current_id == "b"


def test_the_welcome_step_is_never_marked_as_visited() -> None:
    """Es gibt dort nichts einzustellen -- ein Haken waere sinnlos."""
    m = modell("a")
    m.betreten("a")
    assert WELCOME_ID not in m.visited


def test_direction_tells_forward_from_backward() -> None:
    m = modell("a", "b", "c")
    m.current_id = "b"
    assert m.richtung("c") == 1
    assert m.richtung("a") == -1


@pytest.mark.parametrize("ziel", ["a", "b", "c"])
def test_loading_a_profile_marks_everything_as_visited(ziel: str) -> None:
    """Wer ein Profil laedt, hat alles eingestellt -- und darf ueberall hin."""
    m = modell("a", "b", "c")
    m.alles_besucht()
    schritt = m.step(ziel)
    assert schritt is not None and m.anklickbar(schritt)
    assert m.statuses()[ziel] is Status.ERLEDIGT
