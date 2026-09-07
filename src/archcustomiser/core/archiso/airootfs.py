"""Das airootfs-Overlay: alles unter ``airootfs/``.

mkarchiso kopiert dieses Verzeichnis mit ``cp -af --no-preserve=ownership,mode``
in das entstehende Abbild. Zwei Folgen daraus bestimmen den Aufbau hier:

* **Dateimodi werden verworfen.** Alles, was besondere Rechte braucht, muss
  ueber ``file_permissions`` in profiledef.sh gehen -- deshalb ruft dieses
  Modul ``tree.add_permission`` statt irgendwo ``chmod`` zu setzen.
* **Symlinks bleiben Symlinks.** Baumelnde Verknuepfungen sind dabei voellig
  normal: das Ziel entsteht erst, wenn pacstrap die Pakete installiert.
"""

from __future__ import annotations

import logging

from ..catalog import EnableIn
from ..config import BuildConfig
from ..resolver import Resolution
from ..secrets import SecretStore
from .errors import HashingUnavailable
from .settings import ArchisoSettings
from .tree import ProfileTree
from .users import (
    LOCKED,
    UserAccount,
    hash_password,
    root_passwd_line,
    root_shadow_line,
    sudoers_content,
    sysusers_line,
)

log = logging.getLogger(__name__)

AIROOTFS = "airootfs"
ORIGIN = "generator"

MKINITCPIO_CONF = """# Erzeugt von ArchCustomiser -- nicht von Hand bearbeiten.
#
# Die archiso-Haken muessen enthalten sein, sonst findet das startende System
# sein Dateisystem auf dem Medium nicht.
HOOKS=(base udev microcode modconf kms memdisk archiso archiso_loop_mnt block filesystems keyboard)
COMPRESSION="xz"
COMPRESSION_OPTIONS=(-9e)
"""


def _preset(kernel: str) -> str:
    """Die mkinitcpio-Preset-Datei.

    Dateiname, ``ALL_kver`` und die Bootmenue-Eintraege muessen denselben
    Kernelnamen tragen -- mkarchiso kopiert spaeter per Glob
    ``/boot/vmlinuz-*`` und ``/boot/initramfs-*.img``.
    """
    return (
        f"# mkinitcpio-Vorgabe fuer den Kernel '{kernel}', erzeugt von ArchCustomiser.\n"
        f"\n"
        f"PRESETS=('archiso')\n"
        f"\n"
        f"ALL_kver='/boot/vmlinuz-{kernel}'\n"
        f"archiso_config='/etc/mkinitcpio.conf.d/archiso.conf'\n"
        f"\n"
        f'archiso_image="/boot/initramfs-{kernel}.img"\n'
    )


def _autologin_dropin(username: str) -> str:
    return (
        "# Erzeugt von ArchCustomiser -- automatische Anmeldung in der Live-Sitzung.\n"
        "[Service]\n"
        "ExecStart=\n"
        f"ExecStart=-/usr/bin/agetty --noreset --noclear --autologin {username} - ${{TERM}}\n"
    )


def build_airootfs(
    tree: ProfileTree,
    config: BuildConfig,
    resolution: Resolution,
    settings: ArchisoSettings,
    secrets: SecretStore | None = None,
) -> None:
    """Fuellt ``airootfs/`` im Baum."""
    _base_files(tree, config, settings)
    _kernel_files(tree, settings)
    _services(tree, resolution)
    _user(tree, config, settings, secrets)
    _catalog_files(tree, resolution)


# ---------------------------------------------------------------------------
# Grundeinstellungen
# ---------------------------------------------------------------------------


def _base_files(tree: ProfileTree, config: BuildConfig, settings: ArchisoSettings) -> None:
    tree.add_file(f"{AIROOTFS}/etc/hostname", config.hostname + "\n", origin=ORIGIN)

    locale = config.field_str("basics.locale", "C.UTF-8") or "C.UTF-8"
    tree.add_file(f"{AIROOTFS}/etc/locale.conf", f"LANG={locale}\n", origin=ORIGIN)

    keymap = config.field_str("basics.keymap")
    if keymap:
        tree.add_file(f"{AIROOTFS}/etc/vconsole.conf", f"KEYMAP={keymap}\n", origin=ORIGIN)

    # /etc/localtime ist auch im echten System ein Symlink; ein kopierter
    # Zeitzonen-Blob wuerde beim naechsten tzdata-Update veralten.
    timezone = config.field_str("basics.timezone", "UTC") or "UTC"
    tree.add_symlink(
        f"{AIROOTFS}/etc/localtime",
        f"/usr/share/zoneinfo/{timezone}",
        origin=ORIGIN,
    )

    if config.field_bool("basics.ntp", True):
        tree.add_symlink(
            f"{AIROOTFS}/etc/systemd/system/sysinit.target.wants/systemd-timesyncd.service",
            "/usr/lib/systemd/system/systemd-timesyncd.service",
            origin=ORIGIN,
        )


def _kernel_files(tree: ProfileTree, settings: ArchisoSettings) -> None:
    tree.add_file(
        f"{AIROOTFS}/etc/mkinitcpio.conf.d/archiso.conf", MKINITCPIO_CONF, origin=ORIGIN
    )
    tree.add_file(
        f"{AIROOTFS}/etc/mkinitcpio.d/{settings.preset_filename}",
        _preset(settings.kernel),
        origin=ORIGIN,
    )


# ---------------------------------------------------------------------------
# Dienste
# ---------------------------------------------------------------------------


