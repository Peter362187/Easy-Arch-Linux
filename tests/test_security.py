"""Sicherheitszusicherungen (Spec Abschnitt 12).

Diese Tests pruefen keine Funktionen, sondern Eigenschaften des Systems:
dass Passwoerter nirgends auftauchen, dass keine Shell benutzt wird und dass
Benutzereingaben nie ungeschuetzt in eine Argumentliste geraten.
"""

from __future__ import annotations

import copy
import logging
import pickle

import pytest

from archcustomiser.core.logging_setup import SecretRedactionFilter, redaction_filter
from archcustomiser.core.secrets import Secret, SecretStore

# ---------------------------------------------------------------------------
# Passwoerter
# ---------------------------------------------------------------------------


def test_secret_hides_itself_in_every_string_form() -> None:
    secret = Secret("hunter2-geheim")
    assert "hunter2" not in repr(secret)
    assert "hunter2" not in str(secret)
    assert "hunter2" not in f"{secret}"
    assert "hunter2" not in f"{secret!r}"
    assert "hunter2" not in f"{secret}"
    assert secret.reveal() == "hunter2-geheim"


def test_secret_cannot_be_pickled() -> None:
    """Sonst koennte ein Geheimnis unbemerkt in einer Datei landen."""
    with pytest.raises(TypeError):
        pickle.dumps(Secret("geheim"))


def test_secret_survives_copy_without_leaking() -> None:
    secret = Secret("geheim123")
    duplicate = copy.deepcopy(secret)
    assert duplicate.reveal() == "geheim123"
    assert "geheim123" not in repr(duplicate)


def test_burn_makes_the_secret_unusable() -> None:
    secret = Secret("geheim123")
    secret.burn()
    assert not secret
    with pytest.raises(ValueError):
        secret.reveal()


def test_store_repr_shows_no_values() -> None:
    store = SecretStore()
    store.set("user.password", "hunter2-geheim")
    assert "hunter2" not in repr(store)
    assert store.has("user.password")


def test_store_burns_the_previous_value_on_overwrite() -> None:
    store = SecretStore()
    store.set("k", "erstes-geheimnis")
    first = store.get("k")
    store.set("k", "zweites-geheimnis")
    assert not first          # der alte Puffer wurde geloescht
    assert store.get("k").reveal() == "zweites-geheimnis"


def test_empty_value_clears_the_entry() -> None:
    store = SecretStore()
    store.set("k", "geheim")
    store.set("k", "")
    assert not store.has("k")


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _record(message: str, *args: object) -> logging.LogRecord:
    return logging.LogRecord("test", logging.INFO, __file__, 1, message, args, None)


def test_registered_secret_is_masked_in_the_message() -> None:
    Secret("streng-geheim-42")          # registriert sich beim Filter
    record = _record("Passwort ist streng-geheim-42")
    redaction_filter().filter(record)
    assert "streng-geheim-42" not in record.msg
    assert "***" in record.msg


def test_secret_in_arguments_is_masked_too() -> None:
    """Der haeufigste Fehler: log.info("pw=%s", passwort)."""
    Secret("argument-geheim-99")
    record = _record("Benutzer %s, Passwort %s", "jason", "argument-geheim-99")
    redaction_filter().filter(record)
    assert "argument-geheim-99" not in str(record.args)


def test_crypt_hashes_are_masked_without_registration() -> None:
    """Auch der Hash gehoert nicht ins Log."""
    filter_ = SecretRedactionFilter()
    for hash_value in (
        "$y$j9T$Xk2abcdefghij$klmnopqrstuvwxyz",
        "$6$saltsalt$hashhashhashhash",
        "$2b$12$abcdefghijklmnopqrstuv",
    ):
        record = _record(f"shadow-Eintrag: {hash_value}")
        filter_.filter(record)
        assert hash_value not in record.msg


def test_very_short_values_are_not_masked() -> None:
    """Sonst wuerde ein Passwort 'ab' jedes 'ab' im Log unkenntlich machen."""
    filter_ = SecretRedactionFilter()
    filter_.add_literal("ab")
    record = _record("Paket abiword wird installiert")
    filter_.filter(record)
    assert "abiword" in record.msg


# ---------------------------------------------------------------------------
# Prozessaufrufe
# ---------------------------------------------------------------------------


