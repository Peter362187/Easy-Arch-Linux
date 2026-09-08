"""Tests der ISO-Plausibilitaetspruefung.

``mkarchiso`` mit Rueckgabewert 0 heisst nicht, dass eine brauchbare ISO
entstanden ist. Genau die Faelle, in denen sie es nicht ist, muessen hier
auffallen -- sonst faellt es erst am Rechner auf, der nicht startet.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from archcustomiser.core.build.verify import (
    DESKRIPTOR_START,
    MINDESTGROESSE,
    SEKTOR,
    pruefe_iso,
    schreibe_pruefsumme,
    sha256_von,
)


def baue_iso(
    pfad: Path,
    *,
    groesse: int = MINDESTGROESSE,
    cd001: bool = True,
    mbr: bool = True,
    el_torito: bool = True,
    label: str = "MEINARCH_1_0",
) -> Path:
    """Eine Datei, die genug wie eine ISO aussieht, um geprueft zu werden.

    Sie wird duenn angelegt (``truncate``); der Test schreibt nur die paar
    Bytes, auf die es ankommt.
    """
    with pfad.open("wb") as datei:
        datei.truncate(groesse)

        if mbr:
            datei.seek(510)
            datei.write(b"\x55\xaa")

        if cd001:
            # Primaerer Volume Descriptor
            datei.seek(DESKRIPTOR_START)
            datei.write(bytes([1]) + b"CD001" + bytes([1, 0]))
            datei.seek(DESKRIPTOR_START + 40)
            datei.write(label.ljust(32).encode("ascii"))

        if el_torito:
            # Boot-Record im zweiten Deskriptorsektor
            datei.seek(DESKRIPTOR_START + SEKTOR)
            datei.write(
                bytes([0]) + b"CD001" + bytes([1]) + b"EL TORITO SPECIFICATION".ljust(32)
            )

        # Abschluss der Deskriptorenfolge
        datei.seek(DESKRIPTOR_START + 2 * SEKTOR)
        datei.write(bytes([255]) + b"CD001" + bytes([1]))
    return pfad


# ---------------------------------------------------------------------------
# Der gute Fall
# ---------------------------------------------------------------------------


def test_a_well_formed_image_is_plausible(tmp_path: Path) -> None:
    befund = pruefe_iso(baue_iso(tmp_path / "arch.iso"))
    assert befund.plausibel
    assert befund.iso9660
    assert befund.mbr_signatur
    assert befund.el_torito
    assert not befund.probleme


def test_the_volume_label_is_read(tmp_path: Path) -> None:
    befund = pruefe_iso(baue_iso(tmp_path / "arch.iso", label="MINIARCH_1_0"))
    assert befund.datentraeger == "MINIARCH_1_0"


def test_the_summary_mentions_every_check(tmp_path: Path) -> None:
    text = pruefe_iso(baue_iso(tmp_path / "arch.iso")).zusammenfassung()
    for stichwort in ("ISO-9660", "Datentraeger", "MBR-Signatur", "El Torito"):
        assert stichwort in text


# ---------------------------------------------------------------------------
# Die Faelle, wegen derer es die Pruefung gibt
# ---------------------------------------------------------------------------


def test_an_empty_file_is_reported(tmp_path: Path) -> None:
    leer = tmp_path / "leer.iso"
    leer.touch()
    befund = pruefe_iso(leer)
    assert not befund.plausibel
    assert "leer" in befund.probleme[0].lower()


def test_a_missing_file_is_reported(tmp_path: Path) -> None:
    befund = pruefe_iso(tmp_path / "gibt-es-nicht.iso")
    assert not befund.plausibel
    assert befund.probleme


def test_a_truncated_image_is_caught(tmp_path: Path) -> None:
    """Ein abgebrochener Kopiervorgang endet fast immer mitten im Sektor."""
    pfad = baue_iso(tmp_path / "halb.iso", groesse=MINDESTGROESSE + 777)
    befund = pruefe_iso(pfad)
    assert not befund.plausibel
    assert any("2048" in problem for problem in befund.probleme)


def test_a_far_too_small_file_is_caught(tmp_path: Path) -> None:
    pfad = tmp_path / "winzig.iso"
    pfad.write_bytes(b"\x00" * SEKTOR)
    befund = pruefe_iso(pfad)
    assert not befund.plausibel


def test_a_file_without_cd001_is_not_an_image(tmp_path: Path) -> None:
    befund = pruefe_iso(baue_iso(tmp_path / "keine.iso", cd001=False))
    assert not befund.plausibel
    assert any("CD001" in problem for problem in befund.probleme)


# ---------------------------------------------------------------------------
# Hinweise sind keine Probleme
# ---------------------------------------------------------------------------


def test_a_missing_mbr_signature_is_only_a_hint(tmp_path: Path) -> None:
    """Ein reines UEFI-Abbild hat keine -- das ist kein Fehler."""
    befund = pruefe_iso(baue_iso(tmp_path / "uefi.iso", mbr=False))
    assert befund.plausibel
    assert not befund.mbr_signatur
    assert befund.hinweise


def test_a_missing_boot_catalogue_is_only_a_hint(tmp_path: Path) -> None:
    befund = pruefe_iso(baue_iso(tmp_path / "daten.iso", el_torito=False))
    assert befund.plausibel
    assert not befund.el_torito
    assert any("El-Torito" in hinweis for hinweis in befund.hinweise)


# ---------------------------------------------------------------------------
# Pruefsumme
# ---------------------------------------------------------------------------


def test_the_checksum_matches_hashlib(tmp_path: Path) -> None:
    pfad = tmp_path / "datei.bin"
    pfad.write_bytes(b"archcustomiser" * 5000)
    assert sha256_von(pfad) == hashlib.sha256(pfad.read_bytes()).hexdigest()


def test_a_cancelled_checksum_yields_nothing(tmp_path: Path) -> None:
    """Ein Abbruch muss auch mitten in einer 4-GB-Datei durchgreifen."""
    pfad = tmp_path / "datei.bin"
    pfad.write_bytes(b"x" * 1024)
    assert sha256_von(pfad, abbruch=lambda: True) == ""


def test_an_unreadable_file_yields_no_checksum(tmp_path: Path) -> None:
    assert sha256_von(tmp_path / "weg.bin") == ""


def test_the_checksum_file_has_the_sha256sum_format(tmp_path: Path) -> None:
    """``sha256sum -c datei.iso.sha256`` muss ohne Zutun funktionieren."""
    iso = tmp_path / "arch.iso"
    iso.write_bytes(b"x")
    ziel = schreibe_pruefsumme(iso, "abc123")
    assert ziel is not None
    assert ziel.name == "arch.iso.sha256"
    assert ziel.read_text(encoding="utf-8") == "abc123  arch.iso\n"


def test_no_checksum_file_without_a_checksum(tmp_path: Path) -> None:
    assert schreibe_pruefsumme(tmp_path / "arch.iso", "") is None


@pytest.mark.parametrize("name", ["arch.iso", "mein-arch-2026.01.iso"])
def test_the_checksum_file_sits_next_to_the_image(tmp_path: Path, name: str) -> None:
    """``with_suffix`` haette bei einem Punkt in der Version danebengelegen."""
    iso = tmp_path / name
    iso.write_bytes(b"x")
    ziel = schreibe_pruefsumme(iso, "deadbeef")
    assert ziel is not None and ziel.name == name + ".sha256"