def _services(tree: ProfileTree, resolution: Resolution) -> None:
    """Die Symlinks, die ``systemctl enable`` anlegen wuerde.

    Welche das genau sind, weiss nur der Katalog: NetworkManager braucht einen
    Eintrag unter ``multi-user.target.wants/``, ein Display-Manager dagegen den
    Alias ``display-manager.service`` und gar keinen ``.wants``-Eintrag.
    """
    for resolved in resolution.services_for(EnableIn.LIVE):
        for link_path, target in resolved.symlinks():
            tree.add_symlink(
                f"{AIROOTFS}/{link_path}", target, origin=resolved.origin
            )


# ---------------------------------------------------------------------------
# Benutzer
# ---------------------------------------------------------------------------


def _user(
    tree: ProfileTree,
    config: BuildConfig,
    settings: ArchisoSettings,
    secrets: SecretStore | None,
) -> None:
    root_locked = config.field_bool("user.root_locked", True)
    passwd_lines = [root_passwd_line()]
    shadow_lines = [root_shadow_line(locked=root_locked)]

    if not root_locked:
        tree.note(
            "Das Root-Konto ist nicht gesperrt. Auf einem Live-System ist das ein "
            "unmittelbarer Administratorzugang."
        )

    # Der umgekehrte Fall, der bis zum 07.09.2026 unbemerkt blieb: kein Konto
    # UND ein gesperrtes Root-Konto ergibt eine ISO, die startet, einen
    # Anmeldeschirm zeigt -- und in die niemand hineinkommt. Nicht gefaehrlich,
    # aber unbrauchbar, und ohne Hinweis merkt man es erst nach dem Brennen.
    if root_locked and not config.creates_user:
        tree.note(
            "Es wird kein Benutzerkonto angelegt und das Root-Konto ist gesperrt "
            "-- an dieser ISO kann sich niemand anmelden. Entweder ein "
            "Benutzerkonto anlegen oder das Root-Konto entsperren."
        )

    if config.creates_user:
        account = UserAccount(
            username=config.username,
            full_name=config.field_str("user.full_name"),
            sudo=config.field_bool("user.sudo", True),
        )
        password_hash = _password_hash(tree, secrets)

        passwd_lines.append(account.passwd_line())
        shadow_lines.append(account.shadow_line(password_hash))

        if account.sudo:
            # Gruppenzugehoerigkeit ueber sysusers, damit /etc/group unangetastet
            # bleibt -- siehe users.py.
            tree.add_file(
                f"{AIROOTFS}/usr/lib/sysusers.d/10-archcustomiser.conf",
                sysusers_line(account),
                origin=ORIGIN,
            )
            tree.add_file(
                f"{AIROOTFS}/etc/sudoers.d/10-wheel", sudoers_content(), origin=ORIGIN
            )
            # Ohne genau diesen Modus verweigert sudo den Dienst.
            tree.add_permission(
                "/etc/sudoers.d/10-wheel", mode="0440", origin=ORIGIN
            )

        if settings.autologin_user:
            tree.add_file(
                f"{AIROOTFS}/etc/systemd/system/getty@tty1.service.d/autologin.conf",
                _autologin_dropin(settings.autologin_user),
                origin=ORIGIN,
            )

    tree.add_file(f"{AIROOTFS}/etc/passwd", "\n".join(passwd_lines) + "\n", origin=ORIGIN)
    tree.add_file(f"{AIROOTFS}/etc/shadow", "\n".join(shadow_lines) + "\n", origin=ORIGIN)
    tree.add_permission("/etc/shadow", mode="0400", origin=ORIGIN)


def _password_hash(tree: ProfileTree, secrets: SecretStore | None) -> str:
    """Der Hash -- oder ein gesperrtes Konto mit deutlichem Hinweis."""
    secret = secrets.get("user.password") if secrets is not None else None
    if secret is None or not secret:
        tree.note(
            "Es wurde kein Passwort vergeben. Das Konto wird gesperrt angelegt; "
            "die Anmeldung ist dann nur ueber die automatische Anmeldung moeglich."
        )
        return LOCKED
    try:
        return hash_password(secret)
    except HashingUnavailable as exc:
        # Kein Abbruch: das Profil ist auch ohne Hash brauchbar, und auf dem
        # Arch-System laesst sich das Passwort nachtragen.
        log.warning("Passwort-Hash nicht moeglich: %s", exc.technical)
        # Der Text nannte frueher libcrypt und openssl als Voraussetzung. Seit
        # die Kaskade eine eigene sha512crypt-Rechnung enthaelt, braucht sie
        # weder das eine noch das andere -- der Fall tritt praktisch nur noch
        # bei einem leeren Passwort ein.
        tree.note(
            "Der Passwort-Hash liess sich nicht erzeugen. Das Konto wird "
            "gesperrt angelegt -- das Passwort laesst sich im laufenden System "
            "mit 'passwd' setzen."
        )
        return LOCKED


# ---------------------------------------------------------------------------
# Dateien aus dem Katalog
# ---------------------------------------------------------------------------


def _catalog_files(tree: ProfileTree, resolution: Resolution) -> None:
    """``files:``-Eintraege der gewaehlten Optionen.

    Die Zielpfade sind absolute Pfade im Abbild und stammen moeglicherweise aus
    einem Benutzer-Overlay -- ``ProfileTree`` prueft sie.
    """
    for entry in resolution.files:
        target = entry.target.lstrip("/")
        if entry.content:
            tree.add_file(
                f"{AIROOTFS}/{target}", entry.content, origin=entry.owned_by or "katalog"
            )
        if entry.mode and entry.mode != "0644":
            owner, _, group = entry.owner.partition(":")
            tree.add_permission(
                entry.target,
                owner=owner or "0",
                group=group or "0",
                mode=entry.mode,
                origin=entry.owned_by or "katalog",
            )
