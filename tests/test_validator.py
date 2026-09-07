"""Tests der Einordnung von Paketnamen.

Der wichtigste Test steht ganz oben: ohne Index darf nie "existiert nicht"
behauptet werden.
"""

from __future__ import annotations

import pytest

from archcustomiser.core.packages.models import EntryKind
from archcustomiser.core.packages.validator import classify, validate_all

# ---------------------------------------------------------------------------
# Die zentrale Zusicherung
# ---------------------------------------------------------------------------


def test_without_index_nothing_is_claimed_missing() -> None:
    """Ein Netzausfall darf nicht wie ein Tippfehler aussehen.

    Wuerde hier NOT_FOUND kommen, wuerde der Benutzer einen voellig korrekten
    Paketnamen loeschen, weil das Programm behauptet, es gaebe ihn nicht.
    """
    result = classify("firefox", index=None)
    assert result.kind is EntryKind.UNVERIFIED
    assert "nicht pruefbar" in result.message


def test_degraded_index_never_claims_missing(sample_index) -> None:
    result = classify("gibtesnicht", sample_index, degraded=True)
    assert result.kind is EntryKind.UNVERIFIED


def test_unverified_entries_still_reach_the_package_list(sample_index) -> None:
    """Ungeprueft heisst nicht verworfen -- der Benutzer soll bauen koennen."""
    report = validate_all(["firefox", "htop"], None, degraded=True)
    assert report.profile_packages() == ("firefox", "htop")
    assert report.degraded is True


# ---------------------------------------------------------------------------
# Einordnung
# ---------------------------------------------------------------------------


def test_exact_package(sample_index) -> None:
    result = classify("firefox", sample_index)
    assert result.kind is EntryKind.PACKAGE
    assert result.repo == "extra"
    assert result.version == "154.0-1"


def test_meta_package_is_a_normal_package(sample_index) -> None:
    """base-devel ist seit 2022 ein Meta-Paket, keine Gruppe."""
    result = classify("base-devel", sample_index)
    assert result.kind is EntryKind.PACKAGE


def test_group(sample_index) -> None:
    result = classify("plasma", sample_index)
    assert result.kind is EntryKind.GROUP
    assert set(result.members) == {"plasma-desktop", "plasma-workspace"}


def test_group_is_not_expanded_into_the_package_list(sample_index) -> None:
    """Gruppen loest pacstrap zur Bauzeit auf -- eine Kopie waere sofort veraltet."""
    report = validate_all(["plasma"], sample_index)
    assert report.profile_packages() == ("plasma",)
    assert set(report.expanded_packages()) == {"plasma-desktop", "plasma-workspace"}


def test_ambiguous_provider_blocks(sample_index) -> None:
    """pacman wuerde nachfragen; mkarchiso laeuft ohne Rueckfrage."""
    result = classify("ttf-font", sample_index)
    assert result.kind is EntryKind.PROVIDES_AMBIG
    assert len(result.members) == 2
    assert result.kind.is_blocking


def test_provider_choice_resolves_ambiguity(sample_index) -> None:
    result = classify("ttf-font", sample_index, provider_choices={"ttf-font": "noto-fonts"})
    assert result.kind is EntryKind.PROVIDES_UNIQUE
    assert result.profile_name == "noto-fonts"


def test_unique_provider(sample_index) -> None:
    result = classify("display-manager", sample_index)
    assert result.kind is EntryKind.PROVIDES_UNIQUE
    assert result.members == ("sddm",)


def test_typo_gets_suggestions(sample_index) -> None:
    result = classify("neovm", sample_index)
    assert result.kind is EntryKind.NOT_FOUND
    assert "neovim" in result.suggestions


def test_invalid_name(sample_index) -> None:
    result = classify("-Sy", sample_index)
    assert result.kind is EntryKind.INVALID_NAME


