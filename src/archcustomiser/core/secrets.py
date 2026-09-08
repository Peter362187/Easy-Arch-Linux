"""Passwortbehandlung.

Spec §6/§12: Passwörter dürfen niemals im Klartext in Logs erscheinen und sollen
nicht dauerhaft gespeichert werden.

Drei Schutzschichten:
1. ``Secret`` verweigert die versehentliche Preisgabe: ``repr``/``str`` liefern
   ``***``, Pickling wirft. Der Klartext ist nur über ``reveal()`` erreichbar --
   eine Stelle, die man beim Code-Review greppen kann.
2. Der Klartext liegt in einem ``bytearray``, das ``burn()`` mit Nullen
   überschreibt. Kein absoluter Schutz (CPython kopiert Strings), aber es
   verkürzt die Lebensdauer erheblich.
3. ``SecretStore`` registriert jeden Wert beim Log-Filter, damit selbst eine
   versehentliche Interpolation im Log maskiert wird.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

_MASK: Final = "***"

# Der Log-Filter registriert sich hier, um Kreisimporte zu vermeiden.
# Zwei Rueckrufe, nicht einer: ein Geheimnis anzumelden ohne es je wieder
# abmelden zu koennen machte ``burn()`` wirkungslos -- der Klartext lebte in
# der Filterliste weiter, nachdem der Puffer laengst genullt war.
_observers: list[tuple[Callable[[str], None], Callable[[str], None]]] = []


def register_observer(
    on_add: Callable[[str], None],
    on_remove: Callable[[str], None],
) -> None:
    _observers.append((on_add, on_remove))


class Secret:
    """Ein Passwort, das sich gegen versehentliche Preisgabe wehrt."""

    __slots__ = ("_buffer", "_burned")

    def __init__(self, value: str | bytes | bytearray) -> None:
        if isinstance(value, str):
            raw = value.encode("utf-8")
        else:
            raw = bytes(value)
        self._buffer = bytearray(raw)
        self._burned = False
        for on_add, _on_remove in _observers:
            on_add(raw.decode("utf-8", errors="replace"))

    # -- kontrollierter Zugriff ------------------------------------------------
    def reveal(self) -> str:
        """Der einzige legitime Weg zum Klartext. Sparsam verwenden."""
        if self._burned:
            raise ValueError("Secret wurde bereits gelöscht")
        return self._buffer.decode("utf-8")

    def burn(self) -> None:
        """Überschreibt den Puffer. Danach ist das Secret unbrauchbar.

        Meldet den Wert zusätzlich bei den Beobachtern ab. Ohne diesen Schritt
        überlebte der Klartext in der Literalliste des Log-Filters -- der Puffer
        war genullt, die Kopie blieb für die gesamte Prozesslaufzeit liegen.
        """
        if self._burned:
            return
        klartext = self._buffer.decode("utf-8", errors="replace")
        for index in range(len(self._buffer)):
            self._buffer[index] = 0
        self._buffer.clear()
        self._burned = True
        for _on_add, on_remove in _observers:
            on_remove(klartext)

    # -- Preisgabe-Sperren -----------------------------------------------------
    def __repr__(self) -> str:
        return f"<Secret {_MASK}>"

    def __str__(self) -> str:
        return _MASK

    def __format__(self, _spec: str) -> str:
        return _MASK

    def __bool__(self) -> bool:
        return not self._burned and len(self._buffer) > 0

    def __len__(self) -> int:
        return len(self._buffer)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Secret):
            return NotImplemented
        return self._buffer == other._buffer

    def __hash__(self) -> int:  # bewusst nicht vom Inhalt abhängig
        return hash(id(self))

    def __reduce__(self) -> Any:
        raise TypeError("Secret darf nicht serialisiert werden")

    def __getstate__(self) -> Any:
        raise TypeError("Secret darf nicht serialisiert werden")

    def __copy__(self) -> Secret:
        return Secret(bytes(self._buffer))

    def __deepcopy__(self, _memo: dict[int, Any]) -> Secret:
        return Secret(bytes(self._buffer))


class SecretStore:
    """Prozesslokaler Halter für Passwörter.

    Getrennt von ``BuildConfig``, damit ein versehentliches ``yaml.dump(config)``
    strukturell kein Passwort erfassen kann.
    """

    __slots__ = ("_values",)

    def __init__(self) -> None:
        self._values: dict[str, Secret] = {}

    def set(self, key: str, value: str | Secret | None) -> None:
        old = self._values.pop(key, None)
        if old is not None:
            old.burn()
        if value is None or value == "":
            return
        self._values[key] = value if isinstance(value, Secret) else Secret(value)

    def get(self, key: str) -> Secret | None:
        return self._values.get(key)

    def has(self, key: str) -> bool:
        return bool(self._values.get(key))

    def keys(self) -> tuple[str, ...]:
        return tuple(self._values)

    def clear(self) -> None:
        for secret in self._values.values():
            secret.burn()
        self._values.clear()

    def __repr__(self) -> str:
        return f"<SecretStore keys={list(self._values)} values={_MASK}>"
