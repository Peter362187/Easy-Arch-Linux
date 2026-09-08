"""Laden und Validieren des YAML-Katalogs.

Der Loader ist die einzige Stelle, die YAML kennt. Er nutzt ``yaml.safe_load``
(nie ``load``), prueft jedes Feld beim Einlesen und wirft ``CatalogError`` mit
einer Ortsangabe, statt spaeter mit einem AttributeError zu sterben.

Overlays: Dateien aus dem Benutzer-Konfigverzeichnis koennen Kategorien
ergaenzen oder Optionen ueberschreiben, ohne die ausgelieferten Dateien
anzufassen. Zusammengefuehrt wird ueber ``category.id`` + ``option.id``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from .. import validation
from ..paths import data_root, user_catalog_dir
from . import predicate
from .models import (
    Arity,
    BootContribution,
    CapabilitySpec,
    Catalog,
    Category,
    Choice,
    EnableIn,
    FieldSpec,
    FileEntry,
    Option,
    OptionGroup,
    PackageRef,
    PageType,
    SelectionMode,
    ServiceAction,
    ServiceRef,
    ServiceScope,
)

log = logging.getLogger(__name__)

SUPPORTED_SCHEMA_VERSION = 1

_VALID_WIDGETS = frozenset(
    {
        "line",
        "combo",
        "editable_combo",
        "password",
        "path",
        "int",
        "bool",
        "textarea",
        "tag_list",
    }
)
_VALID_LAYOUTS = frozenset({"list", "grid", "cards"})
_VALID_SEVERITIES = frozenset({"error", "warning", "info"})


class CatalogError(Exception):
    """Der Katalog ist fehlerhaft. Die Meldung nennt immer Datei und Feld."""


# ---------------------------------------------------------------------------
# Kleine Helfer: nachsichtig beim Lesen, streng beim Typ
# ---------------------------------------------------------------------------


def _require_mapping(value: Any, where: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise CatalogError(f"{where}: erwartet ein Objekt, gefunden {type(value).__name__}")
    return value


def _str(data: Mapping[str, Any], key: str, where: str, default: str = "") -> str:
    value = data.get(key, default)
    if value is None:
        return default
    if not isinstance(value, (str, int, float)):
        raise CatalogError(f"{where}: '{key}' muss Text sein, gefunden {type(value).__name__}")
    return str(value)


def _int(data: Mapping[str, Any], key: str, where: str, default: int = 0) -> int:
    value = data.get(key, default)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise CatalogError(f"{where}: '{key}' muss eine ganze Zahl sein")
    return value


def _bool(data: Mapping[str, Any], key: str, where: str, default: bool = False) -> bool:
    value = data.get(key, default)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise CatalogError(f"{where}: '{key}' muss true oder false sein")
    return value


def _str_tuple(data: Mapping[str, Any], key: str, where: str) -> tuple[str, ...]:
    """Nimmt eine Liste oder einen Einzelwert -- YAML-Autoren schreiben beides."""
    value = data.get(key)
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, Sequence):
        raise CatalogError(f"{where}: '{key}' muss eine Liste sein")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, (str, int, float)):
            raise CatalogError(f"{where}: '{key}[{index}]' muss Text sein")
        result.append(str(item))
    return tuple(result)


def _enum(enum_cls: Any, data: Mapping[str, Any], key: str, where: str, default: Any) -> Any:
    raw = data.get(key)
    if raw is None:
        return default
    try:
        return enum_cls(str(raw))
    except ValueError:
        allowed = [member.value for member in enum_cls]
        raise CatalogError(f"{where}: '{key}' = {raw!r} unbekannt; erlaubt: {allowed}") from None


def _predicate(data: Mapping[str, Any], key: str, where: str) -> predicate.Predicate:
    try:
        return predicate.parse(data.get(key), where=f"{where}.{key}")
    except predicate.PredicateError as exc:
        raise CatalogError(str(exc)) from exc


def _unknown_keys(data: Mapping[str, Any], allowed: frozenset[str], where: str) -> None:
    """Tippfehler im Katalog sollen auffallen, nicht stillschweigend wirkungslos sein."""
    unknown = sorted(set(data) - allowed)
    if unknown:
        log.warning("%s: unbekannte Schluessel werden ignoriert: %s", where, unknown)


# ---------------------------------------------------------------------------
# Teil-Parser
# ---------------------------------------------------------------------------

_PACKAGE_KEYS = frozenset({"name", "when", "reason"})


def _parse_packages(raw: Any, where: str) -> tuple[PackageRef, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise CatalogError(f"{where}: 'packages' muss eine Liste sein")
    result: list[PackageRef] = []
    for index, item in enumerate(raw):
        spot = f"{where}.packages[{index}]"
        if isinstance(item, str):
            result.append(PackageRef(name=item))
            continue
        data = _require_mapping(item, spot)
        _unknown_keys(data, _PACKAGE_KEYS, spot)
        name = _str(data, "name", spot)
        if not name:
            raise CatalogError(f"{spot}: 'name' fehlt")
        result.append(
            PackageRef(
                name=name,
                when=_predicate(data, "when", spot),
                reason=_str(data, "reason", spot),
            )
        )
    return tuple(result)


_SERVICE_KEYS = frozenset(
    {
        "unit",
        "scope",
        "action",
        "wanted_by",
        "required_by",
        "aliases",
        "owned_by",
        "enable_in",
        "package",
        "reason",
        "when",
    }
)


def _parse_services(raw: Any, where: str) -> tuple[ServiceRef, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise CatalogError(f"{where}: 'services' muss eine Liste sein")
    result: list[ServiceRef] = []
    for index, item in enumerate(raw):
        spot = f"{where}.services[{index}]"
        if isinstance(item, str):
            # Kurzform: nur der Unit-Name. Sinnvoller Default ist multi-user.
            result.append(ServiceRef(unit=_normalize_unit(item), wanted_by=("multi-user.target",)))
            continue
        data = _require_mapping(item, spot)
        _unknown_keys(data, _SERVICE_KEYS, spot)
        unit = _str(data, "unit", spot)
        if not unit:
            raise CatalogError(f"{spot}: 'unit' fehlt")
        action = _enum(ServiceAction, data, "action", spot, ServiceAction.ENABLE)
        wanted_by = _str_tuple(data, "wanted_by", spot)
        aliases = _str_tuple(data, "aliases", spot)
        required_by = _str_tuple(data, "required_by", spot)
        if action is ServiceAction.ENABLE and not (wanted_by or aliases or required_by):
            raise CatalogError(
                f"{spot}: Dienst {unit!r} wird aktiviert, aber es ist weder 'wanted_by' noch "
                f"'aliases' angegeben -- ohne beides erzeugt 'enable' keinen einzigen Symlink "
                f"und der Dienst startet nie. Bitte die [Install]-Sektion der Unit nachsehen."
            )
        result.append(
            ServiceRef(
                unit=_normalize_unit(unit),
                scope=_enum(ServiceScope, data, "scope", spot, ServiceScope.SYSTEM),
                action=action,
                wanted_by=wanted_by,
                required_by=required_by,
                aliases=aliases,
                owned_by=_str(data, "owned_by", spot),
                enable_in=_enum(EnableIn, data, "enable_in", spot, EnableIn.BOTH),
                package=_str(data, "package", spot),
                reason=_str(data, "reason", spot),
                when=_predicate(data, "when", spot),
            )
        )
    return tuple(result)


def _normalize_unit(unit: str) -> str:
    """Ohne Endung ist '.service' gemeint -- wie bei systemctl."""
    known = (".service", ".socket", ".timer", ".target", ".path", ".mount", ".slice")
    return unit if unit.endswith(known) else f"{unit}.service"


_FILE_KEYS = frozenset(
    {"target", "content", "mode", "owner", "when", "owned_by"}
)


def _parse_files(raw: Any, where: str) -> tuple[FileEntry, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise CatalogError(f"{where}: 'files' muss eine Liste sein")
    result: list[FileEntry] = []
    for index, item in enumerate(raw):
        spot = f"{where}.files[{index}]"
        data = _require_mapping(item, spot)
        _unknown_keys(data, _FILE_KEYS, spot)
        target = _str(data, "target", spot)
        if not target:
            raise CatalogError(f"{spot}: 'target' fehlt")
        if not target.startswith("/"):
            raise CatalogError(f"{spot}: 'target' muss ein absoluter Pfad im Zielsystem sein")
        try:
            result.append(
                FileEntry(
                    target=target,
                    content=_str(data, "content", spot),
                    mode=_str(data, "mode", spot, "0644"),
                    owner=_str(data, "owner", spot),
                    when=_predicate(data, "when", spot),
                    owned_by=_str(data, "owned_by", spot),
                )
            )
        except ValueError as exc:
            raise CatalogError(f"{spot}: {exc}") from exc
    return tuple(result)


_BOOT_KEYS = frozenset({"kernel_params", "modules", "mkinitcpio_hooks"})


def _parse_boot(raw: Any, where: str) -> BootContribution:
    data = _require_mapping(raw, f"{where}.boot")
    _unknown_keys(data, _BOOT_KEYS, f"{where}.boot")
    return BootContribution(
        kernel_params=_str_tuple(data, "kernel_params", where),
        modules=_str_tuple(data, "modules", where),
        mkinitcpio_hooks=_str_tuple(data, "mkinitcpio_hooks", where),
    )


def _parse_choices(raw: Any, where: str) -> tuple[Choice, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise CatalogError(f"{where}: 'choices' muss eine Liste sein")
    result: list[Choice] = []
    for index, item in enumerate(raw):
        spot = f"{where}.choices[{index}]"
        if isinstance(item, (str, int, float)):
            result.append(Choice(value=str(item)))
            continue
        data = _require_mapping(item, spot)
        value = _str(data, "value", spot)
        if not value:
            raise CatalogError(f"{spot}: 'value' fehlt")
        result.append(Choice(value=value, label=_str(data, "label", spot)))
    return tuple(result)


_FIELD_KEYS = frozenset(
    {
        "id",
        "label",
        "widget",
        "binding",
        "default",
        "placeholder",
        "help",
        "required",
        "secret",
        "choices",
        "choices_from",
        "validator",
        "min",
        "max",
        "file_filter",
        "visible_when",
        "enabled_when",
        "confirm_field",
        "preview_role",
    }
)


def _parse_fields(raw: Any, category_id: str, where: str) -> tuple[FieldSpec, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise CatalogError(f"{where}: 'fields' muss eine Liste sein")
    result: list[FieldSpec] = []
    for index, item in enumerate(raw):
        spot = f"{where}.fields[{index}]"
        data = _require_mapping(item, spot)
        _unknown_keys(data, _FIELD_KEYS, spot)
        field_id = _str(data, "id", spot)
        if not field_id:
            raise CatalogError(f"{spot}: 'id' fehlt")
        widget = _str(data, "widget", spot, "line")
        if widget not in _VALID_WIDGETS:
            raise CatalogError(
                f"{spot}: widget {widget!r} unbekannt; erlaubt: {sorted(_VALID_WIDGETS)}"
            )
        validator = _str(data, "validator", spot)
        if validator and validator not in validation.registry_names():
            raise CatalogError(
                f"{spot}: validator {validator!r} unbekannt; erlaubt: "
                f"{sorted(validation.registry_names())}"
            )
        secret = _bool(data, "secret", spot, widget == "password")
        default = data.get("default")
        if secret and default not in (None, ""):
            raise CatalogError(f"{spot}: geheime Felder duerfen keinen Vorgabewert haben")
        result.append(
            FieldSpec(
                id=field_id,
                label=_str(data, "label", spot, field_id),
                widget=widget,
                binding=_str(data, "binding", spot) or f"{category_id}.{field_id}",
                default=default,
                placeholder=_str(data, "placeholder", spot),
                help=_str(data, "help", spot),
                required=_bool(data, "required", spot),
                secret=secret,
                choices=_parse_choices(data.get("choices"), spot),
                choices_from=_str(data, "choices_from", spot),
                validator=validator,
                minimum=data.get("min"),
                maximum=data.get("max"),
                file_filter=_str(data, "file_filter", spot),
                visible_when=_predicate(data, "visible_when", spot),
                enabled_when=_predicate(data, "enabled_when", spot),
                confirm_field=_str(data, "confirm_field", spot),
                preview_role=_str(data, "preview_role", spot),
            )
        )

    # ``confirm_field`` erst am Ende pruefen: es darf auf ein Feld zeigen, das
    # weiter unten steht. Ein Tippfehler sperrte sonst den Weiter-Knopf
    # dauerhaft -- verglichen wurde gegen ein Feld, das es nicht gibt.
    bekannt = {spec.id for spec in result}
    for spec in result:
        if spec.confirm_field and spec.confirm_field not in bekannt:
            raise CatalogError(
                f"{where}: confirm_field {spec.confirm_field!r} von {spec.id!r} "
                f"nennt kein Feld dieser Kategorie; vorhanden: {sorted(bekannt)}"
            )
    return tuple(result)


_OPTION_KEYS = frozenset(
    {
        "id",
        "label",
        "description",
        "group",
        "order",
        "icon",
        "docs",
        "tags",
        "recommended",
        "default",
        "est_size_mb",
        "arch",
        "packages",
        "package_groups",
        "aur_packages",
        "provides",
        "implies",
        "recommends",
        "requires",
        "requires_any",
        "conflicts",
        "services",
        "files",
        "boot",
        "repos",
        "semantics",
        "visible_when",
        "enabled_when",
        "aliases",
        "deprecated",
        "replaced_by",
    }
)


def _parse_option(raw: Any, category_id: str, where: str) -> Option:
    data = _require_mapping(raw, where)
    option_id = _str(data, "id", where)
    if not option_id:
        raise CatalogError(f"{where}: 'id' fehlt")
    spot = f"{where}[{option_id}]"
    _unknown_keys(data, _OPTION_KEYS, spot)
    semantics = _require_mapping(data.get("semantics"), f"{spot}.semantics")
    return Option(
        id=option_id,
        category_id=category_id,
        label=_str(data, "label", spot, option_id),
        description=_str(data, "description", spot),
        group=_str(data, "group", spot),
        order=_int(data, "order", spot),
        icon=_str(data, "icon", spot),
        docs=_str(data, "docs", spot),
        tags=_str_tuple(data, "tags", spot),
        recommended=_bool(data, "recommended", spot),
        default=_bool(data, "default", spot),
        est_size_mb=_int(data, "est_size_mb", spot),
        arch=_str_tuple(data, "arch", spot),
        packages=_parse_packages(data.get("packages"), spot),
        package_groups=_str_tuple(data, "package_groups", spot),
        aur_packages=_str_tuple(data, "aur_packages", spot),
        provides=_str_tuple(data, "provides", spot),
        implies=_str_tuple(data, "implies", spot),
        recommends=_str_tuple(data, "recommends", spot),
        requires=_str_tuple(data, "requires", spot),
        requires_any=_str_tuple(data, "requires_any", spot),
        conflicts=_str_tuple(data, "conflicts", spot),
        services=_parse_services(data.get("services"), spot),
        files=_parse_files(data.get("files"), spot),
        boot=_parse_boot(data.get("boot"), spot),
        repos=_str_tuple(data, "repos", spot),
        semantics=dict(semantics),
        visible_when=_predicate(data, "visible_when", spot),
        enabled_when=_predicate(data, "enabled_when", spot),
        aliases=_str_tuple(data, "aliases", spot),
        deprecated=_bool(data, "deprecated", spot),
        replaced_by=_str(data, "replaced_by", spot),
    )


_CATEGORY_KEYS = frozenset(
    {
        "id",
        "title",
        "subtitle",
        "icon",
        "step",
        "visible",
        "page_type",
        "selection_mode",
        "required",
        "min_selected",
        "max_selected",
        "default_selection",
        "layout",
        "columns",
        "help_url",
        "visible_when",
        "groups",
        "renamed_from",
        "preview",
    }
)


def _parse_groups(raw: Any, where: str) -> tuple[OptionGroup, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise CatalogError(f"{where}: 'groups' muss eine Liste sein")
    result: list[OptionGroup] = []
    for index, item in enumerate(raw):
        spot = f"{where}.groups[{index}]"
        data = _require_mapping(item, spot)
        group_id = _str(data, "id", spot)
        if not group_id:
            raise CatalogError(f"{spot}: 'id' fehlt")
        result.append(
            OptionGroup(
                id=group_id,
                label=_str(data, "label", spot),
                order=_int(data, "order", spot),
                description=_str(data, "description", spot),
            )
        )
    return tuple(result)


# ---------------------------------------------------------------------------
# Rohdokumente einlesen und mergen
# ---------------------------------------------------------------------------


class _RawCategory:
    """Zwischenstufe: Kategorie-Kopf plus Optionen/Felder als Rohdaten.

    Getrennt vom fertigen Modell, weil Overlays vor dem Parsen gemergt werden
    muessen -- ein Overlay darf einzelne Optionsfelder patchen.
    """

    def __init__(self, header: dict[str, Any], source: Path) -> None:
        self.header = header
        self.options: dict[str, dict[str, Any]] = {}
        self.option_order: list[str] = []
        self.fields: dict[str, dict[str, Any]] = {}
        self.field_order: list[str] = []
        self.sources: list[str] = [str(source)]

    def add_option(self, data: dict[str, Any], strategy: str) -> None:
        option_id = str(data.get("id", ""))
        if not option_id:
            raise CatalogError(f"{self.sources[-1]}: Option ohne 'id'")
        if option_id in self.options and strategy == "patch":
            self.options[option_id] = _deep_merge(self.options[option_id], data)
            return
        if option_id not in self.options:
            self.option_order.append(option_id)
        self.options[option_id] = data

    def add_field(self, data: dict[str, Any], strategy: str) -> None:
        field_id = str(data.get("id", ""))
        if not field_id:
            raise CatalogError(f"{self.sources[-1]}: Feld ohne 'id'")
        if field_id in self.fields and strategy == "patch":
            self.fields[field_id] = _deep_merge(self.fields[field_id], data)
            return
        if field_id not in self.fields:
            self.field_order.append(field_id)
        self.fields[field_id] = data


def _deep_merge(base: dict[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    """Rekursiver Merge; Listen werden ersetzt, nicht verkettet.

    Verketten waere ueberraschend: wer eine Paketliste im Overlay angibt, will
    sie in aller Regel setzen, nicht anhaengen.
    """
    result = dict(base)
    for key, value in patch.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _deep_merge(dict(result[key]), value)
        else:
            result[key] = value
    return result


def _load_yaml(path: Path) -> Mapping[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise CatalogError(f"{path}: YAML-Syntaxfehler: {exc}") from exc
    except OSError as exc:
        raise CatalogError(f"{path}: nicht lesbar: {exc}") from exc
    if data is None:
        return {}
    return _require_mapping(data, str(path))


def _absorb_document(
    document: Mapping[str, Any], path: Path, raw_categories: dict[str, _RawCategory]
) -> None:
    schema = document.get("schema_version", SUPPORTED_SCHEMA_VERSION)
    if schema != SUPPORTED_SCHEMA_VERSION:
        raise CatalogError(
            f"{path}: schema_version {schema} wird nicht unterstuetzt "
            f"(erwartet {SUPPORTED_SCHEMA_VERSION})"
        )
    header = dict(_require_mapping(document.get("category"), f"{path}.category"))
    if not header:
        return
    category_id = str(header.get("id", ""))
    if not category_id:
        raise CatalogError(f"{path}: 'category.id' fehlt")
    strategy = str(document.get("merge_strategy", "patch"))
    if strategy not in ("patch", "replace", "append"):
        raise CatalogError(f"{path}: merge_strategy {strategy!r} unbekannt")

    existing = raw_categories.get(category_id)
    if existing is None or strategy == "replace":
        raw_categories[category_id] = _RawCategory(header, path)
        existing = raw_categories[category_id]
    else:
        existing.header = _deep_merge(existing.header, header)
        existing.sources.append(str(path))

    for item in document.get("options") or ():
        existing.add_option(dict(_require_mapping(item, f"{path}.options")), strategy)
    for item in document.get("fields") or ():
        existing.add_field(dict(_require_mapping(item, f"{path}.fields")), strategy)


def _parse_category(raw: _RawCategory) -> Category:
    header = raw.header
    where = raw.sources[0]
    category_id = str(header["id"])
    _unknown_keys(header, _CATEGORY_KEYS, f"{where}.category")

    layout = _str(header, "layout", where, "list")
    if layout not in _VALID_LAYOUTS:
        raise CatalogError(f"{where}: layout {layout!r} unbekannt; erlaubt: {sorted(_VALID_LAYOUTS)}")

    page_type = _enum(PageType, header, "page_type", where, PageType.SELECTION)
    selection_mode = _enum(SelectionMode, header, "selection_mode", where, SelectionMode.MULTI)

    options = tuple(
        _parse_option(raw.options[option_id], category_id, where)
        for option_id in raw.option_order
    )
    fields = _parse_fields(
        [raw.fields[field_id] for field_id in raw.field_order], category_id, where
    )

    if page_type is PageType.SELECTION and not options:
        raise CatalogError(f"{where}: Auswahlseite {category_id!r} hat keine Optionen")
    if page_type is PageType.FORM and not fields:
        raise CatalogError(f"{where}: Formularseite {category_id!r} hat keine Felder")

    known_groups = {group.id for group in _parse_groups(header.get("groups"), where)}
    for option in options:
        if option.group and option.group not in known_groups:
            raise CatalogError(
                f"{where}: Option {option.id!r} verweist auf unbekannte Gruppe {option.group!r}"
            )

    defaults = _str_tuple(header, "default_selection", where)
    option_ids = {option.id for option in options}
    for default_id in defaults:
        if default_id not in option_ids:
            raise CatalogError(
                f"{where}: default_selection nennt {default_id!r}, das es in "
                f"{category_id!r} nicht gibt"
            )
    if not defaults:
        defaults = tuple(option.id for option in options if option.default)

    if selection_mode in (SelectionMode.SINGLE, SelectionMode.SINGLE_OPTIONAL) and len(defaults) > 1:
        raise CatalogError(
            f"{where}: {category_id!r} erlaubt nur eine Auswahl, hat aber "
            f"{len(defaults)} Vorgaben: {defaults}"
        )

    return Category(
        id=category_id,
        title=_str(header, "title", where, category_id),
        page_type=page_type,
        subtitle=_str(header, "subtitle", where),
        icon=_str(header, "icon", where),
        step=_int(header, "step", where),
        visible=_bool(header, "visible", where, True),
        help_url=_str(header, "help_url", where),
        selection_mode=selection_mode,
        required=_bool(header, "required", where),
        min_selected=_int(header, "min_selected", where),
        max_selected=_int(header, "max_selected", where),
        default_selection=defaults,
        layout=layout,
        columns=max(1, _int(header, "columns", where, 1)),
        groups=_parse_groups(header.get("groups"), where),
        options=options,
        fields=fields,
        visible_when=_predicate(header, "visible_when", where),
        renamed_from=_str_tuple(header, "renamed_from", where),
        preview=_str(header, "preview", where),
        source_files=tuple(raw.sources),
    )


_CAPABILITY_KEYS = frozenset(
    {
        "label",
        "arity",
        "on_violation",
        "auto_resolve",
        "required_if",
        "default_provider",
        "description",
    }
)


def _parse_capabilities(raw: Any, where: str) -> dict[str, CapabilitySpec]:
    data = _require_mapping(raw, f"{where}.capabilities")
    result: dict[str, CapabilitySpec] = {}
    for name, body in data.items():
        spot = f"{where}.capabilities.{name}"
        entry = _require_mapping(body, spot)
        _unknown_keys(entry, _CAPABILITY_KEYS, spot)
        severity = _str(entry, "on_violation", spot, "error")
        if severity not in _VALID_SEVERITIES:
            raise CatalogError(
                f"{spot}: on_violation {severity!r} unbekannt; erlaubt: {sorted(_VALID_SEVERITIES)}"
            )
        result[str(name)] = CapabilitySpec(
            name=str(name),
            label=_str(entry, "label", spot, str(name)),
            arity=_enum(Arity, entry, "arity", spot, Arity.MANY),
            on_violation=severity,  # type: ignore[arg-type]
            auto_resolve=_str(entry, "auto_resolve", spot, "none"),
            required_if=_str_tuple(entry, "required_if", spot),
            default_provider=_str(entry, "default_provider", spot),
            description=_str(entry, "description", spot),
        )
    return result


# ---------------------------------------------------------------------------
# Referenzpruefung und Index
# ---------------------------------------------------------------------------


def _check_references(
    categories: Sequence[Category], capabilities: Mapping[str, CapabilitySpec]
) -> None:
    """Alle Refs muessen existieren -- sonst faellt es erst zur Laufzeit auf."""
    known_refs = {option.ref for category in categories for option in category.options}
    known_categories = {category.id for category in categories}
    provided = {
        capability
        for category in categories
        for option in category.options
        for capability in option.provides
    }

    def check_leaf(leaf: str, spot: str) -> None:
        leaf = leaf.strip()
        if leaf.startswith("cap:"):
            name = leaf[4:].strip()
            if name not in capabilities and name not in provided:
                raise CatalogError(f"{spot}: unbekannte Capability {name!r}")
            return
        if leaf.startswith("field:"):
            return
        if leaf not in known_refs:
            raise CatalogError(
                f"{spot}: Verweis {leaf!r} zeigt auf keine existierende Option "
                f"(Format: kategorie.option)"
            )

    for category in categories:
        spot = f"Kategorie {category.id}"
        for ref in category.visible_when.references():
            check_leaf(ref, f"{spot}.visible_when")
        for option in category.options:
            option_spot = f"Option {option.ref}"
            for key in ("implies", "recommends", "requires", "conflicts"):
                for ref in getattr(option, key):
                    check_leaf(ref, f"{option_spot}.{key}")
            for ref in option.requires_any:
                check_leaf(ref, f"{option_spot}.requires_any")
            for ref in option.visible_when.references():
                check_leaf(ref, f"{option_spot}.visible_when")
            for ref in option.enabled_when.references():
                check_leaf(ref, f"{option_spot}.enabled_when")
            for capability in option.provides:
                if capability not in capabilities:
                    log.warning(
                        "%s stellt Capability %r bereit, die in catalog.yaml nicht "
                        "deklariert ist -- sie wird ohne Aritaetspruefung behandelt",
                        option_spot,
                        capability,
                    )
            if option.replaced_by and option.replaced_by not in known_refs:
                raise CatalogError(
                    f"{option_spot}.replaced_by verweist auf {option.replaced_by!r}, "
                    f"das nicht existiert"
                )

    # Der Name unterscheidet sich bewusst von der Schleife weiter oben, die
    # ueber 'option.provides' und damit ueber Zeichenketten laeuft.
    for name, spec in capabilities.items():
        if spec.default_provider and spec.default_provider not in known_refs:
            raise CatalogError(
                f"Capability {name!r}: default_provider {spec.default_provider!r} "
                f"existiert nicht"
            )
        if spec.arity is Arity.ONE and not spec.default_provider:
            log.warning(
                "Capability %r verlangt genau einen Anbieter, hat aber keinen "
                "default_provider -- unerfuellbare Auswahlen sind moeglich",
                name,
            )
        for leaf in spec.required_if:
            check_leaf(leaf, f"Capability {name}.required_if")

    unknown_categories = set()
    for category in categories:
        for previous in category.renamed_from:
            if previous in known_categories:
                unknown_categories.add(previous)
    if unknown_categories:
        raise CatalogError(
            f"renamed_from nennt Kategorien, die es noch gibt: {sorted(unknown_categories)}"
        )


def _build_indices(
    categories: Sequence[Category],
) -> tuple[dict[str, Category], dict[str, Option], dict[str, str], dict[str, tuple[str, ...]]]:
    by_id = {category.id: category for category in categories}
    by_ref: dict[str, Option] = {}
    by_alias: dict[str, str] = {}
    providers: dict[str, list[str]] = {}

    for category in categories:
        for option in category.options:
            by_ref[option.ref] = option
            for alias in option.aliases:
                # Alias darf als "kategorie.alias" oder blank stehen.
                keys = (alias,) if "." in alias else (f"{category.id}.{alias}",)
                for key in keys:
                    if key in by_alias and by_alias[key] != option.ref:
                        raise CatalogError(
                            f"Alias {key!r} ist mehrdeutig: {by_alias[key]} und {option.ref}"
                        )
                    by_alias[key] = option.ref
            for capability in option.provides:
                providers.setdefault(capability, []).append(option.ref)

        for previous in category.renamed_from:
            for option in category.options:
                by_alias.setdefault(f"{previous}.{option.id}", option.ref)

    collisions = set(by_alias) & set(by_ref)
    if collisions:
        raise CatalogError(
            f"Aliase verdecken echte Optionen: {sorted(collisions)}"
        )
    return by_id, by_ref, by_alias, {k: tuple(v) for k, v in providers.items()}


# ---------------------------------------------------------------------------
# Oeffentliche API
# ---------------------------------------------------------------------------


def _expand_includes(patterns: Sequence[str], root: Path) -> list[Path]:
    found: list[Path] = []
    for pattern in patterns:
        expanded = sorted(root.glob(pattern))
        if not expanded:
            log.warning("Katalog-Include %r trifft keine Datei unter %s", pattern, root)
        found.extend(path for path in expanded if path.is_file())
    return found


def load_catalog(
    catalog_dir: Path | None = None,
    *,
    include_user_overlays: bool = True,
) -> Catalog:
    """Laedt den Katalog inklusive optionaler Benutzer-Overlays."""
    root = catalog_dir or (data_root() / "catalog")
    index_path = root / "catalog.yaml"
    if not index_path.is_file():
        raise CatalogError(
            f"Katalog-Index nicht gefunden: {index_path}\n"
            f"Erwartet wird eine Datei 'catalog.yaml' im Katalogverzeichnis."
        )

    index = _load_yaml(index_path)
    schema = index.get("schema_version", SUPPORTED_SCHEMA_VERSION)
    if schema != SUPPORTED_SCHEMA_VERSION:
        raise CatalogError(
            f"{index_path}: schema_version {schema} wird nicht unterstuetzt "
            f"(diese Programmversion kennt {SUPPORTED_SCHEMA_VERSION})"
        )

    capabilities = _parse_capabilities(index.get("capabilities"), str(index_path))
    wizard = _require_mapping(index.get("wizard"), f"{index_path}.wizard")
    step_order = _str_tuple(wizard, "step_order", str(index_path))

    raw_categories: dict[str, _RawCategory] = {}
    files = _expand_includes(_str_tuple(index, "includes", str(index_path)) or ("categories/*.yaml",), root)
    if not files:
        raise CatalogError(f"{root}: keine Kategoriedateien gefunden")

    for path in files:
        _absorb_document(_load_yaml(path), path, raw_categories)

    if include_user_overlays:
        overlay_root = user_catalog_dir()
        if overlay_root.is_dir():
            for path in sorted(overlay_root.glob("*.yaml")):
                log.info("Benutzer-Overlay wird geladen: %s", path)
                _absorb_document(_load_yaml(path), path, raw_categories)

    categories = tuple(
        sorted(
            (_parse_category(raw) for raw in raw_categories.values()),
            key=lambda c: (c.step, c.id),
        )
    )

    duplicate_steps: dict[int, list[str]] = {}
    for category in categories:
        duplicate_steps.setdefault(category.step, []).append(category.id)
    for step, ids in duplicate_steps.items():
        if len(ids) > 1:
            raise CatalogError(
                f"Mehrere Kategorien mit step={step}: {ids}. "
                f"Die Schrittnummer ist die Seiten-ID im Wizard und muss eindeutig sein."
            )

    _check_references(categories, capabilities)
    by_id, by_ref, by_alias, providers = _build_indices(categories)

    unknown_steps = [name for name in step_order if name not in by_id]
    if unknown_steps:
        raise CatalogError(f"{index_path}: wizard.step_order nennt unbekannte Kategorien: {unknown_steps}")

    catalog = Catalog(
        schema_version=SUPPORTED_SCHEMA_VERSION,
        catalog_version=_str(index, "catalog_version", str(index_path), "0"),
        name=_str(index, "name", str(index_path), "Katalog"),
        categories=categories,
        capabilities=capabilities,
        step_order=step_order,
        _by_id=by_id,
        _by_ref=by_ref,
        _by_alias=by_alias,
        _providers=providers,
    )
    log.info(
        "Katalog %s geladen: %d Kategorien, %d Optionen, %d Capabilities",
        catalog.catalog_version,
        len(categories),
        len(by_ref),
        len(capabilities),
    )
    return catalog
