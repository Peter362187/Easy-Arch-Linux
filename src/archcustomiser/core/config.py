"""Zentrales Konfigurationsobjekt.

Zwei Prinzipien bestimmen den Aufbau:

1. **Semantik ist die Wahrheit, Pakete sind abgeleitet.** ``BuildConfig`` haelt
   nur, was der Benutzer gewaehlt hat. Die Paketliste entsteht daraus im
   Resolver. Umgekehrt ginge es nicht: das installierte Zielsystem wird ueber
   archinstall konfiguriert, und archinstall will semantische Angaben
   (``profile.details = ["KDE Plasma"]``, ``audio = "pipewire"``), keine
   Paketnamen. Aus einer flachen Paketliste liesse sich das nicht
   zurueckgewinnen.

2. **Keine Geheimnisse.** Passwoerter liegen im ``SecretStore``, nicht hier.
   Damit kann ein versehentliches ``yaml.dump(config)`` strukturell kein
   Passwort erfassen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

SCHEMA_VERSION = 1


class SelectionSource(str, Enum):
    """Woher eine Auswahl stammt.

    Entscheidend fuer korrektes Rueckgaengigmachen: wechselt der Benutzer von
    KDE zu GNOME, muessen die automatisch ergaenzten Refs (sddm, KDE-Portal)
    verschwinden -- die vom Benutzer selbst gesetzten aber nicht, auch wenn sie
    zufaellig dieselbe Option betreffen.
    """

    USER = "user"        # bewusst angeklickt
    AUTO = "auto"        # vom Resolver ueber implies ergaenzt
    PROFILE = "profile"  # aus einem geladenen Profil
    DEFAULT = "default"  # Vorgabe des Katalogs


@dataclass(slots=True)
class BuildConfig:
    """Alles, was der Benutzer eingestellt hat -- und sonst nichts."""

    schema_version: int = SCHEMA_VERSION
    catalog_version: str = ""
    profile_name: str = ""

    selections: dict[str, set[str]] = field(default_factory=dict)
    """category_id -> Menge der ausgewaehlten option_ids (nur USER/PROFILE)."""

    sources: dict[str, SelectionSource] = field(default_factory=dict)
    """ref -> Herkunft. Enthaelt auch AUTO-Refs, damit sie beim Wegfall des
    Ausloesers wieder verschwinden koennen."""

    fields: dict[str, Any] = field(default_factory=dict)
    """binding -> Wert. Geheime Felder stehen hier NICHT."""

    extra_packages: list[str] = field(default_factory=list)

    extra_repositories: list[str] = field(default_factory=list)
    """Repositories, die wegen der Freitextpakete noetig sind.

    Ein Paket wie ``lib32-mesa`` liegt in ``[multilib]``. Die Katalogoptionen
    melden ihre Repositories ueber ``option.repos`` selbst an; das Freitextfeld
    konnte das bis zum 07.09.2026 nicht -- die Paketpruefung fand ``steam``
    im Index (der multilib mitlaedt), die erzeugte ``pacman.conf`` kannte das
    Repository aber nicht, und der Bau scheiterte erst bei pacstrap.
    """
    provider_choices: dict[str, str] = field(default_factory=dict)
    """Virtuelles Paket -> vom Benutzer gewaehlter Anbieter.

    Bei mehreren Anbietern (``ttf-font`` hat ueber ein Dutzend) wuerde pacman
    interaktiv nachfragen. Im nicht-interaktiven mkarchiso waere das ein
    Build-Abbruch, deshalb wird hier vorab entschieden.
    """

    file_permissions_extra: dict[str, str] = field(default_factory=dict)
    """Zusaetzliche Eintraege fuer file_permissions in profiledef.sh.

    Bewusst akkumulierbar: Benutzer-, Branding- und Installer-Erzeugung melden
    unabhaengig voneinander Eintraege an. Ein festes Template wuerde sich beim
    Zusammenfuehren in die Quere kommen.
    """

    unresolved: dict[str, list[str]] = field(default_factory=dict)
    """Refs aus einem Profil, die der aktuelle Katalog nicht kennt.

    Sie werden beim Speichern zurueckgeschrieben. Ohne das waere einmal
    Oeffnen-und-Speichern auf einer Maschine ohne dasselbe Katalog-Overlay
    datenzerstoerend.
    """

    # -- Auswahl --------------------------------------------------------------
    def selected(self, category_id: str) -> frozenset[str]:
        return frozenset(self.selections.get(category_id, ()))

    def is_selected(self, ref: str) -> bool:
        category_id, _, option_id = ref.partition(".")
        return option_id in self.selections.get(category_id, ())

    def set_selection(
        self,
        category_id: str,
        option_ids: Iterable[str],
        source: SelectionSource = SelectionSource.USER,
    ) -> None:
        new = set(option_ids)
        old = self.selections.get(category_id, set())
        for option_id in old - new:
            self.sources.pop(f"{category_id}.{option_id}", None)
        for option_id in new:
            self.sources.setdefault(f"{category_id}.{option_id}", source)
        if new:
            self.selections[category_id] = new
        else:
            self.selections.pop(category_id, None)

    def add(self, ref: str, source: SelectionSource = SelectionSource.USER) -> None:
        category_id, _, option_id = ref.partition(".")
        self.selections.setdefault(category_id, set()).add(option_id)
        # Eine spaetere Benutzerwahl "gewinnt" ueber eine fruehere Automatik:
        # der Benutzer hat die Option damit bestaetigt und sie soll bleiben,
        # auch wenn der urspruengliche Ausloeser wegfaellt.
        if source is not SelectionSource.AUTO or ref not in self.sources:
            self.sources[ref] = source

    def remove(self, ref: str) -> None:
        category_id, _, option_id = ref.partition(".")
        bucket = self.selections.get(category_id)
        if bucket:
            bucket.discard(option_id)
            if not bucket:
                self.selections.pop(category_id, None)
        self.sources.pop(ref, None)

    def source_of(self, ref: str) -> SelectionSource | None:
        return self.sources.get(ref)

    def all_refs(self) -> frozenset[str]:
        return frozenset(
            f"{category_id}.{option_id}"
            for category_id, options in self.selections.items()
            for option_id in options
        )

    def user_refs(self) -> frozenset[str]:
        """Nur bewusst gesetzte Auswahlen -- das, was ins Profil gehoert."""
        return frozenset(
            ref
            for ref in self.all_refs()
            if self.sources.get(ref) is not SelectionSource.AUTO
        )

    # -- Formularfelder -------------------------------------------------------
    def field(self, binding: str, default: Any = None) -> Any:
        return self.fields.get(binding, default)

    def set_field(self, binding: str, value: Any) -> None:
        if value is None:
            self.fields.pop(binding, None)
        else:
            self.fields[binding] = value

    def field_str(self, binding: str, default: str = "") -> str:
        value = self.fields.get(binding, default)
        return default if value is None else str(value)

    def field_bool(self, binding: str, default: bool = False) -> bool:
        value = self.fields.get(binding, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "ja", "on")
        return bool(value)

    def field_int(self, binding: str, default: int = 0) -> int:
        value = self.fields.get(binding, default)
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    # -- Abgeleitete Branding-Werte ------------------------------------------
    # Diese Ableitungen stehen bewusst hier und nicht im Generator: Dry-Run,
    # GUI-Vorschau und ISO-Build muessen denselben Dateinamen nennen.

    @property
    def distro_name(self) -> str:
        return self.field_str("branding.distro_name", "CustomArch") or "CustomArch"

    @property
    def version(self) -> str:
        return self.field_str("branding.version", "1.0") or "1.0"

    @property
    def iso_name(self) -> str:
        """Kleingeschriebener Dateinamensteil, z.B. 'flos'."""
        slug = re.sub(r"[^a-z0-9]+", "-", self.distro_name.lower()).strip("-")
        return slug or "customarch"

    @property
    def install_dir(self) -> str:
        """Verzeichnisname auf der ISO.

        mkarchiso prueft das seit Version 89: nur [a-z0-9], hoechstens 30
        Zeichen.
        """
        explicit = self.field_str("branding.install_dir")
        source = explicit or self.iso_name
        return re.sub(r"[^a-z0-9]", "", source.lower())[:30] or "arch"

    @property
    def iso_label(self) -> str:
        """ISO-9660-Datentraegerbezeichnung: A-Z, 0-9, Unterstrich, max. 32."""
        explicit = self.field_str("branding.iso_label")
        if explicit:
            return re.sub(r"[^A-Z0-9_]", "_", explicit.upper())[:32]
        base = re.sub(r"[^A-Z0-9]+", "_", self.distro_name.upper()).strip("_")
        version = re.sub(r"[^A-Z0-9]+", "_", self.version.upper()).strip("_")
        return f"{base}_{version}"[:32].strip("_") or "CUSTOMARCH"

    @property
    def iso_filename(self) -> str:
        """Exakt das Muster, das mkarchiso erzeugt: name-version-arch.iso."""
        return f"{self.iso_name}-{self.version}-{self.architecture}.iso"

    @property
    def architecture(self) -> str:
        return self.field_str("build.arch", "x86_64") or "x86_64"

    @property
    def hostname(self) -> str:
        return self.field_str("basics.hostname", "archcustom") or "archcustom"

    @property
    def username(self) -> str:
        return self.field_str("user.username")

    @property
    def creates_user(self) -> bool:
        return self.field_bool("user.create", True) and bool(self.username)

    # -- Kopie ---------------------------------------------------------------
    def copy(self) -> "BuildConfig":
        return BuildConfig(
            schema_version=self.schema_version,
            catalog_version=self.catalog_version,
            profile_name=self.profile_name,
            selections={key: set(value) for key, value in self.selections.items()},
            sources=dict(self.sources),
            fields=dict(self.fields),
            extra_packages=list(self.extra_packages),
            extra_repositories=list(self.extra_repositories),
            provider_choices=dict(self.provider_choices),
            file_permissions_extra=dict(self.file_permissions_extra),
            unresolved={key: list(value) for key, value in self.unresolved.items()},
        )
