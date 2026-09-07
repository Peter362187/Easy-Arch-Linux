"""Ableitung der archiso-Einstellungen aus der Benutzerkonfiguration.

Eine eigene Schicht zwischen ``BuildConfig`` (was der Benutzer eingestellt hat)
und ``profiledef.sh`` (was mkarchiso liest). Sie beantwortet Fragen wie „welche
Bootmodi ergeben sich aus den Haken?" an genau einer Stelle -- der
Paketgenerator, der Bootloader-Generator und die Profildatei brauchen dieselbe
Antwort und duerfen nicht auseinanderlaufen.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ..config import BuildConfig
from ..resolver import Resolution
from .errors import ProfileError

BIOS_SYSLINUX = "bios.syslinux"
UEFI_SYSTEMD_BOOT = "uefi.systemd-boot"
UEFI_GRUB = "uefi.grub"

# Kompressionsvorgaben. Der Unterschied ist erheblich: xz mit BCJ-Filter braucht
# auf einem KDE+Steam-Dateisystem leicht 20 bis 40 Minuten, zstd wenige.
COMPRESSION_PRESETS: Mapping[str, tuple[str, ...]] = {
    "fast": ("-comp", "zstd", "-Xcompression-level", "10", "-b", "1M"),
    "balanced": ("-comp", "zstd", "-Xcompression-level", "18", "-b", "1M"),
    "max": ("-comp", "xz", "-Xbcj", "x86,arm64", "-b", "1M", "-Xdict-size", "1M"),
}

DEFAULT_KERNEL = "linux"


@dataclass(frozen=True, slots=True)
class ArchisoSettings:
    """Alles, was profiledef.sh und die Bootloader-Dateien brauchen."""

    iso_name: str
    iso_label: str
    iso_version: str
    iso_publisher: str
    iso_application: str
    install_dir: str
    arch: str
    bootmodes: tuple[str, ...]
    airootfs_image_tool_options: tuple[str, ...]
    boot_timeout: int
    kernel: str
    """Kernelsuffix, z.B. 'linux-zen'. Steuert Preset-Dateiname UND Bootmenue."""
    cow_spacesize: str
    kernel_params: tuple[str, ...]
    include_memtest: bool
    include_installer: bool
    autologin_user: str = ""
    file_permissions: Mapping[str, str] = field(default_factory=dict)

    # -- abgeleitete Namen ----------------------------------------------------
    @property
    def vmlinuz(self) -> str:
        return f"vmlinuz-{self.kernel}"

    @property
    def initramfs(self) -> str:
        return f"initramfs-{self.kernel}.img"

    @property
    def preset_filename(self) -> str:
        return f"{self.kernel}.preset"

    @property
    def iso_filename(self) -> str:
        return f"{self.iso_name}-{self.iso_version}-{self.arch}.iso"

    # -- Bootmodi -------------------------------------------------------------
    @property
    def has_bios(self) -> bool:
        return BIOS_SYSLINUX in self.bootmodes

    @property
    def has_systemd_boot(self) -> bool:
        return UEFI_SYSTEMD_BOOT in self.bootmodes

    @property
    def has_grub(self) -> bool:
        return UEFI_GRUB in self.bootmodes

    @property
    def has_uefi(self) -> bool:
        return self.has_systemd_boot or self.has_grub

    def boot_options(self) -> str:
        """Die Kernel-Kommandozeile fuer alle Bootloader.

        ``archisobasedir`` und ``archisosearchuuid`` sind zwingend -- ohne sie
        findet der archiso-Hook das Abbild nicht. Die Platzhalter ersetzt
        mkarchiso beim Bauen.
        """
        parts = [
            "archisobasedir=%INSTALL_DIR%",
            "archisosearchuuid=%ARCHISO_UUID%",
        ]
        if self.cow_spacesize:
            parts.append(f"cow_spacesize={self.cow_spacesize}")
        parts.extend(self.kernel_params)
        return " ".join(parts)


def derive_bootmodes(config: BuildConfig) -> tuple[str, ...]:
    """Bootmodi aus den Einstellungen.

    Die Kombination ``uefi.systemd-boot`` + ``uefi.grub`` ist seit archiso 89
    ein Validierungsfehler. Weil die Oberflaeche fuer UEFI eine Einfachauswahl
    anbietet, kann sie hier gar nicht entstehen -- die Pruefung bleibt trotzdem,
    damit ein von Hand bearbeitetes Profil nicht erst bei mkarchiso auffaellt.
    """
    modes: list[str] = []
    if config.field_bool("build.bios_boot", True):
        modes.append(BIOS_SYSLINUX)

    uefi = config.field_str("build.uefi_boot", "systemd-boot")
    if uefi == "systemd-boot":
        modes.append(UEFI_SYSTEMD_BOOT)
    elif uefi == "grub":
        modes.append(UEFI_GRUB)
    elif uefi not in ("", "none"):
        raise ProfileError(
            f"Unbekannter UEFI-Bootloader: {uefi!r}. "
            f"Erlaubt sind 'systemd-boot', 'grub' oder 'none'."
        )

    if UEFI_SYSTEMD_BOOT in modes and UEFI_GRUB in modes:
        raise ProfileError(
            "systemd-boot und GRUB koennen nicht gleichzeitig verwendet werden. "
            "mkarchiso bricht bei dieser Kombination ab."
        )
    if not modes:
        raise ProfileError(
            "Es ist kein Startverfahren ausgewaehlt. Ohne BIOS- und ohne "
            "UEFI-Start liesse sich die ISO von keinem Rechner booten."
        )
    return tuple(modes)


def kernel_suffix(resolution: Resolution) -> str:
    """Der Kernelname aus dem Katalog, z.B. 'linux-zen'.

    Steuert drei Stellen, die zusammenpassen muessen: den Namen der
    mkinitcpio-Preset-Datei, ``ALL_kver`` darin und jeden Bootmenue-Eintrag.
    Deshalb kommt er aus einer einzigen Quelle.
    """
    value = resolution.semantics.get("kernel_suffix")
    if isinstance(value, str) and value:
        return value
    return DEFAULT_KERNEL


def build_settings(config: BuildConfig, resolution: Resolution) -> ArchisoSettings:
    preset = config.field_str("build.compression", "balanced")
    options = COMPRESSION_PRESETS.get(preset)
    if options is None:
        raise ProfileError(
            f"Unbekannte Kompressionsstufe: {preset!r}. "
            f"Erlaubt sind {', '.join(sorted(COMPRESSION_PRESETS))}."
        )

    publisher = config.field_str("branding.publisher") or f"{config.distro_name}"
    application = f"{config.distro_name} {config.version} Live/Rescue"

    autologin = ""
    if config.creates_user and config.field_bool("user.autologin", True):
        autologin = config.username

    return ArchisoSettings(
        iso_name=config.iso_name,
        iso_label=config.iso_label,
        iso_version=config.version,
        iso_publisher=publisher,
        iso_application=application,
        install_dir=config.install_dir,
        arch=config.architecture,
        bootmodes=derive_bootmodes(config),
        airootfs_image_tool_options=options,
        boot_timeout=max(0, config.field_int("build.boot_timeout", 15)),
        kernel=kernel_suffix(resolution),
        cow_spacesize=config.field_str("build.cow_spacesize", "2G"),
        kernel_params=resolution.kernel_params,
        include_memtest=config.field_bool("build.include_memtest", False),
        include_installer=config.field_bool("build.include_installer", True),
        autologin_user=autologin,
    )
