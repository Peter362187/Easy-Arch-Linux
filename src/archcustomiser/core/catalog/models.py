"""Datenmodell des Katalogs.

Der Katalog ist die einzige Quelle der Wahrheit darueber, welche Optionen es gibt,
welche Pakete und Services sie mitbringen und wie sie zusammenhaengen. Die GUI
rendert ihn nur -- sie kennt keine einzige Desktop-Umgebung namentlich.

Alle Typen sind eingefroren: der geladene Katalog wird von GUI-Thread und
Worker-Threads gemeinsam gelesen und darf sich nie aendern.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterator, Literal, Mapping

from .predicate import ALWAYS, Predicate

Severity = Literal["error", "warning", "info"]


class SelectionMode(str, Enum):
    SINGLE = "single"
    SINGLE_OPTIONAL = "single_optional"
    MULTI = "multi"


class PageType(str, Enum):
    SELECTION = "selection"
    FORM = "form"
    FREE_PACKAGES = "free_packages"
    SUMMARY = "summary"


class Arity(str, Enum):
    ONE = "one"                  # genau ein Anbieter noetig
    AT_MOST_ONE = "at_most_one"  # hoechstens einer
    MANY = "many"                # beliebig viele


class ServiceScope(str, Enum):
    SYSTEM = "system"
    USER = "user"


class ServiceAction(str, Enum):
    ENABLE = "enable"
    DISABLE = "disable"
    MASK = "mask"


class EnableIn(str, Enum):
    """Live-ISO und installiertes Zielsystem sind nicht dasselbe."""

    LIVE = "live"
    TARGET = "target"
    BOTH = "both"


# ---------------------------------------------------------------------------
# Bausteine einer Option
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PackageRef:
    """Ein Paket, optional an eine Bedingung geknuepft."""

    name: str
    when: Predicate = ALWAYS
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ServiceRef:
    """Ein systemd-Dienst mit allem, was zum Aktivieren noetig ist.

    "systemctl enable" erzeugt je nach [Install]-Sektion unterschiedliche
    Symlinks: NetworkManager braucht multi-user.target.wants/, sddm dagegen
    den Alias display-manager.service und *keinen* .wants-Link. Das ist aus
    dem Paketnamen nicht ableitbar und muss kuratiert im Katalog stehen.
    """

    unit: str
    scope: ServiceScope = ServiceScope.SYSTEM
    action: ServiceAction = ServiceAction.ENABLE
    wanted_by: tuple[str, ...] = ()
    required_by: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    owned_by: str = ""            # Dedup-Schluessel bei Mehrfachbeitrag
    enable_in: EnableIn = EnableIn.BOTH
    package: str = ""
    reason: str = ""
    when: Predicate = ALWAYS

    @property
    def dedup_key(self) -> str:
        return self.owned_by or f"{self.scope.value}:{self.unit}"

    def symlinks(self) -> tuple[tuple[str, str], ...]:
        """(Linkpfad relativ zu airootfs, Linkziel) -- exakt was "enable" taete.

        Wird erst in Phase 5 geschrieben, gehoert aber hierher, weil nur der
        Katalog die noetigen Informationen hat.
        """
        base = "etc/systemd/system" if self.scope is ServiceScope.SYSTEM else "etc/systemd/user"
        lib = "/usr/lib/systemd/system" if self.scope is ServiceScope.SYSTEM else "/usr/lib/systemd/user"
        target = f"{lib}/{self.unit}"
        if self.action is ServiceAction.MASK:
            return ((f"{base}/{self.unit}", "/dev/null"),)
        if self.action is ServiceAction.DISABLE:
            return ()
        links: list[tuple[str, str]] = []
        for wants in self.wanted_by:
            links.append((f"{base}/{wants}.wants/{self.unit}", target))
        for requires in self.required_by:
            links.append((f"{base}/{requires}.requires/{self.unit}", target))
        for alias in self.aliases:
            links.append((f"{base}/{alias}", target))
        return tuple(links)


@dataclass(frozen=True, slots=True)
class FileEntry:
    """Eine Datei im airootfs-Overlay, mit ihrem Inhalt im Katalog.

    Frueher gab es hier zusaetzlich ``source`` (Datei danebenlegen statt
    einbetten) und ``template``. Beide wurden geparst und validiert, aber von
    ``core/archiso/airootfs.py`` nie ausgewertet: ein Katalogeintrag mit
    ``source:`` erzeugte still **keine** Datei -- trug aber trotzdem einen
    Rechte-Eintrag fuer einen Pfad ein, den es im Abbild nicht gab.

    Ein Feld, das aussieht als taete es etwas und nichts tut, ist schlimmer als
    keines. Beide sind deshalb entfernt; kein Katalog hat sie je benutzt. Wenn
    ``source`` einmal gebraucht wird, gehoert dazu die Aufloesung relativ zum
    jeweiligen Katalogverzeichnis samt Ausbruchspruefung -- das ist eine eigene
    Aufgabe, kein Nebenher.
    """

    target: str
    content: str = ""
    mode: str = "0644"
    owner: str = ""               # "uid:gid", leer = root:root
    when: Predicate = ALWAYS
    owned_by: str = ""

    def __post_init__(self) -> None:
        if not self.content:
            raise ValueError(f"FileEntry {self.target!r}: 'content' fehlt")


@dataclass(frozen=True, slots=True)
class BootContribution:
    kernel_params: tuple[str, ...] = ()
    modules: tuple[str, ...] = ()
    mkinitcpio_hooks: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Choice:
    """Ein Eintrag in einem Dropdown eines Formularfelds."""

    value: str
    label: str = ""

    @property
    def display(self) -> str:
        return self.label or self.value


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """Ein Formularfeld. Auch Hostname, Passwort und Branding kommen aus YAML."""

    id: str
    label: str
    widget: str = "line"
    # line | editable_combo | combo | password | path | int | bool | textarea | tag_list
    binding: str = ""             # Schluessel in BuildConfig.fields
    default: Any = None
    placeholder: str = ""
    help: str = ""
    required: bool = False
    secret: bool = False          # wandert in den SecretStore, nie in Profile
    choices: tuple[Choice, ...] = ()
    choices_from: str = ""        # Name in der ChoiceRegistry
    validator: str = ""           # Name in der ValidatorRegistry
    minimum: int | None = None
    maximum: int | None = None
    file_filter: str = ""
    visible_when: Predicate = ALWAYS
    enabled_when: Predicate = ALWAYS
    confirm_field: str = ""       # Passwortwiederholung


@dataclass(frozen=True, slots=True)
class OptionGroup:
    id: str
    label: str = ""
    order: int = 0
    description: str = ""


@dataclass(frozen=True, slots=True)
class Option:
    """Eine waehlbare Option, z.B. eine Desktop-Umgebung."""

    id: str
    category_id: str
    label: str
    description: str = ""
    group: str = ""
    order: int = 0
    icon: str = ""
    docs: str = ""
    tags: tuple[str, ...] = ()
    recommended: bool = False     # nur ein Badge in der GUI
    default: bool = False         # setzt tatsaechlich die Startauswahl
    est_size_mb: int = 0
    arch: tuple[str, ...] = ()

    packages: tuple[PackageRef, ...] = ()
    package_groups: tuple[str, ...] = ()
    aur_packages: tuple[str, ...] = ()

    provides: tuple[str, ...] = ()
    implies: tuple[str, ...] = ()      # hart, transitiv, in der GUI gesperrt
    recommends: tuple[str, ...] = ()   # weich, einmalig vorgehakt, abwaehlbar
    requires: tuple[str, ...] = ()
    requires_any: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()

    services: tuple[ServiceRef, ...] = ()
    files: tuple[FileEntry, ...] = ()
    boot: BootContribution = field(default_factory=BootContribution)

    repos: tuple[str, ...] = ()        # z.B. ("multilib",) bei Steam

    semantics: Mapping[str, Any] = field(default_factory=dict)
    """Semantische Werte fuer archinstall.

    archinstall will profile.details=["KDE Plasma"], audio="pipewire",
    greeter="sddm" -- keine Paketnamen. Diese Zuordnung ist aus der Paketliste
    nicht rekonstruierbar und muss deshalb im Katalog stehen.
    """

    visible_when: Predicate = ALWAYS
    enabled_when: Predicate = ALWAYS

    aliases: tuple[str, ...] = ()
    deprecated: bool = False
    replaced_by: str = ""

    @property
    def ref(self) -> str:
        return f"{self.category_id}.{self.id}"


@dataclass(frozen=True, slots=True)
class Category:
    """Eine Wizard-Seite -- oder eine unsichtbare Sammelkategorie."""

    id: str
    title: str
    page_type: PageType = PageType.SELECTION
    subtitle: str = ""
    icon: str = ""
    step: int = 0
    visible: bool = True
    help_url: str = ""

    selection_mode: SelectionMode = SelectionMode.MULTI
    required: bool = False
    min_selected: int = 0
    max_selected: int = 0          # 0 = unbegrenzt
    default_selection: tuple[str, ...] = ()

    layout: str = "list"           # list | grid | cards
    columns: int = 1

    groups: tuple[OptionGroup, ...] = ()
    options: tuple[Option, ...] = ()
    fields: tuple[FieldSpec, ...] = ()

    visible_when: Predicate = ALWAYS
    renamed_from: tuple[str, ...] = ()
    source_files: tuple[str, ...] = ()

    def option(self, option_id: str) -> Option | None:
        for option in self.options:
            if option.id == option_id:
                return option
        return None

    def field(self, field_id: str) -> FieldSpec | None:
        for spec in self.fields:
            if spec.id == field_id:
                return spec
        return None


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    """Eine abstrakte Rolle, z.B. 'display-manager'.

    Capabilities ersetzen n-Quadrat-Konfliktlisten: statt jede Audio-Option
    gegen jede andere zu deklarieren, liefern alle die Capability
    "audio-server" mit arity=at_most_one.
    """

    name: str
    label: str = ""
    arity: Arity = Arity.MANY
    on_violation: Severity = "error"
    auto_resolve: str = "none"          # none | deselect_previous
    required_if: tuple[str, ...] = ()   # Praedikat-Blaetter
    default_provider: str = ""
    description: str = ""


@dataclass(frozen=True, slots=True)
class Catalog:
    """Der vollstaendig geladene, validierte Katalog."""

    schema_version: int
    catalog_version: str
    name: str
    categories: tuple[Category, ...]
    capabilities: Mapping[str, CapabilitySpec]
    step_order: tuple[str, ...]
    _by_id: Mapping[str, Category] = field(default_factory=dict, repr=False)
    _by_ref: Mapping[str, Option] = field(default_factory=dict, repr=False)
    _by_alias: Mapping[str, str] = field(default_factory=dict, repr=False)
    _providers: Mapping[str, tuple[str, ...]] = field(default_factory=dict, repr=False)

    # -- Nachschlagen ---------------------------------------------------------
    def category(self, category_id: str) -> Category | None:
        return self._by_id.get(category_id)

    def option(self, ref: str) -> Option | None:
        return self._by_ref.get(ref)

    def resolve_alias(self, ref: str) -> str | None:
        """Alte Profil-Refs auf aktuelle abbilden."""
        if ref in self._by_ref:
            return ref
        return self._by_alias.get(ref)

    def visible_categories(self) -> tuple[Category, ...]:
        return tuple(c for c in self.categories if c.visible)

    def ordered_categories(self) -> tuple[Category, ...]:
        """Reihenfolge aus step_order; unbekannte Kategorien nach ``step``.

        Eine Kategorie aus einem Benutzer-Overlay steht nicht in
        ``step_order`` (die kommt nur aus catalog.yaml). Frueher landete
        sie deshalb hinter *allen* gelisteten -- also auch hinter der
        Zusammenfassung, hinter der der Bau beginnt. Die neue Seite kam
        damit nach dem Dry-Run, und 'ISO erstellen' stand auf der
        falschen Seite. Das dokumentierte Versprechen, eine Kategorie sei
        eine YAML-Datei, war fuer neue Kategorien gebrochen.

        Jetzt entscheidet bei unbekannten Kategorien die Schrittnummer:
        sie werden zwischen die gelisteten einsortiert, an der Stelle,
        die ihr ``step`` vorgibt.
        """
        index = {name: pos for pos, name in enumerate(self.step_order)}
        nach_schritt = {
            kategorie.id: kategorie.step for kategorie in self.categories
        }

        def platz(kategorie: Category) -> tuple[float, int, str]:
            if kategorie.id in index:
                return (float(index[kategorie.id]), kategorie.step, kategorie.id)
            # Zwischen die gelisteten einsortieren: der Platz der letzten
            # gelisteten Kategorie mit kleinerer Schrittnummer, plus ein
            # halber Schritt.
            davor = -1.0
            for name, position in index.items():
                schritt = nach_schritt.get(name)
                if schritt is not None and schritt < kategorie.step:
                    davor = max(davor, float(position))
            return (davor + 0.5, kategorie.step, kategorie.id)

        return tuple(sorted(self.categories, key=platz))

    def all_options(self) -> Iterator[Option]:
        for category in self.categories:
            yield from category.options
