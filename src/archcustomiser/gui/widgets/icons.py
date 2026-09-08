"""Symbole aus den mitgelieferten SVG-Dateien.

``Option.icon`` und ``Category.icon`` gab es im Datenmodell seit jeher, der
Lader las sie, sechs YAML-Dateien setzten sie -- und im gesamten Programm
existierte lange kein einziger ``QIcon``-Aufruf.

Die Dateien sind einfarbig und nutzen ``currentColor``. Qt wertet das in einem
``QIcon`` allerdings nicht aus: die Farbe steht in der Datei, nicht im
Kontext. Deshalb wird die Zeichnung hier eingefaerbt -- sonst verschwindet ein
dunkles Symbol im dunklen Modus.

Der Zwischenspeicher haengt an der Farbe. Ohne diesen Schluessel blieben nach
einem Designwechsel die alten Symbole stehen; ``cache_leeren()`` raeumt ihn
zusaetzlich, wenn sich mehr geaendert hat als die Farbe.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from ...core.paths import package_root

log = logging.getLogger(__name__)

_cache: dict[tuple[str, str, int], QIcon] = {}


def icons_dir():
    return package_root() / "assets" / "icons"


def cache_leeren() -> None:
    """Nach einem Designwechsel: die Farben stimmen nicht mehr."""
    _cache.clear()


def load_icon(name: str, farbe: str = "", groesse: int = 20) -> QIcon | None:
    """Laedt ein Symbol in der gewuenschten Farbe.

    Gibt ``None`` zurueck, wenn es keines gibt. Bewusst kein Ersatzsymbol: ein
    Platzhalter neben echten Symbolen sieht nach einem Fehler aus.
    """
    if not name:
        return None
    if not farbe:
        from ..design import tokens

        farbe = tokens().palette.text

    schluessel = (name, farbe, groesse)
    gemerkt = _cache.get(schluessel)
    if gemerkt is not None:
        return gemerkt

    pfad = icons_dir() / f"{name}.svg"
    if not pfad.is_file():
        log.debug("Symbol %r nicht gefunden (%s)", name, pfad)
        return None

    symbol = _eingefaerbt(str(pfad), farbe, groesse)
    if symbol is None:
        return None
    _cache[schluessel] = symbol
    return symbol


def _eingefaerbt(pfad: str, farbe: str, groesse: int) -> QIcon | None:
    """Zeichnet das SVG und legt die Farbe darueber.

    ``CompositionMode_SourceIn`` faerbt nur, wo tatsaechlich gezeichnet wurde:
    die Form bleibt, die Farbe wird ersetzt. Ein einfaches Uebermalen wuerde
    das ganze Rechteck einfaerben.
    """
    renderer = QSvgRenderer(pfad)
    if not renderer.isValid():
        return None

    # Mit devicePixelRatio: sonst ist das Symbol bei 125 % oder 150 %
    # sichtbar unscharf.
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    dpr = app.devicePixelRatio() if app is not None else 1.0
    pixmap = QPixmap(QSize(int(groesse * dpr), int(groesse * dpr)))
    pixmap.setDevicePixelRatio(dpr)
    pixmap.fill(Qt.GlobalColor.transparent)

    maler = QPainter(pixmap)
    maler.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(maler, QRectF(0, 0, groesse, groesse))
    maler.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    maler.fillRect(QRectF(0, 0, groesse, groesse), QColor(farbe))
    maler.end()

    return QIcon(pixmap)
