"""Portable Darstellung eines archiso-Profils.

Das Profil entsteht zuerst als Datenstruktur im Speicher und wird erst danach
geschrieben. Drei Gruende:

1. **Auf jeder Plattform testbar.** Ein archiso-Profil besteht zu einem guten
   Teil aus symbolischen Verknuepfungen. Die lassen sich auf NTFS ohne besondere
   Rechte nicht anlegen -- im Speicher dagegen problemlos pruefen.
2. **Zwei Ausgabewege ohne doppelten Code.** Verzeichnis und tar-Archiv sind
   nur zwei Senken ueber demselben Baum.
3. **Pfadpruefung an genau einer Stelle.** Zielpfade stammen aus dem Katalog und
   damit potenziell aus einem Benutzer-Overlay. ``add_file`` und ``add_symlink``
   sind die einzigen Eingaenge -- was hier durchkommt, ist geprueft.

Dateimodi werden bewusst NICHT im Baum gefuehrt: mkarchiso kopiert das
airootfs mit ``cp -af --no-preserve=ownership,mode`` und ignoriert sie. Rechte
kommen ausschliesslich aus dem ``file_permissions``-Array in ``profiledef.sh``,
das dieser Baum unter ``permissions`` sammelt.
"""

from __future__ import annotations

import posixpath
from collections.abc import Iterator
from dataclasses import dataclass, field

from .errors import DuplicateEntryError, UnsafePathError

# Der Unterordner des Profils, dessen Inhalt spaeter das Wurzeldateisystem des
# Abbilds wird. Hier definiert und nicht importiert: tree.py ist die unterste
# Schicht und darf von den Erzeugern nichts wissen.
AIROOTFS = "airootfs"

MAX_FILE_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class TreeFile:
    path: str
    content: bytes
    origin: str = ""

    @property
    def size(self) -> int:
        return len(self.content)

    def text(self, encoding: str = "utf-8") -> str:
        return self.content.decode(encoding, errors="replace")


@dataclass(frozen=True, slots=True)
class TreeSymlink:
    path: str
    target: str
    origin: str = ""


@dataclass(frozen=True, slots=True)
class Permission:
    """Ein Eintrag fuer ``file_permissions`` in profiledef.sh."""

    path: str          # absoluter Pfad IM Abbild, z.B. "/etc/shadow"
    owner: str = "0"
    group: str = "0"
    mode: str = "0644"
    origin: str = ""

    @property
    def value(self) -> str:
        return f"{self.owner}:{self.group}:{self.mode}"


