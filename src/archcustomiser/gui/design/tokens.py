"""Die Werte, aus denen die Oberflaeche besteht.

Vorher standen Farben, Abstaende und Schriftgroessen an rund zwanzig
``setStyleSheet``-Aufrufen verteilt; ``font-size: 11px`` kam neunmal woertlich
vor. Hier steht jeder Wert genau einmal, und jede Zeichenroutine liest ihn von
hier.

Zwei Entscheidungen, die alles andere bestimmen:

**Dunkel ist die Vorgabe.** Ein ISO-Bau laeuft lange und oft abends; die
Oberflaeche steht dabei neben einem Terminal. Hell bleibt vollwertig -- beide
Paletten sind gegen dieselbe Kontrastschwelle (WCAG AA, 4.5:1 fuer Fliesstext)
geprueft.

**Die Akzentfarbe ist frei waehlbar.** Alles, was daraus folgt -- Hover,
gedrueckter Zustand, die Schriftfarbe darauf, die weiche Fuellung hinter einer
ausgewaehlten Karte -- wird gerechnet, nicht gepflegt. Sonst waere jede neue
Farbe ein Satz neuer Konstanten.
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Palette:
    """Alle Farben einer Erscheinung."""

    bg: str
    surface: str
    surface_alt: str
    border: str
    border_strong: str
    text: str
    text_muted: str
    text_subtle: str
    accent: str
    accent_hover: str
    accent_pressed: str
    accent_soft: str
    accent_text: str
    success: str
    warning: str
    danger: str
    info: str
    overlay: str
    shadow: str
    dunkel: bool


@dataclass(frozen=True, slots=True)
class Spacing:
    xs: int = 4
    sm: int = 8
    md: int = 12
    lg: int = 16
    xl: int = 24
    xxl: int = 32


@dataclass(frozen=True, slots=True)
class Radius:
    sm: int = 4
    md: int = 8
    lg: int = 12
    pill: int = 999


@dataclass(frozen=True, slots=True)
class Tokens:
    palette: Palette
    space: Spacing = Spacing()
    radius: Radius = Radius()


# ---------------------------------------------------------------------------
# Farbrechnen
# ---------------------------------------------------------------------------


def _rgb(hexwert: str) -> tuple[float, float, float]:
    text = hexwert.lstrip("#")
    if len(text) == 3:
        text = "".join(zeichen * 2 for zeichen in text)
    return tuple(int(text[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(kanal * 255))):02x}" for kanal in rgb)


def heller(farbe: str, anteil: float) -> str:
    """Verschiebt die Helligkeit -- negativ macht dunkler.

    Ueber HSL statt ueber die RGB-Kanaele: eine Aufhellung im RGB-Raum
    entsaettigt und laesst Blau grau werden.
    """
    farbton, helligkeit, saettigung = colorsys.rgb_to_hls(*_rgb(farbe))
    return _hex(
        colorsys.hls_to_rgb(
            farbton, max(0.0, min(1.0, helligkeit + anteil)), saettigung
        )
    )


def luminanz(farbe: str) -> float:
    """Relative Helligkeit nach WCAG -- Grundlage jedes Kontrastvergleichs."""

    def kanal(wert: float) -> float:
        return wert / 12.92 if wert <= 0.03928 else ((wert + 0.055) / 1.055) ** 2.4

    r, g, b = (kanal(teil) for teil in _rgb(farbe))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def kontrast(vorne: str, hinten: str) -> float:
    """Das Kontrastverhaeltnis zweier Farben (1 bis 21)."""
    a, b = luminanz(vorne), luminanz(hinten)
    hell, dunkel = max(a, b), min(a, b)
    return (hell + 0.05) / (dunkel + 0.05)


def lesbare_schrift(hintergrund: str) -> str:
    """Schwarz oder Weiss -- je nachdem, was auf dem Grund besser lesbar ist.

    Frueher stand auf einem Abzeichen fest weisse Schrift; auf hellem Orange
    erfuellte das kein AA-Verhaeltnis.
    """
    return "#ffffff" if kontrast("#ffffff", hintergrund) >= kontrast("#111111", hintergrund) else "#111111"


def mit_alpha(farbe: str, alpha: float) -> str:
    """Dieselbe Farbe als ``rgba(...)`` -- fuer weiche Fuellungen."""
    r, g, b = (round(kanal * 255) for kanal in _rgb(farbe))
    return f"rgba({r}, {g}, {b}, {alpha:.3f})"


# ---------------------------------------------------------------------------
# Die beiden Paletten
# ---------------------------------------------------------------------------


def _palette(dunkel: bool, akzent: str) -> Palette:
    akzent = akzent if akzent.startswith("#") else "#" + akzent
    if dunkel:
        return Palette(
            bg="#161719",
            surface="#1f2124",
            surface_alt="#26292d",
            border="#33373c",
            border_strong="#4a4f56",
            text="#e8e9ea",
            text_muted="#a4a8ad",
            text_subtle="#7c8087",
            accent=akzent,
            accent_hover=heller(akzent, 0.08),
            accent_pressed=heller(akzent, -0.10),
            accent_soft=mit_alpha(akzent, 0.16),
            accent_text=lesbare_schrift(akzent),
            success="#6cc070",
            warning="#e0a53a",
            danger="#f0736a",
            info="#5fb3d9",
            overlay="rgba(0, 0, 0, 0.55)",
            shadow="rgba(0, 0, 0, 0.45)",
            dunkel=True,
        )
    return Palette(
        bg="#f6f7f9",
        surface="#ffffff",
        surface_alt="#eef0f3",
        border="#d9dce1",
        border_strong="#b6bcc5",
        text="#1b1d20",
        text_muted="#5f6570",
        text_subtle="#8a9099",
        accent=akzent,
        accent_hover=heller(akzent, -0.06),
        accent_pressed=heller(akzent, -0.14),
        accent_soft=mit_alpha(akzent, 0.12),
        accent_text=lesbare_schrift(akzent),
        success="#1e7a24",
        warning="#8a5d00",
        danger="#b3261e",
        info="#14567a",
        overlay="rgba(20, 22, 26, 0.40)",
        shadow="rgba(20, 22, 26, 0.18)",
        dunkel=False,
    )


def tokens_for(dunkel: bool, akzent: str) -> Tokens:
    """Der vollstaendige Satz Werte fuer eine Erscheinung."""
    return Tokens(palette=_palette(dunkel, akzent))
