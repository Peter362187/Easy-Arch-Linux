"""Tests der WSL-Anbindung.

Auf diesem Rechner ist WSL nicht eingerichtet -- geprueft wird deshalb mit
einem nachgebildeten ``wsl.exe`` und mit aufgezeichneter Ausgabe. Die beiden
Fallen, an denen eine naive Umsetzung scheitert, sind damit abgedeckt: die
UTF-16-Kodierung der Verwaltungsmeldungen und der Verlust von Verknuepfungen
beim Weg ueber ein Windows-Laufwerk.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path, PurePosixPath

import pytest

from archcustomiser.core.archiso.tree import ProfileTree
from archcustomiser.core.build import wsl
from archcustomiser.core.build.targets import LocalTarget, WslExecutionTarget


# ---------------------------------------------------------------------------
# Kodierung
# ---------------------------------------------------------------------------


def test_management_output_is_utf16() -> None:
    """wsl.exe schreibt seine eigenen Meldungen in UTF-16-LE.

    Wer sie als UTF-8 liest, bekommt Zeichensalat -- und wer auf Text prueft,
    scheitert ohnehin an der Uebersetzung.
    """
    original = "Der Windows-Subsystem für Linux ist nicht installiert."
    assert wsl._decode_management(original.encode("utf-16-le")) == original


def test_management_output_falls_back_to_utf8() -> None:
    """Falls Microsoft das eines Tages aendert."""
    assert wsl._decode_management("Hallo Welt".encode("utf-8")) == "Hallo Welt"


def test_empty_output() -> None:
    assert wsl._decode_management(b"") == ""


# ---------------------------------------------------------------------------
# Verteilungsliste
# ---------------------------------------------------------------------------


ENGLISH = """  NAME                   STATE           VERSION
* archlinux              Running         2
  Ubuntu-24.04           Stopped         2
  docker-desktop         Stopped         2
"""

GERMAN = """  NAME              ZUSTAND             VERSION
* archlinux         Wird ausgefuehrt    2
  Ubuntu            Beendet             2
