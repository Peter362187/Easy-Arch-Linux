"""Die Optionskarte -- das meistgesehene Element der Oberflaeche.

Vollstaendig selbst gezeichnet, aus drei Gruenden:

* **Die ganze Flaeche ist anklickbar.** Vorher reagierte nur der kleine Haken,
  waehrend der Rahmen beim Ueberfahren aufleuchtete und damit Klickbarkeit
  versprach, die es nicht gab.
* **Animierte Zustaende brauchen Zwischenwerte.** Ein ``QCheckBox`` kennt an
  oder aus; eine Karte, die sich beim Auswaehlen fuellt, braucht 0.37.
* **Ein Widget statt sechs.** Titel, Beschreibung, drei Abzeichen und ein
  Haken waren vorher sechs Kindwidgets je Karte -- bei 24 Programmen also
  ueber hundert, jedes mit eigenem Stylesheet.

Die Tastaturbedienung bleibt vollstaendig: Fokus, Leertaste, Eingabetaste, ein
sichtbarer Fokusring und ein Name fuer Vorlesewerkzeuge.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from ...core.catalog import Option, SelectionMode
from .. import motion
from ..design import qfarbe, tokens
from ..design.typo import BODY, CAPTION, schrift


class OptionCard(QWidget):
    """Eine Karte fuer genau eine Katalogoption."""

    toggled = Signal(str, bool)

    def __init__(
        self,
        option: Option,
        mode: SelectionMode,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.option = option
        self.mode = mode

        self._checked = False
        self._auto = False
        self._verfuegbar = True
        # Zwei verschiedene Gruende mit zwei verschiedenen Lebensdauern. Sie
        # teilten sich frueher ein Feld: die Begruendung einer automatischen
        # Ergaenzung ueberlebte deren Ende und stand danach rot unter einer
        # ganz normal waehlbaren Karte.
        self._auto_grund = ""
        self._sperrgrund = ""

        # Animierte Zwischenwerte. Sie liegen absichtlich als einfache Zahlen
        # vor und nicht als Qt-Properties: die Karte zeichnet sich selbst, ein
        # Property-System braeuchte sie nicht.
        self._hover = 0.0
        self._auswahl = 0.0
        self._haken = 0.0

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setAccessibleName(option.label)
        if option.description:
            self.setAccessibleDescription(option.description)
        self.setToolTip(self._tooltip())

    # -- Zustand --------------------------------------------------------------
    def set_checked(self, wert: bool, *, animiert: bool = True) -> None:
        if self._checked == wert:
            return
        self._checked = wert
        ziel = 1.0 if wert else 0.0
        motion.animate(
            self,
            von=self._auswahl,
            bis=ziel,
            dauer=motion.NORMAL,
            setzen=self._setze_auswahl,
        )
        motion.animate(
            self,
            von=self._haken,
            bis=ziel,
            dauer=motion.NORMAL,
            setzen=self._setze_haken,
        )

    def is_checked(self) -> bool:
        return self._checked

    def set_auto(self, auto: bool, grund: str = "") -> None:
        """Automatisch ergaenzt: angehakt, aber nicht anklickbar."""
        if self._auto == auto and grund == self._auto_grund:
            return
        self._auto = auto
        # Nur zuruecksetzen, wenn die Ergaenzung wegfaellt -- sonst bleibt der
        # Grund einer laengst aufgehobenen Ergaenzung stehen.
        self._auto_grund = grund if auto else ""
        self.setCursor(
            Qt.CursorShape.ArrowCursor if auto else Qt.CursorShape.PointingHandCursor
        )
        self.setToolTip(self._auto_grund or self._sperrgrund or self._tooltip())
        self.update()

    def set_availability(self, verfuegbar: bool, grund: str = "") -> None:
        """Nicht waehlbar, weil eine andere Auswahl fehlt."""
        neuer_grund = "" if verfuegbar else grund
        if self._verfuegbar == verfuegbar and neuer_grund == self._sperrgrund:
            return
        self._verfuegbar = verfuegbar
        self._sperrgrund = neuer_grund
        self.setToolTip(self._auto_grund or self._sperrgrund or self._tooltip())
        self.update()

    def _setze_auswahl(self, wert) -> None:
        self._auswahl = float(wert)
        self.update()

    def _setze_haken(self, wert) -> None:
        self._haken = float(wert)
        self.update()

    def _setze_hover(self, wert) -> None:
        self._hover = float(wert)
        self.update()

    def _tooltip(self) -> str:
        teile = [self.option.description] if self.option.description else []
        namen = [ref.name for ref in self.option.packages]
        if namen:
            teile.append("Pakete: " + ", ".join(namen[:8]) + ("" if len(namen) <= 8 else " ..."))
        return chr(10).join(teile)

    # -- Ereignisse -----------------------------------------------------------
    def enterEvent(self, event) -> None:
        motion.animate(
            self, von=self._hover, bis=1.0, dauer=motion.SCHNELL, setzen=self._setze_hover
        )
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        motion.animate(
            self, von=self._hover, bis=0.0, dauer=motion.SCHNELL, setzen=self._setze_hover
        )
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._umschalten()
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._umschalten()
            event.accept()
            return
        super().keyPressEvent(event)

    def _umschalten(self) -> None:
        if self._auto or not self._verfuegbar:
            return
        # Bei Einfachauswahl ist ein erneuter Klick auf die gewaehlte Option
        # wirkungslos -- abwaehlen wuerde die Kategorie leer lassen, und genau
        # das verhindert der Resolver anschliessend ohnehin.
        if self._checked and self.mode is not SelectionMode.MULTI:
            return
        self.toggled.emit(self.option.id, not self._checked)

    # -- Groesse --------------------------------------------------------------
    def sizeHint(self) -> QSize:
        werte = tokens()
        titel = QFontMetricsF(schrift(BODY, fett=True))
        klein = QFontMetricsF(schrift(CAPTION))
        hoehe = (
            werte.space.md
            + titel.height()
            + werte.space.xs
            + klein.height() * 2
            + werte.space.md
        )
        return QSize(240, int(hoehe))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    # -- Zeichnen -------------------------------------------------------------
    def paintEvent(self, event) -> None:
        werte = tokens()
        p = werte.palette
        maler = QPainter(self)
        maler.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        flaeche = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        radius = float(werte.radius.lg)

        self._zeichne_grund(maler, flaeche, radius, p)
        rand = self._zeichne_rand(maler, flaeche, radius, p)
        self._zeichne_haken(maler, flaeche, werte, p)
        self._zeichne_text(maler, flaeche, werte, p)
        self._zeichne_abzeichen(maler, flaeche, werte, p)

        if self.hasFocus():
            maler.setPen(QPen(QColor(p.accent), 2.0))
            maler.setBrush(Qt.BrushStyle.NoBrush)
            maler.drawRoundedRect(flaeche.adjusted(-1, -1, 1, 1), radius + 1, radius + 1)
        del rand
        maler.end()

    def _zeichne_grund(self, maler, flaeche, radius, p) -> None:
        # Automatisch ergaenzte Karten sitzen auf einem abgesetzten Grund;
        # der Akzentschleier darueber kommt weiter unten.
        maler.setBrush(QColor(p.surface_alt if self._auto else p.surface))
        maler.setPen(Qt.PenStyle.NoPen)
        maler.drawRoundedRect(flaeche, radius, radius)

        if self._auswahl > 0 or self._auto:
            staerke = max(self._auswahl, 0.9 if self._auto else 0.0)
            maler.setBrush(qfarbe(p.accent, 0.14 * staerke))
            maler.drawRoundedRect(flaeche, radius, radius)

        if self._hover > 0 and self._verfuegbar and not self._auto:
            maler.setBrush(qfarbe(p.accent, 0.06 * self._hover))
            maler.drawRoundedRect(flaeche, radius, radius)

    def _zeichne_rand(self, maler, flaeche, radius, p) -> QColor:
        randfarbe = QColor(p.border)
        if self._auswahl > 0 or self._auto:
            randfarbe = QColor(p.accent)
        elif self._hover > 0:
            gemischt = QColor(p.border)
            ziel = QColor(p.border_strong)
            randfarbe = QColor(
                int(gemischt.red() + (ziel.red() - gemischt.red()) * self._hover),
                int(gemischt.green() + (ziel.green() - gemischt.green()) * self._hover),
                int(gemischt.blue() + (ziel.blue() - gemischt.blue()) * self._hover),
            )
        breite = 2.0 if (self._auswahl > 0 or self._auto) else 1.0
        maler.setPen(QPen(randfarbe, breite))
        maler.setBrush(Qt.BrushStyle.NoBrush)
        maler.drawRoundedRect(flaeche, radius, radius)
        return randfarbe

    def _zeichne_haken(self, maler, flaeche, werte, p) -> None:
        """Der Auswahlanzeiger -- rund bei Einfachauswahl, eckig bei Mehrfach."""
        seite = 18.0
        kasten = QRectF(
            flaeche.left() + werte.space.md,
            flaeche.top() + werte.space.md,
            seite,
            seite,
        )
        einfach = self.mode is not SelectionMode.MULTI
        radius = seite / 2 if einfach else float(werte.radius.sm)

        gefuellt = self._auswahl > 0 or self._auto
        maler.setPen(QPen(QColor(p.accent if gefuellt else p.border_strong), 2.0))
        maler.setBrush(QColor(p.accent) if gefuellt else Qt.BrushStyle.NoBrush)
        maler.drawRoundedRect(kasten.adjusted(1, 1, -1, -1), radius, radius)

        if self._haken <= 0 and not self._auto:
            return
        anteil = 1.0 if self._auto else self._haken
        maler.setPen(QPen(QColor(p.accent_text), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if einfach:
            punkt = kasten.center()
            r = 3.5 * anteil
            maler.setBrush(QColor(p.accent_text))
            maler.drawEllipse(punkt, r, r)
        else:
            pfad = QPainterPath()
            pfad.moveTo(kasten.left() + 4.5, kasten.center().y())
            pfad.lineTo(kasten.center().x() - 0.5, kasten.bottom() - 5.0)
            pfad.lineTo(kasten.right() - 4.0, kasten.top() + 5.0)
            maler.setBrush(Qt.BrushStyle.NoBrush)
            maler.drawPath(_teilpfad(pfad, anteil))

    def _zeichne_text(self, maler, flaeche, werte, p) -> None:
        links = flaeche.left() + werte.space.md + 18 + werte.space.sm
        rechts = flaeche.right() - werte.space.md
        oben = flaeche.top() + werte.space.md

        titelschrift = schrift(BODY, fett=True)
        maler.setFont(titelschrift)
        titelfarbe = p.text if self._verfuegbar or self._auto else p.text_subtle
        maler.setPen(QColor(titelfarbe))
        metrik = QFontMetricsF(titelschrift)
        breite_titel = max(0.0, rechts - links - self._abzeichenbreite(werte))
        maler.drawText(
            QRectF(links, oben, breite_titel, metrik.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            metrik.elidedText(self.option.label, Qt.TextElideMode.ElideRight, breite_titel),
        )

        # Ein Sperrgrund ist eine Beanstandung und wird rot gezeigt; die
        # Begruendung einer Ergaenzung ist eine Auskunft und bleibt gedaempft.
        if self._sperrgrund:
            text, textfarbe = self._sperrgrund, p.danger
        elif self._auto_grund:
            text, textfarbe = self._auto_grund, p.text_muted
        else:
            text, textfarbe = self.option.description, p.text_muted
        if not text:
            return
        kleinschrift = schrift(CAPTION)
        maler.setFont(kleinschrift)
        maler.setPen(QColor(textfarbe))
        kleinmetrik = QFontMetricsF(kleinschrift)
        kasten = QRectF(
            links,
            oben + metrik.height() + werte.space.xs,
            rechts - links,
            kleinmetrik.height() * 2,
        )
        maler.drawText(
            kasten,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
            text,
        )

    def _abzeichen(self) -> list[tuple[str, str]]:
        """Die Abzeichen dieser Karte als (Text, Rolle)."""
        eintraege: list[tuple[str, str]] = []
        if self._auto:
            eintraege.append(("automatisch", "neutral"))
        if self.option.recommended:
            eintraege.append(("Empfohlen", "accent"))
        if self.option.est_size_mb:
            eintraege.append((f"~{self.option.est_size_mb} MB", "neutral"))
        if "multilib" in self.option.repos:
            eintraege.append(("multilib", "warn"))
        if self.option.deprecated:
            eintraege.append(("veraltet", "warn"))
        return eintraege

    def _abzeichenbreite(self, werte) -> float:
        kleinmetrik = QFontMetricsF(schrift(CAPTION, fett=True))
        breite = 0.0
        for text, _rolle in self._abzeichen():
            breite += kleinmetrik.horizontalAdvance(text) + werte.space.md + werte.space.xs
        return breite

    def _zeichne_abzeichen(self, maler, flaeche, werte, p) -> None:
        eintraege = self._abzeichen()
        if not eintraege:
            return
        kleinschrift = schrift(CAPTION, fett=True)
        maler.setFont(kleinschrift)
        metrik = QFontMetricsF(kleinschrift)
        hoehe = metrik.height() + 2

        x = flaeche.right() - werte.space.md
        for text, rolle in reversed(eintraege):
            breite = metrik.horizontalAdvance(text) + werte.space.md
            kasten = QRectF(x - breite, flaeche.top() + werte.space.md, breite, hoehe)
            grund, schriftfarbe = _abzeichenfarben(rolle, p)
            maler.setPen(Qt.PenStyle.NoPen)
            maler.setBrush(grund)
            maler.drawRoundedRect(kasten, hoehe / 2, hoehe / 2)
            maler.setPen(QColor(schriftfarbe))
            maler.drawText(kasten, int(Qt.AlignmentFlag.AlignCenter), text)
            x -= breite + werte.space.xs


def _abzeichenfarben(rolle: str, p) -> tuple[QColor, str]:
    """(Hintergrund, Schrift) fuer ein Abzeichen.

    Die Schriftfarbe wird gerechnet, nicht gepflegt: weisse Schrift auf hellem
    Orange erfuellte frueher kein AA-Kontrastverhaeltnis.
    """
    from ..design.tokens import lesbare_schrift

    if rolle == "accent":
        return qfarbe(p.accent, 0.9), p.accent_text
    if rolle == "warn":
        return qfarbe(p.warning, 0.9), lesbare_schrift(p.warning)
    return qfarbe(p.text_subtle, 0.28), p.text


def _teilpfad(pfad: QPainterPath, anteil: float) -> QPainterPath:
    """Der Anfang eines Pfades -- fuer einen Haken, der sich zeichnet."""
    anteil = max(0.0, min(1.0, anteil))
    if anteil >= 1.0:
        return pfad
    ergebnis = QPainterPath()
    if anteil <= 0.0:
        return ergebnis
    schritte = 24
    ergebnis.moveTo(pfad.pointAtPercent(0.0))
    for i in range(1, schritte + 1):
        punkt: QPointF = pfad.pointAtPercent(anteil * i / schritte)
        ergebnis.lineTo(punkt)
    return ergebnis
