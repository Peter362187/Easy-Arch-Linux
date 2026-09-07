"""Prozessaufrufe -- die einzige Stelle mit ``subprocess``.

Als Protokoll formuliert, damit Tests auf Windows einen ``FakeRunner``
einsetzen und die Argumentliste pruefen koennen, ohne pacman zu haben.

Drei Regeln sind hier fest verdrahtet und koennen vom Aufrufer nicht
aufgeweicht werden:

* ``shell=False``. Es gibt keinen Weg, eine Shell einzuschalten.
* Die Argumentliste ist eine Liste. Ein String waere ein Programmierfehler und
  wird abgelehnt.
* Feste Umgebung mit ``LC_ALL=C``. Sonst haengt das Parsen der Ausgabe von der
  Spracheinstellung des Benutzers ab.
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from .errors import PacmanInvocationError

log = logging.getLogger(__name__)

MAX_STDERR_TAIL = 4096


@dataclass(frozen=True, slots=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def raise_for_status(self) -> CommandResult:
        if not self.ok:
            raise PacmanInvocationError(self.returncode, self.stderr[-MAX_STDERR_TAIL:])
        return self


class Runner(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float = 60.0,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult: ...


class SubprocessRunner:
    """Die einzige Umsetzung mit echtem Prozessstart."""

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float = 60.0,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        if isinstance(argv, (str, bytes)):
            raise TypeError(
                "argv muss eine Liste sein. Ein String wuerde eine Shell-Interpretation "
                "nahelegen, die hier bewusst nicht existiert."
            )
        arguments = [str(item) for item in argv]
        if not arguments:
            raise ValueError("leere Argumentliste")

        environment = {
            "LC_ALL": "C",
            "LANG": "C",
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", "/tmp"),
        }
        if env:
            environment.update(env)

        log.debug("Aufruf: %s", " ".join(arguments))
        try:
            completed = subprocess.run(
                arguments,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                shell=False,
                env=environment,
            )
        except FileNotFoundError as exc:
            raise PacmanInvocationError(127, f"{arguments[0]}: nicht gefunden ({exc})") from exc
        except subprocess.TimeoutExpired as exc:
            raise PacmanInvocationError(
                124, f"{arguments[0]}: Zeitueberschreitung nach {timeout}s"
            ) from exc
        except OSError as exc:
            raise PacmanInvocationError(1, f"{arguments[0]}: {exc}") from exc

        return CommandResult(
            argv=tuple(arguments),
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )
