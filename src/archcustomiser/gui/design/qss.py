"""Erzeugt das Stylesheet der Anwendung aus den Tokens.

Eine Regel gilt im ganzen Programm: **ausserhalb dieses Pakets steht kein
einziger ``setStyleSheet``-Aufruf.** Vorher brannten rund zwanzig Stellen ihre
Farben beim Bau der Widgets ein -- ein Designwechsel zur Laufzeit blieb
deshalb wirkungslos, obwohl es dafuer eine Funktion gab, die niemand rief.

Zwei Entscheidungen, die man beim Nachbau falsch macht:

* **Fusion auf allen Plattformen.** Der Windows-Stil zeichnet Teile selbst und
  ignoriert dabei Stylesheet-Angaben; das Ergebnis sind halb gestylte Widgets.
  Fusion respektiert Palette und Stylesheet ueberall gleich.
* **Der Fokusrahmen ist immer da.** Er ist im Ruhezustand nur durchsichtig.
  Waere er es nicht, spraenge das Widget beim Fokussieren um zwei Pixel --
  genau die Art Zappeln, die eine Tastaturbedienung unbrauchbar macht.
"""

from __future__ import annotations

from .tokens import Tokens


def build_stylesheet(tokens: Tokens) -> str:
    """Das komplette Stylesheet als Zeichenkette."""
    p = tokens.palette
    s = tokens.space
    r = tokens.radius

    return f"""
/* --- Grundlage ------------------------------------------------------- */
QWidget {{
    background: transparent;
    color: {p.text};
}}
QMainWindow, QDialog {{
    background: {p.bg};
}}
QToolTip {{
    background: {p.surface_alt};
    color: {p.text};
    border: 1px solid {p.border};
    border-radius: {r.sm}px;
    padding: {s.xs}px {s.sm}px;
}}

/* --- Schaltflaechen --------------------------------------------------- */
QPushButton {{
    background: {p.surface_alt};
    color: {p.text};
    border: 2px solid transparent;
    border-radius: {r.md}px;
    padding: {s.sm}px {s.lg}px;
    min-height: 18px;
}}
QPushButton:hover {{
    background: {p.border};
}}
QPushButton:pressed {{
    background: {p.border_strong};
}}
QPushButton:disabled {{
    color: {p.text_subtle};
    background: {p.surface};
}}
QPushButton:focus {{
    border-color: {p.accent};
}}
QPushButton[variant="primary"] {{
    background: {p.accent};
    color: {p.accent_text};
    font-weight: 600;
}}
QPushButton[variant="primary"]:hover {{
    background: {p.accent_hover};
}}
QPushButton[variant="primary"]:pressed {{
    background: {p.accent_pressed};
}}
QPushButton[variant="primary"]:disabled {{
    background: {p.surface_alt};
    color: {p.text_subtle};
}}
QPushButton[variant="ghost"] {{
    background: transparent;
    color: {p.text_muted};
}}
QPushButton[variant="ghost"]:hover {{
    background: {p.surface_alt};
    color: {p.text};
}}
QPushButton[variant="danger"] {{
    background: {p.danger};
    /* Nicht fest weiss: auf dem dunklen Rot #f0736a erreicht Weiss nur
       2.85:1 -- unter jeder Schwelle. Welche Schrift lesbar ist, rechnet die
       Palette aus. */
    color: {p.danger_text};
}}
QPushButton[variant="danger"]:hover {{
    background: {p.danger};
    color: {p.danger_text};
}}
QPushButton[variant="danger"]:pressed {{
    background: {p.danger};
    color: {p.danger_text};
}}
QPushButton[variant="danger"]:disabled {{
    background: {p.surface_alt};
    color: {p.text_subtle};
}}
/* Der Fokusrahmen muss auf der gefuellten Flaeche sichtbar bleiben. Die
   allgemeine Regel faerbt ihn im Akzent -- auf einem akzentfarbenen Knopf
   war er damit unsichtbar. */
QPushButton[variant="primary"]:focus {{
    border-color: {p.accent_text};
}}
QPushButton[variant="danger"]:focus {{
    border-color: {p.danger_text};
}}

/* --- Eingabefelder ---------------------------------------------------- */
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{
    background: {p.surface};
    color: {p.text};
    border: 2px solid {p.border};
    border-radius: {r.md}px;
    padding: {s.xs}px {s.sm}px;
    selection-background-color: {p.accent};
    selection-color: {p.accent_text};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QSpinBox:focus, QComboBox:focus {{
    border-color: {p.accent};
}}
QLineEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled,
QSpinBox:disabled, QComboBox:disabled {{
    background: {p.surface_alt};
    color: {p.text_subtle};
}}
QComboBox::drop-down {{
    border: none;
    width: 20px;
}}
QComboBox QAbstractItemView {{
    background: {p.surface};
    color: {p.text};
    border: 1px solid {p.border};
    selection-background-color: {p.accent_soft};
    selection-color: {p.text};
    outline: none;
}}

/* --- Ankreuzfelder ---------------------------------------------------- */
QCheckBox, QRadioButton {{
    spacing: {s.sm}px;
    background: transparent;
}}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border: 2px solid {p.border_strong};
    background: {p.surface};
}}
QCheckBox::indicator {{
    border-radius: {r.sm}px;
}}
QRadioButton::indicator {{
    border-radius: 9px;
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background: {p.accent};
    border-color: {p.accent};
}}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    border-color: {p.border};
    background: {p.surface_alt};
}}
/* Gesperrt UND angehakt. Ohne diese Regel gewinnt die Regel darueber bei
   gleicher Spezifitaet allein durch ihre Position, und ein automatisch
   ergaenztes, gesperrtes Haekchen sah aus wie ein leeres. */
QCheckBox::indicator:checked:disabled, QRadioButton::indicator:checked:disabled {{
    background: {p.border_strong};
    border-color: {p.border_strong};
}}

/* --- Listen und Baeume ------------------------------------------------ */
QTreeWidget, QTreeView, QListWidget, QListView, QTableView {{
    background: {p.surface};
    alternate-background-color: {p.surface_alt};
    border: 1px solid {p.border};
    border-radius: {r.md}px;
    outline: none;
}}
QTreeView::item, QListView::item {{
    padding: 3px;
    border-radius: {r.sm}px;
}}
QTreeView::item:selected, QListView::item:selected {{
    background: {p.accent_soft};
    color: {p.text};
}}
QHeaderView::section {{
    background: {p.surface_alt};
    color: {p.text_muted};
    border: none;
    border-bottom: 1px solid {p.border};
    padding: {s.xs}px {s.sm}px;
}}

/* --- Registerkarten --------------------------------------------------- */
QTabWidget::pane {{
    border: 1px solid {p.border};
    border-radius: {r.md}px;
    top: -1px;
}}
QTabBar::tab {{
    background: transparent;
    color: {p.text_muted};
    padding: {s.sm}px {s.lg}px;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{
    color: {p.text};
    border-bottom-color: {p.accent};
}}
QTabBar::tab:hover {{
    color: {p.text};
}}

/* --- Bildlaufleisten -------------------------------------------------- */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {p.border_strong};
    border-radius: 5px;
    min-height: 32px;
    min-width: 32px;
}}
QScrollBar::handle:hover {{
    background: {p.text_subtle};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
    width: 0;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

/* --- Sonstiges -------------------------------------------------------- */
QGroupBox {{
    border: 1px solid {p.border};
    border-radius: {r.md}px;
    margin-top: {s.md}px;
    padding-top: {s.md}px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: {s.md}px;
    padding: 0 {s.xs}px;
    color: {p.text_muted};
}}
QSplitter::handle {{
    background: {p.border};
}}
QSplitter::handle:horizontal {{
    width: 1px;
}}
QSplitter::handle:vertical {{
    height: 1px;
}}
QProgressBar {{
    background: {p.surface_alt};
    border: none;
    border-radius: {r.sm}px;
    height: 6px;
    text-align: center;
    color: {p.text_muted};
}}
QProgressBar::chunk {{
    background: {p.accent};
    border-radius: {r.sm}px;
}}
QMenu {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: {r.md}px;
    padding: {s.xs}px;
}}
QMenu::item {{
    padding: {s.xs}px {s.lg}px;
    border-radius: {r.sm}px;
}}
QMenu::item:selected {{
    background: {p.accent_soft};
}}
QMenuBar {{
    background: {p.bg};
}}
QMenuBar::item:selected {{
    background: {p.surface_alt};
    border-radius: {r.sm}px;
}}
QLabel[rolle="titel"] {{
    font-weight: 700;
}}
QLabel[rolle="gedaempft"] {{
    color: {p.text_muted};
}}
QLabel[rolle="fehler"] {{
    color: {p.danger};
}}
QLabel[rolle="warnung"] {{
    color: {p.warning};
}}
QLabel[rolle="erfolg"] {{
    color: {p.success};
}}
QLabel[rolle="akzent"] {{
    color: {p.accent_lesbar};
}}
"""
