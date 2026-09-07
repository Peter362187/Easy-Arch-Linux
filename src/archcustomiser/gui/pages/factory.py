"""Erzeugt zu jeder Katalogkategorie die passende Seite.

Eine Registry, kein if-Baum: ein neuer Seitentyp ist ein Eintrag. Der Katalog
bestimmt ueber ``page_type``, welcher davon zum Zug kommt.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtWidgets import QWizardPage

from ...core.catalog import Category, PageType
from ..packages_worker import PackageController
from ..store import SelectionStore
from .form import CatalogFormPage
from .free_packages import FreePackagesPage
from .selection import CatalogSelectionPage
from .summary import SummaryPage

log = logging.getLogger(__name__)

PageBuilder = Callable[[Category, SelectionStore, PackageController], QWizardPage]


class PageFactory:
    def __init__(self, store: SelectionStore, controller: PackageController) -> None:
        self.store = store
        self.controller = controller
        self._builders: dict[PageType, PageBuilder] = {
            PageType.SELECTION: lambda c, s, _p: CatalogSelectionPage(c, s),
            PageType.FORM: lambda c, s, _p: CatalogFormPage(c, s),
            PageType.FREE_PACKAGES: lambda c, s, p: FreePackagesPage(c, s, p),
            PageType.SUMMARY: lambda c, s, p: SummaryPage(c, s, p),
        }

    def create(self, category: Category) -> QWizardPage | None:
        builder = self._builders.get(category.page_type)
        if builder is None:
            log.error("Kein Seitentyp fuer %r registriert", category.page_type)
            return None
        return builder(category, self.store, self.controller)