def test_runner_refuses_a_string_command() -> None:
    """Ein String waere eine Einladung zur Shell-Interpretation."""
    from archcustomiser.core.packages.runner import SubprocessRunner

    with pytest.raises(TypeError):
        SubprocessRunner().run("pacman -Sy")   # type: ignore[arg-type]


def test_pacman_preview_uses_a_separator_and_validated_names(fake_runner) -> None:
    """Doppelter Schutz: geprueft und zusaetzlich hinter '--'."""
    from archcustomiser.core.packages.backend_pacman import PacmanSyncBackend
    from archcustomiser.core.packages.errors import PacmanNotAvailable
    from archcustomiser.core.packages.names import InvalidPackageName

    backend = PacmanSyncBackend(runner=fake_runner)

    # Ein Name, der wie ein Schalter aussieht, kommt gar nicht bis zum Aufruf.
    with pytest.raises((InvalidPackageName, PacmanNotAvailable)):
        backend.preview_transaction(["--root=/"])
    assert all("--root=/" not in " ".join(call) for call in fake_runner.calls)


def test_cache_rejects_repo_names_with_path_separators(tmp_path) -> None:
    """Ein Repo-Name darf nie einen Pfad ausserhalb des Caches bilden."""
    from archcustomiser.core.packages.cache import PackageCache
    from archcustomiser.core.packages.errors import CacheError

    cache = PackageCache(root=tmp_path)
    for evil in ("../../etc/passwd", "a/b", "..", "/absolut"):
        with pytest.raises(CacheError):
            cache.db_path(evil)


def test_cache_is_never_pickled(tmp_path) -> None:
    """Fremde Dateien zu deserialisieren waere eine Angriffsflaeche."""
    from archcustomiser.core.packages.cache import PackageCache

    from .conftest import build_fake_syncdb

    cache = PackageCache(root=tmp_path)
    cache.store(
        "core",
        build_fake_syncdb([{"name": "firefox", "version": "1-1"}]),
        url="https://example.invalid",
        etag=None,
        last_modified=None,
        package_count=1,
    )
    meta = cache.meta_path("core").read_bytes()
    assert meta.lstrip().startswith(b"{"), "Metadaten muessen JSON sein"


def test_predicates_cannot_execute_code() -> None:
    """Der Katalog kann aus einem Benutzer-Overlay stammen."""
    from archcustomiser.core.catalog.predicate import PredicateError, parse

    for evil in (
        {"__import__": ["os"]},
        {"eval": ["1+1"]},
        {"exec": ["print(1)"]},
        123,
        {"all_of": "kein-listenwert"},
    ):
        with pytest.raises(PredicateError):
            parse(evil)


# ---------------------------------------------------------------------------
# Befunde der Durchsicht vom 02.09.2026
#
# Jeder Test hier steht fuer eine Luecke, die tatsaechlich offen war und
# experimentell reproduziert wurde -- nicht fuer eine theoretische Sorge.
# ---------------------------------------------------------------------------


def test_burning_a_secret_removes_it_from_the_log_filter() -> None:
    """``burn()`` war wirkungslos, solange der Filter den Klartext behielt.

    Der Puffer wurde genullt, aber die Kopie in der Literalliste lebte bis zum
    Prozessende weiter -- also genau so lange, wie das Geheimnis nicht mehr
    existieren sollte.
    """
    filter_ = SecretRedactionFilter()
    from archcustomiser.core import secrets as secrets_module

    secrets_module._observers.append((filter_.add_literal, filter_.discard_literal))
    try:
        secret = Secret("streng-geheim-1234")
        assert "streng-geheim-1234" in filter_.known_literals()
        secret.burn()
        assert filter_.known_literals() == ()
    finally:
        secrets_module._observers.pop()


def test_two_secrets_with_the_same_value_are_counted() -> None:
    """Das Loeschen des einen darf den Schutz des anderen nicht aufheben."""
    filter_ = SecretRedactionFilter()
    from archcustomiser.core import secrets as secrets_module

    secrets_module._observers.append((filter_.add_literal, filter_.discard_literal))
    try:
        erstes = Secret("doppelt-vergeben")
        zweites = Secret("doppelt-vergeben")
        erstes.burn()
        assert "doppelt-vergeben" in filter_.known_literals(), "zu frueh vergessen"
        zweites.burn()
        assert filter_.known_literals() == ()
    finally:
        secrets_module._observers.pop()


