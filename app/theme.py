"""
Theme system — exact port of zone_app's index.css + tailwind.config.js.

Three themes:
  - "offwhite" (default): warm cream page background, matches the
    web-app screenshots.
  - "white":              pure white, for printing / high-light use.
  - "dark":               near-black with cool tint.

Switching themes is a single call: ``theme.apply(app, "dark")``. The
QSS is rebuilt with the chosen palette and applied to QApplication so
every widget restyles in one shot.

ROOM_COLORS are theme-independent (a fixed material palette) and match
the existing PySide apps' palette + the web ``roomColor()`` helper.
"""

from typing import Dict, Tuple

from PySide6.QtGui import QColor


# ─────────────────────────────────────────────────────────────────────
#  Palettes — RGB triplets ported verbatim from index.css
# ─────────────────────────────────────────────────────────────────────

PALETTES: Dict[str, Dict[str, Tuple[int, int, int]]] = {
    "offwhite": {
        "bg":          (250, 246, 239),
        "surface":     (255, 253, 248),
        "surface2":    (244, 239, 230),
        "border":      (226, 219, 206),
        "ink":         ( 26,  26,  26),
        "ink2":        (102, 102, 102),
        "ink3":        (153, 153, 153),
        "accent":      (181,  36,  62),
        "accent-2":    (154,  30,  52),
        "accent-soft": (254, 242, 244),
        "good":        ( 22, 163,  74),
        "warn":        (217, 119,   6),
        "bad":         (220,  38,  38),
        "teal":        ( 13, 148, 136),
    },
    "white": {
        "bg":          (255, 255, 255),
        "surface":     (255, 255, 255),
        "surface2":    (248, 248, 248),
        "border":      (230, 230, 230),
        "ink":         ( 26,  26,  26),
        "ink2":        ( 90,  90,  90),
        "ink3":        (160, 160, 160),
        "accent":      (181,  36,  62),
        "accent-2":    (154,  30,  52),
        "accent-soft": (254, 242, 244),
        "good":        ( 22, 163,  74),
        "warn":        (217, 119,   6),
        "bad":         (220,  38,  38),
        "teal":        ( 13, 148, 136),
    },
    "dark": {
        "bg":          ( 18,  18,  20),
        "surface":     ( 27,  27,  30),
        "surface2":    ( 38,  38,  43),
        "border":      ( 56,  56,  63),
        "ink":         (240, 240, 240),
        "ink2":        (170, 170, 175),
        "ink3":        (120, 120, 125),
        "accent":      (220,  75, 100),
        "accent-2":    (240, 105, 130),
        "accent-soft": ( 52,  22,  30),
        "good":        ( 74, 222, 128),
        "warn":        (251, 191,  36),
        "bad":         (248, 113, 113),
        "teal":        ( 45, 212, 191),
    },
}

THEME_LABELS = {"offwhite": "Off-white", "white": "White", "dark": "Dark"}
THEME_CYCLE  = ["offwhite", "white", "dark"]

# Room-type colours: theme-independent. Match Area_distribution_*.py
# and the web app's roomColor() exactly.
ROOM_COLORS = {
    "living":   "#8d746f",
    "kitchen":  "#e0a03c",
    "bedroom":  "#c0413f",
    "bathroom": "#e7a9b3",
    "dining":   "#9a6f52",
    "study":    "#5f7d9a",
    "office":   "#4f9d8e",
    "laundry":  "#7d9bb5",
    "other":    "#9aa0a6",
    "balcony":  "#9e9e9e",
    "storage":  "#e3c24a",
    "stair":    "#4d4d4d",
    "veranda":  "#66c2a5",
    "garden":   "#6b6b6b",
    "parking":  "#7d7d7d",
    "pool":     "#5fb0d6",
    "outside":  "#6f6f6f",     # matches upstream Connectivity tool
}


def color(theme_name: str, key: str, alpha: float = 1.0) -> QColor:
    """Return a QColor for the given palette key, optionally with alpha."""
    r, g, b = PALETTES[theme_name][key]
    return QColor(r, g, b, int(alpha * 255))


