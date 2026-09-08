"""Profile speichern und laden (Spec Abschnitt 8).

Gespeichert wird nur, was der Benutzer bewusst gewaehlt hat: Auswahlen,
Formularfelder und Zusatzpakete. Automatisch ergaenzte Refs (``sddm`` bei KDE)
kommen ausdruecklich NICHT ins Profil -- so uebernimmt ein altes Profil
automatisch spaetere Katalogaenderungen.

Passwoerter werden nie geschrieben. Die Felder sind als ``secret`` markiert und
liegen im ``SecretStore``, nicht in ``BuildConfig`` -- ein Profil kann sie
strukturell gar nicht enthalten.

Der ``resolved_snapshot`` ist reines Diagnosematerial und dient als Notreserve,
wenn eine Option im aktuellen Katalog fehlt.

Beim Laden gilt: **nie ein harter Abbruch**. Jede Referenz durchlaeuft eine
Rettungskette, und was uebrig bleibt, landet in ``config.unresolved`` und wird
beim naechsten Speichern unveraendert zurueckgeschrieben. Ohne das waere einmal
Oeffnen-und-Speichern auf einem Rechner ohne dasselbe Katalog-Overlay
datenzerstoerend.
"""

from __future__ import annotations

import logging
import re
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml

from .catalog import Catalog
from .config import SCHEMA_VERSION, BuildConfig, SelectionSource
from .packages.errors import InvalidPackageName
from .packages.names import split_constraint, validate_name
from .paths import bundled_profiles_dir, ensure_dir, user_profiles_dir
from .resolver import Resolution

log = logging.getLogger(__name__)

# Repository-Namen in pacman.conf: Buchstaben, Ziffern, Bindestrich,
# Unterstrich. Alles andere koennte die erzeugte Datei zerreissen.
_REPO_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

ProfileIssueCode = Literal[
    "unknown_option",
    "unknown_category",
    "renamed",
    "deprecated",
    "moved_to_extra",
    "version_mismatch",
    "secret_dropped",
    "invalid_package",
]


# Innerhalb von ``ProfileService`` verdeckt die Methode ``list()`` den
# eingebauten Typ: eine Annotation ``list[ProfileIssue]`` bezeichnet dort
# die Methode, nicht die Liste. Diese Aliase stehen deshalb hier oben, wo
# ``list`` noch der eingebaute Typ ist.
_Issues = list  # type: ignore[assignment]
_Namen = list  # type: ignore[assignment]


class ProfileError(Exception):
    """Das Profil kann nicht geladen werden -- mit verstaendlicher Begruendung."""


@dataclass(frozen=True, slots=True)
class ProfileIssue:
    severity: Literal["error", "warning", "info"]
    code: ProfileIssueCode
    ref: str
    message: str
    action_taken: str = ""


@dataclass(frozen=True, slots=True)
class ProfileInfo:
    path: Path
    name: str
    description: str = ""
    catalog_version: str = ""
    builtin: bool = False
    created: str = ""

    @property
    def display_name(self) -> str:
        return self.name or self.path.stem


@dataclass(slots=True)
class ProfileLoadResult:
    config: BuildConfig
    issues: tuple[ProfileIssue, ...] = ()
    secret_fields: tuple[str, ...] = ()
    """Geheime Felder, die das Profil nicht enthalten kann und die neu
    eingegeben werden muessen."""