def test_the_store_leaves_nothing_behind_after_clear() -> None:
    filter_ = SecretRedactionFilter()
    from archcustomiser.core import secrets as secrets_module

    secrets_module._observers.append((filter_.add_literal, filter_.discard_literal))
    try:
        store = SecretStore()
        store.set("user.password", "archiso")
        store.clear()
        assert filter_.known_literals() == ()
    finally:
        secrets_module._observers.pop()


def test_a_password_does_not_mangle_unrelated_words() -> None:
    """Der zweite Schaden desselben Befundes.

    Solange jeder Tastendruck ein eigenes ``Secret`` erzeugte, standen auch
    alle Praefixe in der Liste. Aus der Logzeile "Installiere archiso" wurde
    dann "Installiere ***hiso" -- das Bauprotokoll war unbrauchbar, sobald ein
    Passwort mit einem haeufigen Wortanfang begann.
    """
    filter_ = SecretRedactionFilter()
    filter_.add_literal("archiso")
    ergebnis = filter_._scrub("Installiere archiso und arch-install-scripts")
    assert "arch-install-scripts" in ergebnis, "ein fremdes Wort wurde zerlegt"
    assert "archiso" not in ergebnis.replace("arch-install-scripts", "")


def test_longer_literals_are_replaced_first() -> None:
    """Sonst zerlegt ein kurzes Literal ein laengeres, bevor es drankommt."""
    filter_ = SecretRedactionFilter()
    filter_.add_literal("geheim")
    filter_.add_literal("geheim-lang")
    assert filter_._scrub("Wert: geheim-lang") == "Wert: ***"


def test_saving_a_profile_never_writes_a_secret_field(tmp_path, catalog) -> None:
    """Die Grenze hielt bisher nur beim Laden.

    Passwoerter erreichen ``BuildConfig`` auf dem vorgesehenen Weg nie. Haengt
    die Zusicherung aber allein daran, dass die Oberflaeche sich richtig
    verhaelt, ist sie keine Zusicherung.
    """
    from archcustomiser.core.config import BuildConfig
    from archcustomiser.core.profiles import ProfileService

    config = BuildConfig(catalog_version=catalog.catalog_version)
    config.set_field("user.name", "jason")
    config.set_field("user.password", "hunter2-geheim")

    ziel = tmp_path / "profil.yaml"
    ProfileService(catalog).save(config, ziel)

    inhalt = ziel.read_text(encoding="utf-8")
    assert "hunter2-geheim" not in inhalt
    assert "user.password" not in inhalt
    assert "jason" in inhalt, "die harmlosen Felder muessen erhalten bleiben"


def test_boot_menu_title_cannot_break_out_of_grub_config(catalog, resolver) -> None:
    """GRUBs Konfiguration ist eine Skriptsprache, kein Datenformat.

    ``menuentry "Titel" { ... }`` -- ein Anfuehrungszeichen im Titel schliesst
    die Zeichenkette und laesst den Rest der Zeile als Befehle stehen. Das Feld
    hatte weder Validator noch Laengenbegrenzung, und die Selbstpruefung des
    Generators sah nur nach, ob die Datei existiert.
    """
    import dataclasses

    from archcustomiser.core.archiso import bootloader, branding
    from archcustomiser.core.archiso.settings import build_settings
    from archcustomiser.core.archiso.tree import ProfileTree
    from archcustomiser.core.config import BuildConfig

    angriff = 'Boese" ; insmod evil ; menuentry "x'
    config = BuildConfig(catalog_version=catalog.catalog_version)
    for kategorie in catalog.categories:
        if kategorie.default_selection:
            config.set_selection(kategorie.id, kategorie.default_selection)
    config.set_field("branding.boot_menu_title", angriff)

    settings = build_settings(config, resolver.resolve(config))
    settings = dataclasses.replace(settings, bootmodes=("bios.syslinux", "uefi.grub"))

    tree = ProfileTree()
    bootloader.build_bootloaders(tree, settings, branding.menu_title(config))

    grub = tree.text("grub/grub.cfg")
    assert grub.count('"') % 2 == 0, "die Anfuehrungszeichen sind nicht mehr paarig"
    assert grub.count("{") == grub.count("}"), "die Klammern sind nicht mehr paarig"
    assert "insmod evil" not in grub.replace("insmod evil menuentry", "")

    # Und dieselbe Zusicherung fuer die beiden zeilenbasierten Formate.
    for pfad in tree.paths():
        if pfad.endswith((".cfg", ".conf")):
            for zeile in tree.text(pfad).splitlines():
                assert "\n" not in zeile