"""


def test_parses_english_listing() -> None:
    found = wsl.parse_distribution_list(ENGLISH)
    assert [d.name for d in found] == ["archlinux", "Ubuntu-24.04", "docker-desktop"]
    assert found[0].default and found[0].is_arch and found[0].running
    assert not found[1].is_arch


def test_parses_translated_listing() -> None:
    """Die Spaltenueberschriften sind uebersetzt, die Struktur nicht."""
    found = wsl.parse_distribution_list(GERMAN)
    assert [d.name for d in found] == ["archlinux", "Ubuntu"]
    assert found[0].is_arch
    assert found[0].running, "der uebersetzte Zustand muss erkannt werden"


def test_header_line_is_skipped() -> None:
    assert wsl.parse_distribution_list("  NAME   STATE   VERSION\n") == ()


def test_empty_listing() -> None:
    assert wsl.parse_distribution_list("") == ()


def test_status_picks_the_default_arch_distribution() -> None:
    status = wsl.WslStatus(installed=True, distributions=wsl.parse_distribution_list(ENGLISH))
    assert status.usable
    assert status.preferred is not None and status.preferred.name == "archlinux"


def test_status_without_arch_is_not_usable() -> None:
    listing = "  NAME     STATE     VERSION\n* Ubuntu   Running   2\n"
    status = wsl.WslStatus(installed=True, distributions=wsl.parse_distribution_list(listing))
    assert not status.usable
    assert status.preferred is None


def test_detect_does_not_raise_when_wsl_is_missing(monkeypatch) -> None:
    """Egal wie das System aussieht -- die Erkennung darf nie werfen.

    Frueher startete dieser Test das echte ``wsl.exe`` (zwei Aufrufe mit je
    60 s Zeitlimit) und pruefte danach nur, dass ``installed`` ein bool ist.
    Ergebnis und Laufzeit hingen damit vom Rechner ab. Hier wird die
    Verwaltungsschicht ersetzt und beide Ausgaenge geprueft.
    """
    monkeypatch.setattr(wsl, "wsl_executable", lambda: None)
    status = wsl.detect()
    assert isinstance(status.installed, bool)
    assert not status.installed
    assert status.problem


def test_detect_reports_a_missing_subsystem(monkeypatch) -> None:
    """Exit-Code 50 heisst: WSL ist gar nicht eingerichtet."""
    import subprocess

    meldung = "Das Windows-Subsystem fuer Linux ist nicht installiert." + chr(13) + chr(10)

    monkeypatch.setattr(wsl.os, "name", "nt")
    monkeypatch.setattr(wsl, "wsl_executable", lambda: "wsl.exe")
    monkeypatch.setattr(
        wsl.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a[0] if a else [], wsl.EXIT_NOT_INSTALLED, b"", meldung.encode("utf-16-le")
        ),
    )
    status = wsl.detect()
    assert not status.installed
    assert "nicht installiert" in status.problem


# ---------------------------------------------------------------------------
# Aufrufe
# ---------------------------------------------------------------------------


class FakeWsl:
    """Ersetzt ``WslTarget`` und zeichnet jede Argumentliste auf."""

    def __init__(self, distribution: str = "archlinux") -> None:
        self.distribution = distribution
        self.calls: list[tuple[str, ...]] = []
        self.responses: dict[str, wsl.WslResult] = {}
        self.default = wsl.WslResult(0, "", "")

    def wrap(self, argv):
        return ["wsl.exe", "-d", self.distribution, "-e", *[str(a) for a in argv]]

    def run(self, argv, *, timeout: float = 60.0) -> wsl.WslResult:
        arguments = tuple(str(a) for a in argv)
        self.calls.append(arguments)
        for key, value in self.responses.items():
            if key in " ".join(arguments):
                return value
        return self.default

    def to_linux_path(self, path) -> str:
        text = str(path).replace("\\", "/")
        if len(text) > 1 and text[1] == ":":
            return f"/mnt/{text[0].lower()}{text[2:]}"
        return text

    def home(self) -> PurePosixPath:
        return PurePosixPath("/home/jason")

    def has_command(self, name: str) -> bool:
        return name in ("mkarchiso", "tar", "pacman")

    def is_arch(self) -> bool:
        return True

    def free_space_gb(self, path: str):
        return 120.0

    def subid_ready(self) -> bool:
        return True

    def userns_ready(self) -> bool:
        return True


def test_wrap_builds_a_wsl_call() -> None:
    fake = FakeWsl()
    target = WslExecutionTarget(fake)
    argv = target.wrap(["mkarchiso", "-v", "-w", "/home/jason/work"])
    assert argv[:4] == ["wsl.exe", "-d", "archlinux", "-e"]
    assert "mkarchiso" in argv


def test_linux_paths_survive_on_windows() -> None:
    """Ein Linux-Pfad darf nicht durch pathlib laufen.

    Unter Windows wuerde aus '/home/jason' sonst '\\home\\jason'.
    """
    from archcustomiser.core.build.runner import MkarchisoRunner

    runner = MkarchisoRunner(
        "/home/jason/profil",
        "/home/jason/work",
        "/home/jason/out",
        target=WslExecutionTarget(FakeWsl()),
    )
    argv = runner.build_argv()
    assert "/home/jason/profil" in argv
    assert "/home/jason/work" in argv
    assert not any("\\" in item for item in argv if item.startswith("/"))


def test_local_target_is_unchanged() -> None:
    target = LocalTarget(executable="/usr/bin/mkarchiso")
    assert target.wrap(["mkarchiso", "-v"]) == ["mkarchiso", "-v"]
    assert target.cwd() is None


def test_readiness_reports_missing_archiso() -> None:
    fake = FakeWsl()
    fake.has_command = lambda name: name != "mkarchiso"    # type: ignore[assignment]
    readiness = wsl.check_readiness(fake)
    assert not readiness.ready
    assert any("archiso" in problem for problem in readiness.problems)
    assert any("pacman" in remedy for remedy in readiness.remedies)


def test_readiness_reports_non_arch_distribution() -> None:
    fake = FakeWsl("Ubuntu")
    fake.is_arch = lambda: False        # type: ignore[assignment]
    readiness = wsl.check_readiness(fake)
    assert not readiness.ready
    assert any("kein Arch" in problem for problem in readiness.problems)


def test_readiness_warns_about_space() -> None:
    fake = FakeWsl()
    fake.free_space_gb = lambda _p: 3.0    # type: ignore[assignment]
    readiness = wsl.check_readiness(fake, needed_gb=25.0)
    assert any("GB frei" in problem for problem in readiness.problems)


# ---------------------------------------------------------------------------
# Profiluebertragung -- der eigentliche Kniff
# ---------------------------------------------------------------------------


@pytest.fixture
def tree() -> ProfileTree:
    profile = ProfileTree()
    profile.add_file("profiledef.sh", "iso_name=flos\n", origin="generator")
    profile.add_symlink(
        "airootfs/etc/systemd/system/display-manager.service",
        "/usr/lib/systemd/system/sddm.service",
        origin="display_manager.sddm",
    )
    profile.add_symlink(
        "airootfs/etc/localtime", "/usr/share/zoneinfo/Europe/Berlin", origin="generator"
    )
    return profile


def test_transfer_uses_an_archive_not_a_windows_path(tree: ProfileTree) -> None:
    """Der Kern der WSL-Anbindung.

    Direkt auf ein Windows-Laufwerk geschrieben verloere das Profil seine
    symbolischen Verknuepfungen -- unter /mnt gibt es keine. Deshalb geht es
    als tar-Archiv hinueber und wird *innerhalb* von Linux ausgepackt.
    """
    from archcustomiser.core.build.wsl_build import WslPaths, transfer_profile

    fake = FakeWsl()
    # Die Gegenprobe zaehlt die Verknuepfungen im Zielverzeichnis.
    fake.responses = {"find": wsl.WslResult(0, "2\n", "")}
    paths = WslPaths(
        root=PurePosixPath("/home/jason/.cache/archcustomiser/flos"),
        profile=PurePosixPath("/home/jason/.cache/archcustomiser/flos/profile"),
        work=PurePosixPath("/home/jason/.cache/archcustomiser/flos/work"),
        out=PurePosixPath("/home/jason/.cache/archcustomiser/flos/out"),
    )

    transfer_profile(fake, tree, paths, "flos")

    commands = [" ".join(call) for call in fake.calls]
    assert any(call.startswith("tar xzf") for call in commands), "es wurde kein Archiv ausgepackt"
    extract = next(call for call in commands if call.startswith("tar xzf"))
    # Ausgepackt wird ins Linux-Dateisystem, nicht nach /mnt.
    assert "-C /home/jason" in extract
    assert "/mnt/" in extract, "das Archiv wird von der Windows-Seite gelesen"


def test_transfer_detects_lost_symlinks(tree: ProfileTree) -> None:
    """Kaemen weniger Verknuepfungen an, liessen sich keine Dienste aktivieren."""
    from archcustomiser.core.build.wsl_build import WslPaths, transfer_profile

    fake = FakeWsl()
    fake.responses = {"find": wsl.WslResult(0, "0\n", "")}   # nichts angekommen
    paths = WslPaths(
        root=PurePosixPath("/home/jason/x"),
        profile=PurePosixPath("/home/jason/x/profile"),
        work=PurePosixPath("/home/jason/x/work"),
        out=PurePosixPath("/home/jason/x/out"),
    )
    with pytest.raises(wsl.WslError) as info:
        transfer_profile(fake, tree, paths, "flos")
    assert "Verknuepfungen" in str(info.value)


def test_transfer_removes_the_temporary_archive(tree: ProfileTree, tmp_path) -> None:
    from archcustomiser.core.build.wsl_build import WslPaths, transfer_profile

    fake = FakeWsl()
    fake.responses = {"find": wsl.WslResult(0, "2\n", "")}
    paths = WslPaths(
        root=PurePosixPath("/home/jason/x"),
        profile=PurePosixPath("/home/jason/x/profile"),
        work=PurePosixPath("/home/jason/x/work"),
        out=PurePosixPath("/home/jason/x/out"),
    )
    before = set(Path(__import__("tempfile").gettempdir()).glob("flos-profil-*.tar.gz"))
    transfer_profile(fake, tree, paths, "flos")
    after = set(Path(__import__("tempfile").gettempdir()).glob("flos-profil-*.tar.gz"))
    assert after == before, "das Zwischenarchiv blieb liegen"


def test_paths_stay_posix(tree: ProfileTree) -> None:
    """Alle Pfade in der Verteilung muessen Linux-Pfade bleiben."""
    from archcustomiser.core.build.wsl_build import prepare_paths

    fake = FakeWsl()
    paths = prepare_paths(fake, "FLOS Gaming!")
    for value in paths.as_strings():
        assert value.startswith("/home/jason/")
        assert "\\" not in value
    # Der Name wird bereinigt, bevor er in einen Pfad wandert.
    assert "!" not in str(paths.root) and " " not in str(paths.root)


def test_cleanup_removes_the_work_directory() -> None:
    from archcustomiser.core.build.wsl_build import WslPaths, cleanup

    fake = FakeWsl()
    paths = WslPaths(
        root=PurePosixPath("/home/jason/x"),
        profile=PurePosixPath("/home/jason/x/profile"),
        work=PurePosixPath("/home/jason/x/work"),
        out=PurePosixPath("/home/jason/x/out"),
    )
    cleanup(fake, paths, keep_work_dir=False)
    removed = [" ".join(call) for call in fake.calls if call[0] == "rm"]
    assert any("/home/jason/x/work" in call for call in removed)
    assert any("/home/jason/x/profile" in call for call in removed)


def test_cleanup_can_keep_the_work_directory() -> None:
    from archcustomiser.core.build.wsl_build import WslPaths, cleanup

    fake = FakeWsl()
    paths = WslPaths(
        root=PurePosixPath("/home/jason/x"),
        profile=PurePosixPath("/home/jason/x/profile"),
        work=PurePosixPath("/home/jason/x/work"),
        out=PurePosixPath("/home/jason/x/out"),
    )
    cleanup(fake, paths, keep_work_dir=True)
    removed = [" ".join(call) for call in fake.calls if call[0] == "rm"]
    assert not any("/home/jason/x/work" in call for call in removed)


def test_iso_is_copied_back_to_windows(tmp_path) -> None:
    fake = FakeWsl()
    target = WslExecutionTarget(fake)
    destination = tmp_path / "flos-1.0-x86_64.iso"
    target.fetch_iso("/home/jason/x/out/flos-1.0-x86_64.iso", destination)
    copies = [call for call in fake.calls if call[0] == "cp"]
    assert copies, "die ISO wurde nicht zurueckkopiert"
    assert "/mnt/" in copies[0][-1], "das Ziel muss die Windows-Seite sein"


# ---------------------------------------------------------------------------
# Vorabpruefung
# ---------------------------------------------------------------------------


def test_wsl_preflight_checks_the_distribution_not_windows(tmp_path) -> None:
    """Auf Windows nach mkarchiso zu suchen waere irrefuehrend."""
    from archcustomiser.core.build.preflight import run_wsl_preflight

    report = run_wsl_preflight(FakeWsl(), tmp_path, installed_mb=3000)
    names = [check.name for check in report.checks]
    assert "Linux-Verteilung" in names
    assert "archiso" in names
    assert "Betriebssystem" not in names
    assert report.ok


def test_wsl_preflight_blocks_without_archiso(tmp_path) -> None:
    from archcustomiser.core.build.preflight import run_wsl_preflight

    fake = FakeWsl()
    fake.has_command = lambda name: name != "mkarchiso"   # type: ignore[assignment]
    report = run_wsl_preflight(fake, tmp_path, installed_mb=3000)
    assert not report.ok
    assert any("archiso" in check.name for check in report.blocking)


# ---------------------------------------------------------------------------
# Umgebungsvariablen ueber die Systemgrenze
# ---------------------------------------------------------------------------


def test_source_date_epoch_reaches_the_distribution() -> None:
    """Auf Windows gesetzte Variablen erreichen WSL nicht von selbst.

    mkarchiso liest SOURCE_DATE_EPOCH ausdruecklich aus der Umgebung. Ohne
    Weitergabe setzte es einen eigenen Zeitstempel -- und ein wiederverwendetes
    Arbeitsverzeichnis fror den alten ein.
    """
    from archcustomiser.core.build.runner import MkarchisoRunner

    runner = MkarchisoRunner(
        "/home/jason/profil",
        "/home/jason/work",
        "/home/jason/out",
        source_date_epoch=1735689600,
        target=WslExecutionTarget(FakeWsl()),
    )
    argv = runner.build_argv()
    assert "env" in argv
    assert "SOURCE_DATE_EPOCH=1735689600" in argv
    assert argv.index("env") < argv.index("mkarchiso")


def test_local_build_does_not_use_env_prefix() -> None:
    """Lokal erbt der Prozess die Umgebung -- ein env-Praefix waere Ballast."""
    from archcustomiser.core.build.runner import MkarchisoRunner

    runner = MkarchisoRunner(
        "/tmp/profil", "/tmp/work", "/tmp/out",
        source_date_epoch=1735689600,
        executable="/usr/bin/mkarchiso",
    )
    argv = runner.build_argv()
    assert argv[0] == "/usr/bin/mkarchiso"
    assert "env" not in argv
    # Weitergereicht wird sie trotzdem -- ueber die Prozessumgebung.
    assert runner.environment()["SOURCE_DATE_EPOCH"] == "1735689600"


def test_control_characters_in_env_are_refused() -> None:
    """Ein Zeilenumbruch im Wert wuerde die Argumentliste zerreissen."""
    target = WslExecutionTarget(FakeWsl())
    with pytest.raises(ValueError):
        target.wrap(["mkarchiso"], env={"X": "eins\nzwei"})
    with pytest.raises(ValueError):
        target.wrap(["mkarchiso"], env={"X": "mit\x00Nullbyte"})


def test_invalid_env_name_is_refused() -> None:
    target = WslExecutionTarget(FakeWsl())
    for name in ("2FOO", "mit-strich", "mit leer", ""):
        with pytest.raises(ValueError):
            target.wrap(["mkarchiso"], env={name: "wert"})


def test_wsl_cwd_is_a_translatable_drive() -> None:
    """Sonst meldet wsl.exe 'Failed to translate' in den ersten Logzeilen.

    Folgenlos, sieht aber wie ein Fehler aus -- ausgerechnet dort, wo man nach
    Ursachen sucht.
    """
    import os

    target = WslExecutionTarget(FakeWsl())
    cwd = target.cwd()
    assert cwd is not None
    if os.name == "nt":
        assert cwd[1:] == ":" + os.sep


def test_wsl_strips_untranslatable_path_entries() -> None:
    """Netzlaufwerke im PATH kann WSL nicht auf die Linux-Seite abbilden."""
    import os

    target = WslExecutionTarget(FakeWsl())
    env = {"PATH": os.pathsep.join([r"C:\Windows", r"U:\bin", r"C:\tools"])}
    cleaned = target.sanitize_environment(env)
    entries = cleaned["PATH"].split(os.pathsep)
    if os.name == "nt":
        # U: existiert auf einem Testrechner meist nicht -- dann gilt es als
        # nicht lokal und faellt heraus.
        assert r"C:\Windows" in entries
    else:
        assert entries == [r"C:\Windows", r"U:\bin", r"C:\tools"]


def test_local_target_leaves_the_environment_alone() -> None:
    env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"}
    assert LocalTarget().sanitize_environment(env) == env


def test_sanitize_without_path_is_harmless() -> None:
    target = WslExecutionTarget(FakeWsl())
    assert target.sanitize_environment({"LC_ALL": "C"}) == {"LC_ALL": "C"}


def test_linux_iso_path_is_never_mangled_by_pathlib() -> None:
    """Der Fehler, an dem der erste echte Bau gescheitert ist.

    Der Runner fand die fertige ISO unter /root/.../x.iso, schickte den Pfad
    aber durch pathlib.Path -- unter Windows wurden daraus Backslashes, und cp
    meldete "cannot stat". Die ISO war fertig gebaut, nur nicht mehr
    auffindbar.
    """
    from archcustomiser.core.build.runner import BuildResult, MkarchisoRunner

    fake = FakeWsl()
    fake.responses = {
        "test -f": wsl.WslResult(0, "", ""),
    }
    target = WslExecutionTarget(fake)
    found = target.find_iso("/root/.cache/archcustomiser/miniarch/out", "miniarch-1.0-x86_64.iso")
    assert found is not None
    assert found.startswith("/root/"), found
    assert "\\" not in found, "der Linux-Pfad wurde verbogen"

    # Und der Weg durch BuildResult darf ihn ebenfalls nicht anfassen.
    result = BuildResult(returncode=0, iso_path=None, duration_seconds=1.0, iso_location=found)
    assert result.succeeded
    assert result.iso_location == found


def test_fetch_iso_receives_an_unmangled_linux_path(tmp_path) -> None:
    fake = FakeWsl()
    target = WslExecutionTarget(fake)
    quelle = "/root/.cache/archcustomiser/miniarch/out/miniarch-1.0-x86_64.iso"
    target.fetch_iso(quelle, tmp_path / "miniarch-1.0-x86_64.iso")

    kopien = [call for call in fake.calls if call[0] == "cp"]
    assert kopien, "es wurde nicht kopiert"
    assert quelle in kopien[0], f"die Quelle wurde veraendert: {kopien[0]}"


def test_cleanup_keeps_the_iso_until_it_was_fetched() -> None:
    """Sonst waere die Arbeit einer halben Stunde weg."""
    from archcustomiser.core.build.wsl_build import WslPaths, cleanup

    fake = FakeWsl()
    paths = WslPaths(
        root=PurePosixPath("/root/x"),
        profile=PurePosixPath("/root/x/profile"),
        work=PurePosixPath("/root/x/work"),
        out=PurePosixPath("/root/x/out"),
    )
    cleanup(fake, paths, remove_output=False)
    entfernt = [" ".join(c) for c in fake.calls if c[0] == "rm"]
    assert not any("/root/x/out" in c for c in entfernt)


def test_cleanup_removes_the_iso_once_it_is_on_windows() -> None:
    """Ohne das laege jede ISO doppelt und die WSL-Platte liefe voll."""
    from archcustomiser.core.build.wsl_build import WslPaths, cleanup

    fake = FakeWsl()
    paths = WslPaths(
        root=PurePosixPath("/root/x"),
        profile=PurePosixPath("/root/x/profile"),
        work=PurePosixPath("/root/x/work"),
        out=PurePosixPath("/root/x/out"),
    )
    cleanup(fake, paths, remove_output=True)
    entfernt = [" ".join(c) for c in fake.calls if c[0] == "rm"]
    assert any("/root/x/out" in c for c in entfernt)