def test_version_constraint_is_stripped_with_a_note(sample_index) -> None:
    """archiso-Paketlisten kennen keine Versionsbindung."""
    result = classify("firefox>=140", sample_index)
    assert result.kind is EntryKind.PACKAGE
    assert result.normalized == "firefox"
    assert result.constraint == ">=140"
    assert any("Versionsangabe" in note for note in result.notes)


def test_report_separates_blocking_from_usable(sample_index) -> None:
    report = validate_all(
        ["firefox", "gibtesnicht", "-Sy", "plasma", "ttf-font"], sample_index
    )
    assert not report.is_clean
    assert {entry.query for entry in report.blocking} == {
        "gibtesnicht",
        "-Sy",
        "ttf-font",
    }
    assert report.profile_packages() == ("firefox", "plasma")


@pytest.mark.parametrize(
    ("size_bytes", "expected"),
    [
        (50_000, "49 KB"),        # winzig -> Kilobyte, niemals "0 MB"
        (400_000, "0.4 MB"),      # klein -> eine Nachkommastelle
        (309_215_874, "295 MB"),  # gross -> ganze Megabyte
        (None, ""),
    ],
)
def test_size_is_never_displayed_as_zero(size_bytes, expected) -> None:
    """Ein Paket mit 50 KB als '0 MB' anzuzeigen sieht nach einem Fehler aus."""
    from archcustomiser.core.packages.models import Resolution

    entry = Resolution(
        query="x", normalized="x", kind=EntryKind.PACKAGE, installed_size=size_bytes
    )
    assert entry.size_text == (f", {expected}" if expected else "")
    assert "0 MB" not in entry.size_text


def test_a_partial_index_never_claims_a_package_is_missing(sample_index) -> None:
    """Faellt ein Repository aus, gilt der Rest nicht als vollstaendig.

    Vorher meldete ein Teilindex jedes Paket des ausgefallenen Repositories als
    "nicht gefunden" -- blockierend, mit Tippfehler-Vorschlaegen. Genau die
    Aussage, die die zentrale Zusicherung der Schicht verbietet.
    """
    import dataclasses

    from archcustomiser.core.packages.models import EntryKind
    from archcustomiser.core.packages.validator import classify

    # Ohne Ausfall: unbekannte Namen sind unbekannt.
    assert classify("gibtesnicht", sample_index).kind is EntryKind.NOT_FOUND

    unvollstaendig = dataclasses.replace(
        sample_index.meta, missing_repos=("core",)
    )
    sample_index.meta = unvollstaendig

    ergebnis = classify("linux", sample_index)
    assert ergebnis.kind is EntryKind.UNVERIFIED
    assert any("core" in notiz for notiz in ergebnis.notes)


def test_the_chosen_provider_is_named_in_the_message(sample_index) -> None:
    """Der Text nannte weiterhin den alphabetisch ersten Anbieter."""
    from archcustomiser.core.packages.validator import classify

    ergebnis = classify(
        "ttf-font", sample_index, provider_choices={"ttf-font": "ttf-dejavu"}
    )
    assert "ttf-dejavu" in ergebnis.message


def test_free_multilib_packages_pull_in_their_repository(sample_index) -> None:
    """Ein frei eingegebenes lib32-Paket braucht multilib in der pacman.conf.

    Der Katalog nennt das Repository nur an seinen eigenen Optionen; ein
    Freitext-Paket wurde zwar gegen multilib geprueft und als gefunden
    gemeldet, aktivierte es aber nicht -- pacstrap brach im Bau ab.
    """
    from archcustomiser.core.packages.validator import validate_all

    bericht = validate_all(["firefox"], sample_index)
    assert bericht.required_repositories() == ()

    # Das Testarchiv fuehrt alles unter 'extra'; ein multilib-Eintrag wird
    # deshalb von Hand nachgestellt.
    import dataclasses

    from archcustomiser.core.packages.models import ValidationReport

    eintrag = dataclasses.replace(bericht.entries[0], repo="multilib")
    kuenstlich = ValidationReport(entries=(eintrag,), index_meta=bericht.index_meta)
    assert kuenstlich.required_repositories() == ("multilib",)
