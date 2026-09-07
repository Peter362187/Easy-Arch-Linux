"""Tests der Lastgrenze -- die Regel "ein Bau bekommt nie den ganzen Rechner".

Anlass ist ein echter Vorfall am 03.09.2026: ein Bau ueber WSL hat einen
Windows-Rechner mit zwoelf Kernen vollstaendig unbedienbar gemacht. Kein
Fenster liess sich mehr verschieben, der Abbrechen-Knopf war nicht mehr
erreichbar, nur ein harter Neustart half. Die letzte Zeile im Protokoll war
``Parallel mksquashfs: Using 12 processors``.

Diese Tests halten beide Haelften der Reparatur fest:

* Der Bau bekommt eine Kernbindung -- sonst nimmt mksquashfs alles.
* Der Abbruch trifft die Prozesse, die die Last erzeugen -- vorher suchte er
  nach "mkarchiso" und damit ausgerechnet nicht nach mksquashfs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from archcustomiser.core.build.limits import cpu_budget, host_cores, host_memory_gb
from archcustomiser.core.build.targets import WslExecutionTarget

# ---------------------------------------------------------------------------
# Die Regel selbst
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kerne, erwartet",
    [(1, 1), (2, 1), (3, 1), (4, 2), (6, 3), (8, 4), (12, 6), (16, 8), (32, 16)],
)
def test_the_budget_is_half_the_machine(kerne: int, erwartet: int) -> None:
    assert cpu_budget(kerne) == erwartet


@pytest.mark.parametrize("kerne", [1, 2, 3, 4, 5, 8, 12, 16, 24, 32, 64, 128])
def test_a_build_never_gets_the_whole_machine(kerne: int) -> None:
    """Die eine Zusicherung, um die es geht.

    Bei mehr als einem Kern muss mindestens einer frei bleiben -- sonst
    wiederholt sich der Vorfall.
    """
    erlaubt = cpu_budget(kerne)
    assert 1 <= erlaubt
    if kerne > 1:
        assert erlaubt < kerne, "kein Kern blieb fuer die Bedienung frei"


def test_an_impossible_core_count_is_refused() -> None:
    with pytest.raises(ValueError):
        cpu_budget(0)


def test_the_host_is_measurable() -> None:
    assert host_cores() >= 1
    speicher = host_memory_gb()
    # Auf ungewoehnlichen Systemen darf nichts behauptet werden -- aber wenn
    # etwas behauptet wird, muss es plausibel sein.
    assert speicher is None or 0.1 < speicher < 10_000


# ---------------------------------------------------------------------------
# Ein WSL-Ziel, das sich beobachten laesst
# ---------------------------------------------------------------------------


class FakeErgebnis:
    def __init__(self, stdout: str = "", ok: bool = True) -> None:
        self.ok = ok
        self.returncode = 0 if ok else 1
        self.stdout = stdout
        self.stderr = ""


class FakeWsl:
    """Eine Verteilung, die mitschreibt, was man ihr auftraegt."""

    distribution = "archlinux"

    def __init__(self, *, kerne: int = 12, taskset: bool = True, laeuft_noch: int = 0) -> None:
        self.aufrufe: list[list[str]] = []
        self.kerne = kerne
        self.taskset = taskset
        # Wie viele pgrep-Abfragen noch "es laeuft noch etwas" melden sollen.
        self.laeuft_noch = laeuft_noch

    def home(self) -> str:
        return "/root"

    def has_command(self, name: str) -> bool:
        return self.taskset if name == "taskset" else True

    #: Was ``du -sm`` auf dem Bauverzeichnis melden soll, in MB.
    reste_mb = 0

    def run(self, argv, **kwargs):
        argv = [str(item) for item in argv]
        self.aufrufe.append(argv)
        if argv[:1] == ["nproc"]:
            return FakeErgebnis(f"{self.kerne}\n")
        if argv[:1] == ["du"]:
            return FakeErgebnis(f"{self.reste_mb}\t{argv[-1]}\n")
        if argv[:1] == ["pgrep"]:
            if self.laeuft_noch > 0:
                self.laeuft_noch -= 1
                return FakeErgebnis("4711\n")
            return FakeErgebnis("", ok=False)
        return FakeErgebnis()

    def wrap(self, argv):
        return ["wsl.exe", "-d", self.distribution, "-e", *argv]


# ---------------------------------------------------------------------------
# Die Kernbindung
# ---------------------------------------------------------------------------


def test_the_build_is_pinned_to_half_the_cores() -> None:
    """Der Kern der Reparatur: mksquashfs darf nicht alle Kerne sehen."""
    ziel = WslExecutionTarget(FakeWsl(kerne=12))
    befehl = ziel.wrap(["mkarchiso", "-v"])

    assert "taskset" in befehl, "der Bau laeuft ohne jede Kerngrenze"
    stelle = befehl.index("taskset")
    assert befehl[stelle + 1 : stelle + 3] == ["-c", "0-5"], "12 Kerne -> 6 erlaubt"
    # Und mkarchiso muss danach immer noch aufgerufen werden.
    assert befehl[stelle + 3] == "mkarchiso"


def test_the_pinning_survives_the_environment_prefix() -> None:
    """taskset muss VOR env stehen, sonst erbt der Baum die Bindung nicht."""
    ziel = WslExecutionTarget(FakeWsl(kerne=8))
    befehl = ziel.wrap(["mkarchiso"], env={"SOURCE_DATE_EPOCH": "1700000000"})

    assert befehl.index("taskset") < befehl.index("env")
    assert "0-3" in befehl, "8 Kerne -> 4 erlaubt"


def test_the_distribution_is_asked_only_once() -> None:
    """wrap() darf nicht bei jedem Aufruf Unterprozesse starten."""
    wsl = FakeWsl(kerne=12)
    ziel = WslExecutionTarget(wsl)
    ziel.wrap(["mkarchiso"])
    ziel.wrap(["mkarchiso"])
    ziel.wrap(["mkarchiso"])

    assert sum(1 for argv in wsl.aufrufe if argv[:1] == ["nproc"]) == 1


def test_a_missing_taskset_does_not_stop_the_build() -> None:
    """Eine Schutzmassnahme darf nie zum Hindernis werden."""
    ziel = WslExecutionTarget(FakeWsl(taskset=False))
    befehl = ziel.wrap(["mkarchiso", "-v"])

    assert "taskset" not in befehl
    assert "mkarchiso" in befehl


def test_a_single_core_machine_is_left_alone() -> None:
    """Auf einem Kern gibt es nichts zu verteilen."""
    ziel = WslExecutionTarget(FakeWsl(kerne=1))
    assert "taskset" not in ziel.wrap(["mkarchiso"])


def test_an_unreadable_core_count_does_not_break_anything() -> None:
    class Kaputt(FakeWsl):
        def run(self, argv, **kwargs):
            if list(argv)[:1] == ["nproc"]:
                return FakeErgebnis("weiss nicht")
            return super().run(argv, **kwargs)

    befehl = WslExecutionTarget(Kaputt()).wrap(["mkarchiso"])
    assert "mkarchiso" in befehl


# ---------------------------------------------------------------------------
# Der Abbruch
# ---------------------------------------------------------------------------


def test_the_cancel_pattern_matches_mksquashfs_not_just_mkarchiso() -> None:
    """Der zweite Befund des Vorfalls.

    ``pkill -f mkarchiso`` trifft mksquashfs nicht -- dessen Befehlszeile
    enthaelt das Wort gar nicht. Das Arbeitsverzeichnis dagegen steht in der
    Befehlszeile jedes beteiligten Prozesses.
    """
    ziel = WslExecutionTarget(FakeWsl())
    ziel.prepare("customarch", Path("C:/egal"), Path("C:/egal"))

    muster = ziel.kill_pattern()
    assert "archcustomiser" in muster
    assert "customarch" in muster

    # Die echte Befehlszeile von mksquashfs aus dem Protokoll des Vorfalls --
    # sie enthaelt "mkarchiso" nicht, den Pfad aber sehr wohl.
    echte_zeile = (
        "mksquashfs /root/.cache/archcustomiser/customarch/work/x86_64/airootfs "
        "/root/.cache/archcustomiser/customarch/work/iso/customarch/x86_64/airootfs.sfs "
        "-comp zstd"
    )
    assert "mkarchiso" not in echte_zeile, "sonst pruefte dieser Test nichts"

    import re

    assert re.search(muster, echte_zeile), "der Abbruch geht wieder ins Leere"


def test_the_pattern_does_not_reach_beyond_the_build() -> None:
    """Ein pacman des Benutzers in derselben Verteilung bleibt unbehelligt."""
    ziel = WslExecutionTarget(FakeWsl())
    ziel.prepare("customarch", Path("C:/egal"), Path("C:/egal"))

    import re

    for fremd in ("pacman -Syu", "vim /etc/fstab", "mksquashfs /home/jason/foo /tmp/a.sfs"):
        assert not re.search(ziel.kill_pattern(), fremd), fremd


def test_dots_in_the_path_are_escaped() -> None:
    """/root/.cache -- ohne Maskierung traefe der Punkt jedes Zeichen."""
    ziel = WslExecutionTarget(FakeWsl())
    ziel.prepare("customarch", Path("C:/egal"), Path("C:/egal"))
    assert "\\." in ziel.kill_pattern()


def test_cancelling_before_the_start_still_works() -> None:
    """Vor prepare() gibt es kein Arbeitsverzeichnis -- und darf nicht krachen."""
    wsl = FakeWsl()
    ziel = WslExecutionTarget(wsl)
    ziel.cancel_run(None, grace_seconds=0.01)
    assert any("pkill" in argv for argv in wsl.aufrufe)


def test_a_stubborn_build_is_killed_hard() -> None:
    """Reagiert drueben nichts auf TERM, muss KILL folgen."""
    wsl = FakeWsl(laeuft_noch=5)
    ziel = WslExecutionTarget(wsl)
    ziel.prepare("customarch", Path("C:/egal"), Path("C:/egal"))
    ziel.cancel_run(None, grace_seconds=0.05)

    signale = [argv[1] for argv in wsl.aufrufe if argv[:1] == ["pkill"]]
    assert "-TERM" in signale
    assert "-KILL" in signale, "ein haengender Bau wurde nie hart beendet"


def test_a_cooperative_build_is_not_killed_hard() -> None:
    """Wer auf TERM hoert, wird nicht zusaetzlich erschossen."""
    wsl = FakeWsl(laeuft_noch=0)
    ziel = WslExecutionTarget(wsl)
    ziel.prepare("customarch", Path("C:/egal"), Path("C:/egal"))
    ziel.cancel_run(None, grace_seconds=5.0)

    signale = [argv[1] for argv in wsl.aufrufe if argv[:1] == ["pkill"]]
    assert signale == ["-TERM"]


# ---------------------------------------------------------------------------
# Der Container-Weg braucht dieselbe Zusicherung
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Was der Benutzer vor dem Start erfaehrt
# ---------------------------------------------------------------------------


def _bericht_mit(wsl: FakeWsl):
    from archcustomiser.core.build.preflight import PreflightReport

    ziel = WslExecutionTarget(wsl)
    bericht = PreflightReport()
    ziel._report_load(bericht)
    ziel._report_leftovers(bericht)
    return bericht


def test_the_user_is_told_what_the_build_will_take() -> None:
    """Ohne diese Zeile erlebt er nur, dass sein Rechner zaeh wird."""
    bericht = _bericht_mit(FakeWsl(kerne=12))
    last = [c for c in bericht.checks if c.name == "Rechenlast"]
    assert last, "die Vorabpruefung schweigt zur Rechenlast"
    assert "6 von 12" in last[0].detail
    assert last[0].ok


def test_a_build_without_a_limit_is_flagged_as_a_warning() -> None:
    bericht = _bericht_mit(FakeWsl(taskset=False))
    last = [c for c in bericht.checks if c.name == "Rechenlast"][0]
    assert not last.ok, "eine ungebremste Bauumgebung muss auffallen"
    assert not last.fatal, "sie darf aber nichts blockieren"


def test_leftovers_from_a_crashed_build_are_reported() -> None:
    """Nach dem Vorfall standen 5,3 GB in der virtuellen Platte."""
    wsl = FakeWsl()
    wsl.reste_mb = 5400
    bericht = _bericht_mit(wsl)

    reste = [c for c in bericht.checks if "Reste" in c.name]
    assert reste, "5,3 GB Reste blieben unbemerkt"
    assert "5.3 GB" in reste[0].detail
    assert "rm -rf" in reste[0].detail, "ohne Befehl kann der Benutzer nichts tun"
    assert not reste[0].fatal, "Reste duerfen den naechsten Bau nicht verhindern"


def test_a_clean_distribution_is_not_nagged() -> None:
    wsl = FakeWsl()
    wsl.reste_mb = 1
    assert not [c for c in _bericht_mit(wsl).checks if "Reste" in c.name]


def test_the_container_gets_a_cpu_limit() -> None:
    from archcustomiser.core.build.targets import ContainerExecutionTarget

    class FakeContainer:
        engine = "podman"
        image = "localhost/archcustomiser-archiso:latest"

    befehl = ContainerExecutionTarget(FakeContainer()).wrap(["mkarchiso", "-v"])

    if host_cores() > 1:
        assert "--cpus" in befehl, "der Container darf nicht den ganzen Rechner nehmen"
        erlaubt = befehl[befehl.index("--cpus") + 1]
        assert int(erlaubt) == cpu_budget(host_cores())
    assert "mkarchiso" in befehl