def room_qcolor(room_type: str) -> QColor:
    """Return a QColor for a room type (theme-independent)."""
    hex_str = ROOM_COLORS.get(room_type.lower(), ROOM_COLORS["other"])
    return QColor(hex_str)


def _rgb(palette: Dict[str, Tuple[int, int, int]],
         key: str, alpha: float = 1.0) -> str:
    r, g, b = palette[key]
    if alpha >= 0.999:
        return f"rgb({r},{g},{b})"
    return f"rgba({r},{g},{b},{alpha:.3f})"


def build_qss(theme_name: str) -> str:
    """Compose the full Qt stylesheet for the given theme."""
    p = PALETTES[theme_name]
    def c(k, a=1.0): return _rgb(p, k, a)

    return f"""
    /* ─── BASE ───────────────────────────────────────────────────── */
    QMainWindow, QWidget {{
        background: {c("bg")};
        color: {c("ink")};
        font-family: "Inter", "Segoe UI", system-ui, sans-serif;
        font-size: 12px;
    }}

    /* ─── HEADER ─────────────────────────────────────────────────── */
    QFrame#AppHeader {{
        background: {c("surface")};
        border-bottom: 1px solid {c("border")};
    }}
    QLabel#AppTitle {{
        color: {c("accent")};
        font-size: 17px;
        font-weight: 600;
    }}
    QLabel#AppSubtitle {{
        color: {c("teal")};
        font-size: 11px;
    }}
    QFrame#HeaderSep {{
        background: {c("border")};
        min-width: 1px;
        max-width: 1px;
    }}

    /* ─── PILL BUTTONS ──────────────────────────────────────────── */
    QPushButton[pillRole="default"] {{
        background: {c("surface2")};
        border: 1px solid {c("border")};
        color: {c("ink")};
        border-radius: 12px;
        padding: 4px 12px;
        font-size: 12px;
    }}
    QPushButton[pillRole="default"]:hover {{
        background: {c("accent-soft")};
        border-color: {c("accent")};
        color: {c("accent")};
    }}

    QPushButton[pillRole="danger"] {{
        background: {c("surface2")};
        border: 1px solid {c("border")};
        color: {c("ink2")};
        border-radius: 12px;
        padding: 4px 12px;
        font-size: 12px;
    }}
    QPushButton[pillRole="danger"]:hover {{
        border-color: {c("bad")};
        color: {c("bad")};
    }}

    QPushButton[pillRole="theme"] {{
        background: {c("accent")};
        color: white;
        border: none;
        border-radius: 12px;
        padding: 4px 14px;
        font-size: 12px;
    }}
    QPushButton[pillRole="theme"]:hover {{
        background: {c("accent-2")};
    }}

    QPushButton[pillRole="primary"] {{
        background: {c("accent")};
        color: white;
        border: none;
        border-radius: 6px;
        padding: 6px 14px;
        font-size: 12px;
        font-weight: 600;
    }}
    QPushButton[pillRole="primary"]:hover {{
        background: {c("accent-2")};
    }}
    QPushButton[pillRole="primary"]:disabled {{
        background: {c("ink3")};
        color: {c("surface")};
    }}

    QPushButton[pillRole="ghost"] {{
        background: transparent;
        border: 1px solid {c("border")};
        color: {c("ink2")};
        border-radius: 10px;
        padding: 3px 10px;
        font-size: 11px;
    }}
    QPushButton[pillRole="ghost"]:hover {{
        border-color: {c("accent")};
        color: {c("accent")};
    }}

    /* ─── STAGE CARDS ───────────────────────────────────────────── */
    QFrame[stageCard="true"] {{
        background: {c("surface")};
        border: 1px solid {c("border")};
        border-radius: 8px;
    }}
    QFrame#StageHeader {{
        background: transparent;
    }}
    QFrame#StageDivider {{
        background: {c("border")};
        max-height: 1px;
        min-height: 1px;
    }}
    QLabel[stageNumber="true"] {{
        color: {c("ink3")};
        font-family: "JetBrains Mono", Consolas, monospace;
        font-size: 11px;
    }}
    QLabel[stageTitle="true"] {{
        color: {c("ink")};
        font-size: 13px;
        font-weight: 600;
    }}
    QLabel[stageSubtitle="true"] {{
        color: {c("ink3")};
        font-size: 11px;
    }}

    QLabel[statusDot="green"]  {{ color: {c("good")}; font-size: 14px; }}
    QLabel[statusDot="yellow"] {{ color: {c("warn")}; font-size: 14px; }}
    QLabel[statusDot="grey"]   {{ color: {c("ink3")}; font-size: 14px; }}

    QLabel[placeholder="true"] {{
        color: {c("ink3")};
        font-size: 12px;
        padding: 40px;
    }}

    /* ─── SECTION HEADERS (inside Inputs panel, etc.) ───────────── */
    QLabel[sectionHeader="true"] {{
        color: {c("ink3")};
        font-size: 10px;
        font-weight: 600;
        letter-spacing: 1px;
    }}
    QLabel[fieldLabel="true"] {{
        color: {c("ink3")};
        font-size: 9px;
        font-weight: 600;
        letter-spacing: 1px;
    }}
    QLabel[sectionSubtitle="true"] {{
        color: {c("ink3")};
        font-size: 10px;
        font-weight: 400;
        letter-spacing: 0;
    }}
    QLabel[caret="true"] {{
        color: {c("ink3")};
        font-size: 10px;
    }}
    QLabel[monoValue="true"] {{
        color: {c("ink")};
        font-family: "JetBrains Mono", Consolas, monospace;
        font-size: 11px;
    }}

    /* ─── COUNTER ± BUTTONS (room-count rows) ───────────────────── */
    QPushButton[counterBtn="true"] {{
        background: transparent;
        border: 1px solid {c("border")};
        color: {c("ink2")};
        border-radius: 10px;
        padding: 0;
        font-size: 14px;
        font-weight: 500;
    }}
    QPushButton[counterBtn="true"]:hover {{
        border-color: {c("accent")};
        color: {c("accent")};
    }}

    /* ─── UNITS SEGMENTED TOGGLE (px / m / ft) ──────────────────── */
    QPushButton[unitsToggle="true"] {{
        background: transparent;
        color: {c("ink2")};
        border: none;
        border-radius: 11px;
        padding: 3px 12px;
        font-size: 11px;
    }}
    QPushButton[unitsToggle="true"][unitsActive="true"] {{
        background: {c("accent")};
        color: white;
    }}
    QPushButton[unitsToggle="true"]:hover {{
        color: {c("accent")};
    }}
    QPushButton[unitsToggle="true"][unitsActive="true"]:hover {{
        color: white;
    }}

    /* ─── LINK-STYLE BUTTON (Reset to defaults, etc.) ───────────── */
    QPushButton[linkBtn="true"] {{
        background: transparent;
        border: none;
        color: {c("ink3")};
        font-size: 11px;
        padding: 4px 0;
    }}
    QPushButton[linkBtn="true"]:hover {{
        color: {c("accent")};
    }}

    /* ─── INPUTS PANEL BOTTOM BAR ───────────────────────────────── */
    QFrame#InputsBottomBar {{
        background: {c("surface")};
        border-top: 1px solid {c("border")};
    }}

    /* ─── AREA STAGE (Stage 1) ──────────────────────────────────── */
    QFrame[areaCard="true"] {{
        background: {c("surface")};
        border: 1px solid {c("border")};
        border-radius: 6px;
    }}
    QFrame[areaCard="true"]:hover {{
        border-color: {c("accent", 0.45)};
    }}
    QFrame[areaCard="true"][areaCardSelected="true"] {{
        border-color: {c("accent")};
        border-width: 1px;
        background: {c("surface")};
    }}
    QFrame#AreaCardHeader {{
        background: {c("surface2")};
        border-bottom: 1px solid {c("border")};
    }}
    QFrame#AreaCardBody {{
        background: {c("surface2", 0.35)};
    }}
    QLabel[areaCardIdx="true"] {{
        color: {c("ink2")};
        font-family: "JetBrains Mono", Consolas, monospace;
        font-size: 11px;
    }}
    QLabel[areaCardSummary="true"] {{
        color: {c("ink3")};
        font-size: 10px;
    }}
    QLabel[selectedBadge="true"] {{
        background: {c("ink3", 0.22)};
        color: {c("ink2")};
        font-size: 9px;
        font-weight: 700;
        letter-spacing: 1px;
        padding: 1px 6px;
        border-radius: 7px;
    }}
    QFrame[areaComparison="true"] {{
        background: {c("surface2", 0.4)};
        border: 1px solid {c("border")};
        border-radius: 4px;
    }}
    QFrame#AreaToolbar {{
        background: {c("surface")};
        border-top: 1px solid {c("border")};
    }}
    QFrame#AreaGenInputs {{
        background: {c("surface")};
        border-bottom: 1px solid {c("border")};
    }}
    QFrame#AreaModeBar {{
        background: {c("surface")};
        border-bottom: 1px solid {c("border")};
    }}
    QFrame#AreaDrawPanel {{
        background: {c("surface")};
    }}
    QLabel[mockPill="true"] {{
        background: {c("warn", 0.12)};
        color: {c("warn")};
        border: 1px solid {c("warn", 0.35)};
        border-radius: 9px;
        padding: 2px 8px;
        font-size: 10px;
    }}

    /* ─── ZONE STAGE (Stage 2) ──────────────────────────────────── */
    QFrame[zoneCard="true"] {{
        background: {c("surface")};
        border: 1px solid {c("border")};
        border-radius: 6px;
    }}
    QFrame[zoneCard="true"]:hover {{
        border-color: {c("ink3", 0.6)};
    }}
    /* Selection is deliberately quiet: a mid-grey border and a faint
       grey wash, so the accent red is reserved for metrics and actions. */
    QFrame[zoneCard="true"][zoneCardSelected="true"] {{
        border: 1.5px solid {c("ink3")};
        background: {c("surface2", 0.55)};
    }}
    QFrame#ZoneCardHeader {{
        background: {c("surface2")};
        border-bottom: 1px solid {c("border")};
    }}
    QFrame#ZoneCardBodyTop {{
        background: {c("surface")};
        border-bottom: 1px solid {c("border")};
    }}
    QLabel[zoneCardIdx="true"] {{
        color: {c("ink2")};
        font-family: "JetBrains Mono", Consolas, monospace;
        font-size: 11px;
    }}
    QLabel[zoneMetricLabel="true"] {{
        color: {c("ink3")};
        font-size: 9px;
        font-weight: 600;
        letter-spacing: 1px;
    }}
    QLabel[zoneMetricValue="true"] {{
        color: {c("ink")};
        font-family: "JetBrains Mono", Consolas, monospace;
        font-size: 11px;
        font-weight: 600;
    }}
    QLabel[zoneMetricValue="true"][zoneMetricAccent="true"] {{
        color: {c("accent")};
    }}
    QLabel[zoneMetricSuffix="true"] {{
        color: {c("ink3")};
        font-size: 9px;
    }}
    QFrame#ZoneToolbar {{
        background: {c("surface")};
        border-top: 1px solid {c("border")};
    }}

    /* ─── TOGGLE CHIPS (Zone weight / Select all / etc.) ──────── */
    QPushButton[toggleChip="true"] {{
        background: {c("surface2")};
        color: {c("ink2")};
        border: 1px solid {c("border")};
        border-radius: 11px;
        padding: 3px 10px;
        font-size: 11px;
    }}
    QPushButton[toggleChip="true"]:hover {{
        border-color: {c("accent", 0.45)};
        color: {c("accent")};
    }}
    QPushButton[toggleChip="true"][toggleChipActive="true"] {{
        background: {c("accent-soft")};
        color: {c("accent")};
        border-color: {c("accent")};
    }}
    QPushButton[toggleChip="true"]:disabled {{
        color: {c("ink3")};
        background: {c("surface")};
    }}

    /* ─── DESIGN EXPLORER (collapsible parcoords) ──────────────── */
    QFrame#DesignExplorerPanel {{
        background: {c("surface")};
        border-top: 1px solid {c("border")};
    }}
    QFrame#ExplorerHeader {{
        background: {c("surface")};
    }}
    QFrame#ExplorerHeader:hover {{
        background: {c("surface2")};
    }}
    QFrame#ExplorerDivider {{
        background: {c("border")};
        max-height: 1px;
        min-height: 1px;
    }}
    QFrame#ExplorerBody {{
        background: {c("surface")};
    }}
    QFrame#ParcoordsSummary {{
        background: {c("surface2", 0.5)};
        border-bottom: 1px solid {c("border")};
    }}
    QFrame#ParcoordsReadout {{
        background: {c("surface")};
        border-top: 1px solid {c("border")};
        max-height: 22px;
        min-height: 22px;
    }}
    QLabel[linkHint="true"] {{
        color: {c("ink3")};
        font-size: 10px;
    }}
    QLabel[linkHint="true"]:hover {{
        color: {c("accent")};
    }}

    /* ─── CONNECTIVITY STAGE (Stage 3) ──────────────────────────── */
    /* plain canvas: no card outline or panel fills, only a quiet border
       when selected */
    QFrame[connCard="true"] {{
        background: transparent;
        border: 1px solid transparent;
        border-radius: 6px;
    }}
    QFrame[connCard="true"]:hover {{
        border-color: {c("ink3", 0.35)};
    }}
    /* Selection is deliberately quiet: a mid-grey border and a faint
       grey wash, so the accent red is reserved for metrics and actions. */
    QFrame[connCard="true"][connCardSelected="true"] {{
        border: 1px solid transparent;      /* selection shown by the badge only */
        background: transparent;
    }}
    QFrame#ConnCardHeader {{
        background: transparent;
        border: none;
    }}
    QFrame#ConnCardBody {{
        background: transparent;
    }}
    QFrame#ConnCardFooter {{
        background: transparent;
        border: none;
    }}
    QLabel[connFooter="true"] {{
        color: {c("ink2")};
        font-size: 10px;
    }}
    QFrame#ConnToolbar {{
        background: {c("surface")};
        border-top: 1px solid {c("border")};
    }}
    QFrame#ConnToolbar QSpinBox {{
        background: {c("surface2")};
        color: {c("ink")};
        border: 1px solid {c("border")};
        border-radius: 3px;
        padding: 2px 4px;
        selection-background-color: {c("accent-soft")};
    }}
    QFrame#ConnToolbar QSpinBox:hover {{
        border-color: {c("accent", 0.45)};
    }}
    QFrame#ConnToolbar QSpinBox::up-button,
    QFrame#ConnToolbar QSpinBox::down-button {{
        width: 12px;
    }}

    /* ─── SCHEMATIC STAGE (Stage 4) ─────────────────────────────── */
    /* plain canvas: no card outline or panel fills, only a quiet border
       when selected */
    QFrame[schemCard="true"] {{
        background: transparent;
        border: 1px solid transparent;
        border-radius: 6px;
    }}
    QFrame[schemCard="true"]:hover {{
        border-color: {c("ink3", 0.35)};
    }}
    /* Selection is deliberately quiet: a mid-grey border and a faint
       grey wash, so the accent red is reserved for metrics and actions. */
    QFrame[schemCard="true"][schemCardSelected="true"] {{
        border: 1px solid transparent;      /* selection shown by the badge only */
        background: transparent;
    }}
    QFrame#SchemCardHeader {{
        background: transparent;
        border: none;
    }}
    QFrame#SchemCardBody {{
        background: transparent;
    }}
    QFrame#SchemCardFooter {{
        background: transparent;
        border: none;
    }}
    QFrame#SchemToolbar {{
        background: {c("surface")};
        border-top: 1px solid {c("border")};
    }}
    QFrame#ParetoWrap {{
        background: {c("surface")};
        border-top: 1px solid {c("border")};
    }}
    QFrame#ParetoHeader {{
        background: {c("surface")};
        border-bottom: 1px solid {c("border")};
    }}

    /* ─── STAGE 4 NEW TOOLBAR CONTROLS ──────────────────────────── */
    QFrame#ToolbarSep {{
        background: {c("border")};
        max-width: 1px;
        min-width: 1px;
    }}

    /* Mode pill toggle (Bubble / Rectangle) */
    QFrame#ModePill {{
        background: {c("surface2")};
        border-radius: 11px;
    }}
    QPushButton[pillToggle="true"] {{
        background: transparent;
        color: {c("ink2")};
        border: none;
        border-radius: 9px;
        padding: 2px 10px;
        font-size: 11px;
    }}
    QPushButton[pillToggle="true"]:hover {{
        color: {c("accent")};
    }}
    QPushButton[pillToggle="true"][pillActive="true"] {{
        background: {c("accent")};
        color: white;
    }}
    QPushButton[pillToggle="true"][pillActive="true"]:hover {{
        background: {c("accent-2")};
        color: white;
    }}
    QPushButton[pillToggle="true"]:disabled {{
        color: {c("ink3")};
    }}

    /* Alignment buttons (N S E W C) */
    QPushButton[alignBtn="true"] {{
        background: {c("surface")};
        color: {c("ink2")};
        border: 1px solid {c("border")};
        border-radius: 3px;
        min-width: 22px;
        max-width: 22px;
        min-height: 22px;
        max-height: 22px;
        padding: 0px;
        font-size: 11px;
        font-weight: 600;
    }}
    QPushButton[alignBtn="true"]:hover {{
        border-color: {c("accent", 0.45)};
        color: {c("accent")};
    }}
    QPushButton[alignBtn="true"][alignActive="true"] {{
        background: {c("accent")};
        color: white;
        border-color: {c("accent")};
    }}
    QPushButton[alignBtn="true"]:disabled {{
        background: {c("surface2")};
        color: {c("ink3")};
        border-color: {c("border")};
    }}

    /* Schematic card footer metrics */
    QLabel[schemMetricLbl="true"] {{
        color: {c("ink3")};
        font-size: 9px;
    }}
    QLabel[schemMetricVal="true"] {{
        color: {c("ink")};
        font-family: "JetBrains Mono", Consolas, monospace;
        font-size: 10px;
        font-weight: 600;
    }}
    QLabel[schemMetricVal="true"][schemMetricAccent="true"] {{
        color: {c("accent")};
    }}
    QLabel[schemRoomsLbl="true"] {{
        color: {c("ink3")};
        font-size: 9px;
    }}

    /* ─── INPUT WIDGETS ─────────────────────────────────────────── */
    QSpinBox, QDoubleSpinBox, QLineEdit, QComboBox {{
        background: {c("surface")};
        border: 1px solid {c("border")};
        border-radius: 4px;
        padding: 3px 6px;
        color: {c("ink")};
        font-size: 11px;
        selection-background-color: {c("accent-soft")};
        selection-color: {c("accent")};
    }}
    QSpinBox:focus, QDoubleSpinBox:focus,
    QLineEdit:focus, QComboBox:focus {{
        border-color: {c("accent")};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 18px;
        subcontrol-origin: padding;
        subcontrol-position: top right;
    }}
    QComboBox::down-arrow {{
        image: none;
        width: 0; height: 0;
        border-left:  3px solid transparent;
        border-right: 3px solid transparent;
        border-top:   4px solid {c("ink3")};
        margin-right: 6px;
    }}
    QComboBox::down-arrow:hover {{
        border-top-color: {c("ink2")};
    }}
    QComboBox QAbstractItemView {{
        background: {c("surface")};
        border: 1px solid {c("border")};
        selection-background-color: {c("accent-soft")};
        selection-color: {c("accent")};
    }}

    /* ─── Spin box arrows — slim, minimal, light ─────────────────
       Suppress Qt's native chevron bitmaps and draw the arrows from
       CSS borders (the standard "triangle from a zero-size element
       with thick borders" trick — light, deterministic, no asset
       files needed). Buttons are thin column on the right, arrows
       use the ink3 colour so they recede until hovered. The right
       padding makes room for the buttons; NoButtons spinboxes (used
       by the room-count widgets, which have their own ± pills next
       to them) are exempted so their narrow 36-px field can still
       fit a one- or two-digit number. */
    QSpinBox, QDoubleSpinBox {{
        padding-right: 13px;
    }}
    QSpinBox[buttonSymbols="2"], QDoubleSpinBox[buttonSymbols="2"] {{
        padding-right: 6px;
    }}
    QSpinBox::up-button, QDoubleSpinBox::up-button {{
        subcontrol-origin: border;
        subcontrol-position: top right;
        width: 11px;
        border: none;
        background: transparent;
        margin: 0;
    }}
    QSpinBox::down-button, QDoubleSpinBox::down-button {{
        subcontrol-origin: border;
        subcontrol-position: bottom right;
        width: 11px;
        border: none;
        background: transparent;
        margin: 0;
    }}
    QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
    QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
        background: {c("surface2")};
    }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
        image: none;
        width: 0; height: 0;
        border-left:  3px solid transparent;
        border-right: 3px solid transparent;
        border-bottom: 4px solid {c("ink3")};
    }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
        image: none;
        width: 0; height: 0;
        border-left:  3px solid transparent;
        border-right: 3px solid transparent;
        border-top:   4px solid {c("ink3")};
    }}
    QSpinBox::up-arrow:hover, QDoubleSpinBox::up-arrow:hover {{
        border-bottom-color: {c("ink2")};
    }}
    QSpinBox::down-arrow:hover, QDoubleSpinBox::down-arrow:hover {{
        border-top-color: {c("ink2")};
    }}
    QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled {{
        border-bottom-color: {c("border")};
    }}
    QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {{
        border-top-color: {c("border")};
    }}

    QCheckBox {{ color: {c("ink")}; font-size: 11px; }}
    QCheckBox::indicator {{
        width: 13px; height: 13px;
        border-radius: 3px;
        border: 1px solid {c("border")};
        background: {c("surface")};
    }}
    QCheckBox::indicator:checked {{
        background: {c("accent")};
        border-color: {c("accent")};
    }}

    /* ─── SCROLLBARS — thin and quiet, matching index.css ───────── */
    QScrollArea {{ background: transparent; border: none; }}
    QScrollBar:vertical {{
        background: transparent; width: 8px; margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {c("border")}; border-radius: 4px; min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {c("ink3")}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
    }}
    QScrollBar:horizontal {{
        background: transparent; height: 8px; margin: 0;
    }}
    QScrollBar::handle:horizontal {{
        background: {c("border")}; border-radius: 4px; min-width: 30px;
    }}
    QScrollBar::handle:horizontal:hover {{ background: {c("ink3")}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
        width: 0;
    }}

    /* ─── SLIDERS — light grey track, accent handle ─────────────── */
    QSlider::groove:horizontal {{
        background: {c("border", 0.35)};
        height: 3px;
        border-radius: 2px;
        margin: 0;
    }}
    QSlider::sub-page:horizontal {{
        background: {c("border", 0.55)};
        height: 3px;
        border-radius: 2px;
    }}
    QSlider::add-page:horizontal {{
        background: {c("border", 0.25)};
        height: 3px;
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background: {c("surface")};
        border: 1.5px solid {c("ink3")};
        width: 10px; height: 10px;
        margin: -5px 0;
        border-radius: 6px;
    }}
    QSlider::handle:horizontal:hover {{
        border-color: {c("accent")};
    }}
    QSlider::handle:horizontal:pressed {{
        background: {c("accent-soft")};
        border-color: {c("accent")};
    }}

    /* ─── TOOLTIPS ──────────────────────────────────────────────── */
    QToolTip {{
        background: {c("surface")};
        color: {c("ink")};
        border: 1px solid {c("border")};
        padding: 3px 6px;
    }}
    """


def apply(app, theme_name: str) -> None:
    """Apply ``theme_name`` to the running QApplication."""
    if theme_name not in PALETTES:
        theme_name = "offwhite"
    app.setStyleSheet(build_qss(theme_name))


def next_theme(current: str) -> str:
    """Return the next theme in the cycle: offwhite → white → dark → offwhite."""
    try:
        i = THEME_CYCLE.index(current)
    except ValueError:
        i = -1
    return THEME_CYCLE[(i + 1) % len(THEME_CYCLE)]
