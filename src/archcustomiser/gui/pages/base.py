"""Gemeinsame Basis aller Seiten der neuen Oberflaeche.

Eine Seite ist ein gewoehnliches ``QWidget`` -- kein ``QWizardPage`` mehr. Was
frueher Qt uebernahm (Titel, Untertitel, Weiter-Sperre), macht jetzt die
Navigation; die Seite meldet nur, ob sie vollstaendig ist.

Die Regel von vorher gilt unveraendert weiter und ist der Grund, warum ein
Sprung ueberhaupt gefahrlos ist: **Seiten halten keinen eigenen Zustand.** Sie
zeichnen sich beim Betreten aus dem Store neu. Damit ist die Frage "was ist
beim Zurueckblaettern mit meinen Eingaben?" keine Frage mehr.

Zwei Seiten gehoeren zu keiner Katalogkategorie -- die Startseite und die
Bauseite. Sie erben trotzdem von hier, damit die Navigation nur eine Sorte
Seite kennt; ``category`` ist dann ``None``.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from ...core.catalog import Category
from ...core.resolver import Issue
from ..design import tokens
from ..design.typo import CAPTION, schrift
from ..store import SelectionStore
from ..widgets.issue_banner import IssueBanner

log = logging.getLogger(__name__)


class PageBase(QWidget):
    """Basis fuer alle Seiten des Hauptfensters."""

    completeChanged = Signal()

    def __init__(self, category: Category | None, store: SelectionStore) -> None:
        super().__init__()
        self.category = category
        self.store = store
        self._local_issues: tuple[Issue, ...] = ()

        werte = tokens()
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        self._root.setSpacing(werte.space.sm)

        self.banner = IssueBanner()
        self.banner.fixRequested.connect(self.store.apply_fix)
        self._root.addWidget(self.banner)

        self.store.issuesChanged.connect(self._refresh_issues)

    # -- Kennzeichnung --------------------------------------------------------
    @property
    def category_id(self) -> str:
        return self.category.id if self.category is not None else ""

    def titel(self) -> str:
        return self.category.title if self.category is not None else ""

    def untertitel(self) -> str:
        return self.category.subtitle if self.category is not None else ""

    # -- Lebenszyklus ---------------------------------------------------------
    def enter(self) -> None:
        """Wird beim Betreten gerufen -- die Seite holt sich alles aus dem Store."""
        self.sync_from_store()
        self._refresh_issues()

    def leave(self) -> bool:
        """Darf die Seite verlassen werden?

        Nur die Startseite sagt hier jemals Nein: dort haengt ein Dateidialog
        daran, den man abbrechen kann.
        """
        return True

    def sync_from_store(self) -> None:
        """Widgets an den Store angleichen -- von Unterklassen zu fuellen."""

    def is_complete(self) -> bool:
        return not any(problem.blocking for problem in self._store_issues()) and not any(
            problem.blocking for problem in self._local_issues
        )

    def _store_issues(self) -> tuple[Issue, ...]:
        if self.category is None:
            return ()
        return self.store.issues(self.category.id)

    # -- Meldungen ------------------------------------------------------------
    def set_local_issues(self, issues: tuple[Issue, ...]) -> None:
        """Meldungen, die nur diese Seite kennt.

        Feldfehler und ungueltige Paketnamen sperrten den Weiter-Knopf, ohne
        dass an prominenter Stelle stand, warum: die Begruendung hing an der
        betroffenen Zeile, und die lag im Formular womoeglich ausserhalb des
        sichtbaren Ausschnitts. Ueber diesen Weg landen sie zusaetzlich oben
        in der Hinweisleiste.
        """
        self._local_issues = issues
        self._refresh_issues()

    def local_issues(self) -> tuple[Issue, ...]:
        return self._local_issues

    def _refresh_issues(self) -> None:
        issues = self._store_issues() + self._local_issues
        self.banner.set_issues(issues)
        self.completeChanged.emit()

    # -- Bausteine ------------------------------------------------------------
    def add_help_link(self) -> None:
        if self.category is None or not self.category.help_url:
            return
        ziel = self.category.help_url
        link = QLabel(f'<a href="{ziel}">Weitere Informationen: {_gastgeber(ziel)}</a>')
        link.setOpenExternalLinks(True)
        # Ohne diese Flagge ist der Link nur mit der Maus erreichbar -- er
        # kommt gar nicht erst in die Tabreihenfolge.
        link.setTextInteractionFlags(
            Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        )
        link.setFont(schrift(CAPTION))
        self._root.addWidget(link)


def _gastgeber(url: str) -> str:
    """Der Rechnername einer Adresse -- als Beschriftung des Links.

    "Arch-Wiki" stand fest im Code, obwohl ``help_url`` aus dem Katalog kommt
    und ueberallhin zeigen darf. Bei einem Overlay, das auf eine eigene Seite
    verweist, war die Beschriftung schlicht falsch.
    """
    from urllib.parse import urlparse

    name = urlparse(url).netloc
    return name.removeprefix("www.") or url


__all__ = ["PageBase"]
