"""Rueckfallschutz fuer die Durchsicht vom 07.09.2026.

Gemeldet wurden zwanzig Punkte, geprueft wurde jeder am Quelltext. Zwei
erwiesen sich als falsch: ein angeblich doppeltes Locale-Suffix, das
archinstall selbst abfaengt, und ein bash-3.2-Fehler, den es nur in bash 4.0.x
gab. Was hier steht, sind die bestaetigten.

Jeder Test nennt, was ohne ihn zurueckkehren wuerde -- ein Test, dessen Zweck
man nicht kennt, wird beim naechsten Umbau achtlos angepasst.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from archcustomiser.core.catalog.loader import load_catalog
from archcustomiser.core.config import BuildConfig
from archcustomiser.core.resolver import Resolver


def _config(**felder) -> BuildConfig:
    config = BuildConfig()
    for ref in ("desktop.none", "kernel.linux", "audio.none", "network.networkmanager"):
        config.add(ref)
    config.set_field("branding.distro_name", "FLOS")
    config.set_field("branding.version", "1.0")
    config.set_field("basics.hostname", "flos")
    for schluessel, wert in felder.items():
        config.set_field(schluessel.replace("__", "."), wert)
    return config


# ---------------------------------------------------------------------------
# Multilib: gefunden, aber nicht installierbar
# ---------------------------------------------------------------------------


def test_a_multilib_package_enables_the_multilib_repository() -> None:
    """Der Index laedt multilib mit, die pacman.conf kannte es nicht.

    Ein eingetipptes ``steam`` galt damit als gefunden, und der Bau scheiterte
    erst Minuten spaeter in pacstrap mit "target not found".
    """
    from archcustomiser.core.archiso.pacman_conf import render_pacman_conf

    katalog = load_catalog()
    config = _config()
    config.extra_packages = ["lib32-mesa"]
    config.extra_repositories = ["multilib"]

    aufloesung = Resolver(katalog).resolve(config)
    assert "multilib" in aufloesung.repositories
    assert "[multilib]" in render_pacman_conf(aufloesung.repositories)


def test_the_repository_of_a_package_is_derived_from_the_check() -> None:
    """Woher die Angabe kommt: aus der Paketpruefung, nicht aus einer Liste."""
    from archcustomiser.core.packages import EntryKind
    from archcustomiser.core.packages.validator import repositories_of

    class Eintrag:
        def __init__(self, kind, repo):
            self.kind, self.repo = kind, repo

    class Bericht:
        entries = (
            Eintrag(EntryKind.PACKAGE, "core"),        # Standard -- zaehlt nicht
            Eintrag(EntryKind.PACKAGE, "extra"),       # Standard -- zaehlt nicht
            Eintrag(EntryKind.PACKAGE, "multilib"),    # muss gemeldet werden
            Eintrag(EntryKind.UNVERIFIED, None),       # ungeprueft -- kein Repo
        )

    assert repositories_of(Bericht()) == ("multilib",)


def test_the_extra_repositories_survive_a_save_and_load(tmp_path) -> None:
    """Ohne Persistenz kaeme der Fehler beim naechsten Laden sofort zurueck."""
    from archcustomiser.core.profiles import ProfileService

    katalog = load_catalog()
    dienst = ProfileService(katalog, profiles_dir=tmp_path)
    config = _config()
    config.extra_packages = ["lib32-mesa"]
    config.extra_repositories = ["multilib"]

    ziel = dienst.save(config, tmp_path / "test.yaml", include_snapshot=False)
    assert ProfileService(katalog).load(ziel).config.extra_repositories == ["multilib"]


def test_a_bogus_repository_name_is_refused(tmp_path) -> None:
    """Der Name landet woertlich in der pacman.conf des Abbilds."""
    import yaml

    from archcustomiser.core.profiles import ProfileService

    datei = tmp_path / "boese.yaml"
    datei.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "name": "boese",
                "selections": {},
                "fields": {},
                "extra_repositories": ["multilib", "boese]\n[core"],
            }
        ),
        encoding="utf-8",
    )
    geladen = ProfileService(load_catalog()).load(datei)
    assert geladen.config.extra_repositories == ["multilib"]


def _meta(*repos: str):
    """Eine Index-Beschreibung mit genau diesen Repositories."""
    from datetime import datetime, timezone

    from archcustomiser.core.packages.models import IndexMetadata, RepoMeta

    jetzt = datetime(2026, 9, 7, tzinfo=timezone.utc)
    return IndexMetadata(
        backend="test",
        arch="x86_64",
        repos=tuple(RepoMeta(name=name, source="test", fetched_at=jetzt) for name in repos),
    )


# ---------------------------------------------------------------------------
# Teilindex: vorhandene Pakete als "gibt es nicht" gemeldet
# ---------------------------------------------------------------------------


def test_a_partial_index_is_reported_as_degraded() -> None:
    """Faellt ein Repository beim Laden aus, war der Dienst trotzdem zufrieden.

    Jedes Paket aus dem fehlenden Repository galt danach als NOT_FOUND --
    rot, blockierend, "in den offiziellen Repositories nicht gefunden". Wer
    das glaubte, loeschte einen voellig korrekten Paketnamen.
    """
    from archcustomiser.core.packages.service import PackageConfig, PackageService

    class TeilIndex:
        meta = _meta("core", "extra")

        def describe(self) -> str:
            return "core, extra"

    class TeilBackend:
        def load_index(self, **_kwargs):
            return TeilIndex()

    dienst = PackageService(
        config=PackageConfig(repos=("core", "extra", "multilib")), backend=TeilBackend()
    )
    dienst.load()

    assert dienst.degraded, "ein Teilindex galt als vollstaendig"
    assert any("multilib" in problem.message for problem in dienst.problems())


def test_a_complete_index_is_not_flagged() -> None:
    from archcustomiser.core.packages.service import PackageConfig, PackageService

    class VollIndex:
        meta = _meta("core", "extra", "multilib")

        def describe(self) -> str:
            return "vollstaendig"

    class VollBackend:
        def load_index(self, **_kwargs):
            return VollIndex()

    dienst = PackageService(
        config=PackageConfig(repos=("core", "extra", "multilib")), backend=VollBackend()
    )
    dienst.load()
    assert not dienst.degraded
    assert dienst.problems() == ()


# ---------------------------------------------------------------------------
# archinstall
# ---------------------------------------------------------------------------


def test_the_installer_gets_every_chosen_package() -> None:
    """Frueher standen dort nur die Freitextpakete.

    Wer Firefox, Steam und Docker anhakte und die ISO danach installierte,
    fand sie im fertigen System nicht wieder.
    """
    from archcustomiser.core.plan import build_archinstall_config

    katalog = load_catalog()
    config = _config()
    config.extra_packages = ["neovim"]
    aufloesung = Resolver(katalog).resolve(config)

    dokument = build_archinstall_config(config, aufloesung)
    pakete = set(dokument.get("packages", ()))

    assert "neovim" in pakete, "die Freitextpakete fehlen"
    fehlend = set(aufloesung.package_names) - pakete
    assert not fehlend, f"gewaehlte Pakete fehlen in archinstall.json: {sorted(fehlend)[:5]}"


# Was archinstall wirklich kennt. Kein Ratespiel: die Werte stammen aus den
# Enums GfxDriver und GreeterType und wurden gegen v3.0.0 und master geprueft.
BEKANNTE_TREIBER = {
    "All open-source",
    "AMD / ATI (open-source)",
    "Intel (open-source)",
    "Nvidia (open kernel module for newer GPUs, Turing+)",
    "Nvidia (open-source nouveau driver)",
    "Nvidia (proprietary)",
}
BEKANNTE_GREETER = {"sddm", "gdm", "lightdm-gtk-greeter", "lxdm", "ly", "cosmic-greeter"}


@pytest.mark.parametrize(
    "schluessel, erlaubt",
    [
        ("profile_config.gfx_driver", BEKANNTE_TREIBER),
        ("profile_config.greeter", BEKANNTE_GREETER),
    ],
)
def test_the_catalog_only_uses_values_archinstall_knows(schluessel, erlaubt) -> None:
    """Ein erfundener Wert bricht die ganze archinstall-Konfiguration.

    Im Katalog stand "Mesa open-source" statt "All open-source" und "lightdm"
    statt "lightdm-gtk-greeter". Beides sind keine freien Zeichenketten,
    sondern Enum-Werte -- archinstall wirft dabei einen ValueError und
    verwirft die gesamte Datei, nicht nur das eine Feld.
    """
    katalog = load_catalog()
    gefunden = {
        option.semantics[schluessel]
        for kategorie in katalog.categories
        for option in kategorie.options
        if schluessel in option.semantics
    }
    unbekannt = gefunden - erlaubt
    assert not unbekannt, f"{schluessel}: archinstall kennt {sorted(unbekannt)} nicht"


# ---------------------------------------------------------------------------
# Das Live-System, in das niemand hineinkommt
# ---------------------------------------------------------------------------


def test_an_iso_nobody_can_log_into_says_so() -> None:
    """Kein Konto plus gesperrtes Root ergibt eine unbrauchbare ISO.

    Nicht gefaehrlich, aber ohne Hinweis merkt man es erst nach dem Brennen.
    """
    from archcustomiser.core.archiso.generator import ProfileGenerator

    katalog = load_catalog()
    config = _config()
    config.set_field("user.create", False)
    config.set_field("user.root_locked", True)

    erzeugt = ProfileGenerator(katalog, config, Resolver(katalog).resolve(config)).generate()
    baum = getattr(erzeugt, "tree", erzeugt)
    assert any("niemand anmelden" in hinweis for hinweis in baum.notes)


def test_the_minimal_profile_produces_a_usable_iso() -> None:
    from archcustomiser.core.archiso.generator import ProfileGenerator
    from archcustomiser.core.profiles import ProfileService

    katalog = load_catalog()
    geladen = ProfileService(katalog).load(Path("src/archcustomiser/profiles/minimal.yaml"))
    assert geladen.config.creates_user, "die ISO haette gar kein Konto"

    erzeugt = ProfileGenerator(
        katalog, geladen.config, Resolver(katalog).resolve(geladen.config)
    ).generate()
    baum = getattr(erzeugt, "tree", erzeugt)
    assert "arch:x:1000" in baum.files["airootfs/etc/passwd"].content.decode()
