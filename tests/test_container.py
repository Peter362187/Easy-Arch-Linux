"""Tests des Container-Ziels.

Nach dem Muster von ``test_wsl.py``, aber mit einer nachgebildeten Engine statt
einer nachgebildeten Verteilung. Kein Test startet einen Container -- geprueft
wird, dass die richtigen Aufrufe zusammengebaut werden.

Der Container ist der Weg fuer jedes Linux, das kein Arch ist: ``archiso`` ist
in keiner anderen Verteilung paketiert.
"""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

import pytest

from archcustomiser.core.build.container import (
    ContainerError,
    ContainerResult,
    ContainerTarget,
    container_name,
    find_engine,
)
from archcustomiser.core.build.targets import ContainerExecutionTarget


class FakeEngine:
    """Zeichnet jede Argumentliste auf und antwortet nach Vorgabe."""

    def __init__(self, antworten: dict[str, ContainerResult] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.eingaben: list[str | None] = []
        self.antworten = antworten or {}

    def __call__(self, argv, timeout=None, eingabe=None) -> ContainerResult:
        argv = [str(item) for item in argv]
        self.calls.append(argv)
        self.eingaben.append(eingabe)
        for schluessel, antwort in self.antworten.items():
            if schluessel in argv:
                return antwort
        return ContainerResult(0, stdout="true")

    def eingabe_zu(self, *teile: str) -> str | None:
        """Die Standardeingabe des Aufrufs, der alle Teile enthaelt."""
        for argv, eingabe in zip(self.calls, self.eingaben):
            if all(t in argv for t in teile):
                return eingabe
        return None

    def saw(self, *teile: str) -> bool:
        return any(all(t in argv for t in teile) for argv in self.calls)


@pytest.fixture
def engine() -> FakeEngine:
    return FakeEngine()


@pytest.fixture
def ziel(engine: FakeEngine) -> ContainerExecutionTarget:
    return ContainerExecutionTarget(ContainerTarget("podman", runner=engine))


@pytest.fixture
def vorbereitet(engine: FakeEngine) -> ContainerExecutionTarget:
    """Ein Ziel mit gesetzten POSIX-Pfaden, ohne Dateisystemarbeit.

    Das Container-Ziel weist Windows-Pfade ab -- zu Recht, denn ein Bind-Mount
    unter demselben Pfad ginge dort nicht. Fuer die Pruefung des Aufrufs
    braucht es aber gar kein echtes Verzeichnis.
    """
    ziel = ContainerExecutionTarget(ContainerTarget("podman", runner=engine))
    ziel._work_dir = PurePosixPath("/home/x/work")
    ziel._out_dir = PurePosixPath("/home/x/out")
    ziel._container_name = "archcustomiser-flos-1234"
    return ziel


# ---------------------------------------------------------------------------
# Der Aufruf
# ---------------------------------------------------------------------------


def test_the_call_is_privileged(vorbereitet) -> None:
    """Nicht wegen mkarchiso, sondern wegen pacstrap.

    Dessen chroot_setup haengt acht Dateisysteme in den Zielbaum ein und
    braucht dafuer CAP_SYS_ADMIN. Ohne --privileged scheitert der Bau am
    allerersten Mount.
    """
    argv = vorbereitet.wrap(["mkarchiso", "-v"])
    assert "--privileged" in argv


def test_the_container_gets_a_name(vorbereitet) -> None:
    """Ohne festen Namen gibt es beim Abbrechen nichts zu toeten."""
    argv = vorbereitet.wrap(["mkarchiso"])
    assert "--name" in argv
    name = argv[argv.index("--name") + 1]
    assert name.startswith("archcustomiser-")
    assert "flos" in name


def test_both_directories_are_mounted(vorbereitet) -> None:
    argv = vorbereitet.wrap(["mkarchiso"])
    mounts = [argv[i + 1] for i, teil in enumerate(argv) if teil == "-v"]
    assert len(mounts) == 2
    assert any("/home/x/work" in m for m in mounts)
    assert any("/home/x/out" in m for m in mounts)


def test_host_and_container_paths_are_identical(vorbereitet) -> None:
    """Der ganze Trick des Bind-Mounts.

    Weil beide Seiten denselben Pfad sehen, sind fuenf der sieben
    Protokollmethoden woertlich die von LocalTarget -- und die 166 Zeilen
    Uebertragungscode aus wsl_build.py entfallen ersatzlos.

    Mit festen POSIX-Pfaden geprueft, damit der Test unter Windows dasselbe
    aussagt wie unter Linux.
    """
    argv = vorbereitet.wrap(["mkarchiso"])
    for mount in [argv[i + 1] for i, t in enumerate(argv) if t == "-v"]:
        links, rechts = mount.split(":", 1)
        assert links == rechts.removesuffix(":Z"), "beide Seiten muessen gleich sein"


def test_windows_paths_are_refused(ziel) -> None:
    """Unter Windows staende links ein Laufwerksbuchstabe und rechts ein Pfad
    unterhalb von /mnt.

    Eine Zuordnung, die Docker Desktop selbst vornimmt -- hier produzierte sie
    nur Fehler. Windows hat mit WSL ohnehin den besseren Weg.
    """
    with pytest.raises(ValueError) as info:
        ziel.prepare("flos", Path("C:/Users/x/work"), Path("C:/Users/x/out"))
    assert "WSL" in str(info.value)


def test_environment_goes_through_dash_e(vorbereitet) -> None:
    """Ueber die Systemgrenze erbt der Prozess nichts."""
    argv = vorbereitet.wrap(["mkarchiso"], env={"SOURCE_DATE_EPOCH": "1735689600"})
    assert "-e" in argv
    assert "SOURCE_DATE_EPOCH=1735689600" in argv


def test_an_invalid_variable_name_is_refused(vorbereitet) -> None:
    """Sonst liesse sich ein weiteres Argument einschmuggeln."""
    with pytest.raises(ValueError):
        vorbereitet.wrap(["mkarchiso"], env={"BOESE; rm -rf /": "x"})


# ---------------------------------------------------------------------------
# Abbruch
# ---------------------------------------------------------------------------


def test_cancel_kills_the_container_not_the_client(vorbereitet, engine) -> None:
    """Der Befund, der den Umbau ausgeloest hat.

    ``terminate()`` auf den podman-Prozess traefe nur den Client; der Container
    mit dem laufenden pacstrap ueberlebt ihn im conmon-Baum.
    """
    vorbereitet.cancel_run(None, grace_seconds=0.01)
    assert engine.saw("kill"), "der Container wurde nicht beendet"


@pytest.mark.skipif(os.name == "nt", reason="Container-Ziel weist Windows-Pfade ab")
def test_discard_removes_the_container(ziel, engine, tmp_path) -> None:
    paths = ziel.prepare("flos", tmp_path / "w", tmp_path / "o")
    ziel.discard(paths, keep_work_dir=True, remove_output=True)
    assert engine.saw("rm"), "der Container blieb liegen"


@pytest.mark.skipif(os.name == "nt", reason="Container-Ziel weist Windows-Pfade ab")
def test_discard_never_removes_the_output(ziel, tmp_path) -> None:
    """Die Ausgabe liegt ueber den Bind-Mount schon am Zielort.

    Sie zu loeschen waere hier ein Fehler -- anders als bei WSL, wo die ISO
    drueben nur eine Kopie ist.
    """
    out = tmp_path / "o"
    paths = ziel.prepare("flos", tmp_path / "w", out)
    (out / "fertig.iso").write_bytes(b"x")
    ziel.discard(paths, keep_work_dir=False, remove_output=True)
    assert (out / "fertig.iso").exists()


# ---------------------------------------------------------------------------
# Abbild
# ---------------------------------------------------------------------------


def test_the_image_is_built_only_once(engine) -> None:
    """Das Abbild bei jedem Bau neu zu holen kostete jedes Mal hunderte MB."""
    engine.antworten = {"exists": ContainerResult(1)}
    container = ContainerTarget("podman", runner=engine)
    container.ensure_image()
    assert engine.saw("build")

    engine.calls.clear()
    engine.antworten = {"exists": ContainerResult(0)}
    container.ensure_image()
    assert not engine.saw("build"), "vorhandenes Abbild wurde neu gebaut"


def test_the_containerfile_actually_reaches_the_engine(engine) -> None:
    """Der Fund, der den Container-Weg unbenutzbar machte.

    ``build --file -`` liest das Containerfile von der Standardeingabe. Die
    wurde nie befuellt: die Engine las die Standardeingabe der Anwendung --
    aus einem Desktop-Start also nichts, aus einem Terminal blockierte sie bis
    zum Zeitlimit von einer halben Stunde. Ohne diesen Test bleibt der Fehler
    unsichtbar, weil kein Test je einen Container startet.
    """
    engine.antworten = {"exists": ContainerResult(1)}
    ContainerTarget("podman", runner=engine).ensure_image()

    eingabe = engine.eingabe_zu("build")
    assert eingabe, "das Containerfile erreicht die Engine nicht"
    assert eingabe.startswith("FROM docker.io/library/archlinux")
    assert "archiso" in eingabe


def test_the_build_context_is_not_the_current_directory(engine) -> None:
    """Der Kontext war '.', also das Arbeitsverzeichnis der Anwendung.

    podman uebertraegt ihn, docker schickt ihn vollstaendig an seinen Daemon.
    Steht die Anwendung im Heimatverzeichnis, sind das Gigabyte fremder
    Dateien -- fuer ein Abbild, das keine einzige davon braucht.
    """
    engine.antworten = {"exists": ContainerResult(1)}
    ContainerTarget("podman", runner=engine).ensure_image()

    aufruf = next(argv for argv in engine.calls if "build" in argv)
    assert aufruf[-1] != ".", "der Bau-Kontext ist weiterhin das CWD"


def test_a_failed_image_build_says_why(engine) -> None:
    engine.antworten = {
        "exists": ContainerResult(1),
        "build": ContainerResult(1, stderr="could not resolve host"),
    }
    container = ContainerTarget("podman", runner=engine)
    with pytest.raises(ContainerError) as info:
        container.ensure_image()
    assert "Internetverbindung" in info.value.user_message
    assert "resolve host" in info.value.technical


# ---------------------------------------------------------------------------
# Kleinigkeiten mit Wirkung
# ---------------------------------------------------------------------------


def test_the_container_name_is_safe_and_unique() -> None:
    name = container_name("FLOS Super/Edition 2026")
    assert "/" not in name and " " not in name
    # Zwei Programmfenster duerfen sich nicht gegenseitig den Container toeten.
    assert str(__import__("os").getpid()) in name


def test_a_missing_engine_is_reported_clearly(monkeypatch) -> None:
    monkeypatch.setattr(
        "archcustomiser.core.build.container.shutil.which", lambda _n: None
    )
    assert find_engine() is None
    with pytest.raises(ContainerError) as info:
        ContainerTarget()
    assert "podman" in info.value.user_message


def test_podman_is_preferred_over_docker(monkeypatch) -> None:
    """podman braucht keinen Dienst und laeuft als der aufrufende Benutzer."""
    monkeypatch.setattr(
        "archcustomiser.core.build.container.shutil.which",
        lambda name: f"/usr/bin/{name}",
    )
    assert find_engine() == "podman"


def test_selinux_mounts_are_labelled(vorbereitet, monkeypatch) -> None:
    """Ohne :Z scheitert der Bau auf Fedora mit "Permission denied"."""
    monkeypatch.setattr(
        "archcustomiser.core.build.container.selinux_active", lambda: True
    )
    argv = vorbereitet.wrap(["mkarchiso"])
    mounts = [argv[i + 1] for i, t in enumerate(argv) if t == "-v"]
    assert all(m.endswith(":Z") for m in mounts)


def test_without_selinux_there_is_no_label(vorbereitet, monkeypatch) -> None:
    monkeypatch.setattr(
        "archcustomiser.core.build.container.selinux_active", lambda: False
    )
    argv = vorbereitet.wrap(["mkarchiso"])
    mounts = [argv[i + 1] for i, t in enumerate(argv) if t == "-v"]
    assert not any(m.endswith(":Z") for m in mounts)


# ---------------------------------------------------------------------------
# Das Abbild entsteht auch wirklich
# ---------------------------------------------------------------------------


def test_the_image_is_ensured_before_the_profile_is_placed(vorbereitet, engine, tmp_path) -> None:
    """ensure_image() hatte keinen einzigen Aufrufer.

    Die Vorabpruefung kuendigte an, das Abbild werde beim ersten Mal erzeugt;
    tatsaechlich scheiterte der erste Bau auf einem Rechner ohne das lokale
    Abbild sofort am 'run'.
    """
    from archcustomiser.core.archiso.tree import ProfileTree
    from archcustomiser.core.build.targets import BuildPaths

    engine.antworten = {"exists": ContainerResult(1)}
    baum = ProfileTree()
    baum.add_file("profiledef.sh", "# leer", origin="test")

    paths = BuildPaths(
        profile=str(tmp_path / "profile"),
        work=str(tmp_path / "work"),
        out=str(tmp_path / "out"),
    )
    vorbereitet.deliver_profile(baum, paths, iso_name="flos")

    assert engine.saw("build"), "das Abbild wurde nicht gebaut"


def test_rootless_is_reported_instead_of_silently_failing(engine, tmp_path) -> None:
    """Erkannt wurde rootless immer -- gesagt wurde es nie.

    pacstrap haengt devtmpfs ein, und das geht in einem User-Namespace
    grundsaetzlich nicht. Ohne Hinweis lief der Benutzer minutenlang in einen
    Fehlschlag, dessen Ursache im Container nirgends steht.

    Der Hinweis gilt fuer den Fall OHNE Abbild. Liegt eines vor, wird nicht
    mehr auf das Anzeichen geschaut, sondern gemessen (siehe
    ``test_the_preflight_blocks_when_mounting_is_impossible``) -- eine Messung
    schlaegt ein Anzeichen. Ohne Abbild darf nicht gemessen werden, weil die
    Vorabpruefung sonst hunderte MB nachlaedt.
    """
    from archcustomiser.core.build.preflight import run_container_preflight

    engine.antworten = {"exists": ContainerResult(1)}      # Abbild fehlt noch
    ziel = ContainerTarget("podman", runner=engine)
    ziel.rootless = True
    bericht = run_container_preflight(ziel, tmp_path / "work", tmp_path / "out")

    rechte = [pruefung for pruefung in bericht.checks if pruefung.name == "Rechte"]
    assert rechte, "die Rechte-Pruefung fehlt"
    assert not rechte[0].ok
    assert "rootless" in rechte[0].detail
    assert not rechte[0].fatal, "eine Warnung, keine Sperre"


# ---------------------------------------------------------------------------
# Aus dem Zweig main uebernommen: drei Eigenschaften, die hier sonst
# ungeprueft blieben. Sie stammen aus dem ersten echten Container-Lauf am
# 07.09.2026 -- Fehler, die Attrappentests strukturell nicht finden, weil sie
# die FORM der Aufrufe pruefen und nicht ihre WIRKUNG.
# ---------------------------------------------------------------------------


def test_docker_is_not_declared_dead_when_it_is_running(monkeypatch) -> None:
    """Die Rootless-Frage wurde in podman-Vokabular gestellt.

    "{{.Host.Security.Rootless}}" ist podman-eigen. Dockers info-Vorlage
    arbeitet auf einer Struktur ohne Feld "Host": die Vorlage scheitert, docker
    endet mit einem Fehlercode, und detect() meldete "docker laeuft nicht" --
    auch bei laufendem Dienst. Auf macOS war der Container-Weg damit immer tot.
    """
    from archcustomiser.core.build import container as modul

    def docker(argv, timeout=None, eingabe=None) -> ContainerResult:
        """Verhaelt sich wie docker, nicht wie podman.

        Eine Vorlage mit ".Host" kennt docker nicht und quittiert sie mit einem
        Fehler. Eine Attrappe, die jede Vorlage beantwortet, bildete den Fehler
        gar nicht ab.
        """
        argv = [str(i) for i in argv]
        if argv[:2] == ["docker", "--version"]:
            return ContainerResult(0, stdout="Docker version 28.0.4")
        if "--format" in argv:
            if ".Host" in argv[argv.index("--format") + 1]:
                return ContainerResult(1, stderr="template parsing error")
            return ContainerResult(0, stdout="[name=seccomp,profile=builtin]")
        if "info" in argv:
            return ContainerResult(0, stdout="Server Version: 28.0.4")
        return ContainerResult(1)

    monkeypatch.setattr(modul, "find_engine", lambda: "docker")
    monkeypatch.setattr(modul, "_run", docker)
    status = modul.detect()

    assert status.engine == "docker"
    assert not status.problem, f"docker faelschlich fuer tot erklaert: {status.problem}"
    assert status.usable


def test_a_rootless_docker_daemon_is_recognised(monkeypatch) -> None:
    """docker meldet rootless nicht als 'true', sondern in den SecurityOptions."""
    from archcustomiser.core.build import container as modul

    def docker(argv, timeout=None, eingabe=None) -> ContainerResult:
        argv = [str(i) for i in argv]
        if argv[:2] == ["docker", "--version"]:
            return ContainerResult(0, stdout="Docker version 28.0.4")
        if "--format" in argv:
            if ".Host" in argv[argv.index("--format") + 1]:
                return ContainerResult(1, stderr="template parsing error")
            return ContainerResult(0, stdout="[name=seccomp name=rootless]")
        if "info" in argv:
            return ContainerResult(0, stdout="Server Version: 28.0.4")
        return ContainerResult(1)

    monkeypatch.setattr(modul, "find_engine", lambda: "docker")
    monkeypatch.setattr(modul, "_run", docker)

    assert modul.detect().rootless, "ein rootless laufender docker blieb unbemerkt"


def test_the_mount_probe_uses_devtmpfs(engine: FakeEngine) -> None:
    """devtmpfs ist der richtige Pruefstein.

    Es hat im Kernel kein FS_USERNS_MOUNT-Flag und laesst sich in einem
    Benutzer-Namensraum grundsaetzlich nicht einhaengen. Am 07.09.2026 auf
    echter Hardware nachgemessen: mit --privileged gelingt es, ohne nicht.
    """
    assert ContainerTarget("podman", runner=engine).can_mount_privileged()

    aufruf = next(argv for argv in engine.calls if "devtmpfs" in " ".join(argv))
    assert "--privileged" in aufruf, "ohne --privileged sagt die Probe nichts aus"
    assert "run" in aufruf


def test_a_container_that_cannot_mount_is_reported(engine: FakeEngine) -> None:
    engine.antworten = {"--privileged": ContainerResult(1, stderr="permission denied")}
    assert not ContainerTarget("podman", runner=engine).can_mount_privileged()


def test_the_preflight_blocks_when_mounting_is_impossible(engine: FakeEngine, tmp_path) -> None:
    """Rootless podman ist die Vorgabe auf jedem normalen Ubuntu.

    Dort gibt --privileged alle Faehigkeiten nur INNERHALB des
    Benutzer-Namensraums. Der Bau lief bis pacstrap und starb dort am ersten
    Mount -- nach Minuten, mit einer Meldung, die niemand deuten kann.
    """
    from archcustomiser.core.build.preflight import run_container_preflight

    engine.antworten = {
        "exists": ContainerResult(0),                       # Abbild ist da
        "--privileged": ContainerResult(1, stderr="operation not permitted"),
    }
    bericht = run_container_preflight(
        ContainerTarget("podman", runner=engine), tmp_path / "work", tmp_path / "out"
    )

    rechte = [c for c in bericht.checks if c.name == "Rechte"][0]
    assert not rechte.ok
    assert rechte.fatal, "ein Bau, der zwangslaeufig scheitert, darf nicht anlaufen"
    assert "sudo" in rechte.detail, "ohne Abhilfe steht der Benutzer davor"


def test_the_preflight_claims_nothing_without_an_image(engine: FakeEngine, tmp_path) -> None:
    """Ohne Abbild darf nicht geprueft werden -- das lueden hunderte MB nach."""
    from archcustomiser.core.build.preflight import run_container_preflight

    engine.antworten = {"exists": ContainerResult(1)}       # Abbild fehlt
    run_container_preflight(
        ContainerTarget("podman", runner=engine), tmp_path / "work", tmp_path / "out"
    )
    assert not engine.saw("--privileged"), "die Vorabpruefung hat einen Container gestartet"


def test_the_image_carries_every_tool_a_boot_mode_may_need() -> None:
    """Sonst entsteht eine Sackgasse.

    Beim ersten echten Lauf brach mkarchiso mit "grub-install is not available
    on this host" ab: archiso zieht grub nicht mit, das Programm bietet den
    Bootmodus uefi.grub aber an. Die Vorabpruefung haette daraufhin gesagt "das
    Abbild muss neu gebaut werden" -- und dabei waere wieder dasselbe Abbild
    ohne grub entstanden.
    """
    from archcustomiser.core.build.container import CONTAINERFILE
    from archcustomiser.core.environment import CONDITIONAL_TOOLS

    for _werkzeug, paket, zweck in CONDITIONAL_TOOLS.values():
        assert paket in CONTAINERFILE, (
            f"Das Container-Abbild bringt {paket} nicht mit ({zweck}) -- "
            f"ein Bau mit diesem Bootmodus koennte darin nie gelingen."
        )
