"""Die Fassade: erzeugt aus der Konfiguration ein vollstaendiges archiso-Profil.

Der Ablauf ist bewusst geradlinig -- jeder Schritt fuellt denselben
``ProfileTree``, und erst ganz am Ende entsteht ``profiledef.sh``, weil dessen
``file_permissions``-Block alles einsammelt, was die vorherigen Schritte
angemeldet haben.

Am Schluss steht eine Selbstpruefung: erfuellt das Erzeugte die Bedingungen, die
mkarchiso beim Bauen selbst prueft? Diese Fehler jetzt zu melden ist deutlich
freundlicher, als sie den Benutzer nach dem Kopieren auf ein Arch-System
entdecken zu lassen.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from ..catalog import Catalog
from ..config import BuildConfig
from ..plan import build_archinstall_config
from ..resolver import Resolution
from ..secrets import SecretStore
from .airootfs import build_airootfs
from .bootloader import build_bootloaders
from .branding import build_branding, menu_title
from .errors import ProfileError
from .packages import AddedPackage, render_packages, required_packages
from .pacman_conf import render_pacman_conf
from .profiledef import render_profiledef
from .settings import ArchisoSettings, build_settings
from .tree import ProfileTree

log = logging.getLogger(__name__)

ORIGIN = "generator"
INSTALLER_DIR = "airootfs/etc/archcustomiser"

# Vorlagenmarken, die mkarchiso in Bootloader-Dateien ersetzt.
# %ARCHISO_SEARCH_FILENAME% gilt nur fuer GRUB-Dateien.
PLACEHOLDERS: tuple[str, ...] = (
    "%ARCHISO_LABEL%",
    "%ARCHISO_UUID%",
    "%ARCHISO_SEARCH_FILENAME%",
    "%INSTALL_DIR%",
    "%ARCH%",
)


@dataclass(slots=True)
class GeneratedProfile:
    """Das Ergebnis: der Baum plus was dabei aufgefallen ist."""

    tree: ProfileTree
    settings: ArchisoSettings
    added_packages: tuple[AddedPackage, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def iso_filename(self) -> str:
        return self.settings.iso_filename

    def build_command(self, work_dir: str = "work", out_dir: str = "out") -> str:
        """Der Befehl, den der Benutzer auf einem Arch-System ausfuehrt.

        ``-v`` ist nicht optional: ohne den Schalter gibt mkarchiso keine
        einzige Fortschrittsmeldung aus.
        """
        return f"mkarchiso -v -w {work_dir} -o {out_dir} ."


class ProfileGenerator:
    def __init__(
        self,
        catalog: Catalog,
        config: BuildConfig,
        resolution: Resolution,
        secrets: SecretStore | None = None,
    ) -> None:
        self.catalog = catalog
        self.config = config
        self.resolution = resolution
        self.secrets = secrets

    def generate(self) -> GeneratedProfile:
        if not self.resolution.is_valid:
            blocking = [issue.message for issue in self.resolution.blocking_issues]
            raise ProfileError(
                "Die Konfiguration ist noch nicht vollstaendig:\n\n"
                + "\n".join(f"- {message}" for message in blocking)
            )

        settings = build_settings(self.config, self.resolution)
        tree = ProfileTree()
        title = menu_title(self.config)

        build_airootfs(tree, self.config, self.resolution, settings, self.secrets)
        build_branding(tree, self.config, settings)
        build_bootloaders(tree, settings, title)

        added = self._packages(tree, settings)
        self._pacman_conf(tree)
        self._installer_config(tree, settings)
        self._profiledef(tree, settings)

        warnings = self._self_check(tree, settings, added)
        profile = GeneratedProfile(
            tree=tree,
            settings=settings,
            added_packages=added,
            warnings=tuple(tree.notes) + tuple(warnings),
        )
        log.info(
            "Profil erzeugt fuer %s: %s", settings.iso_filename, tree.describe()
        )
        return profile

    # -- Einzelschritte -------------------------------------------------------
    def _packages(self, tree: ProfileTree, settings: ArchisoSettings) -> tuple[AddedPackage, ...]:
        selected = list(self.resolution.package_names)
        added = required_packages(settings, self.config, selected)
        # Nur die ausgewaehlten weitergeben: die ergaenzten schreibt
        # render_packages selbst, in einem eigenen Block mit Begruendung.
        tree.add_file(
            f"packages.{settings.arch}",
            render_packages(selected, self.resolution.package_groups, added),
            origin=ORIGIN,
        )
        return added

    def _pacman_conf(self, tree: ProfileTree) -> None:
        tree.add_file(
            "pacman.conf",
            render_pacman_conf(self.resolution.repositories),
            origin=ORIGIN,
        )

    def _installer_config(self, tree: ProfileTree, settings: ArchisoSettings) -> None:
        """Die archinstall-Konfiguration fuer das Zielsystem.

        archiso erzeugt nur ein Live-System. Ohne diese Datei koennte der
        Benutzer sein Ergebnis nicht dauerhaft installieren.

        Bewusst ohne ``disk_config`` und ohne Zugangsdaten: die Platte soll der
        Benutzer am Zielrechner selbst bestaetigen, und Passwoerter gehoeren
        nicht in eine Datei mit Modus 0644.
        """
        if not settings.include_installer:
            tree.note(
                "Es wird kein Installationsprogramm mitgeliefert. Das erzeugte "
                "Live-System ist nach einem Neustart wieder verschwunden -- es "
                "laesst sich nicht auf eine Festplatte installieren. Umstellen "
                "laesst sich das unter 'ISO-Einstellungen'."
            )
            return
        document = build_archinstall_config(self.config, self.resolution)
        tree.add_file(
            f"{INSTALLER_DIR}/archinstall.json",
            json.dumps(document, indent=2, ensure_ascii=False) + "\n",
            origin=ORIGIN,
        )

    def _profiledef(self, tree: ProfileTree, settings: ArchisoSettings) -> None:
        # Zuletzt, damit file_permissions alle vorherigen Anmeldungen enthaelt.
        permissions = dict(tree.file_permissions())
        permissions.update(self.config.file_permissions_extra)
        tree.add_file(
            "profiledef.sh", render_profiledef(settings, permissions), origin=ORIGIN
        )

    # -- Selbstpruefung -------------------------------------------------------
    def _self_check(
        self,
        tree: ProfileTree,
        settings: ArchisoSettings,
        added: tuple[AddedPackage, ...],
    ) -> list[str]:
        """Prueft dieselben Bedingungen wie mkarchiso -- nur schon jetzt."""
        problems: list[str] = []

        for required in ("profiledef.sh", f"packages.{settings.arch}", "pacman.conf"):
            if not tree.has(required):
                problems.append(f"Die Datei {required} fehlt im Profil.")

        packages = tree.file(f"packages.{settings.arch}")
        package_text = packages.text() if packages else ""

        if settings.has_bios:
            if not tree.under("syslinux"):
                problems.append("Fuer den BIOS-Start fehlt das Verzeichnis 'syslinux'.")
            elif not any(path.endswith(".cfg") for path in tree.under("syslinux")):
                problems.append("In 'syslinux' liegt keine .cfg-Datei.")
            # mkarchiso prueft das ausdruecklich und bricht sonst ab.
            if "syslinux" not in package_text.split():
                problems.append(
                    "Der BIOS-Start braucht das Paket 'syslinux' in der Paketliste."
                )

        if settings.has_systemd_boot:
            if not tree.has("efiboot/loader/loader.conf"):
                problems.append("Fuer systemd-boot fehlt 'efiboot/loader/loader.conf'.")
            if not tree.under("efiboot/loader/entries"):
                problems.append("Fuer systemd-boot fehlt ein Eintrag unter 'efiboot/loader/entries'.")
            loader = tree.file("efiboot/loader/loader.conf")
            if loader is not None:
                # In dieser Datei ersetzt mkarchiso nichts -- eine Vorlagenmarke
                # bliebe woertlich stehen und der Bootloader faende sein Abbild
                # nicht. Geprueft werden nur echte Anweisungszeilen; in
                # Kommentaren darf ueber Platzhalter geschrieben werden.
                for line in loader.text().splitlines():
                    stripped = line.strip()
                    if stripped.startswith("#") or not stripped:
                        continue
                    found = [marker for marker in PLACEHOLDERS if marker in stripped]
                    if found:
                        problems.append(
                            f"In 'efiboot/loader/loader.conf' steht {found[0]}. "
                            f"In dieser Datei ersetzt mkarchiso keine Vorlagenmarken."
                        )
                        break

        if settings.has_grub and not tree.has("grub/grub.cfg"):
            problems.append("Fuer GRUB fehlt 'grub/grub.cfg'.")

        if settings.has_systemd_boot and settings.has_grub:
            problems.append(
                "systemd-boot und GRUB koennen nicht gleichzeitig verwendet werden."
            )

        # Der Kernelname muss ueberall derselbe sein, sonst bootet nichts.
        preset = f"airootfs/etc/mkinitcpio.d/{settings.preset_filename}"
        if not tree.has(preset):
            problems.append(f"Die mkinitcpio-Vorgabe {preset} fehlt.")
        for path in tree.under("syslinux") + tree.under("efiboot") + tree.under("grub"):
            entry = tree.file(path)
            if entry is None or not path.endswith((".cfg", ".conf")):
                continue
            text = entry.text()
            if "vmlinuz-" in text and settings.vmlinuz not in text:
                problems.append(
                    f"{path} verweist auf einen anderen Kernel als {settings.kernel}."
                )

        if problems:
            log.warning("Selbstpruefung: %d Beanstandungen", len(problems))
        return problems
