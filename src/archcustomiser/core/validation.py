"""Validatoren fuer Formularfelder.

Registry-basiert: ein Feld im Katalog nennt ``validator: hostname``, und die
GUI zieht die Pruefung hier heraus. Ein neuer Validator ist eine Funktion plus
ein Registry-Eintrag -- keine Aenderung an der GUI.

Alle Validatoren geben ``ValidationResult`` zurueck statt zu werfen: der Wizard
zeigt Fehler an, waehrend der Benutzer noch tippt, und darf dabei nicht
abstuerzen.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_HOSTNAME_LENGTH = 63
MAX_USERNAME_LENGTH = 32
MAX_ISO_LABEL_LENGTH = 32
MAX_INSTALL_DIR_LENGTH = 30


@dataclass(frozen=True, slots=True)
class ValidationResult:
    ok: bool
    message: str = ""
    severity: str = "error"   # error | warning

    @property
    def is_warning(self) -> bool:
        return not self.ok and self.severity == "warning"


OK = ValidationResult(True)


def _fail(message: str) -> ValidationResult:
    return ValidationResult(False, message)


def _warn(message: str) -> ValidationResult:
    return ValidationResult(False, message, severity="warning")


Validator = Callable[[Any], ValidationResult]


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------

_HOSTNAME_LABEL = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")


def validate_hostname(value: Any) -> ValidationResult:
    text = str(value or "").strip()
    if not text:
        return _fail("Ein Hostname wird benoetigt.")
    if len(text) > 253:
        return _fail("Der Hostname darf hoechstens 253 Zeichen lang sein.")
    for label in text.split("."):
        if not label:
            return _fail("Der Hostname enthaelt einen leeren Abschnitt (doppelter Punkt).")
        if len(label) > MAX_HOSTNAME_LENGTH:
            return _fail(
                f"Der Abschnitt {label!r} ist laenger als {MAX_HOSTNAME_LENGTH} Zeichen."
            )
        if not _HOSTNAME_LABEL.match(label):
            return _fail(
                "Erlaubt sind Buchstaben, Ziffern und Bindestriche; "
                "Anfang und Ende muessen Buchstabe oder Ziffer sein."
            )
    return OK


# useradd erlaubt genau das (NAME_REGEX aus shadow-utils).
_USERNAME = re.compile(r"^[a-z_][a-z0-9_-]*\$?$")

# Namen, die auf Arch-Systemen bereits von Systemdiensten belegt sind.
_RESERVED_USERNAMES = frozenset(
    {
        "root", "bin", "daemon", "mail", "ftp", "http", "nobody", "dbus",
        "systemd-journal-remote", "systemd-network", "systemd-resolve",
        "systemd-timesync", "systemd-coredump", "uuidd", "polkitd", "sys",
        "adm", "lp", "mem", "kmem", "wheel", "tty", "disk",
    }
)


def validate_username(value: Any) -> ValidationResult:
    text = str(value or "").strip()
    if not text:
        return _fail("Ein Benutzername wird benoetigt.")
    if len(text) > MAX_USERNAME_LENGTH:
        return _fail(f"Hoechstens {MAX_USERNAME_LENGTH} Zeichen.")
    if not _USERNAME.match(text):
        return _fail(
            "Erlaubt sind Kleinbuchstaben, Ziffern, Bindestrich und Unterstrich. "
            "Das erste Zeichen muss ein Kleinbuchstabe oder Unterstrich sein."
        )
    if text in _RESERVED_USERNAMES:
        return _fail(f"{text!r} ist ein Systemkonto und kann nicht verwendet werden.")
    return OK


def validate_gecos(value: Any) -> ValidationResult:
    """Der Klarname landet in /etc/passwd -- Doppelpunkt waere ein Feldtrenner."""
    text = str(value or "")
    if ":" in text:
        return _fail("Doppelpunkte sind hier nicht erlaubt (Feldtrenner in /etc/passwd).")
    if "\n" in text or "\r" in text:
        return _fail("Zeilenumbrueche sind nicht erlaubt.")
    if len(text) > 255:
        return _fail("Hoechstens 255 Zeichen.")
    return OK


def validate_password(value: Any) -> ValidationResult:
    """Bewusst milde: eine Warnung, keine Blockade.

    Die Anwendung baut ein persoenliches Installationsmedium. Erzwungene
    Passwortregeln fuehren dort erfahrungsgemaess zu schlechteren Passwoertern,
    nicht zu besseren.
    """
    text = str(value or "")
    if not text:
        return OK
    if len(text) < 8:
        return _warn("Kurze Passwoerter sind leicht zu erraten (empfohlen: mindestens 8 Zeichen).")
    return OK


_LOCALE = re.compile(r"^[a-z]{2,3}(_[A-Z]{2})?(\.[A-Za-z0-9-]+)?(@[A-Za-z0-9]+)?$")


# Von glibc eingebaute Locales ohne Landesteil. ``C.UTF-8`` ist die, die
# archiso selbst verwendet -- und der Vorgabewert in ``airootfs.py`` und
# ``packages.py``. Der Validator lehnte sie ab: wer genau den Wert eintrug, den
# das Programm ohne Eingabe annimmt, kam an dieser Stelle nicht weiter.
BUILTIN_LOCALES = frozenset({"C", "C.UTF-8", "POSIX"})


def validate_locale(value: Any) -> ValidationResult:
    text = str(value or "").strip()
    if not text:
        return _fail("Eine Locale wird benoetigt.")
    if text in BUILTIN_LOCALES:
        return OK
    if not _LOCALE.match(text):
        return _fail(
            "Format erwartet: sprache_LAND.KODIERUNG, z.B. de_DE.UTF-8 "
            "(oder C.UTF-8 fuer ein System ohne Uebersetzung)"
        )
    if "UTF-8" not in text.upper():
        return _warn("Nicht-UTF-8-Locales fuehren regelmaessig zu Darstellungsproblemen.")
    return OK


_KEYMAP = re.compile(r"^[A-Za-z0-9._-]+$")


def validate_keymap(value: Any) -> ValidationResult:
    text = str(value or "").strip()
    if not text:
        return _fail("Eine Tastaturbelegung wird benoetigt.")
    if not _KEYMAP.match(text):
        return _fail("Erlaubt sind Buchstaben, Ziffern, Punkt, Bindestrich und Unterstrich.")
    return OK


_TIMEZONE = re.compile(r"^[A-Za-z][A-Za-z0-9+_-]*(/[A-Za-z0-9+_-]+)*$")


def validate_timezone(value: Any) -> ValidationResult:
    text = str(value or "").strip()
    if not text:
        return _fail("Eine Zeitzone wird benoetigt.")
    if not _TIMEZONE.match(text):
        return _fail("Format erwartet: Region/Stadt, z.B. Europe/Berlin")
    try:
        from zoneinfo import available_timezones

        known = available_timezones()
    except Exception:
        return OK
    if not known:
        # Ohne installierte Zeitzonendatenbank kann hier nichts geprueft werden;
        # dann darf auch nichts behauptet werden.
        return OK
    if text not in known:
        return _warn(f"{text!r} ist in der Zeitzonendatenbank dieses Rechners unbekannt.")
    return OK


# ---------------------------------------------------------------------------
# Branding -- inklusive Markenrichtlinie
# ---------------------------------------------------------------------------

# Arch Linux Trademark Policy (Fassung 2021-04-18): Marken, die mit den
# Buchstaben "ARCH" beginnen, gelten als hinreichend aehnlich und beduerften
# einer Erlaubnis. Ein Derivat darf sich aber als "based on Arch Linux"
# bezeichnen -- genau das setzt der Generator ueber ID_LIKE=arch um.
_ARCH_CLAIM = re.compile(r"^\s*arch\s*linux\s*$", re.IGNORECASE)
_ARCH_PREFIX = re.compile(r"^\s*arch", re.IGNORECASE)
_DISTRO_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+-]{0,31}$")


def validate_distro_name(value: Any) -> ValidationResult:
    text = str(value or "").strip()
    if not text:
        return _fail("Ein Name wird benoetigt.")
    if not _DISTRO_NAME.match(text):
        return _fail(
            "Erlaubt sind Buchstaben, Ziffern, Leerzeichen, Punkt, Unterstrich, "
            "Plus und Bindestrich (hoechstens 32 Zeichen)."
        )
    if _ARCH_CLAIM.match(text):
        return _fail(
            "Das System darf sich nicht selbst als Arch Linux ausgeben. "
            "Die Herkunft wird korrekt ueber ID_LIKE=arch in /etc/os-release "
            "hinterlegt."
        )
    if _ARCH_PREFIX.match(text):
        return _warn(
            "Die Arch-Markenrichtlinie stuft Namen, die mit 'Arch' beginnen, als "
            "verwechselbar ein; dafuer waere eine Erlaubnis noetig. "
            "Zulaessig ist stattdessen der Zusatz 'based on Arch Linux'."
        )
    return OK


_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")


def validate_version_string(value: Any) -> ValidationResult:
    text = str(value or "").strip()
    if not text:
        return _fail("Eine Version wird benoetigt.")
    if not _VERSION.match(text):
        return _fail("Erlaubt sind Buchstaben, Ziffern, Punkt, Unterstrich und Bindestrich.")
    return OK


def validate_iso_label(value: Any) -> ValidationResult:
    text = str(value or "").strip()
    if not text:
        return OK  # wird abgeleitet
    if len(text) > MAX_ISO_LABEL_LENGTH:
        return _fail(f"ISO-9660 erlaubt hoechstens {MAX_ISO_LABEL_LENGTH} Zeichen.")
    if not re.fullmatch(r"[A-Z0-9_]+", text):
        return _fail("Erlaubt sind nur Grossbuchstaben, Ziffern und Unterstrich.")
    return OK


def validate_install_dir(value: Any) -> ValidationResult:
    text = str(value or "").strip()
    if not text:
        return OK  # wird abgeleitet
    if len(text) > MAX_INSTALL_DIR_LENGTH:
        # mkarchiso prueft das selbst und bricht sonst ab.
        return _fail(f"mkarchiso erlaubt hoechstens {MAX_INSTALL_DIR_LENGTH} Zeichen.")
    if not re.fullmatch(r"[a-z0-9]+", text):
        return _fail("Erlaubt sind nur Kleinbuchstaben und Ziffern.")
    return OK


MAX_MENU_TITLE_LENGTH = 60

# Zeichen, die in mindestens einem der drei Bootmenue-Formate die Struktur
# zerlegen. GRUB ist der kritische Fall: seine Konfiguration ist eine echte
# Skriptsprache, und ein Anfuehrungszeichen im Titel beendet dort die
# Zeichenkette mitten im menuentry.
MENU_TITLE_FORBIDDEN = frozenset((
    '"',
    "'",
    '`',
    '$',
    '\\',
    '{',
    '}',
    '(',
    ')',
    ';',
    '#',
))


def validate_menu_title(value: Any) -> ValidationResult:
    """Der Titel geht in drei Bootlader-Formate mit drei Syntaxen.

    syslinux und systemd-boot sind zeilenbasiert -- ein Zeilenumbruch beginnt
    dort eine neue Anweisung. GRUB dagegen wertet seine Konfiguration als
    Skript aus: ``menuentry "Titel" { ... }``. Ein Anfuehrungszeichen im Titel
    schliesst die Zeichenkette und laesst den Rest der Zeile als Befehle
    zurueck.

    Der Titel ist reiner Anzeigetext. Ihn auf unbedenkliche Zeichen zu
    beschraenken kostet nichts und macht alle drei Formate gleichzeitig sicher.
    """
    text = str(value or "").strip()
    if not text:
        return OK  # wird aus Name und Version abgeleitet
    if len(text) > MAX_MENU_TITLE_LENGTH:
        return _fail(
            f"Hoechstens {MAX_MENU_TITLE_LENGTH} Zeichen -- laengere Titel werden "
            f"im Bootmenue abgeschnitten."
        )
    if "\n" in text or "\r" in text:
        return _fail("Zeilenumbrueche sind nicht erlaubt.")
    getroffen = sorted({zeichen for zeichen in text if zeichen in MENU_TITLE_FORBIDDEN})
    if getroffen:
        return _fail(
            "Diese Zeichen zerlegen die Bootmenue-Datei und sind nicht erlaubt: "
            + " ".join(getroffen)
        )
    return OK


def validate_url(value: Any) -> ValidationResult:
    text = str(value or "").strip()
    if not text:
        return OK
    if not text.startswith(("http://", "https://")):
        return _fail("Die Adresse muss mit http:// oder https:// beginnen.")
    if " " in text:
        return _fail("Adressen duerfen keine Leerzeichen enthalten.")
    return OK


# ---------------------------------------------------------------------------
# Pfade
# ---------------------------------------------------------------------------


def _safe_path(text: str) -> ValidationResult | Path:
    if "\x00" in text:
        return _fail("Der Pfad enthaelt ein ungueltiges Zeichen.")
    try:
        return Path(text).expanduser()
    except (OSError, ValueError, RuntimeError) as exc:
        return _fail(f"Ungueltiger Pfad: {exc}")


def validate_existing_file(value: Any) -> ValidationResult:
    text = str(value or "").strip()
    if not text:
        return OK
    path = _safe_path(text)
    if isinstance(path, ValidationResult):
        return path
    if not path.exists():
        return _fail(f"Die Datei existiert nicht: {path}")
    if not path.is_file():
        return _fail(f"Das ist keine Datei: {path}")
    return OK


_IMAGE_SUFFIXES = frozenset({".png", ".svg", ".jpg", ".jpeg", ".webp"})


def validate_image_file(value: Any) -> ValidationResult:
    result = validate_existing_file(value)
    if not result.ok:
        return result
    text = str(value or "").strip()
    if not text:
        return OK
    if Path(text).suffix.lower() not in _IMAGE_SUFFIXES:
        return _warn(
            "Erwartet wird ein Bild (" + ", ".join(sorted(_IMAGE_SUFFIXES)) + ")."
        )
    return OK


def validate_splash_image(value: Any) -> ValidationResult:
    """syslinux zeigt im BIOS-Bootmenue ausschliesslich PNG in 640x480."""
    result = validate_existing_file(value)
    if not result.ok:
        return result
    text = str(value or "").strip()
    if not text:
        return OK
    if Path(text).suffix.lower() != ".png":
        return _fail("Das BIOS-Bootmenue kann nur PNG-Dateien anzeigen.")
    return OK


def validate_writable_dir(value: Any) -> ValidationResult:
    text = str(value or "").strip()
    if not text:
        return OK  # Vorgabe wird verwendet
    path = _safe_path(text)
    if isinstance(path, ValidationResult):
        return path
    if not path.is_absolute():
        return _fail("Bitte einen absoluten Pfad angeben.")
    existing = path
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    if not existing.is_dir():
        return _fail(f"{existing} ist kein Verzeichnis.")
    import os

    if not os.access(existing, os.W_OK):
        return _fail(f"Keine Schreibrechte in {existing}.")
    return OK


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, Validator] = {
    "hostname": validate_hostname,
    "username": validate_username,
    "gecos": validate_gecos,
    "password": validate_password,
    "locale": validate_locale,
    "keymap": validate_keymap,
    "timezone": validate_timezone,
    "distro_name": validate_distro_name,
    "version_string": validate_version_string,
    "iso_label": validate_iso_label,
    "install_dir": validate_install_dir,
    "menu_title": validate_menu_title,
    "url": validate_url,
    "existing_file": validate_existing_file,
    "image_file": validate_image_file,
    "splash_image": validate_splash_image,
    "writable_dir": validate_writable_dir,
}

def registry_names() -> frozenset[str]:
    """Die im Katalog erlaubten Validatornamen.

    Der Loader prueft dagegen. Ohne diese Pruefung deaktivierte ein Tippfehler
    im YAML die Feldpruefung lautlos: ``validate()`` gibt bei einem unbekannten
    Namen kommentarlos "in Ordnung" zurueck -- und ein Feld, das eigentlich
    einen Hostnamen pruefen sollte, nahm alles an.
    """
    return frozenset(_REGISTRY)



def validate(name: str, value: Any) -> ValidationResult:
    validator = _REGISTRY.get(name)
    if validator is None:
        return OK
    try:
        return validator(value)
    except Exception:  # ein defekter Validator darf den Wizard nicht anhalten
        import logging

        logging.getLogger(__name__).exception("Validator %r ist fehlgeschlagen", name)
        return OK
