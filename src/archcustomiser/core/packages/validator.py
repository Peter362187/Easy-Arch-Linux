"""Einordnung eingegebener Paketnamen.

Die Reihenfolge bildet nach, wie pacman einen Namen selbst auffasst:
erst exaktes Paket (dazu zaehlen Meta-Pakete wie ``base-devel``), dann Gruppe,
dann virtuelles Paket.

Die wichtigste Regel des Moduls steht in ``classify``: ohne vollstaendigen
Index wird **nie** ``NOT_FOUND`` behauptet. Ein Netzausfall darf nicht als
"das Paket existiert nicht" erscheinen -- das waere eine falsche Diagnose, die
den Benutzer dazu bringt, einen korrekten Namen zu loeschen.
"""

from __future__ import annotations

import logging
from typing import Iterable, Sequence

from .index import RepoIndex
from .models import BackendProblem, EntryKind, IndexMetadata, Resolution, ValidationReport
from .names import InvalidPackageName, split_constraint, validate_name

log = logging.getLogger(__name__)


def classify(
    query: str,
    index: RepoIndex | None,
    *,
    degraded: bool = False,
    provider_choices: dict[str, str] | None = None,
) -> Resolution:
    """Ordnet einen einzelnen Namen ein."""
    raw = query.strip()
    base, constraint = split_constraint(raw)

    try:
        name = validate_name(base)
    except InvalidPackageName as exc:
        return Resolution(
            query=query,
            normalized=base,
            kind=EntryKind.INVALID_NAME,
            constraint=constraint,
            notes=(exc.reason,),
        )

    notes: list[str] = []
    if constraint:
        # archiso schreibt die Namen unveraendert in packages.x86_64; eine
        # Versionsangabe wuerde dort schlicht als Teil des Namens gelesen.
        notes.append(
            f"Die Versionsangabe {constraint!r} wird ignoriert -- eine "
            f"archiso-Paketliste kennt keine Versionsbindung."
        )

    if index is None or degraded:
        return Resolution(
            query=query,
            normalized=name,
            kind=EntryKind.UNVERIFIED,
            constraint=constraint,
            notes=tuple(notes),
        )

    package = index.get(name)
    if package is not None:
        return Resolution(
            query=query,
            normalized=name,
            kind=EntryKind.PACKAGE,
            repo=package.repo,
            version=package.version,
            description=package.description,
            constraint=constraint,
            notes=tuple(notes),
            installed_size=package.installed_size,
        )

    if index.is_group(name):
        members = index.group_members(name)
        return Resolution(
            query=query,
            normalized=name,
            kind=EntryKind.GROUP,
            members=members,
            constraint=constraint,
            notes=tuple(
                notes
                + [
                    "Gruppen werden zur Bauzeit von pacman aufgeloest und bleiben "
                    "deshalb als Gruppenname in der Paketliste stehen."
                ]
            ),
        )

    providers = index.providers(name)
    if len(providers) == 1:
        return Resolution(
            query=query,
            normalized=name,
            kind=EntryKind.PROVIDES_UNIQUE,
            members=providers,
            constraint=constraint,
            notes=tuple(notes),
        )
    if len(providers) > 1:
        chosen = (provider_choices or {}).get(name)
        if chosen and chosen in providers:
            return Resolution(
                query=query,
                normalized=name,
                kind=EntryKind.PROVIDES_UNIQUE,
                members=providers,
                chosen=chosen,
                constraint=constraint,
                notes=tuple(notes + [f"{chosen} wurde als Anbieter ausgewaehlt."]),
            )
        return Resolution(
            query=query,
            normalized=name,
            kind=EntryKind.PROVIDES_AMBIG,
            members=providers,
            constraint=constraint,
            notes=tuple(
                notes
                + [
                    "pacman wuerde hier nachfragen. Da der ISO-Build ohne Rueckfrage "
                    "laeuft, muss der Anbieter vorher feststehen."
                ]
            ),
        )

    fehlend = getattr(index.meta, "missing_repos", ())
    if fehlend:
        # Die zentrale Zusicherung der Schicht: ein Name gilt nur dann als
        # nicht vorhanden, wenn ein vollstaendiger Index vorliegt. Fiel
        # bisher ein Repository aus, wurde der Rest trotzdem als vollstaendig
        # behandelt -- und jedes Paket daraus blockierte den Weiter-Knopf.
        return Resolution(
            query=query,
            normalized=name,
            kind=EntryKind.UNVERIFIED,
            constraint=constraint,
            notes=tuple(
                notes
                + [
                    "Nicht pruefbar: die Paketdaten von "
                    + ", ".join(fehlend)
                    + " konnten nicht geladen werden."
                ]
            ),
        )

    return Resolution(
        query=query,
        normalized=name,
        kind=EntryKind.NOT_FOUND,
        suggestions=index.close_matches(name),
        constraint=constraint,
        notes=tuple(notes),
    )


def validate_all(
    queries: Iterable[str],
    index: RepoIndex | None,
    *,
    degraded: bool = False,
    provider_choices: dict[str, str] | None = None,
    problems: Sequence[BackendProblem] = (),
    meta: IndexMetadata | None = None,
) -> ValidationReport:
    """Prueft eine ganze Liste -- ohne einen einzigen Netzzugriff.

    Der Index liegt bereits im Speicher; ob fuenf oder fuenfhundert Namen
    geprueft werden, macht keinen messbaren Unterschied.
    """
    entries = tuple(
        classify(query, index, degraded=degraded, provider_choices=provider_choices)
        for query in queries
    )
    return ValidationReport(
        entries=entries,
        index_meta=meta or (index.meta if index is not None else None),
        degraded=degraded or index is None,
        problems=tuple(problems),
    )
