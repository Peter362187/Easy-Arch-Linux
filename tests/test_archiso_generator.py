"""Tests der Profilerzeugung.

Der wichtigste Test steht ganz oben: die erzeugte ``profiledef.sh`` wird von
mkarchiso **ausgefuehrt**. Was der Benutzer in ein Textfeld tippt, darf dort
niemals zu Code werden.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from archcustomiser.core.archiso import ProfileGenerator
from archcustomiser.core.archiso.errors import ProfileError, UnsafeValueError
from archcustomiser.core.archiso.quoting import bash_array, bash_assoc, shell_quote
from archcustomiser.core.config import BuildConfig
from archcustomiser.core.secrets import SecretStore

BASE_REFS = ("desktop.none", "kernel.linux", "audio.none", "network.networkmanager")


def make_config(**fields) -> BuildConfig:
    config = BuildConfig()
    for ref in BASE_REFS:
        config.add(ref)
    config.set_field("branding.distro_name", "FLOS")
    config.set_field("branding.version", "1.0")
    config.set_field("basics.hostname", "flos")
    config.set_field("basics.locale", "de_DE.UTF-8")
    config.set_field("basics.keymap", "de-latin1")
    config.set_field("basics.timezone", "Europe/Berlin")
    for key, value in fields.items():
        config.set_field(key.replace("__", "."), value)
    return config


def generate(catalog, resolver, config: BuildConfig, secrets: SecretStore | None = None):
    return ProfileGenerator(catalog, config, resolver.resolve(config), secrets).generate()


# ---------------------------------------------------------------------------
# Bash-Injektion
# ---------------------------------------------------------------------------

ATTACKS = [
    'Jason"; touch /tmp/pwned; echo "',
    "Jason'; touch /tmp/pwned; echo '",
    "$(touch /tmp/pwned)",
    "`touch /tmp/pwned`",
    "${HOME}",
    "x; rm -rf ~",
    "x && rm -rf ~",
    "x | tee /etc/passwd",
    'x" $(id) "',
]


@pytest.mark.parametrize("payload", ATTACKS)
def test_injection_stays_literal_text(catalog, resolver, payload: str) -> None:
    """Der Wert muss unveraendert als Text ankommen -- nicht ausgefuehrt."""
    profile = generate(catalog, resolver, make_config(branding__publisher=payload))
    line = _assignment(profile.tree.text("profiledef.sh"), "iso_publisher")
    # In einfachen Anfuehrungszeichen verliert in Bash jedes Zeichen seine
    # Sonderbedeutung. Das ist die einzige Form, die das garantiert.
    assert line.startswith("iso_publisher='")
    assert line.endswith("'")


@pytest.mark.parametrize("payload", ["mit\nZeilenumbruch", "mit\x00Nullbyte", "a\rb"])
def test_control_characters_are_refused(payload: str) -> None:
    with pytest.raises(UnsafeValueError):
        shell_quote(payload)


def test_newline_cannot_start_a_second_assignment(catalog, resolver) -> None:
    """Ein Zeilenumbruch koennte sonst eine eigene Zuweisung eroeffnen."""
    config = make_config(branding__publisher="x\niso_name=boese")
    with pytest.raises(UnsafeValueError):
        generate(catalog, resolver, config)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash nicht verfuegbar")
def test_generated_profiledef_is_valid_bash(catalog, resolver, tmp_path) -> None:
    """Syntaxpruefung mit dem echten bash -- so wie mkarchiso die Datei liest."""
    profile = generate(
        catalog, resolver, make_config(branding__publisher=ATTACKS[0])
    )
    path = tmp_path / "profiledef.sh"
    path.write_text(profile.tree.text("profiledef.sh"), encoding="utf-8", newline="")
    result = subprocess.run(
        [shutil.which("bash"), "-n", str(path)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash nicht verfuegbar")
def test_sourcing_preserves_the_value_without_executing(catalog, resolver, tmp_path) -> None:
    """Der eigentliche Beweis.

    Die Datei wird genau so eingelesen, wie mkarchiso es tut. Danach muss
    gelten: der Wert ist woertlich erhalten, und die darin enthaltene
    Anweisung wurde *nicht* ausgefuehrt. Letzteres wird an einer Datei
    gemessen, die der Angriff anlegen wuerde -- nicht an der Ausgabe, denn die
    enthaelt den Angriffstext ja gerade als harmlosen Text.
    """
    marker = tmp_path / "AUSGEFUEHRT"
    payload = f'Jason"; touch "{marker.as_posix()}"; echo "'
    profile = generate(catalog, resolver, make_config(branding__publisher=payload))
    path = tmp_path / "profiledef.sh"
    path.write_text(profile.tree.text("profiledef.sh"), encoding="utf-8", newline="")

    result = subprocess.run(
        [shutil.which("bash"), "-c", f'. "{path.as_posix()}" && printf "%s" "$iso_publisher"'],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == payload, "der Wert muss woertlich erhalten bleiben"
    assert not marker.exists(), "der eingebettete Befehl wurde ausgefuehrt"


def test_file_permissions_is_declared_as_an_associative_array() -> None:
    """Ohne declare -A liest bash [/etc/shadow] als Rechenausdruck."""
    rendered = bash_assoc("file_permissions", {"/etc/shadow": "0:0:400"})
    assert rendered.startswith("declare -A file_permissions=(")


def test_bash_array_quotes_every_element() -> None:
    rendered = bash_array("bootmodes", ["bios.syslinux", "a b"])
    assert "'a b'" in rendered


# ---------------------------------------------------------------------------
# Pflichtbestandteile
# ---------------------------------------------------------------------------


def test_profile_has_everything_mkarchiso_validates(catalog, resolver) -> None:
    profile = generate(catalog, resolver, make_config())
    for required in ("profiledef.sh", "packages.x86_64", "pacman.conf"):
        assert profile.tree.has(required)
    assert profile.tree.under("syslinux")
    assert profile.tree.has("efiboot/loader/loader.conf")
    assert profile.tree.under("efiboot/loader/entries")
    assert not profile.warnings or all(
        "loader.conf" not in warning for warning in profile.warnings
    )


def test_bios_boot_forces_the_syslinux_package(catalog, resolver) -> None:
    """mkarchiso prueft das ausdruecklich und bricht sonst ab."""
    profile = generate(catalog, resolver, make_config(build__bios_boot=True))
    packages = profile.tree.text("packages.x86_64")
    assert "syslinux" in packages.split()
    assert any(entry.name == "syslinux" for entry in profile.added_packages)


def test_base_packages_are_always_added(catalog, resolver) -> None:
    profile = generate(catalog, resolver, make_config())
    names = {entry.name for entry in profile.added_packages}
    assert {"base", "mkinitcpio", "mkinitcpio-archiso"} <= names


def test_every_added_package_states_a_reason(catalog, resolver) -> None:
    """Wer 'syslinux' in seiner Liste findet, soll nicht raten muessen."""
    profile = generate(catalog, resolver, make_config())
    for entry in profile.added_packages:
        assert entry.reason


def test_installer_packages_follow_the_setting(catalog, resolver) -> None:
    with_installer = generate(catalog, resolver, make_config(build__include_installer=True))
    assert "archinstall" in with_installer.tree.text("packages.x86_64")

    without = generate(catalog, resolver, make_config(build__include_installer=False))
    assert "archinstall" not in without.tree.text("packages.x86_64").split()
    assert any("Live-System" in warning for warning in without.warnings)


def test_non_c_locale_adds_glibc_locales(catalog, resolver) -> None:
    german = generate(catalog, resolver, make_config(basics__locale="de_DE.UTF-8"))
    assert "glibc-locales" in german.tree.text("packages.x86_64")

    plain = generate(catalog, resolver, make_config(basics__locale="C.UTF-8"))
    assert "glibc-locales" not in plain.tree.text("packages.x86_64")


# ---------------------------------------------------------------------------
# Bootmodi
# ---------------------------------------------------------------------------


def test_grub_and_systemd_boot_are_mutually_exclusive(catalog, resolver) -> None:
    grub = generate(catalog, resolver, make_config(build__uefi_boot="grub"))
    assert grub.tree.has("grub/grub.cfg")
    assert not grub.tree.has("efiboot/loader/loader.conf")
    assert grub.settings.has_grub and not grub.settings.has_systemd_boot


def test_no_boot_mode_at_all_is_refused(catalog, resolver) -> None:
    config = make_config(build__bios_boot=False, build__uefi_boot="none")
    with pytest.raises(ProfileError) as info:
        generate(catalog, resolver, config)
    assert "Startverfahren" in str(info.value)


def test_loader_conf_contains_no_placeholders(catalog, resolver) -> None:
    """mkarchiso ersetzt in dieser Datei nichts -- eine Marke bliebe stehen."""
    profile = generate(catalog, resolver, make_config())
    text = profile.tree.text("efiboot/loader/loader.conf")
    for line in text.splitlines():
        if line.strip().startswith("#"):
            continue
        assert "%INSTALL_DIR%" not in line
        assert "%ARCHISO_UUID%" not in line


def test_entry_files_keep_their_placeholders(catalog, resolver) -> None:
    """Dort ersetzt mkarchiso -- vorab einsetzen waere falsch."""
    profile = generate(catalog, resolver, make_config())
    entry = profile.tree.text("efiboot/loader/entries/01-archcustomiser-linux.conf")
    assert "%INSTALL_DIR%" in entry
    assert "%ARCHISO_UUID%" in entry
    assert "%ARCH%" in entry


def test_memtest_entry_only_with_the_package(catalog, resolver) -> None:
    """syslinux kann nicht selbst pruefen, ob die Datei existiert."""
    without = generate(catalog, resolver, make_config(build__include_memtest=False))
    assert "memtest" not in without.tree.text("syslinux/syslinux-linux.cfg")

    with_memtest = generate(catalog, resolver, make_config(build__include_memtest=True))
    assert "memtest" in with_memtest.tree.text("syslinux/syslinux-linux.cfg")
    assert "memtest86+" in with_memtest.tree.text("packages.x86_64")


# ---------------------------------------------------------------------------
# Kernel-Kopplung
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kernel", ["linux", "linux-lts", "linux-zen"])
def test_kernel_name_is_consistent_everywhere(catalog, resolver, kernel: str) -> None:
    """Vorgabedatei und Bootmenue muessen denselben Kernel nennen.

    Stimmen sie nicht ueberein, baut mkinitcpio ein Abbild ohne die
    archiso-Haken -- und die fertige ISO startet nicht.
    """
    config = BuildConfig()
    for ref in ("desktop.none", "audio.none", "network.networkmanager"):
        config.add(ref)
    config.add(f"kernel.{kernel}")
    profile = generate(catalog, resolver, config)

    assert profile.tree.has(f"airootfs/etc/mkinitcpio.d/{kernel}.preset")
    preset = profile.tree.text(f"airootfs/etc/mkinitcpio.d/{kernel}.preset")
    assert f"ALL_kver='/boot/vmlinuz-{kernel}'" in preset
    assert f'archiso_image="/boot/initramfs-{kernel}.img"' in preset
    assert "PRESETS=('archiso')" in preset

    for path in profile.tree.under("syslinux") + profile.tree.under("efiboot"):
        entry = profile.tree.file(path)
        if entry is None or "vmlinuz-" not in entry.text():
            continue
        assert f"vmlinuz-{kernel}" in entry.text(), path
        assert f"initramfs-{kernel}.img" in entry.text(), path


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------


def test_steam_activates_multilib_in_pacman_conf(catalog, resolver) -> None:
    """In der archiso-Vorlage ist multilib auskommentiert."""
    config = make_config()
    config.add("apps.steam")
    profile = generate(catalog, resolver, config)
    text = profile.tree.text("pacman.conf")
    assert "[multilib]" in text
    for line in text.splitlines():
        if line.strip() == "[multilib]":
            break
    else:
        pytest.fail("multilib ist nicht aktiv")


def test_multilib_absent_without_a_reason(catalog, resolver) -> None:
    profile = generate(catalog, resolver, make_config())
    assert "[multilib]" not in profile.tree.text("pacman.conf")


# ---------------------------------------------------------------------------
# Benutzer und Passwoerter
# ---------------------------------------------------------------------------


def test_password_never_appears_anywhere_in_the_profile(catalog, resolver) -> None:
    secrets = SecretStore()
    secrets.set("user.password", "streng-geheim-42")
    config = make_config(user__create=True, user__username="jason", user__sudo=True)
    profile = generate(catalog, resolver, config, secrets)

    for entry in profile.tree.files.values():
        assert b"streng-geheim-42" not in entry.content, entry.path


def test_shadow_gets_restrictive_permissions(catalog, resolver) -> None:
    profile = generate(catalog, resolver, make_config())
    permissions = profile.tree.file_permissions()
    assert permissions["/etc/shadow"].endswith(":0400")


def test_sudoers_needs_mode_0440(catalog, resolver) -> None:
    """Bei anderen Rechten verweigert sudo den Dienst."""
    config = make_config(user__create=True, user__username="jason", user__sudo=True)
    profile = generate(catalog, resolver, config)
    assert profile.tree.file_permissions()["/etc/sudoers.d/10-wheel"].endswith(":0440")


def test_group_membership_via_sysusers_not_etc_group(catalog, resolver) -> None:
    """/etc/group zu ueberschreiben wuerde die Standardgruppen verdraengen."""
    config = make_config(user__create=True, user__username="jason", user__sudo=True)
    profile = generate(catalog, resolver, config)

    assert not profile.tree.has("airootfs/etc/group")
    assert not profile.tree.has("airootfs/etc/gshadow")
    sysusers = profile.tree.text("airootfs/usr/lib/sysusers.d/10-archcustomiser.conf")
    assert "m jason wheel" in sysusers
    # Ein 'u'-Eintrag wuerde ein gesperrtes Systemkonto anlegen.
    assert "\nu " not in sysusers


def test_passwd_contains_root_and_the_user(catalog, resolver) -> None:
    config = make_config(user__create=True, user__username="jason", user__full_name="Jason")
    profile = generate(catalog, resolver, config)
    lines = profile.tree.text("airootfs/etc/passwd").strip().splitlines()
    assert lines[0].startswith("root:x:0:0:")
    assert any(line.startswith("jason:x:1000:1000:Jason:/home/jason:") for line in lines)


def test_root_is_locked_by_default(catalog, resolver) -> None:
    profile = generate(catalog, resolver, make_config())
    root_line = profile.tree.text("airootfs/etc/shadow").splitlines()[0]
    assert root_line.split(":")[1] == "!"


def test_unlocked_root_produces_a_warning(catalog, resolver) -> None:
    profile = generate(catalog, resolver, make_config(user__root_locked=False))
    assert any("Root-Konto" in warning for warning in profile.warnings)


def test_missing_password_locks_the_account_with_a_note(catalog, resolver) -> None:
    config = make_config(user__create=True, user__username="jason")
    profile = generate(catalog, resolver, config, SecretStore())
    user_line = [
        line
        for line in profile.tree.text("airootfs/etc/shadow").splitlines()
        if line.startswith("jason:")
    ][0]
    assert user_line.split(":")[1] == "!"
    assert any("kein Passwort" in warning for warning in profile.warnings)


def test_gecos_cannot_break_the_passwd_format(catalog, resolver) -> None:
    """Ein Doppelpunkt waere ein Feldtrenner in /etc/passwd."""
    config = make_config(
        user__create=True, user__username="jason", user__full_name="Ja:son"
    )
    profile = generate(catalog, resolver, config)
    line = [
        entry
        for entry in profile.tree.text("airootfs/etc/passwd").splitlines()
        if entry.startswith("jason:")
    ][0]
    assert len(line.split(":")) == 7


# ---------------------------------------------------------------------------
# Dienste
# ---------------------------------------------------------------------------


def test_display_manager_uses_the_alias(catalog, resolver) -> None:
    config = make_config()
    config.set_selection("desktop", ["kde"])
    profile = generate(catalog, resolver, config)
    link = profile.tree.symlink("airootfs/etc/systemd/system/display-manager.service")
    assert link is not None
    assert link.target.endswith("sddm.service")


def test_services_become_symlinks_not_files(catalog, resolver) -> None:
    profile = generate(catalog, resolver, make_config())
    path = "airootfs/etc/systemd/system/multi-user.target.wants/NetworkManager.service"
    assert profile.tree.symlink(path) is not None
    assert profile.tree.file(path) is None


def test_symlink_targets_are_absolute_paths_in_the_image(catalog, resolver) -> None:
    """Genau wie 'systemctl enable' und wie archiso/releng es tut.

    Die Ziele existieren zur Erzeugungszeit noch nicht -- sie entstehen erst,
    wenn pacstrap die Pakete installiert. Baumelnde Verknuepfungen sind hier
    also der Normalfall und kein Fehler.
    """
    config = make_config()
    config.set_selection("desktop", ["kde"])
    profile = generate(catalog, resolver, config)

    links = [
        link
        for link in profile.tree.symlinks.values()
        if link.path.startswith("airootfs/etc/systemd/")
    ]
    assert links
    for link in links:
        assert link.target.startswith("/usr/lib/systemd/"), link.path


def test_localtime_is_a_symlink_not_a_copy(catalog, resolver) -> None:
    """Eine kopierte Zeitzonendatei waere beim naechsten tzdata-Update veraltet."""
    profile = generate(catalog, resolver, make_config(basics__timezone="Europe/Berlin"))
    link = profile.tree.symlink("airootfs/etc/localtime")
    assert link is not None
    assert link.target == "/usr/share/zoneinfo/Europe/Berlin"


def test_live_only_service_stays_out_of_the_installer_config(catalog, resolver) -> None:
    config = make_config()
    config.set_selection("desktop", ["kde"])
    profile = generate(catalog, resolver, config)
    document = json.loads(profile.tree.text("airootfs/etc/archcustomiser/archinstall.json"))
    assert "graphical.target" not in document.get("services", [])


# ---------------------------------------------------------------------------
# Branding
# ---------------------------------------------------------------------------


def test_os_release_declares_arch_as_its_basis(catalog, resolver) -> None:
    profile = generate(catalog, resolver, make_config())
    text = profile.tree.text("airootfs/etc/os-release")
    werte = _read_os_release(text)
    assert werte["ID_LIKE"] == "arch"
    assert "based on Arch Linux" in werte["PRETTY_NAME"]
    assert werte["NAME"] == "FLOS"
    # mkarchiso setzt diese beiden selbst; doppelte Pflege waere eine Fehlerquelle.
    assert "IMAGE_ID" not in werte
    assert "IMAGE_VERSION" not in werte


@pytest.mark.parametrize(
    "name",
    [
        'FL"OS',
        "FL'OS",
        "FLOS$(whoami)",
        "FLOS`id`",
        "FLOS${HOME}",
        r"FL\OS",
        "FLOS && rm -rf /",
    ],
)
def test_os_release_survives_shell_metacharacters(catalog, resolver, name: str) -> None:
    """os-release wird per ``. /etc/os-release`` gelesen, also ausgefuehrt.

    Geprueft wird nicht die Schreibweise, sondern das Ergebnis: was beim
    Einlesen herauskommt, muss genau das sein, was eingegeben wurde -- kein
    Kommando darf dabei zur Ausfuehrung kommen. Die frueher hier gepruefte
    Maskierung fing nur Backslash und Anfuehrungszeichen ab und liess ``$``
    und Backtick durch.
    """
    profile = generate(catalog, resolver, make_config(branding__distro_name=name))
    werte = _read_os_release(profile.tree.text("airootfs/etc/os-release"))
    assert werte["NAME"] == name
    assert werte["PRETTY_NAME"].startswith(name)


def _read_os_release(text: str) -> dict[str, str]:
    """Liest os-release so, wie ein Shell-Parser es taete.

    ``shlex`` im POSIX-Modus wendet dieselben Quoting-Regeln an wie Bash beim
    ``.``-Einlesen -- ohne dabei etwas auszufuehren. Damit prueft der Test die
    tatsaechliche Bedeutung der Datei und nicht ihre Formatierung.
    """
    import shlex

    werte: dict[str, str] = {}
    for zeile in text.splitlines():
        if not zeile or zeile.startswith("#") or "=" not in zeile:
            continue
        name, _, roh = zeile.partition("=")
        teile = shlex.split(roh, posix=True)
        werte[name] = teile[0] if teile else ""
    return werte


def test_boot_menu_title_follows_the_branding(catalog, resolver) -> None:
    profile = generate(catalog, resolver, make_config())
    assert "MENU TITLE FLOS 1.0" in profile.tree.text("syslinux/syslinux.cfg")
    entry = profile.tree.text("efiboot/loader/entries/01-archcustomiser-linux.conf")
    assert "title    FLOS 1.0" in entry


def test_missing_splash_is_a_note_not_a_failure(catalog, resolver) -> None:
    profile = generate(catalog, resolver, make_config(branding__splash="/gibt/es/nicht.png"))
    assert any("nicht gefunden" in warning for warning in profile.warnings)
    assert not profile.tree.has("syslinux/splash.png")


def test_derived_names_obey_the_format_rules(catalog, resolver) -> None:
    config = make_config(branding__distro_name="FLOS Super Edition 2026")
    profile = generate(catalog, resolver, config)
    settings = profile.settings
    assert settings.install_dir.isalnum() and settings.install_dir.islower()
    assert len(settings.install_dir) <= 30
    assert len(settings.iso_label) <= 32
    assert all(c.isupper() or c.isdigit() or c == "_" for c in settings.iso_label)


# ---------------------------------------------------------------------------
# Sonstiges
# ---------------------------------------------------------------------------


def test_invalid_configuration_is_refused(catalog, resolver) -> None:
    config = BuildConfig()      # nichts ausgewaehlt -> Pflichtkategorien fehlen
    with pytest.raises(ProfileError) as info:
        generate(catalog, resolver, config)
    assert "nicht vollstaendig" in str(info.value)


def test_bundled_profiles_all_generate(catalog, resolver, profiles_dir, tmp_path) -> None:
    from archcustomiser.core.profiles import ProfileService

    service = ProfileService(catalog, profiles_dir=tmp_path, builtin_dir=profiles_dir)
    for info in service.list():
        loaded = service.load(info.path)
        profile = generate(catalog, resolver, loaded.config)
        assert profile.tree.has("profiledef.sh"), info.display_name
        assert profile.iso_filename.endswith(".iso")


def test_build_command_uses_verbose(catalog, resolver) -> None:
    """Ohne -v gibt mkarchiso keine einzige Fortschrittsmeldung aus."""
    profile = generate(catalog, resolver, make_config())
    assert " -v " in profile.build_command()


def _assignment(text: str, name: str) -> str:
    for line in text.splitlines():
        if line.startswith(f"{name}="):
            return line
    raise AssertionError(f"{name} nicht gefunden")


# ---------------------------------------------------------------------------
# Paketliste: so lesen wie mkarchiso
# ---------------------------------------------------------------------------


def parse_like_mkarchiso(text: str) -> list[str]:
    """Bildet nach, was mkarchiso mit der Paketdatei macht.

    Woertlich aus dem Quelltext:

        sed '/^[[:blank:]]*#.*/d;s/#.*//;/^[[:blank:]]*$/d'

    Entscheidend ist die Reihenfolge: der Kommentar wird abgeschnitten, die
    Leerzeichen davor bleiben stehen. Genau daran ist ein erster echter Bau
    gescheitert -- pacstrap suchte nach einem Paket namens 'base  '.
    """
    import re

    result = []
    for line in text.splitlines():
        if re.match(r"^[ \t]*#", line):
            continue
        line = re.sub(r"#.*", "", line)
        if re.match(r"^[ \t]*$", line):
            continue
        result.append(line)
    return result


def test_package_names_survive_the_mkarchiso_parser(catalog, resolver) -> None:
    """Jede Zeile muss ein sauberer Paketname sein -- ohne Rand.

    mapfile uebernimmt die Zeilen unveraendert; ein einziges Leerzeichen am
    Ende macht den Namen unauffindbar.
    """
    config = make_config(build__include_memtest=True)
    config.add("apps.steam")
    profile = generate(catalog, resolver, config)

    names = parse_like_mkarchiso(profile.tree.text("packages.x86_64"))
    assert names, "die Paketliste ist leer"
    for name in names:
        assert name == name.strip(), f"Rand im Paketnamen: {name!r}"
        assert " " not in name, f"Leerzeichen im Paketnamen: {name!r}"
        assert "#" not in name, f"Kommentarrest im Paketnamen: {name!r}"


def test_required_packages_are_actually_in_the_parsed_list(catalog, resolver) -> None:
    """Die Begruendungen stehen in Kommentaren -- die Namen muessen trotzdem da sein."""
    profile = generate(catalog, resolver, make_config())
    names = set(parse_like_mkarchiso(profile.tree.text("packages.x86_64")))
    for required in ("base", "mkinitcpio", "mkinitcpio-archiso", "syslinux"):
        assert required in names, f"{required} fehlt in der gelesenen Liste"


def test_reasons_are_kept_as_comments(catalog, resolver) -> None:
    """Der Benutzer soll weiterhin sehen, warum ein Paket dabei ist."""
    text = generate(catalog, resolver, make_config()).tree.text("packages.x86_64")
    assert "# " in text
    assert "Grundsystem" in text


def test_no_duplicate_packages(catalog, resolver) -> None:
    """Jedes Paket genau einmal -- doppelte Eintraege sind Ballast."""
    config = make_config(build__include_memtest=True)
    config.add("apps.steam")
    names = parse_like_mkarchiso(
        generate(catalog, resolver, config).tree.text("packages.x86_64")
    )
    doppelt = {name for name in names if names.count(name) > 1}
    assert not doppelt, f"doppelte Eintraege: {sorted(doppelt)}"
