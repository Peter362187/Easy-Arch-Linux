"""Der vollstaendige Ablauf vom Wizard zur fertigen ISO.

Fuenf Schritte, jeder mit eigenem Fortschrittsanteil:

1. **Vorabpruefung** -- Werkzeuge, Rechte, Plattenplatz, Dateisystem.
2. **Profil erzeugen** -- derselbe Generator wie beim Export.
3. **Profil schreiben** -- in ein temporaeres Verzeichnis unterhalb des
   Arbeitsverzeichnisses.
4. **mkarchiso** -- der lange Teil, rund 95 Prozent der Zeit.
5. **Aufraeumen** -- das Arbeitsverzeichnis, sofern gewuenscht.

Bewusst *ein* Ablauf und nicht zwei getrennte Knoepfe: die Profilerzeugung ist
Voraussetzung fuer den Build und dauert Millisekunden. Wer die ISO will, will
nicht vorher noch ein Verzeichnis auswaehlen.

Das Protokoll wird waehrend des Laufs geschrieben, nicht danach. Bei einem
Abbruch nach vierzig Minuten ist gerade das Protokoll das Einzige, was noch
hilft.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable

from ..archiso import GeneratedProfile, ProfileGenerator
from ..archiso.settings import derive_bootmodes
from ..archiso.errors import ProfileError
from ..catalog import Catalog
from ..config import BuildConfig
from ..resolver import Resolution
from ..secrets import SecretStore
from .errors import BuildCancelled, BuildError
from .preflight import PreflightReport
from .progress import ProgressState
from .runner import MAX_KEPT_LINES, BuildResult, MkarchisoRunner
from . import targets as targets_module
from .targets import BuildPaths, ExecutionTarget, LocalTarget

log = logging.getLogger(__name__)

# Wandert mit der Profilablage ins Ziel -- hier nur noch weitergereicht.
PROFILE_DIRNAME = targets_module.PROFILE_DIRNAME

# Wie lange der Bau-Faden auf das Ende des Abbruchs wartet, bevor er aufraeumt.
# Beim WSL-Ziel kostet ein Abbruch die Frist plus mehrere pkill-Aufrufe mit je
# 30 s Zeitlimit; laenger als zwei Minuten darf das Aufraeumen aber nicht
# blockieren, sonst steht die Oberflaeche auf "Wird abgebrochen ...".
CANCEL_WAIT_SECONDS = 120.0


class Step(str, Enum):
    PREFLIGHT = "preflight"
    GENERATE = "generate"
    WRITE = "write"
    MKARCHISO = "mkarchiso"
    CLEANUP = "cleanup"


# Anteil jedes Schrittes am Gesamtfortschritt. mkarchiso dominiert so deutlich,
# dass alles andere zusammen unter fuenf Prozent bleibt.
WEIGHTS: dict[Step, tuple[float, float]] = {
    Step.PREFLIGHT: (0.00, 0.01),
    Step.GENERATE: (0.01, 0.02),
    Step.WRITE: (0.02, 0.04),
    Step.MKARCHISO: (0.04, 0.98),
    Step.CLEANUP: (0.98, 1.00),
}

StepCallback = Callable[[Step, str], None]
ProgressCallback = Callable[[float, str, str], None]   # (0..1, Titel, Detail)
LineCallback = Callable[[str], None]


@dataclass(slots=True)
class BuildOutcome:
    profile: GeneratedProfile | None = None
    result: BuildResult | None = None
    log_path: Path | None = None
    preflight: PreflightReport | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def iso_path(self) -> Path | None:
        return self.result.iso_path if self.result else None

    @property
    def succeeded(self) -> bool:
        return self.result is not None and self.result.succeeded


class BuildController:
    """Fuehrt den gesamten Ablauf aus. Synchron und ohne Qt."""

    def __init__(
        self,
        catalog: Catalog,
        config: BuildConfig,
        resolution: Resolution,
        secrets: SecretStore | None = None,
        *,
        runner_factory: Callable[..., MkarchisoRunner] | None = None,
        target: ExecutionTarget | None = None,
    ) -> None:
        self.catalog = catalog
        self.config = config
        self.resolution = resolution
        self.secrets = secrets
        # Einhaengepunkt fuer die Tests: der Ablauf laesst sich damit
        # vollstaendig pruefen, ohne mkarchiso zu haben.
        self.runner_factory = runner_factory or MkarchisoRunner
        # Wo gebaut wird: hier, in einer WSL-Verteilung oder in einem
        # Container. Der Controller unterscheidet das nicht mehr -- er
        # ruft nur noch Protokollmethoden auf.
        self.target: ExecutionTarget = target or LocalTarget()
        self._paths: BuildPaths | None = None
        self._output_fetched = False
        self._runner: MkarchisoRunner | None = None
        self._cancelled = False
        # Gesetzt heisst: es laeuft gerade kein Abbruch. cancel() loescht das
        # Ereignis, bevor es den Merker setzt, und setzt es am Ende wieder --
        # der Bau-Faden wartet dazwischen, bevor er aufraeumt. Sonst loescht er
        # unter der laufenden Nachkontrolle weg, und die haelt das eigene
        # Aufraeum-rm fuer einen ueberlebenden Bauprozess.
        #
        # Anfangs gesetzt: ein BuildCancelled ohne vorherigen cancel()-Aufruf
        # (der Runner kann es selbst werfen) darf nicht auf ein Ereignis
        # warten, das niemand mehr setzt.
        self._cancel_done = threading.Event()
        self._cancel_done.set()
        # Dieselbe Ueberlegung wie im Runner: zwischen "Runner bauen" und
        # "Runner eintragen" darf kein Abbruch verlorengehen.
        self._lock = threading.Lock()
        # Nur das Ende wird behalten -- dort steht der Fehler. Vorher hielten
        # Runner und Controller die vollstaendige Ausgabe parallel im Speicher.
        self._lines: deque[str] = deque(maxlen=MAX_KEPT_LINES)

    # -- oeffentlich ----------------------------------------------------------
    def preflight(self, work_dir: Path, out_dir: Path) -> PreflightReport:
        """Prueft dort, wo tatsaechlich gebaut wird.

        Bei einem Bau in WSL oder im Container waere eine Pruefung des
        aufrufenden Systems irrefuehrend -- dort fehlt mkarchiso zwangslaeufig,
        ohne dass das ein Hindernis waere. Welche Befunde auf dem Wirt gelten
        und welche drueben, weiss das jeweilige Ziel.
        """
        return self.target.preflight(
            work_dir,
            out_dir,
            installed_mb=self.resolution.estimated_size_mb,
            # Damit die Pruefung weiss, welche Bootlader-Werkzeuge ueberhaupt
            # gebraucht werden -- grub-mkstandalone etwa nur bei uefi.grub.
            bootmodes=derive_bootmodes(self.config),
        )

    def run(
        self,
        work_dir: Path,
        out_dir: Path,
        *,
        keep_work_dir: bool = False,
        on_step: StepCallback | None = None,
        on_progress: ProgressCallback | None = None,
        on_line: LineCallback | None = None,
        skip_preflight: bool = False,
    ) -> BuildOutcome:
        outcome = BuildOutcome()
        started = time.monotonic()
        work_dir = Path(work_dir)
        out_dir = Path(out_dir)

        # Reste des vorigen Laufs raeumen. Ohne das schrieb ein zweiter Lauf
        # auf derselben Instanz die Ausgabe des ersten in sein Protokoll.
        #
        # ``_cancelled`` bleibt bewusst stehen: ein Abbruch gilt auch, wenn er
        # vor ``run()`` kam -- die Oberflaeche nutzt genau das, um einen Build
        # abzublasen, der noch nicht angelaufen ist. Wer nach einem Abbruch
        # erneut bauen will, nimmt eine neue Instanz; die Oberflaeche tut das
        # ohnehin je Build.
        self._lines.clear()
        self._output_fetched = False
        self._paths = None
        with self._lock:
            self._runner = None

        try:
            # 1 -- Vorabpruefung
            self._announce(on_step, on_progress, Step.PREFLIGHT, "Umgebung wird geprueft")
            report = self.preflight(work_dir, out_dir)
            outcome.preflight = report
            outcome.warnings.extend(check.detail for check in report.warnings)
            if not skip_preflight:
                report.raise_if_blocked()
            self._check_cancel()

            # 2 -- Profil erzeugen
            self._announce(on_step, on_progress, Step.GENERATE, "Profil wird erzeugt")
            profile = ProfileGenerator(
                self.catalog, self.config, self.resolution, self.secrets
            ).generate()
            outcome.profile = profile
            outcome.warnings.extend(profile.warnings)
            self._check_cancel()

            # 3 -- Profil dorthin bringen, wo gebaut wird
            self._announce(on_step, on_progress, Step.WRITE, "Profil wird uebertragen")
            paths = self.target.prepare(profile.settings.iso_name, work_dir, out_dir)
            self._paths = paths
            self._place_profile(profile, paths, on_progress)
            self._check_cancel()

            # 4 -- der eigentliche Build
            self._announce(on_step, on_progress, Step.MKARCHISO, "ISO wird gebaut")
            runner = self.runner_factory(
                paths.profile,
                paths.work,
                paths.out,
                privilege_mode=report.privilege_mode,
                source_date_epoch=int(datetime.now(timezone.utc).timestamp()),
                target=self.target,
            )
            with self._lock:
                # Nach dem Eintragen noch einmal pruefen: kam der Abbruch
                # genau in diesem Fenster, sah cancel() den Runner noch nicht.
                self._runner = runner
            if self._cancelled:
                runner.cancel()
            result = runner.run(
                on_line=lambda line: self._record(line, on_line),
                on_progress=lambda state: self._mkarchiso_progress(on_progress, state),
                expected_iso=profile.iso_filename,
            )

            # Bei einem Bau in WSL liegt die ISO noch drueben. Erst nach dem
            # Holen gibt es einen Pfad auf diesem Rechner.
            if result.iso_location:
                # Das Kopieren dauert bei 2,5 GB Minuten. Ein Abbruch trifft
                # dabei das cp -- und hinterliess frueher eine abgeschnittene
                # Datei unter dem erwarteten ISO-Namen, gemeldet als
                # "fehlgeschlagen" statt "abgebrochen".
                self._check_cancel()
                try:
                    result.iso_path = self.target.fetch_iso(
                        result.iso_location, out_dir / profile.iso_filename
                    )
                except BuildError:
                    self._check_cancel()
                    raise
                self._check_cancel()
                # Erst jetzt darf die Kopie drueben weg.
                self._output_fetched = True
            outcome.result = result

            # 5 -- Aufraeumen
            self._announce(on_step, on_progress, Step.CLEANUP, "Aufraeumen")
            self._cleanup_all(paths, keep_work_dir=keep_work_dir)
            if on_progress is not None:
                on_progress(1.0, "Fertig", f"{result.size_mb:.0f} MB")

        except BuildCancelled:
            # Vor dem Werfen noch aufraeumen. Frueher wurde Schritt 5 bei einem
            # Abbruch uebersprungen -- ausgerechnet in dem Fall, in dem am
            # meisten liegenbleibt.
            #
            # Zuerst aber warten, bis cancel() fertig ist. Beide laufen in
            # getrennten Faeden: sobald pkill den Bau beendet, kehrt der
            # Bau-Faden zurueck und beginnt mit 'rm -rf <arbeitsverzeichnis>'.
            # Das Kill-Muster ist genau dieser Pfad -- die Nachkontrolle sah
            # also das eigene Aufraeum-rm, eskalierte auf KILL und liess ein
            # halb geloeschtes Verzeichnis zurueck.
            self._cancel_done.wait(timeout=CANCEL_WAIT_SECONDS)
            if self._paths is not None:
                try:
                    self._cleanup_all(self._paths, keep_work_dir=keep_work_dir)
                except Exception:
                    log.debug("Aufraeumen nach Abbruch fehlgeschlagen", exc_info=True)
            outcome.log_path = self._write_log(outcome, cancelled=True)
            raise
        except (BuildError, ProfileError) as exc:
            # Frueher wurde hier nur das Protokoll geschrieben. Ein in
            # mksquashfs gescheiterter Bau hinterliess damit 10 bis 30 GB
            # Arbeitsverzeichnis -- bei WSL in der virtuellen Platte, die nur
            # waechst. Die Vorabpruefung meldete das beim naechsten Mal als
            # "Reste frueherer Bauten": ein Symptom genau dieser Luecke.
            #
            # Wer den Zustand untersuchen will, setzt keep_work_dir in der
            # Vorabpruefung; das Profilverzeichnis wandert dann ebenfalls nicht.
            if self._paths is not None:
                try:
                    self._cleanup_all(self._paths, keep_work_dir=keep_work_dir)
                except Exception:
                    log.debug("Aufraeumen nach Fehlschlag fehlgeschlagen", exc_info=True)
            outcome.log_path = self._write_log(outcome, error=exc)
            raise
        except Exception as exc:
            # Ein Ziel darf eine unerwartete Ausnahme werfen -- ContainerError
            # etwa erbt nicht von BuildError, ebenso die ValueError aus wrap()
            # und prepare(). Ohne diesen Zweig verliesse sie run() ohne
            # Protokoll und ohne Aufraeumen.
            if self._paths is not None:
                try:
                    self._cleanup_all(self._paths, keep_work_dir=keep_work_dir)
                except Exception:
                    log.debug("Aufraeumen nach Fehlschlag fehlgeschlagen", exc_info=True)
            outcome.log_path = self._write_log(outcome, error=exc)
            raise
        else:
            outcome.log_path = self._write_log(
                outcome, duration=time.monotonic() - started
            )
            return outcome
        finally:
            with self._lock:
                self._runner = None

    def cancel(self) -> None:
        # Erst das Ereignis loeschen, dann den Merker setzen: sonst sieht der
        # Bau-Faden den Merker in genau dem Fenster dazwischen und raeumt auf,
        # waehrend die Nachkontrolle noch laeuft.
        self._cancel_done.clear()
        self._cancelled = True
        try:
            with self._lock:
                runner = self._runner
            if runner is not None:
                runner.cancel()
        finally:
            # Auch bei einem Fehler im Abbruch: der Bau-Faden darf nicht
            # unbegrenzt warten.
            self._cancel_done.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    # -- intern ---------------------------------------------------------------
    def _check_cancel(self) -> None:
        if self._cancelled:
            raise BuildCancelled()

    def _record(self, line: str, on_line: LineCallback | None) -> None:
        self._lines.append(line)
        if on_line is not None:
            on_line(line)

    def _announce(
        self,
        on_step: StepCallback | None,
        on_progress: ProgressCallback | None,
        step: Step,
        label: str,
    ) -> None:
        log.info("Schritt: %s -- %s", step.value, label)
        if on_step is not None:
            on_step(step, label)
        if on_progress is not None:
            on_progress(WEIGHTS[step][0], label, "")

    def _sub_progress(
        self,
        on_progress: ProgressCallback | None,
        step: Step,
        share: float,
        label: str,
        detail: str,
    ) -> None:
        if on_progress is None:
            return
        start, end = WEIGHTS[step]
        on_progress(start + (end - start) * min(1.0, max(0.0, share)), label, detail)

    def _mkarchiso_progress(
        self, on_progress: ProgressCallback | None, state: ProgressState
    ) -> None:
        if on_progress is None:
            return
        start, end = WEIGHTS[Step.MKARCHISO]
        on_progress(start + (end - start) * state.fraction, state.label, state.detail)

    def _place_profile(
        self,
        profile,
        paths: BuildPaths,
        on_progress: ProgressCallback | None,
    ) -> None:
        """Das Profil dorthin bringen, wo mkarchiso es findet.

        Wie das geschieht, weiss das Ziel: lokal als Verzeichnis, bei WSL als
        Archiv hinueber und dort ausgepackt, im Container ueber den Bind-Mount.
        """
        self.target.deliver_profile(
            profile.tree,
            paths,
            iso_name=profile.settings.iso_name,
            on_progress=lambda anteil, text: self._sub_progress(
                on_progress, Step.WRITE, anteil, "Profil wird uebertragen", text
            ),
        )

    def _cleanup_all(self, paths: BuildPaths, *, keep_work_dir: bool) -> None:
        """Aufraeumen ueberlaesst der Controller dem Ziel.

        Frueher stand hier eine isinstance-Verzweigung: WSL raeumte anders auf
        als lokal, und der Controller musste beide Faelle kennen. Bei einem
        dritten Ziel waeren daraus neun Sonderfaelle geworden.
        """
        self.target.discard(
            paths,
            keep_work_dir=keep_work_dir,
            remove_output=self._output_fetched,
        )


    def _write_log(
        self,
        outcome: BuildOutcome,
        *,
        duration: float = 0.0,
        cancelled: bool = False,
        error: Exception | None = None,
    ) -> Path | None:
        from ..logging_setup import write_build_log

        if cancelled:
            status = "Abgebrochen"
        elif error is not None:
            status = f"Fehlgeschlagen: {error}"
        elif outcome.result is not None:
            status = (
                f"Erfolgreich in {duration / 60:.1f} Minuten\n"
                f"ISO:    {outcome.result.iso_path}\n"
                f"Groesse: {outcome.result.size_mb:.0f} MB"
            )
        else:
            status = "Unbekannt"

        sections = {"Ergebnis": status}
        if outcome.preflight is not None:
            sections["Vorabpruefung"] = "\n".join(
                f"  [{'ok  ' if c.ok else 'FEHL'}] {c.name}: {c.detail}"
                for c in outcome.preflight.checks
            )
        if outcome.profile is not None:
            sections["profiledef.sh"] = outcome.profile.tree.text("profiledef.sh")
        if outcome.warnings:
            sections["Hinweise"] = "\n".join(f"  - {w}" for w in outcome.warnings)
        if self._lines:
            # Die vollstaendige Ausgabe -- ohne sie ist ein Fehlschlag nach
            # vierzig Minuten nicht nachvollziehbar.
            sections["Ausgabe von mkarchiso"] = "\n".join(self._lines)

        name = outcome.profile.settings.iso_name if outcome.profile else "build"
        return write_build_log(name, sections)
