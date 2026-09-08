"""Tests des Protokoll-Filters.

Der Filter maskiert Passwoerter in jeder Logzeile. Geprueft werden hier die
beiden Wege, auf denen frueher etwas an ihm vorbeikam: ein Traceback (der erst
im Formatter entsteht) und ein Argument, das keine Zeichenkette ist (das erst
bei der Interpolation zu Text wird).
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Zwei Wege am Filter vorbei
# ---------------------------------------------------------------------------


def test_a_traceback_is_redacted_too() -> None:
    """Der Traceback entsteht erst im Formatter -- am Argumentweg vorbei.

    Ein ``ValueError("... hunter2 ...")`` waere im Klartext im Protokoll
    gelandet, obwohl jede gewoehnliche Zeile maskiert wird.
    """
    import logging

    from archcustomiser.core.logging_setup import redaction_filter
    from archcustomiser.core.secrets import Secret

    geheim = Secret("hunter2-sehr-geheim")
    try:
        try:
            raise ValueError(f"Passwort {geheim.reveal()} ist ungueltig")
        except ValueError:
            record = logging.LogRecord(
                "test", logging.ERROR, __file__, 1, "Fehlgeschlagen", None,
                __import__("sys").exc_info(),
            )
            assert redaction_filter().filter(record)
            text = record.exc_text or ""
            assert "hunter2" not in text
            assert "***" in text
    finally:
        geheim.burn()


def test_a_non_string_argument_is_redacted() -> None:
    """Ein Ausnahmeobjekt als Argument wird erst im Formatter zu Text."""
    import logging

    from archcustomiser.core.logging_setup import redaction_filter
    from archcustomiser.core.secrets import Secret

    geheim = Secret("hunter2-sehr-geheim")
    try:
        record = logging.LogRecord(
            "test", logging.WARNING, __file__, 1, "Fehler: %s",
            (OSError(f"Datei {geheim.reveal()} fehlt"),), None,
        )
        assert redaction_filter().filter(record)
        assert "hunter2" not in record.getMessage()
    finally:
        geheim.burn()