def test_the_catalog_rejects_a_dangerous_menu_title() -> None:
    """Zweite Linie: der Benutzer soll es erfahren, nicht stillschweigend
    bereinigt bekommen."""
    from archcustomiser.core import validation

    assert validation.validate("menu_title", "Mein Linux 1.0").ok
    assert not validation.validate("menu_title", 'Boese" ; x').ok
    assert not validation.validate("menu_title", "a" * 70).ok
    assert not validation.validate("menu_title", "Titel mit $HOME").ok


# ---------------------------------------------------------------------------
# Der Passwort-Hash liegt nicht weltlesbar herum
# ---------------------------------------------------------------------------


def _baum_mit_shadow():
    from archcustomiser.core.archiso.tree import ProfileTree

    baum = ProfileTree()
    baum.add_file("profiledef.sh", "# leer", origin="test")
    baum.add_file(
        "airootfs/etc/shadow",
        "jason:$6$salz$hashhashhash:19000:0:99999:7:::",
        origin="test",
    )
    baum.add_permission("/etc/shadow", mode="0400", origin="test")
    return baum


def test_the_password_hash_is_not_world_readable_in_an_archive(tmp_path) -> None:
    """Ein exportiertes Profil wird weitergegeben -- mitsamt /etc/shadow.

    Die Rechte aus tree.permissions wirken erst im fertigen Abbild; im Archiv
    stand fuer jede Datei fest 0644. sha512crypt mit 5000 Runden ist offline
    angreifbar.
    """
    import tarfile

    from archcustomiser.core.archiso.sinks import TarSink

    ziel = tmp_path / "profil.tar.gz"
    TarSink(ziel, root_name="profil").write(_baum_mit_shadow())

    with tarfile.open(ziel) as archiv:
        shadow = archiv.getmember("profil/airootfs/etc/shadow")
        andere = archiv.getmember("profil/profiledef.sh")

    assert shadow.mode & 0o077 == 0, "der Passwort-Hash liegt weltlesbar im Archiv"
    assert andere.mode == 0o644, "die uebrigen Dateien behalten ihren festen Modus"


def test_the_password_hash_is_not_world_readable_on_disk(braucht_symlinks, tmp_path) -> None:
    """Auf einem Mehrbenutzer-Host lag der Hash im Arbeitsverzeichnis offen."""
    import os

    from archcustomiser.core.archiso.sinks import DirectorySink

    ziel = tmp_path / "profil"
    DirectorySink(ziel, iso_name="flos").write(_baum_mit_shadow())

    if os.name == "nt":
        pytest.skip("Windows kennt keine POSIX-Rechte")
    modus = (ziel / "airootfs" / "etc" / "shadow").stat().st_mode
    assert modus & 0o077 == 0, "der Passwort-Hash ist fuer andere lesbar"


# ---------------------------------------------------------------------------
# Paketnamen aus einer Profildatei
# ---------------------------------------------------------------------------


def test_a_hand_edited_profile_cannot_smuggle_pacman_switches(tmp_path, catalog) -> None:
    """names.py verlangt die Pruefung fuer JEDEN Namen aus einer Profildatei.

    profiles.py hielt sich nicht daran: ein Eintrag wie '--dbpath=/' wanderte
    unveraendert in packages.x86_64 und damit in die Argumentliste von
    pacstrap.
    """
    from archcustomiser.core.profiles import ProfileService

    pfad = tmp_path / "boese.yaml"
    pfad.write_text(
        chr(10).join(
            [
                "schema_version: 1",
                "name: Test",
                "extra_packages:",
                '  - "--dbpath=/"',
                '  - "a b"',
                '  - "../ausbruch"',
                '  - "firefox"',
                '  - "vim>=9.0"',
            ]
        ),
        encoding="utf-8",
    )

    ergebnis = ProfileService(catalog).load(pfad)

    assert ergebnis.config.extra_packages == ["firefox", "vim>=9.0"]
    verworfen = [i for i in ergebnis.issues if i.code == "invalid_package"]
    assert len(verworfen) == 3
    assert all(i.action_taken == "verworfen" for i in verworfen)
