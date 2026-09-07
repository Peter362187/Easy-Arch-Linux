"""Sichere Uebertragung von Werten nach Bash.

Das ist die Sicherheitsgrenze der Profilerzeugung.

Hintergrund: ``mkarchiso`` liest ``profiledef.sh`` nicht als Datenformat, sondern
fuehrt es aus -- ``_read_profile()`` enthaelt woertlich::

    . "${profile}/profiledef.sh"

Jeder unmaskierte Benutzertext in dieser Datei ist damit Codeausfuehrung mit den
Rechten des bauenden Benutzers. Ein Herausgeber-Feld wie::

    Jason"; rm -rf ~; echo "

wuerde beim naechsten Build ausgefuehrt. Das betrifft alle Felder, die der
Benutzer frei eingeben kann: Distributionsname, Version, Herausgeber,
Anwendungsbezeichnung.

Zwei Massnahmen:

1. **Einfache Anfuehrungszeichen mit korrekter Maskierung** (``shlex.quote``).
   Innerhalb von ``'...'`` verliert Bash jede Sonderbedeutung -- auch ``$``,
   Backtick und ``;``.
2. **Zeilenumbrueche werden abgelehnt.** Sie waeren zwar innerhalb einfacher
   Anfuehrungszeichen ungefaehrlich, machen die erzeugte Datei aber unlesbar und
   erschweren jede spaetere Pruefung. Ein Paketname oder Herausgeber mit
   Zeilenumbruch ist ohnehin ein Eingabefehler.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterable, Mapping

from .errors import UnsafeValueError

# Zeichen, die in keinem Wert vorkommen duerfen, der nach Bash geht.
_FORBIDDEN = re.compile(r"[\x00-\x08\x0a-\x1f\x7f]")

MAX_VALUE_LENGTH = 512


def ensure_safe(value: str, *, field: str = "Wert") -> str:
    """Prueft einen Wert, bevor er irgendwo hingeschrieben wird."""
    if not isinstance(value, str):
        raise UnsafeValueError(field, f"erwartet Text, gefunden {type(value).__name__}")
    if len(value) > MAX_VALUE_LENGTH:
        raise UnsafeValueError(field, f"laenger als {MAX_VALUE_LENGTH} Zeichen")
    match = _FORBIDDEN.search(value)
    if match:
        raise UnsafeValueError(
            field,
            f"enthaelt ein Steuerzeichen (0x{ord(match.group()):02x}) an Position "
            f"{match.start()}",
        )
    return value


def shell_quote(value: str, *, field: str = "Wert") -> str:
    """Ein Wert, sicher als Bash-Literal.

    ``shlex.quote`` setzt einfache Anfuehrungszeichen und maskiert enthaltene
    einfache Anfuehrungszeichen als ``'\\''``. Das ist die einzige Form, die in
    Bash garantiert keine Ersetzung mehr ausloest.
    """
    ensure_safe(value, field=field)
    return shlex.quote(value)


def bash_assignment(name: str, value: str, *, field: str = "") -> str:
    """``name='wert'``"""
    _check_identifier(name)
    return f"{name}={shell_quote(value, field=field or name)}"


def bash_array(name: str, values: Iterable[str], *, indent: int = 0) -> str:
    """``name=('a' 'b')`` -- bei mehreren Werten umgebrochen und eingerueckt."""
    _check_identifier(name)
    quoted = [shell_quote(str(value), field=name) for value in values]
    if not quoted:
        return f"{name}=()"
    if len(quoted) == 1:
        return f"{name}=({quoted[0]})"
    padding = " " * (len(name) + 2 + indent)
    joined = ("\n" + padding).join(quoted)
    return f"{name}=({joined})"


def bash_assoc(name: str, entries: Mapping[str, str]) -> str:
    """Assoziatives Array, wie ``file_permissions`` in profiledef.sh.

    Das vorangestellte ``declare -A`` ist wichtig: ohne die Deklaration liest
    Bash ``[/etc/shadow]`` als Rechenausdruck und bricht mit
    „arithmetic syntax error" ab. mkarchiso deklariert die Variable zwar selbst,
    bevor es die Datei einliest -- aber dann laesst sich die erzeugte Datei
    nicht mehr eigenstaendig pruefen. Eine erneute Deklaration schadet nicht.
    """
    _check_identifier(name)
    if not entries:
        return f"declare -A {name}=()"
    lines = [f"declare -A {name}=("]
    for key, value in sorted(entries.items()):
        lines.append(
            f"  [{shell_quote(key, field=f'{name}-Schluessel')}]="
            f"{shell_quote(value, field=f'{name}[{key}]')}"
        )
    lines.append(")")
    return "\n".join(lines)


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _check_identifier(name: str) -> None:
    """Variablennamen stammen aus dem Code, nie aus Benutzereingaben.

    Die Pruefung faengt Programmierfehler ab, keine Angriffe.
    """
    if not _IDENTIFIER.match(name):
        raise UnsafeValueError(name, "kein gueltiger Bash-Variablenname")
