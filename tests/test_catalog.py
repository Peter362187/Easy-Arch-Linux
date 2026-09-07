"""Tests des Katalog-Laders.

Der Lader ist die Stelle, an der Fehler frueh auffallen sollen: ein Tippfehler
in einer YAML-Datei soll beim Start eine verstaendliche Meldung erzeugen und
nicht spaeter einen AttributeError irgendwo in der Oberflaeche.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from archcustomiser.core.catalog import CatalogError, PageType, SelectionMode, load_catalog
from archcustomiser.core.catalog.predicate import parse

# ---------------------------------------------------------------------------
# Der mitgelieferte Katalog
# ---------------------------------------------------------------------------


def test_bundled_catalog_loads(catalog) -> None:
    assert catalog.categories
    assert catalog.capabilities
    assert len(list(catalog.all_options())) > 50


def test_every_reference_points_somewhere(catalog) -> None:
    """Wird bereits beim Laden geprueft -- hier als ausdrueckliche Zusicherung."""
    known = {option.ref for option in catalog.all_options()}
    for option in catalog.all_options():
        for ref in option.implies + option.recommends + option.requires + option.conflicts:
            assert ref in known, f"{option.ref} verweist auf unbekanntes {ref}"


def test_steps_are_unique(catalog) -> None:
    steps = [category.step for category in catalog.categories]
    assert len(steps) == len(set(steps))


def test_every_enabled_service_creates_at_least_one_symlink(catalog) -> None:
    """Ein Dienst ohne wanted_by und ohne Alias wuerde nie starten."""
    from archcustomiser.core.catalog import ServiceAction

    for option in catalog.all_options():
        for service in option.services:
            if service.action is ServiceAction.ENABLE:
                assert service.symlinks(), f"{option.ref}: {service.unit} erzeugt keinen Symlink"


def test_display_managers_use_an_alias(catalog) -> None:
    """sddm & Co. werden ueber display-manager.service aktiviert, nicht ueber .wants."""
    category = catalog.category("display_manager")
    assert category is not None
    for option in category.options:
        for service in option.services:
            assert "display-manager.service" in service.aliases


def test_required_single_categories_have_a_default(catalog) -> None:
    for category in catalog.categories:
        if category.required and category.selection_mode is SelectionMode.SINGLE:
            assert category.default_selection, f"{category.id} hat keine Vorgabe"


def test_form_pages_have_fields_and_selection_pages_have_options(catalog) -> None:
    for category in catalog.categories:
        if category.page_type is PageType.FORM:
            assert category.fields
        if category.page_type is PageType.SELECTION:
            assert category.options


def test_secret_fields_have_no_default(catalog) -> None:
    for category in catalog.categories:
        for spec in category.fields:
            if spec.secret:
                assert spec.default in (None, "")


# ---------------------------------------------------------------------------
# Fehlerhafte Kataloge
# ---------------------------------------------------------------------------


def write_catalog(root: Path, category: dict, options: list | None = None, index: dict | None = None) -> Path:
    (root / "categories").mkdir(parents=True, exist_ok=True)
    (root / "catalog.yaml").write_text(
        yaml.safe_dump(
            index
            or {
                "schema_version": 1,
                "catalog_version": "test",
                "name": "Test",
                "capabilities": {},
                "wizard": {"step_order": []},
                "includes": ["categories/*.yaml"],
            }
        ),
        encoding="utf-8",
    )
    (root / "categories" / "a.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "category": category, "options": options or []}),
        encoding="utf-8",
    )
    return root


def test_missing_index_gives_a_clear_message(tmp_path) -> None:
    with pytest.raises(CatalogError) as info:
        load_catalog(tmp_path, include_user_overlays=False)
    assert "catalog.yaml" in str(info.value)


def test_unsupported_schema_version_is_refused(tmp_path) -> None:
    write_catalog(
        tmp_path,
        {"id": "a", "title": "A", "step": 1},
        [{"id": "x", "label": "X"}],
        index={"schema_version": 99, "capabilities": {}, "wizard": {}},
    )
    with pytest.raises(CatalogError) as info:
        load_catalog(tmp_path, include_user_overlays=False)
    assert "schema_version" in str(info.value)


def test_dangling_reference_is_reported(tmp_path) -> None:
    write_catalog(
        tmp_path,
        {"id": "a", "title": "A", "step": 1},
        [{"id": "x", "label": "X", "implies": ["gibtes.nicht"]}],
    )
    with pytest.raises(CatalogError) as info:
        load_catalog(tmp_path, include_user_overlays=False)
    assert "gibtes.nicht" in str(info.value)


def test_enabled_service_without_install_target_is_refused(tmp_path) -> None:
    """Der haeufigste Katalogfehler -- und einer, der sonst still bleibt."""
    write_catalog(
        tmp_path,
        {"id": "a", "title": "A", "step": 1},
        [{"id": "x", "label": "X", "services": [{"unit": "foo.service", "action": "enable"}]}],
    )
    with pytest.raises(CatalogError) as info:
        load_catalog(tmp_path, include_user_overlays=False)
    assert "wanted_by" in str(info.value)


def test_default_selection_must_exist(tmp_path) -> None:
    write_catalog(
        tmp_path,
        {"id": "a", "title": "A", "step": 1, "default_selection": ["gibtesnicht"]},
        [{"id": "x", "label": "X"}],
    )
    with pytest.raises(CatalogError):
        load_catalog(tmp_path, include_user_overlays=False)


def test_duplicate_step_is_refused(tmp_path) -> None:
    root = write_catalog(tmp_path, {"id": "a", "title": "A", "step": 1}, [{"id": "x", "label": "X"}])
    (root / "categories" / "b.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "category": {"id": "b", "title": "B", "step": 1},
                "options": [{"id": "y", "label": "Y"}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CatalogError) as info:
        load_catalog(root, include_user_overlays=False)
    assert "step=1" in str(info.value)


def test_file_entry_without_content_is_refused(tmp_path) -> None:
    """Frueher gab es hier zusaetzlich ``source``.

    Das Feld wurde geparst und validiert, aber nie ausgewertet: der Eintrag
    erzeugte still keine Datei und trug trotzdem einen Rechte-Eintrag fuer
    einen Pfad ein, den es im Abbild nicht gab. Jetzt ist ``content`` der
    einzige Weg -- und sein Fehlen ein Fehler statt eines Nichts.
    """
    write_catalog(
        tmp_path,
        {"id": "a", "title": "A", "step": 1},
        [{"id": "x", "label": "X", "files": [{"target": "/etc/x"}]}],
    )
    with pytest.raises(CatalogError):
        load_catalog(tmp_path, include_user_overlays=False)


def test_relative_file_target_is_refused(tmp_path) -> None:
    write_catalog(
        tmp_path,
        {"id": "a", "title": "A", "step": 1},
        [{"id": "x", "label": "X", "files": [{"target": "etc/x", "content": "b"}]}],
    )
    with pytest.raises(CatalogError) as info:
        load_catalog(tmp_path, include_user_overlays=False)
    assert "absoluter Pfad" in str(info.value)


# ---------------------------------------------------------------------------
# Erweiterbarkeit (Spec Abschnitt 17)
# ---------------------------------------------------------------------------


def test_a_new_category_appears_without_touching_any_code(tmp_path) -> None:
    """Der Kernanspruch: neue Optionen rein ueber YAML."""
    root = write_catalog(tmp_path, {"id": "a", "title": "A", "step": 1}, [{"id": "x", "label": "X"}])
    (root / "categories" / "neu.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "category": {
                    "id": "themes",
                    "title": "Themes",
                    "step": 50,
                    "selection_mode": "multi",
                },
                "options": [
                    {"id": "papirus", "label": "Papirus", "packages": ["papirus-icon-theme"]}
                ],
            }
        ),
        encoding="utf-8",
    )
    catalog = load_catalog(root, include_user_overlays=False)
    assert catalog.category("themes") is not None
    assert catalog.option("themes.papirus").packages[0].name == "papirus-icon-theme"


def test_overlay_patches_an_existing_option(tmp_path) -> None:
    root = write_catalog(
        tmp_path,
        {"id": "a", "title": "A", "step": 1},
        [{"id": "x", "label": "Alt", "packages": ["alt"]}],
    )
    (root / "categories" / "z-overlay.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "category": {"id": "a"},
                "merge_strategy": "patch",
                "options": [{"id": "x", "label": "Neu"}],
            }
        ),
        encoding="utf-8",
    )
    catalog = load_catalog(root, include_user_overlays=False)
    option = catalog.option("a.x")
    assert option.label == "Neu"
    assert option.packages[0].name == "alt"    # nicht ueberschriebene Felder bleiben


# ---------------------------------------------------------------------------
# Praedikate
# ---------------------------------------------------------------------------


class Context:
    def __init__(self, refs=(), caps=(), fields=None) -> None:
        self.refs, self.caps, self.fields = set(refs), set(caps), fields or {}

    def is_selected(self, ref): return ref in self.refs
    def has_capability(self, name): return name in self.caps
    def field_value(self, binding): return self.fields.get(binding)


def test_predicate_forms() -> None:
    context = Context(refs={"desktop.kde"}, caps={"graphical-session"}, fields={"a.b": True})
    assert parse("desktop.kde").evaluate(context)
    assert not parse("desktop.gnome").evaluate(context)
    assert parse("cap:graphical-session").evaluate(context)
    assert parse("field:a.b").evaluate(context)
    assert parse({"any_of": ["desktop.gnome", "desktop.kde"]}).evaluate(context)
    assert parse({"all_of": ["desktop.kde", "cap:graphical-session"]}).evaluate(context)
    assert parse({"none_of": ["desktop.gnome"]}).evaluate(context)
    assert not parse({"none_of": ["desktop.kde"]}).evaluate(context)
    assert parse(None).evaluate(context)


def test_predicate_field_comparison() -> None:
    context = Context(fields={"build.uefi": "grub", "user.create": False})
    assert parse("field:build.uefi=grub").evaluate(context)
    assert not parse("field:build.uefi=systemd-boot").evaluate(context)
    assert not parse("field:user.create").evaluate(context)


def test_predicate_references_only_option_refs() -> None:
    predicate = parse({"all_of": ["desktop.kde", "cap:x", "field:y"]})
    assert predicate.references() == frozenset({"desktop.kde"})
