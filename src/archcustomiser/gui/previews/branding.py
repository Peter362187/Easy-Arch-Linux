"""Vorschau ``iso_branding`` -- wie die fertige ISO aussehen wird.

Branding besteht aus Feldern, deren Wirkung man erst nach einem Bau sieht:
ein Bootmenue-Titel, ein Splash-Bild, ein Hintergrundbild, ein Name in
``os-release``. Bis dahin vergeht eine halbe Stunde. Wer dann feststellt, dass
das Splash-Bild in der falschen Groesse vorliegt, faengt von vorn an.

Drei Kacheln zeigen dasselbe vorab:

* das **Bootmenue**, so wie syslinux oder GRUB es zeichnen wuerden,
* den **Desktop** mit Hintergrundbild und Logo,
* die **Identitaet**: was in ``/etc/os-release`` und im Dateinamen steht.

Alles selbst gezeichnet und ohne einen einzigen Schreibzugriff.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from ...core.archiso.branding import png_dimensions
from ..design import tokens
from ..design.typo import BODY, CAPTION, schrift
from ..widgets.common import HeadlineLabel
from .registry import PreviewContext, register

log = logging.getLogger(__name__)

# Ein Splash muss genau so gross sein; syslinux skaliert nicht.
SPLASH_GROESSE = (640, 480)
NEUZEICHNEN_MS = 80

_bildspeicher: dict[tuple[str, float], QPixmap] = {}


def _bild(pfad: str) -> QPixmap | None:
    """Laedt ein Bild und merkt es sich nach Pfad und Aenderungszeit.

    Ohne den Speicher laege bei jedem Tastendruck im Namensfeld ein
    Dateizugriff auf einem mehrere Megabyte grossen Hintergrundbild.
    """
    if not pfad:
        return None
    datei = Path(pfad)
    try:
        stempel = datei.stat().st_mtime
    except OSError:
        return None
    schluessel = (str(datei), stempel)
    if schluessel in _bildspeicher:
        return _bildspeicher[schluessel]
    bild = QPixmap(str(datei))
    if bild.isNull():
        return None
    _bildspeicher.clear()          # nur der zuletzt gebrauchte Satz zaehlt
    _bildspeicher[schluessel] = bild
    return bild


def _splash_warnung(pfad: str) -> str:
    """Ob das Splash-Bild die Groesse hat, die syslinux verlangt."""
    if not pfad:
        return ""
    try:
        kopf = Path(pfad).read_bytes()[:32]
    except OSError:
        return "Datei nicht lesbar."
    masse = png_dimensions(kopf)
    if masse is None:
        return "Kein PNG -- syslinux zeigt es nicht an."
    if masse != SPLASH_GROESSE:
        return f"{masse[0]}x{masse[1]} statt 640x480 -- wird nicht angezeigt."
    return ""


class _Kachel(QWidget):
    """Gemeinsamer Rahmen aller drei Vorschaubilder."""

    def __init__(self, titel: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.titel = titel
        self.hinweis = ""
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(150)
        # Schmaler ist eine Bootmenue-Nachbildung nicht mehr aussagekraeftig.
        self.setMinimumWidth(260)

    def _rahmen(self, maler: QPainter) -> QRectF:
        werte = tokens()
        p = werte.palette
        flaeche = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)

        maler.setPen(Qt.PenStyle.NoPen)
        maler.setBrush(QColor(p.surface))
        maler.drawRoundedRect(flaeche, werte.radius.md, werte.radius.md)
        maler.setBrush(Qt.BrushStyle.NoBrush)
        maler.setPen(QPen(QColor(p.border), 1.0))
        maler.drawRoundedRect(flaeche, werte.radius.md, werte.radius.md)

        maler.setFont(schrift(CAPTION, fett=True))
        maler.setPen(QColor(p.text_subtle))
        kopf = QRectF(
            flaeche.left() + werte.space.md,
            flaeche.top() + werte.space.xs,
            flaeche.width() - 2 * werte.space.md,
            18.0,
        )
        maler.drawText(kopf, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), self.titel)

        unten = 0.0
        if self.hinweis:
            maler.setFont(schrift(CAPTION))
            maler.setPen(QColor(p.warning))
            zeile = QRectF(
                flaeche.left() + werte.space.md,
                flaeche.bottom() - 20.0,
                flaeche.width() - 2 * werte.space.md,
                18.0,
            )
            maler.drawText(zeile, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), self.hinweis)
            unten = 22.0

        return QRectF(
            flaeche.left() + werte.space.md,
            kopf.bottom() + werte.space.xs,
            flaeche.width() - 2 * werte.space.md,
            flaeche.height() - kopf.height() - werte.space.md - unten,
        )


class _Bootmenue(_Kachel):
    """Der erste Bildschirm nach dem Einschalten."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Bootmenue", parent)
        self.menu_titel = ""
        self.eintraege: tuple[str, ...] = ()
        self.timeout = 15
        self.splash = ""
        self.grub = False

    def paintEvent(self, event) -> None:
        werte = tokens()
        maler = QPainter(self)
        maler.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        innen = self._rahmen(maler)

        # Der Bildschirm eines startenden Rechners ist schwarz, unabhaengig
        # vom Erscheinungsbild der Anwendung.
        maler.setPen(Qt.PenStyle.NoPen)
        maler.setBrush(QColor("#0b0b0d"))
        maler.drawRoundedRect(innen, werte.radius.sm, werte.radius.sm)

        maler.save()
        pfad_bild = _bild(self.splash)
        if pfad_bild is not None:
            maler.setClipRect(innen)
            skaliert = pfad_bild.scaled(
                innen.size().toSize(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            maler.setOpacity(0.75)
            maler.drawPixmap(innen.topLeft(), skaliert)
            maler.setOpacity(1.0)
        maler.restore()

        maler.setFont(schrift(BODY, fett=True))
        maler.setPen(QColor("#e6e6e6"))
        kopf = innen.adjusted(12, 8, -12, 0)
        kopf.setHeight(20)
        maler.drawText(
            kopf,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self.menu_titel or "Arch Linux",
        )

        if self.grub:
            # GRUB zeichnet einen Rahmen um die Auswahl, syslinux nicht.
            rahmen = innen.adjusted(10, 32, -10, -26)
            maler.setBrush(Qt.BrushStyle.NoBrush)
            maler.setPen(QPen(QColor("#8a8a8a"), 1.0))
            maler.drawRect(rahmen)
            liste = rahmen.adjusted(8, 6, -8, -6)
        else:
            liste = innen.adjusted(12, 32, -12, -26)

        maler.setFont(schrift(CAPTION))
        oben = liste.top()
        for nummer, eintrag in enumerate(self.eintraege):
            zeile = QRectF(liste.left(), oben, liste.width(), 18.0)
            if zeile.bottom() > liste.bottom():
                break
            if nummer == 0:
                maler.setPen(Qt.PenStyle.NoPen)
                maler.setBrush(QColor("#c9c9c9"))
                maler.drawRect(zeile)
                maler.setPen(QColor("#101010"))
            else:
                maler.setPen(QColor("#c9c9c9"))
            maler.drawText(
                zeile.adjusted(6, 0, -6, 0),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                eintrag,
            )
            oben += 20.0

        maler.setPen(QColor("#9a9a9a"))
        fuss = QRectF(innen.left() + 12, innen.bottom() - 22, innen.width() - 24, 18)
        maler.drawText(
            fuss,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            f"Automatischer Start in {self.timeout} Sekunden",
        )
        maler.end()


class _Desktop(_Kachel):
    """Wie das laufende Live-System aussieht."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Live-System", parent)
        self.wallpaper = ""
        self.logo = ""
        self.name = ""

    def paintEvent(self, event) -> None:
        werte = tokens()
        p = werte.palette
        maler = QPainter(self)
        maler.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        innen = self._rahmen(maler)

        maler.setPen(Qt.PenStyle.NoPen)
        hintergrund = _bild(self.wallpaper)
        maler.save()
        maler.setClipRect(innen)
        if hintergrund is not None:
            skaliert = hintergrund.scaled(
                innen.size().toSize(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            maler.drawPixmap(innen.topLeft(), skaliert)
        else:
            maler.setBrush(QColor(p.accent_pressed))
            maler.drawRect(innen)
        maler.restore()

        # Panel am unteren Rand -- ein Desktop ohne Leiste sieht wie ein Bild aus.
        leiste = QRectF(innen.left(), innen.bottom() - 22, innen.width(), 22)
        maler.setBrush(QColor(0, 0, 0, 150))
        maler.drawRect(leiste)
        maler.setFont(schrift(CAPTION))
        maler.setPen(QColor("#e8e8e8"))
        maler.drawText(
            leiste.adjusted(8, 0, -8, 0),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self.name or "Arch Linux",
        )

        zeichen = _bild(self.logo)
        if zeichen is not None:
            kante = min(64.0, innen.height() - 40)
            ziel = QRectF(
                innen.center().x() - kante / 2,
                innen.center().y() - kante / 2 - 8,
                kante,
                kante,
            )
            maler.drawPixmap(
                ziel.toRect(),
                zeichen.scaled(
                    ziel.size().toSize(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ),
            )
        maler.end()


class _Identitaet(_Kachel):
    """Was das System ueber sich selbst sagt."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Identitaet", parent)
        self.zeilen: tuple[tuple[str, str], ...] = ()
        self.setMinimumHeight(130)

    def paintEvent(self, event) -> None:
        werte = tokens()
        p = werte.palette
        maler = QPainter(self)
        maler.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        innen = self._rahmen(maler)

        oben = innen.top()
        for schluessel, wert in self.zeilen:
            zeile = QRectF(innen.left(), oben, innen.width(), 19.0)
            if zeile.bottom() > innen.bottom():
                break
            maler.setFont(schrift(CAPTION))
            maler.setPen(QColor(p.text_subtle))
            links = QRectF(zeile.left(), zeile.top(), 118.0, zeile.height())
            maler.drawText(
                links, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), schluessel
            )
            maler.setPen(QColor(p.text))
            rechts = QRectF(
                zeile.left() + 122.0, zeile.top(), zeile.width() - 122.0, zeile.height()
            )
            gekuerzt = maler.fontMetrics().elidedText(
                wert, Qt.TextElideMode.ElideMiddle, int(rechts.width())
            )
            maler.drawText(
                rechts, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), gekuerzt
            )
            oben += 21.0
        maler.end()


