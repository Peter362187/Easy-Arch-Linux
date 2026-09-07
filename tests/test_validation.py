"""Tests der Feldpruefungen.

Dieses Modul hatte bis zur Durchsicht am 02.09.2026 **keinen einzigen direkten
Test** -- 393 Zeilen, siebzehn Validatoren, darunter die Markenrichtlinien-
Pruefung, die der Katalog selbst als Compliance-Anforderung dokumentiert.

Geprueft wird jeweils beides: dass Gueltiges durchgeht und dass Ungueltiges mit
einer *verstaendlichen* Begruendung abgelehnt wird. Eine Ablehnung ohne
Begruendung ist in der Oberflaeche fast so schlecht wie gar keine Pruefung.
"""

from __future__ import annotations

import pytest

from archcustomiser.core import validation


def _fehler(name: str, wert) -> str:
    ergebnis = validation.validate(name, wert)
    assert not ergebnis.ok, f"{name}({wert!r}) haette abgelehnt werden muessen"
    assert ergebnis.message.strip(), "eine Ablehnung ohne Begruendung hilft niemandem"
    return ergebnis.message


def _ok(name: str, wert) -> None:
    ergebnis = validation.validate(name, wert)
    assert ergebnis.ok, f"{name}({wert!r}) wurde abgelehnt: {ergebnis.message}"


# ---------------------------------------------------------------------------
# Rechnername und Benutzer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("wert", ["arch", "mein-rechner", "pc1", "a"])
def test_valid_hostnames(wert: str) -> None:
    _ok("hostname", wert)


@pytest.mark.parametrize(
    "wert",
    ["", "-anfang", "ende-", "mit punkt.", "GROSS?", "a" * 70, "mit leerzeichen"],
)
def test_invalid_hostnames(wert: str) -> None:
    _fehler("hostname", wert)


@pytest.mark.parametrize("wert", ["jason", "user1", "_dienst", "a-b"])
def test_valid_usernames(wert: str) -> None:
    _ok("username", wert)


@pytest.mark.parametrize("wert", ["", "1abc", "Gross", "mit leerzeichen", "root!"])
def test_invalid_usernames(wert: str) -> None:
    _fehler("username", wert)


def test_gecos_refuses_the_field_separator() -> None:
    """Der Klarname landet in /etc/passwd -- ein Doppelpunkt trennt dort Felder."""
    _ok("gecos", "Jason Heunisch")
    assert "Doppelpunkt" in _fehler("gecos", "Jason:Heunisch")


def test_gecos_refuses_line_breaks() -> None:
    _fehler("gecos", "Jason\nzweite Zeile")


# ---------------------------------------------------------------------------
# Passwort -- bewusst milde
# ---------------------------------------------------------------------------


def test_a_short_password_warns_but_does_not_block() -> None:
    """Spec: eine Warnung, keine Blockade. Es ist das System des Benutzers."""
    ergebnis = validation.validate("password", "kurz")
    assert ergebnis.ok is False or ergebnis.is_warning, "sollte hoechstens warnen"
    if not ergebnis.ok:
        assert ergebnis.is_warning, "ein kurzes Passwort darf nicht blockieren"


def test_a_long_password_passes() -> None:
    _ok("password", "ein-ordentlich-langes-passwort")


# ---------------------------------------------------------------------------
# Markenrichtlinie -- vom Katalog als Anforderung dokumentiert
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("wert", ["Arch Linux", "arch linux", "ARCH LINUX"])
def test_the_distro_name_may_not_impersonate_arch(wert: str) -> None:
    """Sich als Arch Linux auszugeben ist der Fall, den die Richtlinie meint."""
    _fehler("distro_name", wert)


@pytest.mark.parametrize("wert", ["ArchCustom", "Archly", "ARCHBOX"])
def test_an_arch_prefix_warns(wert: str) -> None:
    """Die Markenrichtlinie verlangt hier eine Warnung -- keine Freigabe.

    Frueher lautete die Zusicherung ``is_warning or ok``; das ist fuer jedes
    Ergebnis ausser einem harten Fehler wahr. Verschwaende der Validator seine
    Warnung, waere der Test gruen geblieben.
    """
    ergebnis = validation.validate("distro_name", wert)
    assert ergebnis.is_warning, "ein Arch-Praefix muss warnen"
    assert "Arch" in ergebnis.message


@pytest.mark.parametrize("wert", ["FLOS", "MeineDistro", "Test 1"])
def test_ordinary_distro_names_pass(wert: str) -> None:
    ergebnis = validation.validate("distro_name", wert)
    assert ergebnis.ok or ergebnis.is_warning


# ---------------------------------------------------------------------------
# Formatgebundene Felder
# ---------------------------------------------------------------------------


def test_iso_label_follows_iso9660() -> None:
    _ok("iso_label", "FLOS_1_0")
    assert "Grossbuchstaben" in _fehler("iso_label", "flos")
    assert "32" in _fehler("iso_label", "A" * 40)


def test_install_dir_follows_what_mkarchiso_accepts() -> None:
    """mkarchiso prueft das selbst und bricht sonst mitten im Lauf ab."""
    _ok("install_dir", "flos")
    _fehler("install_dir", "FLOS")
    _fehler("install_dir", "mit-strich")
    assert "30" in _fehler("install_dir", "a" * 40)


def test_version_string_stays_simple() -> None:
    _ok("version_string", "1.0")
    _ok("version_string", "2026.09-1")
    _fehler("version_string", "")
    _fehler("version_string", "1.0 (beta)")


def test_url_must_be_http() -> None:
    _ok("url", "https://example.org")
    _ok("url", "")            # leer ist erlaubt, das Feld ist freiwillig
    _fehler("url", "example.org")
    _fehler("url", "ftp://example.org")
    _fehler("url", "https://ex ample.org")


# ---------------------------------------------------------------------------
# Bootmenue-Titel -- Befund der Durchsicht
# ---------------------------------------------------------------------------


def test_menu_title_blocks_what_would_break_the_boot_config() -> None:
    """Das Feld hatte weder Validator noch Laengenbegrenzung.

    Der Titel geht in drei Bootlader-Formate; in GRUB ist die Konfiguration
    eine Skriptsprache, und ein Anfuehrungszeichen beendet dort die
    Zeichenkette mitten im ``menuentry``.
    """
    _ok("menu_title", "Mein Linux 1.0")
    _ok("menu_title", "")     # wird aus Name und Version abgeleitet
    for zeichen in ['"', "'", "`", "$", "\\", "{", "}", "(", ")", ";", "#"]:
        _fehler("menu_title", f"Titel{zeichen}x")
    assert "60" in _fehler("menu_title", "a" * 61)


# ---------------------------------------------------------------------------
# Sprache, Tastatur, Zeitzone
# ---------------------------------------------------------------------------


def test_locale_and_keymap_accept_the_usual_values() -> None:
    _ok("locale", "de_DE.UTF-8")
    _ok("locale", "C.UTF-8")
    _ok("keymap", "de-latin1")


def test_timezone_accepts_a_real_zone() -> None:
    ergebnis = validation.validate("timezone", "Europe/Berlin")
    assert ergebnis.ok or ergebnis.is_warning


def test_an_unknown_validator_does_not_crash() -> None:
    """Ein Tippfehler im Katalog darf die Oberflaeche nicht mitreissen."""
    ergebnis = validation.validate("gibtesnicht", "irgendwas")
    assert ergebnis is not None