def normalise_path(raw: str, *, origin: str = "") -> str:
    """Prueft und normalisiert einen Pfad relativ zur Profilwurzel.

    Abgelehnt werden absolute Pfade, ``..``, Backslashes, Laufwerksbuchstaben
    und Nullbytes. Der Rueckgabewert benutzt immer ``/`` -- auch auf Windows,
    denn er beschreibt einen Pfad im spaeteren Linux-Abbild, nicht auf dieser
    Platte.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise UnsafePathError(str(raw), "leerer Pfad", origin)
    if "\x00" in raw:
        raise UnsafePathError(raw, "enthaelt ein Nullbyte", origin)
    if "\\" in raw:
        raise UnsafePathError(raw, "enthaelt einen Backslash", origin)
    if raw.startswith("/"):
        raise UnsafePathError(raw, "muss relativ zur Profilwurzel sein", origin)
    if len(raw) > 1 and raw[1] == ":":
        raise UnsafePathError(raw, "enthaelt einen Laufwerksbuchstaben", origin)

    parts: list[str] = []
    for part in raw.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise UnsafePathError(raw, "enthaelt '..'", origin)
        parts.append(part)

    if not parts:
        raise UnsafePathError(raw, "verweist auf kein Ziel", origin)

    cleaned = "/".join(parts)
    # Doppelt genaeht: auch nach der Normalisierung darf nichts ausbrechen.
    if posixpath.normpath(cleaned) != cleaned:
        raise UnsafePathError(raw, "laesst sich nicht eindeutig aufloesen", origin)
    return cleaned


@dataclass(slots=True)
class ProfileTree:
    """Ein vollstaendiges archiso-Profil im Speicher."""

    files: dict[str, TreeFile] = field(default_factory=dict)
    symlinks: dict[str, TreeSymlink] = field(default_factory=dict)
    permissions: dict[str, Permission] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    # -- Aufbau ---------------------------------------------------------------
    def add_file(
        self,
        path: str,
        content: str | bytes,
        *,
        origin: str = "",
        overwrite: bool = False,
    ) -> TreeFile:
        cleaned = normalise_path(path, origin=origin)
        payload = content.encode("utf-8") if isinstance(content, str) else bytes(content)

        if len(payload) > MAX_FILE_BYTES:
            raise UnsafePathError(
                cleaned, f"Datei groesser als {MAX_FILE_BYTES // 1048576} MB", origin
            )
        if cleaned in self.symlinks:
            raise DuplicateEntryError(cleaned, self.symlinks[cleaned].origin, origin)

        existing = self.files.get(cleaned)
        if existing is not None and not overwrite:
            if existing.content != payload:
                # Zwei Optionen wollen dieselbe Datei verschieden fuellen. Das
                # still zu entscheiden waere schlimmer als es zu melden.
                raise DuplicateEntryError(cleaned, existing.origin, origin)
            return existing

        entry = TreeFile(path=cleaned, content=payload, origin=origin)
        self.files[cleaned] = entry
        return entry

    def append_to_file(self, path: str, content: str, *, origin: str = "") -> TreeFile:
        """Haengt an eine bestehende Datei an -- fuer passwd, shadow und Konsorten."""
        cleaned = normalise_path(path, origin=origin)
        existing = self.files.get(cleaned)
        previous = existing.content.decode("utf-8") if existing else ""
        if previous and not previous.endswith("\n"):
            previous += "\n"
        return self.add_file(cleaned, previous + content, origin=origin, overwrite=True)

    def add_symlink(self, path: str, target: str, *, origin: str = "") -> TreeSymlink:
        cleaned = normalise_path(path, origin=origin)
        if not target or "\x00" in target:
            raise UnsafePathError(target, "ungueltiges Verknuepfungsziel", origin)
        if "\\" in target:
            raise UnsafePathError(target, "Verknuepfungsziel enthaelt einen Backslash", origin)

        if cleaned in self.files:
            raise DuplicateEntryError(cleaned, self.files[cleaned].origin, origin)

        existing = self.symlinks.get(cleaned)
        if existing is not None:
            if existing.target != target:
                raise DuplicateEntryError(cleaned, existing.origin, origin)
            return existing

        entry = TreeSymlink(path=cleaned, target=target, origin=origin)
        self.symlinks[cleaned] = entry
        return entry

    def add_permission(
        self,
        image_path: str,
        *,
        owner: str = "0",
        group: str = "0",
        mode: str = "0644",
        origin: str = "",
    ) -> None:
        """Rechte fuer eine Datei IM Abbild (absoluter Pfad, z.B. '/etc/shadow').

        Ein abschliessender ``/`` wirkt bei mkarchiso rekursiv -- das wird hier
        unveraendert durchgereicht, weil es eine bewusste Angabe ist.
        """
        if not image_path.startswith("/"):
            raise UnsafePathError(image_path, "muss ein absoluter Pfad im Abbild sein", origin)
        if "\x00" in image_path or "\\" in image_path:
            raise UnsafePathError(image_path, "unzulaessiges Zeichen", origin)
        if ".." in image_path.split("/"):
            raise UnsafePathError(image_path, "enthaelt '..'", origin)
        self.permissions[image_path] = Permission(
            path=image_path, owner=owner, group=group, mode=mode, origin=origin
        )

    def note(self, message: str) -> None:
        """Ein Hinweis fuer den Benutzer, der beim Erzeugen aufgefallen ist."""
        if message not in self.notes:
            self.notes.append(message)

    # -- Abfragen -------------------------------------------------------------
    def has(self, path: str) -> bool:
        return path in self.files or path in self.symlinks

    def file(self, path: str) -> TreeFile | None:
        return self.files.get(path)

    def text(self, path: str) -> str:
        """Inhalt einer Datei als Text -- vor allem fuer Tests und die Vorschau."""
        entry = self.files.get(path)
        if entry is None:
            raise KeyError(f"{path} ist nicht im Profil enthalten")
        return entry.text()

    def symlink(self, path: str) -> TreeSymlink | None:
        return self.symlinks.get(path)

    def paths(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.files) | set(self.symlinks)))

    def under(self, prefix: str) -> tuple[str, ...]:
        marker = prefix.rstrip("/") + "/"
        return tuple(path for path in self.paths() if path.startswith(marker))

    def file_permissions(self) -> dict[str, str]:
        """Das Dict fuer ``file_permissions`` in profiledef.sh."""
        return {path: entry.value for path, entry in sorted(self.permissions.items())}

    def mode_for(self, path: str, *, default: int = 0o644) -> int:
        """Der Dateimodus, den dieser Baumpfad tragen soll.

        Uebersetzt zwischen den beiden Schluesselraeumen: ``files`` ist nach
        dem Profilpfad verschluesselt (``airootfs/etc/shadow``),
        ``permissions`` nach dem Pfad im fertigen Abbild (``/etc/shadow``).

        Gebraucht wird das von den Senken. Bis zum 07.09.2026 schrieben die
        einen festen Modus 0644 fuer jede Datei -- der Passwort-Hash in
        ``etc/shadow`` lag im exportierten Archiv damit weltlesbar, obwohl der
        Baum laengst 0400 angemeldet hatte. Beim Bau selbst fiel das nicht auf,
        weil mkarchiso die Rechte aus ``file_permissions`` in profiledef.sh
        nimmt und den Baum gar nicht sieht.
        """
        marker = f"{AIROOTFS}/"
        if not path.startswith(marker):
            return default
        im_abbild = path[len(AIROOTFS):]        # "airootfs/etc/shadow" -> "/etc/shadow"
        eintrag = self.permissions.get(im_abbild)
        if eintrag is None:
            return default
        try:
            return int(eintrag.mode, 8)
        except (TypeError, ValueError):
            return default

    def entries(self) -> Iterator[TreeFile | TreeSymlink]:
        for path in self.paths():
            yield self.files.get(path) or self.symlinks[path]

    # -- Kennzahlen -----------------------------------------------------------
    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def symlink_count(self) -> int:
        return len(self.symlinks)

    @property
    def total_bytes(self) -> int:
        return sum(entry.size for entry in self.files.values())

    def describe(self) -> str:
        return (
            f"{self.file_count} Dateien, {self.symlink_count} Verknuepfungen, "
            f"{self.total_bytes / 1024:.0f} KB"
        )
