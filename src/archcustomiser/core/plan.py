"""Bauplan fuer den Dry-Run (Spec Abschnitt 14).

Fuehrt Auswahl, Aufloesung und Paketpruefung zu einer Darstellung zusammen und
erzeugt zusaetzlich die archinstall-Konfiguration.

Warum die archinstall-Konfiguration schon jetzt entsteht, obwohl der ISO-Build
erst spaeter kommt: archiso erzeugt ausschliesslich ein Live-System. Ohne
Installationsprogramm ist das erzeugte System nach einem Neustart spurlos weg.
Der Weg dorthin fuehrt ueber ``archinstall``, und das will semantische Angaben
(``profile.details = ["KDE Plasma"]``, ``audio = "pipewire"``), keine
Paketnamen. Indem die Vorschau schon im Dry-Run sichtbar ist, faellt sofort
auf, wenn dem Katalog eine semantische Zuordnung fehlt -- statt erst beim
ersten echten Build.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping

from .catalog import Catalog, EnableIn
from .config import BuildConfig
from .packages.models import ValidationReport
from .resolver import Issue, Resolution

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PlanSection:
    """Ein Block in der Zusammenfassung."""

    title: str
    lines: tuple[str, ...]
    detail: tuple[str, ...] = ()   # aufklappbar, z.B. Gruppenmitglieder

    @property
    def is_empty(self) -> bool:
        return not self.lines


@dataclass(frozen=True, slots=True)
class BuildPlan:
    config: BuildConfig
    resolution: Resolution
    report: ValidationReport | None
    sections: tuple[PlanSection, ...]
    packages: tuple[str, ...]
    services: tuple[str, ...]
    symlinks: tuple[tuple[str, str], ...]
    repositories: tuple[str, ...]
    archinstall: Mapping[str, Any] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    @property
    def iso_filename(self) -> str:
        return self.config.iso_filename

    @property
    def issues(self) -> tuple[Issue, ...]:
        return self.resolution.issues

    @property
    def can_build(self) -> bool:
        return self.resolution.is_valid and (self.report is None or self.report.is_clean)


def _set_nested(target: dict[str, Any], dotted_key: str, value: Any) -> None:
    """``profile_config.profile.main`` -> verschachteltes Dict.

    Der Katalog schreibt gepunktete Schluessel, weil sie sich verlustfrei
    zusammenfuehren lassen; archinstall erwartet die verschachtelte Form.
    """
    parts = dotted_key.split(".")
    node = target
    for part in parts[:-1]:
        existing = node.get(part)
        if not isinstance(existing, dict):
            existing = {}
            node[part] = existing
        node = existing
    node[parts[-1]] = value


def build_archinstall_config(
    config: BuildConfig, resolution: Resolution
) -> dict[str, Any]:
    """Erzeugt die Konfiguration fuer das installierte Zielsystem.

    Bewusst ohne ``disk_config``: die Partitionierung soll der Benutzer am
    Zielrechner selbst bestaetigen. Eine vorgegebene Platte wuerde mit
    ``--silent`` ohne Rueckfrage geloescht.

    Ebenfalls bewusst ohne Passwoerter -- die stehen in einer getrennten
    Datei mit Rechten 0600, die erst beim Build aus dem SecretStore erzeugt
    wird.
    """
    document: dict[str, Any] = {}

    for dotted_key, value in sorted(resolution.semantics.items()):
        if dotted_key == "kernel_suffix":
            continue   # nur intern fuer Preset- und Bootmenue-Namen
        _set_nested(document, dotted_key, value)

    document["hostname"] = config.hostname
    document["locale_config"] = {
        "kb_layout": config.field_str("basics.keymap", "de-latin1"),
        "sys_enc": "UTF-8",
        "sys_lang": config.field_str("basics.locale", "de_DE.UTF-8"),
    }
    document["timezone"] = config.field_str("basics.timezone", "Europe/Berlin")
    document["ntp"] = config.field_bool("basics.ntp", True)

    # Alles, was der Benutzer zusammengestellt hat -- nicht nur die
    # Freitextpakete. Die Annahme, archinstall bringe die Pakete der gewaehlten
    # Optionen ueber sein Profil selbst mit, traegt nur fuer die Handvoll
    # Desktop-Profile, die archinstall kennt. Wer Firefox, Steam und Docker
    # anhakte, fand sie im installierten System nicht wieder.
    #
    # ``resolution.package_names`` enthaelt nur die gewaehlten und aufgeloesten
    # Pakete; die reinen Live-Helfer (archinstall, syslinux,
    # mkinitcpio-archiso) kommen erst spaeter ueber ``required_packages()``
    # dazu und schwappen hier nicht mit hinein.
    pakete = set(config.extra_packages) | set(resolution.package_names)
    if pakete:
        document["packages"] = sorted(pakete)

    services = sorted(
        {
            resolved.unit
            for resolved in resolution.services_for(EnableIn.TARGET)
            if resolved.service.scope.value == "system"
        }
    )
    if services:
        document["services"] = services

    optional_repos = [repo for repo in resolution.repositories if repo in ("multilib", "testing")]
    if optional_repos:
        # Ohne diesen Eintrag findet die Installation Steam nicht -- multilib
        # ist in der Standardkonfiguration abgeschaltet.
        document.setdefault("mirror_config", {})["optional_repositories"] = optional_repos

    document["bootloader_config"] = {
        "bootloader": (
            "Grub"
            if config.field_str("build.uefi_boot") == "grub"
            else "Systemd-boot"
        ),
        "uki": False,
    }
    return document


def _describe_selection(catalog: Catalog, resolution: Resolution, category_id: str) -> list[str]:
    lines: list[str] = []
    for ref in sorted(resolution.effective_refs):
        if not ref.startswith(f"{category_id}."):
            continue
        option = catalog.option(ref)
        if option is None:
            continue
        suffix = " (automatisch ergaenzt)" if ref in resolution.auto_refs else ""
        lines.append(f"{option.label}{suffix}")
    return lines


def build_plan(
    catalog: Catalog,
    config: BuildConfig,
    resolution: Resolution,
    report: ValidationReport | None = None,
) -> BuildPlan:
    """Setzt die Zusammenfassung zusammen."""
    sections: list[PlanSection] = []
    warnings: list[str] = []

    sections.append(
        PlanSection(
            "System",
            (
                f"Hostname: {config.hostname}",
                f"Sprache: {config.field_str('basics.locale', '-')}",
                f"Tastatur: {config.field_str('basics.keymap', '-')}",
                f"Zeitzone: {config.field_str('basics.timezone', '-')}",
            ),
        )
    )

    for category in catalog.ordered_categories():
        if not category.options:
            continue
        lines = _describe_selection(catalog, resolution, category.id)
        if lines:
            sections.append(PlanSection(category.title, tuple(lines)))

    if config.creates_user:
        user_lines = [
            f"Benutzername: {config.username}",
            f"Voller Name: {config.field_str('user.full_name', '-')}",
            "Administratorrechte: " + ("ja (Gruppe wheel)" if config.field_bool("user.sudo", True) else "nein"),
            "Root-Konto: " + ("gesperrt" if config.field_bool("user.root_locked", True) else "aktiv"),
        ]
        sections.append(PlanSection("Benutzerkonto", tuple(user_lines)))
    else:
        sections.append(PlanSection("Benutzerkonto", ("Kein Benutzerkonto wird angelegt.",)))

    sections.append(
        PlanSection(
            "Branding",
            (
                f"Name: {config.distro_name} {config.version}",
                f"ISO-Datei: {config.iso_filename}",
                f"Datentraegerbezeichnung: {config.iso_label}",
                f"Verzeichnis auf der ISO: {config.install_dir}",
            ),
        )
    )

    # Paketliste: was tatsaechlich in packages.x86_64 landet.
    package_names = list(resolution.package_names)
    detail: list[str] = []
    if report is not None:
        for entry in report.entries:
            if entry.kind.name == "GROUP":
                detail.append(f"{entry.normalized}: " + ", ".join(entry.members[:12]) + (" ..." if len(entry.members) > 12 else ""))
    for group in resolution.package_groups:
        if group not in package_names:
            package_names.append(group)

    size_hint = (
        f" (geschaetzt {resolution.estimated_size_mb} MB installiert)"
        if resolution.estimated_size_mb
        else ""
    )
    sections.append(
        PlanSection(
            f"Pakete ({len(package_names)}){size_hint}",
            tuple(sorted(package_names)),
            tuple(detail),
        )
    )

    service_units = tuple(resolved.unit for resolved in resolution.services_for(EnableIn.LIVE))
    symlinks = resolution.all_symlinks(EnableIn.LIVE)
    sections.append(
        PlanSection(
            f"Dienste ({len(service_units)})",
            service_units,
            tuple(f"{link}  ->  {target}" for link, target in symlinks),
        )
    )

    if resolution.repositories:
        sections.append(
            PlanSection(
                "Zusaetzliche Repositories",
                tuple(resolution.repositories),
                ("Wird in pacman.conf und in der Installationskonfiguration aktiviert.",),
            )
        )

    build_lines = [
        "BIOS-Start: " + ("ja" if config.field_bool("build.bios_boot", True) else "nein"),
        f"UEFI-Bootloader: {config.field_str('build.uefi_boot', 'systemd-boot')}",
        f"Kompression: {config.field_str('build.compression', 'balanced')}",
        f"Schreibspeicher der Live-Sitzung: {config.field_str('build.cow_spacesize', '2G')}",
    ]
    if config.field_bool("build.include_installer", True):
        build_lines.append("Installationsprogramm archinstall wird mitgeliefert.")
    else:
        warnings.append(
            "Ohne Installationsprogramm entsteht ein reines Live-System: nach einem "
            "Neustart sind alle Aenderungen weg. Das laesst sich unter "
            "'ISO-Einstellungen' aendern."
        )
    sections.append(PlanSection("ISO-Einstellungen", tuple(build_lines)))

    if report is not None:
        if report.degraded:
            warnings.append(
                "Die Paketnamen konnten nicht geprueft werden, weil keine aktuellen "
                "Paketdaten vorliegen. Falsch geschriebene Namen fallen dann erst "
                "beim Build auf."
            )
        for entry in report.blocking:
            warnings.append(f"{entry.query}: {entry.message}")
        if report.index_meta is not None and report.index_meta.data_updated_at:
            sections.append(
                PlanSection(
                    "Paketdaten",
                    (
                        f"Stand: {report.index_meta.data_updated_at.astimezone():%d.%m.%Y %H:%M}",
                        f"{report.index_meta.package_count} Pakete aus "
                        f"{', '.join(report.index_meta.repo_names)}",
                    ),
                )
            )

    for issue in resolution.issues:
        if issue.severity in ("error", "warning"):
            warnings.append(issue.message)

    return BuildPlan(
        config=config,
        resolution=resolution,
        report=report,
        sections=tuple(section for section in sections if not section.is_empty),
        packages=tuple(sorted(package_names)),
        services=service_units,
        symlinks=symlinks,
        repositories=resolution.repositories,
        archinstall=build_archinstall_config(config, resolution),
        warnings=tuple(dict.fromkeys(warnings)),
    )


def plan_as_text(plan: BuildPlan) -> str:
    """Klartextfassung -- fuer Logdatei und Zwischenablage."""
    lines: list[str] = [
        f"Bauplan fuer {plan.config.distro_name} {plan.config.version}",
        f"Ergebnis: {plan.iso_filename}",
        "",
    ]
    for section in plan.sections:
        lines.append(section.title)
        lines.append("-" * len(section.title))
        lines.extend(f"  {line}" for line in section.lines)
        lines.append("")
    if plan.warnings:
        lines.append("Hinweise")
        lines.append("--------")
        lines.extend(f"  - {warning}" for warning in plan.warnings)
    return "\n".join(lines)