class BrandingPreview(QWidget):
    """Die drei Kacheln als eine Vorschau."""

    def __init__(self, kontext: PreviewContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kontext = kontext
        werte = tokens()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(werte.space.sm)

        kopf = HeadlineLabel("Vorschau", level=2)
        layout.addWidget(kopf)

        self.boot = _Bootmenue()
        self.desktop = _Desktop()
        self.identitaet = _Identitaet()
        layout.addWidget(self.boot, 3)
        layout.addWidget(self.desktop, 3)
        layout.addWidget(self.identitaet, 2)

        # Waehrend des Tippens nicht bei jedem Zeichen neu zeichnen: das
        # Bootmenue liest dabei ein Splash-Bild von der Platte.
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(NEUZEICHNEN_MS)
        self._timer.timeout.connect(self.refresh)
        kontext.store.fieldChanged.connect(lambda _b: self._timer.start())
        self.refresh()

    def refresh(self) -> None:
        k = self.kontext
        config = k.store.config

        name = k.text("name") or config.distro_name
        version = k.text("version")
        splash = k.text("splash")

        self.boot.menu_titel = k.text("menu_title") or name
        self.boot.timeout = max(0, k.zahl("boot_timeout", 15))
        self.boot.splash = splash
        self.boot.grub = "grub" in k.text("uefi_bootloader").lower()
        eintraege = [f"{name} (x86_64, UEFI)", f"{name} (x86_64, BIOS)"]
        if config.field_bool("build.include_memtest"):
            eintraege.append("Speichertest (memtest86+)")
        eintraege.append("UEFI-Firmwareeinstellungen")
        self.boot.eintraege = tuple(eintraege)
        self.boot.hinweis = _splash_warnung(splash)
        self.boot.update()

        self.desktop.wallpaper = k.text("wallpaper")
        self.desktop.logo = k.text("logo")
        self.desktop.name = f"{name} {version}".strip()
        self.desktop.update()

        self.identitaet.zeilen = (
            ("NAME", name),
            ("PRETTY_NAME", f"{name} {version} (based on Arch Linux)".strip()),
            ("ID", config.iso_name),
            ("Herausgeber", k.text("publisher") or "-"),
            ("ISO-Datei", config.iso_filename),
            ("Datentraeger", config.iso_label),
        )
        self.identitaet.update()


@register("iso_branding")
def _erzeuge(kontext: PreviewContext) -> QWidget:
    return BrandingPreview(kontext)


__all__ = ["BrandingPreview"]
