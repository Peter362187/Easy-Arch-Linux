"""Die Schrittliste am linken Rand.

Fuenf Zustaende, die sich sichtbar unterscheiden muessen -- und einer davon ist
der Grund, warum die Liste ueberhaupt so viel Aufmerksamkeit bekommt:
**uebersprungen**. Schritte, die unter der aktuellen Auswahl nicht vorkommen,
standen frueher unveraendert da. Wer keine grafische Sitzung gewaehlt hatte,
wartete auf die Seite "Grafiktreiber", die nie kommt.

Neu gegenueber der alten Liste: **Vorwaertsspringen ist erlaubt.** Die Seiten
holen ihren Inhalt ohnehin aus dem Store; es gibt keinen Grund, jemanden durch
vierzehn Schritte zu fuehren, der weiss, was er will.

Selbstgezeichnet statt sechzehn ``QToolButton``: die Zeilen tragen einen
Zustandsring, eine Nummer und eine Beschriftung, und der Ring soll sich beim
Wechsel fuellen koennen.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..design import qfarbe, tokens
from ..design.typo import BODY, CAPTION, SUBTITLE, schrift
from ..navigation import Art, NavigationModel, Status
from .icons import load_icon

ZEILENHOEHE = 34
# Die Symbole des Katalogs, in Zeilenhoehe.
SYMBOLGROESSE = 16


class StepSidebar(QWidget):
    """Zeigt alle Schritte, ihren Zustand und den Gesamtfortschritt."""

    stepClicked = Signal(str)

    def __init__(self, model: NavigationModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.model = model
        self._zustaende: dict[str, Status] = {}
        self._anklickbar: set[str] = set()
        self._notiz = ""
        self._unter_maus = ""

        self._fokus = ""

        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setMinimumWidth(self._natuerliche_breite())
        # Die Liste ist ein vollwertiges Bedienelement, kein Bild: sie gehoert
        # in die Tabreihenfolge. Vorher war sie nur mit der Maus erreichbar --
        # wer die Tastatur benutzt, hatte gar keine Schrittnavigation.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Schritte")
        self.setAccessibleDescription(
            "Pfeiltasten waehlen einen Schritt, Eingabetaste springt hin."
        )

    # -- oeffentlich ----------------------------------------------------------
    def aktualisieren(self) -> None:
        """Zustaende aus dem Modell uebernehmen."""
        self._zustaende = self.model.statuses()
        self._anklickbar = {
            schritt.id for schritt in self.model.steps if self.model.anklickbar(schritt)
        }
        if self._fokus and self._fokus not in self._anklickbar:
            # Der Schritt unter dem Tastaturfokus ist gerade weggefallen.
            erreichbar = self._erreichbare()
            self._fokus = erreichbar[0] if erreichbar else ""
        self.update()

    def set_notice(self, text: str) -> None:
        """Eine Randnotiz unter der Liste -- fuer Auskuenfte zur ganzen Sitzung."""
        if text == self._notiz:
            return
        self._notiz = text
        self.update()

    # -- Groesse --------------------------------------------------------------
    def _natuerliche_breite(self) -> int:
        """An der laengsten Beschriftung ausrichten, nicht an einer festen Zahl.

        Bei groesserer Systemschrift wurden die Titel sonst abgeschnitten.
        """
        metrik = QFontMetricsF(schrift(BODY))
        breite = max(
            (metrik.horizontalAdvance(f"{i}. {schritt.titel}")
             for i, schritt in enumerate(self.model.steps, start=1)),
            default=160.0,
        )
        werte = tokens()
        # 26 fuer die Zustandsmarke, dazu Platz fuer das Symbol der Kategorie.
        return int(breite + 26 + SYMBOLGROESSE + werte.space.lg * 3)

    def sizeHint(self) -> QSize:
        hoehe = len(self.model.steps) * ZEILENHOEHE + 120
        return QSize(self._natuerliche_breite(), hoehe)

    # -- Ereignisse -----------------------------------------------------------
    def _schritt_bei(self, y: float) -> str:
        oben = self._listenanfang()
        index = int((y - oben) // ZEILENHOEHE)
        if 0 <= index < len(self.model.steps):
            return self.model.steps[index].id
        return ""

    def _listenanfang(self) -> float:
        werte = tokens()
        metrik = QFontMetricsF(schrift(CAPTION))
        return werte.space.xl + metrik.height() + werte.space.lg + 8

    # -- Tastatur -------------------------------------------------------------
    def _erreichbare(self) -> list[str]:
        return [
            schritt.id for schritt in self.model.steps if schritt.id in self._anklickbar
        ]

    def focusInEvent(self, event) -> None:
        if not self._fokus:
            erreichbar = self._erreichbare()
            self._fokus = self.model.current_id if self.model.current_id in erreichbar else (
                erreichbar[0] if erreichbar else ""
            )
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event) -> None:
        self.update()
        super().focusOutEvent(event)

    def keyPressEvent(self, event) -> None:
        erreichbar = self._erreichbare()
        if not erreichbar:
            super().keyPressEvent(event)
            return

        taste = event.key()
        if taste in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            richtung = 1 if taste == Qt.Key.Key_Down else -1
            wo = erreichbar.index(self._fokus) if self._fokus in erreichbar else 0
            # Uebersprungene und gesperrte Schritte kommen in ``erreichbar``
            # gar nicht vor -- die Pfeiltasten gehen also von selbst darueber
            # hinweg, genau wie der Weiter-Knopf.
            self._fokus = erreichbar[max(0, min(len(erreichbar) - 1, wo + richtung))]
            self.update()
            event.accept()
            return
        if taste == Qt.Key.Key_Home:
            self._fokus = erreichbar[0]
            self.update()
            event.accept()
            return
        if taste == Qt.Key.Key_End:
            self._fokus = erreichbar[-1]
            self.update()
            event.accept()
            return
        if taste in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            if self._fokus:
                self.stepClicked.emit(self._fokus)
                event.accept()
                return
        super().keyPressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        step_id = self._schritt_bei(event.position().y())
        if step_id not in self._anklickbar:
            step_id = ""
        if step_id != self._unter_maus:
            self._unter_maus = step_id
            self.setCursor(
                Qt.CursorShape.PointingHandCursor if step_id else Qt.CursorShape.ArrowCursor
            )
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        self._unter_maus = ""
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        step_id = self._schritt_bei(event.position().y())
        if step_id and step_id in self._anklickbar:
            self.stepClicked.emit(step_id)
        super().mousePressEvent(event)

    # -- Zeichnen -------------------------------------------------------------
    def paintEvent(self, event) -> None:
        werte = tokens()
        p = werte.palette
        maler = QPainter(self)
        maler.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        maler.setPen(Qt.PenStyle.NoPen)
        maler.setBrush(QColor(p.surface))
        maler.drawRect(self.rect())

        self._zeichne_fortschritt(maler, werte, p)

        oben = self._listenanfang()
        # Gezaehlt werden nur die Kategorien. Der Startschritt traegt keine
        # Nummer -- aus ``enumerate`` ueber alle Schritte wurde sonst
        # "2. Grundkonfiguration" fuer den ersten Schritt, den man einstellt.
        nummer = 0
        for schritt in self.model.steps:
            if schritt.art is Art.CATEGORY:
                nummer += 1
            zustand = self._zustaende.get(schritt.id, Status.OFFEN)
            self._zeichne_zeile(maler, werte, p, schritt, nummer, oben, zustand)
            oben += ZEILENHOEHE

        if self._notiz:
            self._zeichne_notiz(maler, werte, p)
        maler.end()

    def _zeichne_fortschritt(self, maler, werte, p) -> None:
        erledigt, gesamt = self.model.progress()
        maler.setFont(schrift(CAPTION))
        maler.setPen(QColor(p.text_muted))
        text = f"{erledigt} von {gesamt} erledigt" if gesamt else "bereit"
        metrik = QFontMetricsF(schrift(CAPTION))
        maler.drawText(
            QRectF(werte.space.lg, werte.space.xl, self.width() - werte.space.lg * 2, metrik.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            text,
        )

        balken = QRectF(
            werte.space.lg,
            werte.space.xl + metrik.height() + werte.space.xs,
            self.width() - werte.space.lg * 2,
            4.0,
        )
        maler.setPen(Qt.PenStyle.NoPen)
        maler.setBrush(qfarbe(p.text_subtle, 0.22))
        maler.drawRoundedRect(balken, 2, 2)
        if gesamt:
            gefuellt = QRectF(balken)
            gefuellt.setWidth(balken.width() * erledigt / gesamt)
            maler.setBrush(QColor(p.accent))
            maler.drawRoundedRect(gefuellt, 2, 2)

    def _zeichne_zeile(self, maler, werte, p, schritt, nummer, oben, zustand) -> None:
        zeile = QRectF(
            werte.space.sm, oben, self.width() - werte.space.sm * 2, ZEILENHOEHE - 2
        )

        if zustand is Status.AKTUELL:
            maler.setPen(Qt.PenStyle.NoPen)
            maler.setBrush(qfarbe(p.accent, 0.16))
            maler.drawRoundedRect(zeile, werte.radius.md, werte.radius.md)
        elif schritt.id == self._unter_maus:
            maler.setPen(Qt.PenStyle.NoPen)
            maler.setBrush(qfarbe(p.text_subtle, 0.10))
            maler.drawRoundedRect(zeile, werte.radius.md, werte.radius.md)

        if self.hasFocus() and schritt.id == self._fokus:
            maler.setBrush(Qt.BrushStyle.NoBrush)
            maler.setPen(QPen(QColor(p.accent), 2.0))
            maler.drawRoundedRect(
                zeile.adjusted(1, 1, -1, -1), werte.radius.md, werte.radius.md
            )

        farbe = {
            Status.AKTUELL: p.text,
            Status.ERLEDIGT: p.success,
            Status.FEHLER: p.danger,
            Status.UEBERSPRUNGEN: p.text_subtle,
            Status.GESPERRT: p.text_subtle,
            Status.OFFEN: p.text_muted,
        }[zustand]

        self._zeichne_marke(maler, werte, p, zeile, zustand, farbe)

        # Das Symbol der Kategorie -- der Katalog vergibt es, die alte
        # Schrittliste zeigte es, und beim Umbau ging es zunaechst verloren.
        links = zeile.left() + 26 + werte.space.sm
        symbol = load_icon(schritt.icon, farbe, SYMBOLGROESSE) if schritt.icon else None
        if symbol is not None:
            kasten = QRectF(
                links,
                zeile.center().y() - SYMBOLGROESSE / 2,
                float(SYMBOLGROESSE),
                float(SYMBOLGROESSE),
            )
            symbol.paint(maler, kasten.toRect(), Qt.AlignmentFlag.AlignCenter)
            links += SYMBOLGROESSE + werte.space.xs

        maler.setFont(schrift(BODY, fett=zustand is Status.AKTUELL))
        maler.setPen(QColor(farbe))
        textkasten = QRectF(
            links,
            zeile.top(),
            zeile.right() - links - werte.space.sm,
            zeile.height(),
        )
        beschriftung = f"{nummer}. {schritt.titel}" if schritt.art is Art.CATEGORY else schritt.titel
        metrik = QFontMetricsF(maler.font())
        maler.drawText(
            textkasten,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            metrik.elidedText(beschriftung, Qt.TextElideMode.ElideRight, textkasten.width()),
        )

        if zustand is Status.UEBERSPRUNGEN:
            # Durchgestrichen, damit klar ist: der kommt nicht.
            breite = min(metrik.horizontalAdvance(beschriftung), textkasten.width())
            y = textkasten.center().y()
            maler.setPen(QPen(QColor(farbe), 1.0))
            maler.drawLine(
                QPointF(textkasten.left(), y), QPointF(textkasten.left() + breite, y)
            )

    def _zeichne_marke(self, maler, werte, p, zeile, zustand, farbe) -> None:
        seite = 16.0
        kreis = QRectF(
            zeile.left() + werte.space.sm,
            zeile.center().y() - seite / 2,
            seite,
            seite,
        )
        maler.setBrush(Qt.BrushStyle.NoBrush)
        maler.setPen(QPen(QColor(farbe), 1.8))

        if zustand is Status.ERLEDIGT:
            maler.setBrush(QColor(farbe))
            maler.drawEllipse(kreis)
            pfad = QPainterPath()
            pfad.moveTo(kreis.left() + 4.0, kreis.center().y())
            pfad.lineTo(kreis.center().x() - 0.5, kreis.bottom() - 4.5)
            pfad.lineTo(kreis.right() - 3.5, kreis.top() + 4.5)
            maler.setBrush(Qt.BrushStyle.NoBrush)
            maler.setPen(QPen(QColor(p.surface), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            maler.drawPath(pfad)
        elif zustand is Status.FEHLER:
            maler.drawEllipse(kreis)
            rand = 4.5
            maler.drawLine(
                QPointF(kreis.left() + rand, kreis.top() + rand),
                QPointF(kreis.right() - rand, kreis.bottom() - rand),
            )
            maler.drawLine(
                QPointF(kreis.right() - rand, kreis.top() + rand),
                QPointF(kreis.left() + rand, kreis.bottom() - rand),
            )
        elif zustand is Status.AKTUELL:
            maler.setBrush(QColor(p.accent))
            maler.setPen(QPen(QColor(p.accent), 1.8))
            maler.drawEllipse(kreis.adjusted(3, 3, -3, -3))
            maler.setBrush(Qt.BrushStyle.NoBrush)
            maler.drawEllipse(kreis)
        elif zustand is Status.GESPERRT:
            maler.drawEllipse(kreis.adjusted(2, 2, -2, -2))
        else:
            maler.drawEllipse(kreis.adjusted(2, 2, -2, -2))

    def _zeichne_notiz(self, maler, werte, p) -> None:
        maler.setFont(schrift(CAPTION))
        metrik = QFontMetricsF(maler.font())
        kasten = QRectF(
            werte.space.lg,
            self.height() - werte.space.xl - metrik.height() * 3,
            self.width() - werte.space.lg * 2,
            metrik.height() * 3,
        )
        maler.setPen(QColor(p.warning))
        maler.drawText(
            kasten,
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
            self._notiz,
        )


class Kopfzeile(QWidget):
    """Titel und Untertitel einer Seite -- selbst gezeichnet, damit sie mitwaechst."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._titel = ""
        self._untertitel = ""
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._hoehe_setzen()

    def setze(self, titel: str, untertitel: str = "") -> None:
        self._titel = titel
        self._untertitel = untertitel
        self._hoehe_setzen()
        self.update()

    def _hoehe_setzen(self) -> None:
        werte = tokens()
        hoehe = QFontMetricsF(schrift(SUBTITLE, fett=True)).height()
        if self._untertitel:
            hoehe += QFontMetricsF(schrift(CAPTION)).height() * 2 + werte.space.xs
        self.setFixedHeight(int(hoehe + werte.space.sm))

    def paintEvent(self, event) -> None:
        werte = tokens()
        p = werte.palette
        maler = QPainter(self)
        maler.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        maler.setFont(schrift(SUBTITLE, fett=True))
        maler.setPen(QColor(p.text))
        metrik = QFontMetricsF(maler.font())
        maler.drawText(
            QRectF(0, 0, self.width(), metrik.height()),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            self._titel,
        )

        if self._untertitel:
            maler.setFont(schrift(CAPTION))
            maler.setPen(QColor(p.text_muted))
            klein = QFontMetricsF(maler.font())
            maler.drawText(
                QRectF(
                    0,
                    metrik.height() + werte.space.xs,
                    self.width(),
                    klein.height() * 2,
                ),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
                self._untertitel,
            )
        maler.end()