class ProfileService:
    """Laedt und speichert Profile gegen einen konkreten Katalog."""

    def __init__(
        self,
        catalog: Catalog,
        profiles_dir: Path | None = None,
        builtin_dir: Path | None = None,
    ) -> None:
        self.catalog = catalog
        self.profiles_dir = profiles_dir or user_profiles_dir()
        self.builtin_dir = builtin_dir or bundled_profiles_dir()

    # -- Auflisten ------------------------------------------------------------
    def list(self) -> tuple[ProfileInfo, ...]:
        found: list[ProfileInfo] = []
        for directory, builtin in ((self.builtin_dir, True), (self.profiles_dir, False)):
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.yaml")):
                found.append(self._peek(path, builtin))
        return tuple(found)

    def _peek(self, path: Path, builtin: bool) -> ProfileInfo:
        """Kopfdaten lesen, ohne das ganze Profil aufzuloesen."""
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle) or {}
        except (OSError, yaml.YAMLError) as exc:
            log.warning("Profil %s nicht lesbar: %s", path, exc)
            return ProfileInfo(path=path, name=path.stem, description="(beschaedigt)", builtin=builtin)
        if not isinstance(data, Mapping):
            return ProfileInfo(path=path, name=path.stem, description="(ungueltig)", builtin=builtin)
        return ProfileInfo(
            path=path,
            name=str(data.get("name") or path.stem),
            description=str(data.get("description") or ""),
            catalog_version=str(data.get("catalog_version") or ""),
            builtin=builtin,
            created=str(data.get("created") or ""),
        )

    # -- Speichern ------------------------------------------------------------
    def save(
        self,
        config: BuildConfig,
        path: Path,
        *,
        resolution: Resolution | None = None,
        description: str = "",
        include_snapshot: bool = True,
    ) -> Path:
        """Schreibt das Profil atomar.

        Atomar, weil ein abgebrochener Schreibvorgang sonst ein halbes YAML
        hinterlaesst und das Profil damit verloren waere.
        """
        selections: dict[str, list[str]] = {}
        for ref in sorted(config.user_refs()):
            category_id, _, option_id = ref.partition(".")
            selections.setdefault(category_id, []).append(option_id)

        # Nicht aufgeloeste Refs unveraendert zurueckschreiben -- sie stammen
        # aus einem Katalog, den dieser Rechner nicht hat.
        for category_id, option_ids in config.unresolved.items():
            bucket = selections.setdefault(category_id, [])
            for option_id in option_ids:
                if option_id not in bucket:
                    bucket.append(option_id)

        document: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "catalog_version": self.catalog.catalog_version,
            "name": config.profile_name or path.stem,
            "created": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "selections": {key: sorted(value) for key, value in sorted(selections.items())},
            "fields": self._fields_without_secrets(config),
        }
        if description:
            document["description"] = description
        if config.extra_packages:
            document["extra_packages"] = sorted(set(config.extra_packages))
        if config.extra_repositories:
            # Muss mit, sonst geht beim Speichern verloren, WARUM die
            # Freitextpakete ueberhaupt aufloesbar sind.
            document["extra_repositories"] = sorted(set(config.extra_repositories))
        if config.provider_choices:
            document["provider_choices"] = dict(sorted(config.provider_choices.items()))

        if include_snapshot and resolution is not None:
            document["resolved_snapshot"] = {
                "comment": (
                    "Nur zur Information und als Rettungsanker, wenn eine Option "
                    "im Katalog fehlt. Beim Laden wird ausschliesslich 'selections' "
                    "ausgewertet."
                ),
                "packages": list(resolution.package_names),
                "services": [service.unit for service in resolution.services],
                "iso": config.iso_filename,
            }

        self._write_atomic(path, document)
        log.info("Profil gespeichert: %s", path)
        return path

    @staticmethod
    def _write_atomic(path: Path, document: Mapping[str, Any]) -> None:
        ensure_dir(path.parent, mode=0o755)
        text = yaml.safe_dump(
            dict(document),
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
            width=100,
        )
        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        )
        try:
            with handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(handle.name, path)
        except BaseException:
            try:
                os.unlink(handle.name)
            except OSError:
                pass
            raise

    # -- Laden ----------------------------------------------------------------
    def load(self, path: Path) -> ProfileLoadResult:
        try:
            with path.open("r", encoding="utf-8") as file_handle:
                data = yaml.safe_load(file_handle)
        except yaml.YAMLError as exc:
            raise ProfileError(f"{path.name}: Die Datei ist kein gueltiges YAML.\n{exc}") from exc
        except OSError as exc:
            raise ProfileError(f"{path.name}: Datei nicht lesbar.\n{exc}") from exc

        if not isinstance(data, Mapping):
            raise ProfileError(f"{path.name}: Erwartet wurde ein YAML-Objekt.")

        version = data.get("schema_version", SCHEMA_VERSION)
        if not isinstance(version, int):
            raise ProfileError(f"{path.name}: 'schema_version' muss eine Zahl sein.")
        if version > SCHEMA_VERSION:
            # Bewusst kein Rateversuch: ein neueres Format koennte Felder anders
            # bedeuten, und ein halb verstandenes Profil ist schlimmer als keines.
            raise ProfileError(
                f"{path.name} wurde mit einer neueren Programmversion erstellt "
                f"(Format {version}, unterstuetzt wird {SCHEMA_VERSION}).\n"
                f"Bitte ArchCustomiser aktualisieren."
            )

        issues: list[ProfileIssue] = []
        config = BuildConfig(
            catalog_version=str(data.get("catalog_version") or ""),
            profile_name=str(data.get("name") or path.stem),
        )

        if config.catalog_version and config.catalog_version != self.catalog.catalog_version:
            issues.append(
                ProfileIssue(
                    severity="info",
                    code="version_mismatch",
                    ref="",
                    message=(
                        f"Das Profil wurde mit Katalog {config.catalog_version} erstellt, "
                        f"aktuell ist {self.catalog.catalog_version}."
                    ),
                    action_taken="Die Auswahl wird gegen den aktuellen Katalog geprueft.",
                )
            )

        snapshot = data.get("resolved_snapshot") or {}
        snapshot_packages = (
            list(snapshot.get("packages") or ()) if isinstance(snapshot, Mapping) else []
        )

        raw_selections = data.get("selections") or {}
        if not isinstance(raw_selections, Mapping):
            raise ProfileError(f"{path.name}: 'selections' muss ein Objekt sein.")

        for category_id, option_ids in raw_selections.items():
            if not isinstance(option_ids, Sequence) or isinstance(option_ids, str):
                issues.append(
                    ProfileIssue(
                        severity="warning",
                        code="unknown_category",
                        ref=str(category_id),
                        message=f"Der Eintrag {category_id!r} hat kein Listenformat.",
                        action_taken="uebersprungen",
                    )
                )
                continue
            for option_id in option_ids:
                self._resolve_reference(
                    str(category_id), str(option_id), config, issues, snapshot_packages
                )

        fields = data.get("fields") or {}
        if isinstance(fields, Mapping):
            secret_bindings = self._secret_bindings()
            for binding, value in fields.items():
                key = str(binding)
                if key in secret_bindings:
                    # Sollte nicht vorkommen -- aber ein von Hand bearbeitetes
                    # Profil koennte ein Passwort enthalten. Das wird verworfen,
                    # nicht uebernommen.
                    issues.append(
                        ProfileIssue(
                            severity="warning",
                            code="secret_dropped",
                            ref=key,
                            message=(
                                f"Das Profil enthaelt das geheime Feld {key!r}. "
                                f"Der Wert wird nicht uebernommen."
                            ),
                            action_taken="verworfen",
                        )
                    )
                    continue
                config.set_field(key, value)

        extra = data.get("extra_packages") or []
        if isinstance(extra, Sequence) and not isinstance(extra, str):
            # Ergaenzen statt ersetzen: die Rettungskette oben kann bereits
            # Eintraege hinzugefuegt haben (eine entfernte Option, die im
            # Snapshot als Paket auftaucht). Ein Ueberschreiben wuerde genau
            # die Daten verlieren, die gerettet werden sollten.
            #
            # Jeder Name geht durch validate_name. Der Docstring von names.py
            # verlangt das ausdruecklich fuer jeden Namen aus einer Profildatei
            # -- hier geschah es bisher nicht, und ein von Hand bearbeitetes
            # Profil brachte damit einen Eintrag wie '--dbpath=/' unveraendert
            # bis in packages.x86_64 und damit in die Argumentliste von
            # pacstrap.
            for name in extra:
                text = str(name)
                if text in config.extra_packages:
                    continue
                geprueft = _geprueftes_paket(text)
                if geprueft is None:
                    issues.append(
                        ProfileIssue(
                            severity="warning",
                            code="invalid_package",
                            ref=text,
                            message=f"Der Paketname {text!r} ist nicht zulaessig.",
                            action_taken="verworfen",
                        )
                    )
                    continue
                config.extra_packages.append(geprueft)

        repos = data.get("extra_repositories") or []
        if isinstance(repos, Sequence) and not isinstance(repos, str):
            for eintrag in repos:
                # Derselbe Massstab wie bei Paketnamen: was hier steht, landet
                # woertlich in der pacman.conf des Abbilds.
                name = str(eintrag).strip()
                if not _REPO_NAME.match(name):
                    issues.append(
                        ProfileIssue(
                            severity="warning",
                            code="invalid_package",
                            ref=name,
                            message=f"{name!r} ist kein gueltiger Repository-Name.",
                            action_taken="verworfen",
                        )
                    )
                    continue
                if name not in config.extra_repositories:
                    config.extra_repositories.append(name)

        choices = data.get("provider_choices") or {}
        if isinstance(choices, Mapping):
            geprueft_gewaehlt: dict[str, str] = {}
            for virtual, provider in choices.items():
                name = _geprueftes_paket(str(virtual))
                wert = _geprueftes_paket(str(provider))
                if name is None or wert is None:
                    issues.append(
                        ProfileIssue(
                            severity="warning",
                            code="invalid_package",
                            ref=str(virtual),
                            message=(
                                f"Die Anbieterwahl {virtual!r} -> {provider!r} "
                                f"enthaelt einen unzulaessigen Paketnamen."
                            ),
                            action_taken="verworfen",
                        )
                    )
                    continue
                geprueft_gewaehlt[name] = wert
            config.provider_choices = geprueft_gewaehlt

        result = ProfileLoadResult(
            config=config,
            issues=tuple(issues),
            secret_fields=self._required_secret_bindings(config),
        )
        log.info(
            "Profil %s geladen (%d Hinweise, %d nicht aufloesbar)",
            path.name,
            len(issues),
            sum(len(value) for value in config.unresolved.values()),
        )
        return result

    # -- Rettungskette --------------------------------------------------------
    def _resolve_reference(
        self,
        category_id: str,
        option_id: str,
        config: BuildConfig,
        issues: _Issues[ProfileIssue],
        snapshot_packages: _Namen[str],
    ) -> None:
        """Erster Treffer gewinnt; ein Fehlschlag ist nie fatal."""
        ref = f"{category_id}.{option_id}"

        # 1. Exakter Treffer
        option = self.catalog.option(ref)
        if option is not None:
            if option.deprecated and option.replaced_by:
                replacement = self.catalog.option(option.replaced_by)
                if replacement is not None:
                    config.add(option.replaced_by, SelectionSource.PROFILE)
                    issues.append(
                        ProfileIssue(
                            severity="warning",
                            code="deprecated",
                            ref=ref,
                            message=(
                                f"{option.label} gilt als veraltet und wurde durch "
                                f"{replacement.label} ersetzt."
                            ),
                            action_taken=f"{option.replaced_by} ausgewaehlt",
                        )
                    )
                    return
            config.add(ref, SelectionSource.PROFILE)
            return

        # 2./3./4. Alias, Umbenennung der Option oder der Kategorie
        alias_target = self.catalog.resolve_alias(ref)
        if alias_target:
            config.add(alias_target, SelectionSource.PROFILE)
            issues.append(
                ProfileIssue(
                    severity="info",
                    code="renamed",
                    ref=ref,
                    message=(
                        f"{ref} heisst inzwischen {_beschriftung(self.catalog, alias_target)}."
                    ),
                    action_taken=f"auf {alias_target} abgebildet",
                )
            )
            return

        # 5. Notreserve: als Paket aus dem Snapshot uebernehmen
        if option_id in snapshot_packages and option_id not in config.extra_packages:
            config.extra_packages.append(option_id)
            issues.append(
                ProfileIssue(
                    severity="warning",
                    code="moved_to_extra",
                    ref=ref,
                    message=(
                        f"Die Option {ref!r} gibt es im aktuellen Katalog nicht mehr. "
                        f"Da im Profil ein gleichnamiges Paket vermerkt ist, wurde es "
                        f"als Zusatzpaket uebernommen."
                    ),
                    action_taken=f"{option_id} als Zusatzpaket ergaenzt",
                )
            )
            return

        # 6. Unaufloesbar -- aber nicht verloren
        config.unresolved.setdefault(category_id, []).append(option_id)
        issues.append(
            ProfileIssue(
                severity="warning",
                code="unknown_option",
                ref=ref,
                message=(
                    f"Die Option {ref!r} ist in diesem Katalog unbekannt. Sie stammt "
                    f"vermutlich aus einer Erweiterung, die hier nicht installiert ist."
                ),
                action_taken="bleibt im Profil erhalten, ist aber nicht aktiv",
            )
        )

    # -- Hilfsfunktionen ------------------------------------------------------
    def _fields_without_secrets(self, config: BuildConfig) -> dict[str, Any]:
        """Beim Schreiben dieselbe Grenze ziehen wie beim Lesen.

        Passwoerter leben im ``SecretStore`` und erreichen ``BuildConfig`` auf
        dem vorgesehenen Weg nie -- deshalb greift diese Pruefung im Normalfall
        auch nie. Sie steht hier, weil die Zusicherung sonst allein davon
        abhinge, dass die Oberflaeche sich richtig verhaelt: ein einziger
        ``set_field`` auf ein geheimes Feld, und das Passwort stuende im Klartext
        in einer Datei, die der Benutzer weitergibt.

        Beim *Laden* wurde das schon immer abgefangen. Eine Grenze, die nur in
        einer Richtung haelt, ist keine.
        """
        secret_bindings = self._secret_bindings()
        sauber: dict[str, Any] = {}
        for key, value in sorted(config.fields.items()):
            if str(key) in secret_bindings:
                log.warning(
                    "Geheimes Feld %r stand in der Konfiguration und wurde beim "
                    "Speichern entfernt. Das deutet auf einen Fehler im "
                    "Aufrufer hin -- Passwoerter gehoeren in den SecretStore.",
                    key,
                )
                continue
            sauber[key] = value
        return sauber

    def _secret_bindings(self) -> frozenset[str]:
        return frozenset(
            spec.binding
            for category in self.catalog.categories
            for spec in category.fields
            if spec.secret
        )

    def _required_secret_bindings(self, config: BuildConfig) -> tuple[str, ...]:
        """Geheime Felder, die unter den geladenen Einstellungen aktiv sind."""
        needed: list[str] = []
        for category in self.catalog.categories:
            for spec in category.fields:
                if not spec.secret:
                    continue
                if spec.enabled_when.is_always_true or _field_predicate_true(
                    spec.enabled_when, config
                ):
                    needed.append(spec.binding)
        return tuple(needed)

    def default_path(self, name: str) -> Path:
        safe = "".join(char for char in name if char.isalnum() or char in "-_ ").strip()
        safe = safe.replace(" ", "-").lower() or "profil"
        return self.profiles_dir / f"{safe}.yaml"


class _FieldOnlyContext:
    """Praedikat-Kontext, der nur Formularfelder kennt.

    Reicht fuer ``enabled_when: "field:user.create"`` -- Auswahlen sind beim
    Ermitteln der noetigen Passwortfelder noch nicht aufgeloest.
    """

    __slots__ = ("config",)

    def __init__(self, config: BuildConfig) -> None:
        self.config = config

    def is_selected(self, ref: str) -> bool:
        return self.config.is_selected(ref)

    def has_capability(self, name: str) -> bool:
        return False

    def field_value(self, binding: str) -> Any:
        return self.config.fields.get(binding)


def _field_predicate_true(predicate: Any, config: BuildConfig) -> bool:
    try:
        return bool(predicate.evaluate(_FieldOnlyContext(config)))
    except Exception:
        return True


def _geprueftes_paket(text: str) -> str | None:
    """Ein Paketname, wie ihn die Sicherheitsgrenze in names.py verlangt.

    Versionsangaben (``firefox>=140``) sind in ``packages.x86_64`` erlaubt und
    werden deshalb vom Namen getrennt geprueft, nicht abgelehnt.

    Liefert ``None``, wenn der Name nicht zulaessig ist -- der Aufrufer meldet
    das als ProfileIssue, statt ihn stillschweigend weiterzureichen.
    """
    from .packages.names import InvalidPackageName, split_constraint, validate_name

    roh = text.strip()
    if not roh:
        return None
    try:
        name, _bedingung = split_constraint(roh)
        validate_name(name)
    except InvalidPackageName:
        return None
    except Exception:
        return None
    return roh


def _beschriftung(catalog, ref: str) -> str:
    """Die Beschriftung einer Option -- oder ihre Kennung, wenn es sie nicht gibt.

    Ein Alias kann auf eine inzwischen entfernte Option zeigen; der Code
    rechnet an anderer Stelle selbst damit.
    """
    option = catalog.option(ref)
    return option.label if option is not None else ref
