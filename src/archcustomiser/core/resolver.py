"""Aufloesung der Auswahl zu einem konsistenten Systemzustand.

Reines Python, kein Qt -- damit vollstaendig ohne GUI testbar.

Der Resolver macht fuenf Dinge:

1. **implies transitiv aufloesen** (Fixpunktiteration). KDE zieht sddm, sddm
   zieht das grafische Target.
2. **Capability-Aritaeten pruefen.** Statt jede Audio-Option gegen jede andere
   zu deklarieren, liefern alle die Capability ``audio-server`` mit
   ``at_most_one``.
3. **Fehlende Capabilities ergaenzen.** Wer eine grafische Sitzung waehlt,
   braucht einen Display-Manager -- sonst startet nichts.
4. **Beitraege einsammeln und deduplizieren**: Pakete, Services, Dateien,
   Kernel-Parameter, Repositories.
5. **Probleme als Issue-Liste melden** statt eine Exception zu werfen. Der
   Wizard soll widerspruechliche Zwischenzustaende anzeigen koennen, ohne
   abzustuerzen.

Was der Resolver ausdruecklich NICHT tut: Paketabhaengigkeiten aufloesen. Das
macht pacman zur Bauzeit. Ein Nachbau waere sowohl fehleranfaellig als auch
ueberfluessig.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from .catalog import Arity, Catalog, EnableIn, FileEntry, Option, SelectionMode, ServiceRef
from .config import BuildConfig

log = logging.getLogger(__name__)

Severity = Literal["error", "warning", "info"]


@dataclass(frozen=True, slots=True)
class Fix:
    """Ein maschinell anwendbarer Vorschlag zu einem Problem."""

    label: str
    select: tuple[str, ...] = ()
    deselect: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Issue:
    severity: Severity
    code: str
    message: str
    category_id: str | None = None
    refs: tuple[str, ...] = ()
    fix: Fix | None = None

    @property
    def blocking(self) -> bool:
        return self.severity == "error"


@dataclass(frozen=True, slots=True)
class ResolvedService:
    """Ein Dienst mit allen Symlinks, die 'systemctl enable' anlegen wuerde."""

    service: ServiceRef
    origin: str

    @property
    def unit(self) -> str:
        return self.service.unit

    def symlinks(self) -> tuple[tuple[str, str], ...]:
        return self.service.symlinks()


@dataclass(frozen=True, slots=True)
class ResolvedPackage:
    name: str
    origins: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Resolution:
    """Der vollstaendig aufgeloeste Zustand."""

    effective_refs: frozenset[str]
    auto_refs: frozenset[str]
    packages: tuple[ResolvedPackage, ...]
    package_groups: tuple[str, ...]
    aur_packages: tuple[str, ...]
    services: tuple[ResolvedService, ...]
    files: tuple[FileEntry, ...]
    kernel_params: tuple[str, ...]
    mkinitcpio_hooks: tuple[str, ...]
    repositories: tuple[str, ...]
    capabilities: Mapping[str, tuple[str, ...]]
    semantics: Mapping[str, Any]
    issues: tuple[Issue, ...]
    estimated_size_mb: int = 0

    def mit_repositories(self, weitere: Iterable[str]) -> Resolution:
        """Dieselbe Aufloesung, um zusaetzliche Repositorien ergaenzt.

        Der Katalog nennt ein Repository nur an der Option, die es braucht
        (``repos: [multilib]`` bei Steam). Ein *frei eingegebenes* Paket aus
        multilib -- etwa ``lib32-vulkan-icd-loader`` -- wurde dagegen gegen
        multilib geprueft und als gefunden gemeldet, obwohl die erzeugte
        pacman.conf das Repository gar nicht aktiviert: pacstrap brach dann
        mitten im Bau mit "target not found" ab.

        Die Paketschicht weiss, aus welchem Repository ein Name stammt; sie
        reicht das hierueber nach. Bewusst als neue Instanz -- die Aufloesung
        bleibt unveraenderlich.
        """
        import dataclasses

        zusammen = tuple(sorted(set(self.repositories) | {r for r in weitere if r}))
        if zusammen == self.repositories:
            return self
        return dataclasses.replace(self, repositories=zusammen)

    # -- bequeme Sichten ------------------------------------------------------
    @property
    def kernel_suffix(self) -> str:
        """Der Kernelname, z.B. 'linux-zen'.

        Steuert den Namen der mkinitcpio-Vorgabedatei UND jeden
        Bootmenue-Eintrag -- beide muessen zusammenpassen, sonst startet das
        erzeugte Abbild nicht.
        """
        value = self.semantics.get("kernel_suffix")
        return value if isinstance(value, str) and value else "linux"

    @property
    def package_names(self) -> tuple[str, ...]:
        return tuple(package.name for package in self.packages)

    @property
    def blocking_issues(self) -> tuple[Issue, ...]:
        return tuple(issue for issue in self.issues if issue.blocking)

    @property
    def is_valid(self) -> bool:
        return not self.blocking_issues

    def issues_for(self, category_id: str) -> tuple[Issue, ...]:
        return tuple(issue for issue in self.issues if issue.category_id == category_id)

    def services_for(self, target: EnableIn) -> tuple[ResolvedService, ...]:
        """Live-ISO und installiertes System aktivieren nicht dieselben Dienste."""
        return tuple(
            resolved
            for resolved in self.services
            if resolved.service.enable_in in (EnableIn.BOTH, target)
        )

    def all_symlinks(self, target: EnableIn = EnableIn.LIVE) -> tuple[tuple[str, str], ...]:
        links: list[tuple[str, str]] = []
        for resolved in self.services_for(target):
            links.extend(resolved.symlinks())
        return tuple(sorted(set(links)))


class _Context:
    """Auswertungskontext fuer Praedikate waehrend der Aufloesung."""

    __slots__ = ("caps", "config", "refs")

    def __init__(self, refs: set[str], caps: set[str], config: BuildConfig) -> None:
        self.refs = refs
        self.caps = caps
        self.config = config

    def is_selected(self, ref: str) -> bool:
        return ref in self.refs

    def has_capability(self, name: str) -> bool:
        return name in self.caps

    def field_value(self, binding: str) -> Any:
        return self.config.fields.get(binding)


class Resolver:
    """Erzeugt aus einer ``BuildConfig`` eine ``Resolution``."""

    MAX_ITERATIONS = 32

    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog

    # -- oeffentlich ----------------------------------------------------------
    def resolve(self, config: BuildConfig) -> Resolution:
        issues: list[Issue] = []
        refs = self._known_refs(config, issues)
        auto: set[str] = set()

        refs, auto = self._close_implies(refs, auto, config, issues)
        refs, auto = self._fill_capabilities(refs, auto, config, issues)

        capabilities = self._capability_map(refs)
        context = _Context(refs, set(capabilities), config)

        self._check_arities(capabilities, config, issues)
        self._check_requirements(refs, capabilities, issues)
        self._check_conflicts(refs, issues)
        self._check_category_rules(config, refs, issues)
        issues = self._deduplicate(issues)

        options = [option for ref in sorted(refs) if (option := self.catalog.option(ref))]
        return self._collect(options, refs, auto, capabilities, context, config, issues)

    def auto_recommendations(self, config: BuildConfig, ref: str) -> tuple[str, ...]:
        """Weiche Empfehlungen einer gerade gewaehlten Option.

        Bewusst getrennt von ``implies``: Empfehlungen werden einmalig
        vorgehakt und sind danach frei abwaehlbar. Der Resolver setzt sie
        deshalb nicht selbst -- das macht der Store beim Anklicken.
        """
        option = self.catalog.option(ref)
        if option is None:
            return ()
        return tuple(
            candidate
            for candidate in option.recommends
            if self.catalog.option(candidate) and not config.is_selected(candidate)
        )

    # -- Schritte -------------------------------------------------------------
    def _known_refs(self, config: BuildConfig, issues: list[Issue]) -> set[str]:
        refs: set[str] = set()
        for ref in config.all_refs():
            if self.catalog.option(ref) is not None:
                refs.add(ref)
                continue
            category_id = ref.split(".", 1)[0]
            issues.append(
                Issue(
                    severity="warning",
                    code="unknown_option",
                    message=(
                        f"Die Auswahl {ref!r} kommt im aktuellen Katalog nicht vor "
                        f"und wird ignoriert."
                    ),
                    category_id=category_id,
                    refs=(ref,),
                )
            )
        return refs

    def _close_implies(
        self,
        refs: set[str],
        auto: set[str],
        config: BuildConfig,
        issues: list[Issue],
    ) -> tuple[set[str], set[str]]:
        """Transitive Huelle ueber ``implies`` als Fixpunkt."""
        for iteration in range(self.MAX_ITERATIONS):
            added = set()
            for ref in tuple(refs):
                option = self.catalog.option(ref)
                if option is None:
                    continue
                for implied in option.implies:
                    if implied not in refs and self.catalog.option(implied) is not None:
                        added.add(implied)
            if not added:
                break
            refs |= added
            auto |= added
        else:
            # Nur erreichbar bei einem Zyklus im Katalog, der pro Runde neue
            # Refs erzeugt. Der Katalog sollte das nicht koennen -- lieber
            # melden als endlos laufen.
            issues.append(
                Issue(
                    severity="error",
                    code="implies_cycle",
                    message=(
                        "Die Abhaengigkeiten im Katalog liessen sich nicht aufloesen "
                        f"(Abbruch nach {self.MAX_ITERATIONS} Durchlaeufen). "
                        "Vermutlich enthaelt 'implies' einen Zyklus."
                    ),
                )
            )
        # AUTO-Refs, die der Benutzer selbst gesetzt hat, bleiben USER.
        auto -= config.user_refs()
        return refs, auto

    def _fill_capabilities(
        self,
        refs: set[str],
        auto: set[str],
        config: BuildConfig,
        issues: list[Issue],
    ) -> tuple[set[str], set[str]]:
        """Pflicht-Capabilities mit dem Standardanbieter auffuellen."""
        for _ in range(self.MAX_ITERATIONS):
            capabilities = self._capability_map(refs)
            context = _Context(refs, set(capabilities), config)
            added = set()

            for name, spec in self.catalog.capabilities.items():
                if capabilities.get(name):
                    continue
                if not spec.required_if:
                    continue
                if not self._any_leaf_true(spec.required_if, context):
                    continue
                if not spec.default_provider:
                    issues.append(
                        Issue(
                            severity="error",
                            code="capability_missing",
                            message=(
                                f"Es wird ein {spec.label or name} benoetigt, aber keiner "
                                f"ist ausgewaehlt."
                            ),
                        )
                    )
                    continue
                added.add(spec.default_provider)
                issues.append(
                    Issue(
                        severity="info",
                        code="capability_autofill",
                        message=(
                            f"{self._label(spec.default_provider)} wurde automatisch "
                            f"ergaenzt, weil ein {spec.label or name} benoetigt wird."
                        ),
                        refs=(spec.default_provider,),
                    )
                )
            if not added:
                break
            refs |= added
            auto |= added
            # Der neue Anbieter kann selbst wieder etwas implizieren.
            refs, auto = self._close_implies(refs, auto, config, issues)
        auto -= config.user_refs()
        return refs, auto

    def _check_arities(
        self,
        capabilities: Mapping[str, tuple[str, ...]],
        config: BuildConfig,
        issues: list[Issue],
    ) -> None:
        for name, spec in self.catalog.capabilities.items():
            providers = capabilities.get(name, ())
            if spec.arity is Arity.MANY:
                continue
            if len(providers) <= 1:
                continue

            # Bei auto_resolve schlagen wir vor, die aelteren abzuwaehlen --
            # welche das sind, entscheidet der Store anhand der Klick-Reihenfolge.
            keep = providers[-1]
            drop = tuple(ref for ref in providers if ref != keep)
            labels = ", ".join(self._label(ref) for ref in providers)
            fix = (
                Fix(
                    label=f"Nur {self._label(keep)} behalten",
                    deselect=drop,
                )
                if spec.auto_resolve == "deselect_previous"
                else None
            )
            issues.append(
                Issue(
                    severity=spec.on_violation,
                    code="capability_arity",
                    message=(
                        f"{spec.label or name}: {labels} sind gleichzeitig ausgewaehlt. "
                        + (
                            "Es kann nur eines davon aktiv sein."
                            if spec.arity is not Arity.MANY
                            else ""
                        )
                    ).strip(),
                    category_id=providers[0].split(".", 1)[0],
                    refs=providers,
                    fix=fix,
                )
            )

    def _check_requirements(
        self,
        refs: set[str],
        capabilities: Mapping[str, tuple[str, ...]],
        issues: list[Issue],
    ) -> None:
        for ref in sorted(refs):
            option = self.catalog.option(ref)
            if option is None:
                continue

            for required in option.requires:
                if required in refs:
                    continue
                issues.append(
                    Issue(
                        severity="error",
                        code="missing_requirement",
                        message=(
                            f"{option.label} benoetigt {self._label(required)}, "
                            f"das nicht ausgewaehlt ist."
                        ),
                        category_id=option.category_id,
                        refs=(ref, required),
                        fix=Fix(
                            label=f"{self._label(required)} hinzufuegen",
                            select=(required,),
                        ),
                    )
                )

            if option.requires_any:
                if any(self._leaf_true(leaf, refs, capabilities) for leaf in option.requires_any):
                    continue
                readable = ", ".join(self._label(leaf) for leaf in option.requires_any)
                issues.append(
                    Issue(
                        severity="error",
                        code="missing_requirement",
                        message=(
                            f"{option.label} benoetigt mindestens eines davon: {readable}."
                        ),
                        category_id=option.category_id,
                        refs=(ref,) + option.requires_any,
                    )
                )

    def _check_conflicts(self, refs: set[str], issues: list[Issue]) -> None:
        reported: set[frozenset[str]] = set()
        for ref in sorted(refs):
            option = self.catalog.option(ref)
            if option is None:
                continue
            for other in option.conflicts:
                if other not in refs:
                    continue
                pair = frozenset({ref, other})
                if pair in reported:
                    continue
                reported.add(pair)
                issues.append(
                    Issue(
                        severity="error",
                        code="conflict",
                        message=(
                            f"{option.label} und {self._label(other)} koennen nicht "
                            f"gemeinsam verwendet werden."
                        ),
                        category_id=option.category_id,
                        refs=(ref, other),
                        fix=Fix(label=f"{self._label(other)} entfernen", deselect=(other,)),
                    )
                )

    def _check_category_rules(
        self, config: BuildConfig, refs: set[str], issues: list[Issue]
    ) -> None:
        for category in self.catalog.categories:
            selected = {
                ref.split(".", 1)[1] for ref in refs if ref.startswith(f"{category.id}.")
            }
            count = len(selected)

            if category.required and count == 0:
                issues.append(
                    Issue(
                        severity="error",
                        code="selection_required",
                        message=f"Unter '{category.title}' muss etwas ausgewaehlt werden.",
                        category_id=category.id,
                    )
                )
            minimum = category.min_selected
            if minimum and count < minimum:
                issues.append(
                    Issue(
                        severity="error",
                        code="too_few_selected",
                        message=(
                            f"'{category.title}': mindestens {minimum} Eintraege noetig, "
                            f"aktuell {count}."
                        ),
                        category_id=category.id,
                    )
                )
            maximum = category.max_selected
            if maximum and count > maximum:
                issues.append(
                    Issue(
                        severity="error",
                        code="too_many_selected",
                        message=(
                            f"'{category.title}': hoechstens {maximum} Eintraege erlaubt, "
                            f"aktuell {count}."
                        ),
                        category_id=category.id,
                    )
                )
            if (
            category.selection_mode
            in (SelectionMode.SINGLE, SelectionMode.SINGLE_OPTIONAL)
            and count > 1
        ):
                issues.append(
                    Issue(
                        severity="error",
                        code="single_selection",
                        message=f"Unter '{category.title}' ist nur eine Auswahl moeglich.",
                        category_id=category.id,
                    )
                )

    def _collect(
        self,
        options: list[Option],
        refs: set[str],
        auto: set[str],
        capabilities: Mapping[str, tuple[str, ...]],
        context: _Context,
        config: BuildConfig,
        issues: list[Issue],
    ) -> Resolution:
        packages: dict[str, list[str]] = {}
        groups: set[str] = set()
        aur: set[str] = set()
        services: dict[str, ResolvedService] = {}
        files: list[FileEntry] = []
        kernel_params: list[str] = []
        hooks: list[str] = []
        repositories: set[str] = set()
        semantics: dict[str, Any] = {}
        size = 0

        for option in options:
            for package in option.packages:
                if not package.when.evaluate(context):
                    continue
                packages.setdefault(package.name, []).append(option.ref)
            groups.update(option.package_groups)
            aur.update(option.aur_packages)
            repositories.update(option.repos)
            size += option.est_size_mb

            for service in option.services:
                if not service.when.evaluate(context):
                    continue
                key = service.dedup_key
                if key not in services:
                    services[key] = ResolvedService(service=service, origin=option.ref)

            for entry in option.files:
                if entry.when.evaluate(context):
                    files.append(entry)

            for param in option.boot.kernel_params:
                if param not in kernel_params:
                    kernel_params.append(param)
            for hook in option.boot.mkinitcpio_hooks:
                if hook not in hooks:
                    hooks.append(hook)

            for key, value in option.semantics.items():
                semantics[key] = _merge_semantic(semantics.get(key), value)

        for name in config.extra_packages:
            packages.setdefault(name, []).append("extra_packages")

        # Vom Benutzer gewaehlte Anbieter virtueller Pakete ersetzen den
        # virtuellen Namen -- pacman wuerde sonst interaktiv nachfragen und im
        # nicht-interaktiven Build abbrechen.
        for virtual, provider in config.provider_choices.items():
            if virtual in packages and provider != virtual:
                packages.setdefault(provider, []).extend(packages.pop(virtual))

        if aur:
            issues.append(
                Issue(
                    severity="warning",
                    code="aur_packages",
                    message=(
                        f"{len(aur)} Paket(e) stammen aus dem AUR. Diese koennen nicht "
                        f"direkt installiert werden und muessen vor dem Build lokal "
                        f"gebaut werden: {', '.join(sorted(aur))}"
                    ),
                )
            )

        return Resolution(
            effective_refs=frozenset(refs),
            auto_refs=frozenset(auto),
            packages=tuple(
                ResolvedPackage(name=name, origins=tuple(sorted(set(origins))))
                for name, origins in sorted(packages.items())
            ),
            package_groups=tuple(sorted(groups)),
            aur_packages=tuple(sorted(aur)),
            services=tuple(sorted(services.values(), key=lambda s: (s.service.scope.value, s.unit))),
            files=tuple(files),
            kernel_params=tuple(kernel_params),
            mkinitcpio_hooks=tuple(hooks),
            repositories=tuple(sorted(repositories)),
            capabilities=capabilities,
            semantics=semantics,
            issues=tuple(issues),
            estimated_size_mb=size,
        )

    @staticmethod
    def _deduplicate(issues: list[Issue]) -> list[Issue]:
        """Doppelte Aussagen zusammenstreichen.

        Zwei gleichzeitig gewaehlte Audio-Optionen loesen sowohl die
        Capability-Pruefung als auch die Kategorieregel aus. Beide Meldungen
        anzuzeigen erklaert nichts zusaetzlich, verdoppelt aber die Fehlerliste.
        Die Capability-Meldung gewinnt, weil sie den Grund nennt und einen Fix
        anbietet.
        """
        covered = {
            issue.category_id
            for issue in issues
            if issue.code == "capability_arity" and issue.category_id
        }
        return [
            issue
            for issue in issues
            if not (issue.code == "single_selection" and issue.category_id in covered)
        ]

    # -- Hilfsfunktionen ------------------------------------------------------
    def _capability_map(self, refs: Iterable[str]) -> dict[str, tuple[str, ...]]:
        found: dict[str, list[str]] = {}
        for ref in sorted(refs):
            option = self.catalog.option(ref)
            if option is None:
                continue
            for capability in option.provides:
                found.setdefault(capability, []).append(ref)
        return {name: tuple(providers) for name, providers in found.items()}

    def _leaf_true(
        self, leaf: str, refs: set[str], capabilities: Mapping[str, tuple[str, ...]]
    ) -> bool:
        leaf = leaf.strip()
        if leaf.startswith("cap:"):
            return bool(capabilities.get(leaf[4:].strip()))
        return leaf in refs

    def _any_leaf_true(self, leaves: Iterable[str], context: _Context) -> bool:
        for leaf in leaves:
            leaf = leaf.strip()
            if leaf.startswith("cap:"):
                if context.has_capability(leaf[4:].strip()):
                    return True
            elif leaf.startswith("field:"):
                if context.field_value(leaf[6:].strip()):
                    return True
            elif leaf in context.refs:
                return True
        return False

    def _label(self, ref: str) -> str:
        option = self.catalog.option(ref)
        return option.label if option else ref


def _merge_semantic(existing: Any, incoming: Any) -> Any:
    """Semantik-Werte zusammenfuehren.

    Listen werden vereinigt (mehrere Desktops = mehrere Profil-Details),
    alles andere wird ueberschrieben.
    """
    if existing is None:
        return incoming
    if isinstance(existing, list) and isinstance(incoming, list):
        merged = list(existing)
        for item in incoming:
            if item not in merged:
                merged.append(item)
        return merged
    return incoming
