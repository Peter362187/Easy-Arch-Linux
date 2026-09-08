"""Der Repo-Index: Nachschlagewerk fuer Namen, Gruppen und virtuelle Pakete.

Drei Namensraeume, in einem Durchgang aus denselben Rohdaten aufgebaut. Genau
darin liegt der Vorteil gegenueber Einzelabfragen: ``plasma`` ist keine Option
mit Paketnamen, sondern eine Gruppe mit 70 Mitgliedern, und ``ttf-font`` ist
gar kein Paket, sondern eine Rolle, die ein Dutzend Schriftpakete ausfuellt.
Beides ist ueber eine Namenssuche prinzipiell nicht beantwortbar.

Repo-Reihenfolge: bei Namensgleichheit gewinnt das zuerst genannte Repository,
genau wie pacman es tut.
"""

from __future__ import annotations

import difflib
import logging
from collections.abc import Iterable, Sequence

from .models import IndexMetadata, PackageInfo

log = logging.getLogger(__name__)

SUGGESTION_CUTOFF = 0.72
MAX_SUGGESTIONS = 3


class RepoIndex:
    """Unveraenderlicher Index ueber mehrere Repositories."""

    __slots__ = ("_by_group", "_by_name", "_by_provide", "_names", "meta")

    def __init__(self, packages: Iterable[PackageInfo], meta: IndexMetadata) -> None:
        self.meta = meta
        by_name: dict[str, PackageInfo] = {}
        by_group: dict[str, list[str]] = {}
        by_provide: dict[str, list[str]] = {}

        for package in packages:
            # Erstes Repository gewinnt -- wie in pacman.conf.
            if package.name not in by_name:
                by_name[package.name] = package
            for group in package.groups:
                by_group.setdefault(group, []).append(package.name)
            for provide in package.provides:
                by_provide.setdefault(provide.name, []).append(package.name)

        self._by_name = by_name
        self._by_group = {key: tuple(sorted(set(value))) for key, value in by_group.items()}
        self._by_provide = {key: tuple(sorted(set(value))) for key, value in by_provide.items()}
        self._names = tuple(sorted(by_name))

    # -- Nachschlagen ---------------------------------------------------------
    def get(self, name: str) -> PackageInfo | None:
        return self._by_name.get(name)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._by_name

    def __len__(self) -> int:
        return len(self._by_name)

    def group_members(self, group: str) -> tuple[str, ...]:
        return self._by_group.get(group, ())

    def is_group(self, name: str) -> bool:
        return name in self._by_group

    def providers(self, virtual: str) -> tuple[str, ...]:
        """Pakete, die ``virtual`` bereitstellen -- ohne das Paket selbst."""
        found = self._by_provide.get(virtual, ())
        return tuple(name for name in found if name != virtual)

    def names(self) -> tuple[str, ...]:
        return self._names

    def groups(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_group))

    def close_matches(self, name: str, count: int = MAX_SUGGESTIONS) -> tuple[str, ...]:
        """Tippfehler-Vorschlaege ueber alle bekannten Namen und Gruppen."""
        if not name:
            return ()
        candidates = difflib.get_close_matches(
            name, self._names, n=count, cutoff=SUGGESTION_CUTOFF
        )
        if len(candidates) < count:
            # Praefixtreffer fangen abgeschnittene Eingaben ("libreoff"), die
            # difflib bei stark unterschiedlicher Laenge nicht findet.
            prefix = [
                other
                for other in self._names
                if other.startswith(name) and other not in candidates
            ]
            candidates.extend(prefix[: count - len(candidates)])
        return tuple(candidates)

    # -- Kennzahlen -----------------------------------------------------------
    @property
    def package_count(self) -> int:
        return len(self._by_name)

    @property
    def group_count(self) -> int:
        return len(self._by_group)

    @property
    def provide_count(self) -> int:
        return len(self._by_provide)

    def describe(self) -> str:
        return (
            f"{self.package_count} Pakete, {self.group_count} Gruppen, "
            f"{self.provide_count} bereitgestellte Namen aus "
            f"{', '.join(self.meta.repo_names)}"
        )


def build_index(
    repo_packages: Sequence[tuple[str, Sequence[PackageInfo]]], meta: IndexMetadata
) -> RepoIndex:
    """Fuehrt mehrere Repositories in der uebergebenen Reihenfolge zusammen."""
    ordered: list[PackageInfo] = []
    for _, packages in repo_packages:
        ordered.extend(packages)
    index = RepoIndex(ordered, meta)
    log.info("Paketindex aufgebaut: %s", index.describe())
    return index


EMPTY_METADATA = IndexMetadata(backend="none", arch="x86_64", repos=())
