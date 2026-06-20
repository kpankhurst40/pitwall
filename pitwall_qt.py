#!/usr/bin/env python3
# =============================================================================
# Pitwall - the PySIDE6 / QT widget.
# =============================================================================
# This is the widget "face" over the shared brain in ts_core.py. PySide6 is
# DPI-perfect, anti-aliases shapes, gives real styling (rounded translucent
# always-on-top window, smooth bars), and animates the heat pulse smoothly.
#
# It needs PySide6 installed (the packaged .exe lane bundles it; the source lane
# auto-detects/auto-installs it via the first-run chooser - built AFTER this face).
#
# WHERE THE NUMBERS COME FROM: every $ / token / pace / session figure comes from
# ts_core (the same brain the Tkinter face uses), so the two faces can NEVER drift
# apart. This file is pure presentation - it recomputes nothing.
#
# WHAT'S BUILT vs DEFERRED (2026-06-06, Sarah's "PySide6 face" spec governs):
#   BUILT : full card (header, Block A "spend now", a minimal Block B sync line,
#           Block C sessions + reset + rotating tips), the compact strip, the
#           Token Details window, the full Settings dialog (name/tagline/plan/
#           sync-calibration/weekly/drift editors - Sarah spec §8), the heat ring
#           + pulse, the save pill, the A-/A+ text ladder, drag-anywhere + nodrag
#           controls, width-resize with auto-fit height, single-instance guard,
#           and a hidden --demo arg.
#   DEFER : the 1->2->3 column reflow of the MAIN card (single column for now;
#           Sarah's §1c covers it). The Settings dialog itself DOES go 2-col at
#           large text sizes (§8.5). Autonomous mode is a separate later feature.
# =============================================================================

import html
import math
import os
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone, timedelta

# Reuse the shared brain (ts_core) - NOT the Tkinter face, so this lane never
# drags tkinter in. ts_core imports no GUI toolkit.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ts_core as ts  # noqa: E402

from PySide6.QtCore import (  # noqa: E402
    Qt, QTimer, QRect, QRectF, QEvent, QSize, QPoint, QProcess,
    QPropertyAnimation, QEasingCurve, QParallelAnimationGroup,
)
from PySide6.QtGui import (  # noqa: E402
    QFont, QFontDatabase, QFontMetrics, QCursor, QColor, QPalette,
    QPainter, QPen,
)
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QWidget, QFrame, QLabel, QHBoxLayout, QVBoxLayout, QGridLayout,
    QSizePolicy, QMessageBox, QDialog, QScrollArea, QLineEdit, QPushButton,
    QSpacerItem, QPlainTextEdit, QStackedWidget, QAbstractButton, QMenu,
    QSlider,
)

# This face carries its own standalone-component version, tracked in the version
# matrix doc. It is NOT shown in the header (owner, 2026-06-06: both looks show the
# name only, matching the Lite face) - kept here as the component version of record.
QT_FACE_VERSION = "0.1"

# --- shape constants (Pitwall reskin E, 2026-06-11) --------------------------
RING_W = 3.0           # heat-ring pen width (QPen). RESTORED to 3px (owner 2026-06-12,
                       # "we lost the heat ring"): the 2026-06-11 Fluent reskin had thinned
                       # it to a 1px hairline that barely showed the heat colour. 3px is the
                       # historical heat-ring weight; owner's bold-over-muted call wins over
                       # the O3 "1px" spec.
HALO_PX = 10           # transparent outer margin around the card (hosts the window shadow)
CARD_RADIUS = 8        # card corner radius; OverlayCornerRadius per O3 Fluent spec
# Demo MODE flag (set True in main() on `--demo-mode`). Module-level so a theme rebuild,
# which reconstructs StewardQt() with no args, still comes back up as the demo construct.
_DEMO_MODE = False
# Settings → Pitstop pane "full documentation" link target. GitHub renders the page
# (works from a phone too); if the repo slug is ever renamed, GitHub redirects.
PITSTOP_DOC_URL = ("https://github.com/kpankhurst40/pitwall/"
                   "blob/main/docs/pitstop.md")

RESIZE_MARGIN = 8      # px from the RIGHT edge that starts a width-resize (either view)
LEFT_RESIZE_MARGIN = 8 # px from the LEFT edge that starts a width-resize (either view).
                       # BOTH edges are grabbable in BOTH views (owner, 2026-06-06): drag
                       # either side to set this view's width. Flat px, NOT font-scaled —
                       # it's a physical pointer target. Sarah 2026-06-06.
DEFAULT_BASE_W = 364   # base card width at 100%; scales with the text ladder
COLLAPSED_MIN_W = 194  # min collapsed-strip width at 100% (countdown must never clip); scales w/ ladder
COLLAPSED_MAX_W = 434  # max collapsed-strip width at 100% (past this is just ragged gutter)


# --- pulse animation ----------------------------------------------------------
# Drives ONLY the conversation-heat trio: the ring background, the save pill
# background, and the "this chat" dot. Both speed and intensity grow with the
# heat fraction f (ctx / ctx_red): perfectly still when the chat is fresh, fast
# and white-hot near a hand-off. Numbers are Sarah's confirmed shipping values.
def pulse_params(f):
    """(frequency Hz, amplitude 0..1) for heat fraction f; below a floor -> static."""
    if f < 0.08:
        return 0.0, 0.0            # a fresh chat must be perfectly still
    e = f * f                      # ease-in: calm low, ramps hard near red
    freq = 0.35 + (2.30 - 0.35) * e   # ~0.35 Hz slow breath -> ~2.3 Hz urgent throb
    amp = 0.12 + (0.85 - 0.12) * e    # gentle shimmer -> strong throb
    return freq, amp


def _clamp8(x):
    return max(0, min(255, int(round(x))))


def whiten(base_hex, t):
    """Blend base_hex toward white by t in [0,1]. Static brighten for the resize-grip
    hover (the same whitening the pulse does on its up-beat, minus the pulse's f term).
    Sarah's grip feedback, 2026-06-06: instant, no thickness change, no motion."""
    r, g, b = ts._rgb(base_hex)
    return "#%02x%02x%02x" % (_clamp8(r + (255 - r) * t),
                              _clamp8(g + (255 - g) * t),
                              _clamp8(b + (255 - b) * t))


def pulse_color(base_hex, level, f):
    """Modulate `base_hex` by `level` in [-amp, +amp]: whiten on the up-beat (more
    so at high f, for the 'white-hot' near a hand-off), darken on the down-beat."""
    r, g, b = ts._rgb(base_hex)
    if level >= 0:
        t = level * (0.25 + 0.55 * f)            # whiten; hotter near red
        r = r + (255 - r) * t
        g = g + (255 - g) * t
        b = b + (255 - b) * t
    else:
        k = 1.0 + level * (0.35 + 0.35 * f)      # darken (level is negative)
        r, g, b = r * k, g * k, b * k
    return "#%02x%02x%02x" % (_clamp8(r), _clamp8(g), _clamp8(b))


# ---------------------------------------------------------------------------
# Pitwall palette (Kusama "Infinity Dots", owner pick 2026-06-12 — candidate 5
# of the artist bake-off, docs/_artist_mockups.py). True black / gallery white,
# signal heat ramp at full saturation; light *_TXT variants darkened for 4.5:1.
# All hex values are exact sRGB as approved in the mockup — do NOT re-derive.
# ---------------------------------------------------------------------------

PITWALL = {
    "dark": {
        "BG":         "#0A0A0A",
        "PANEL":      "#161616",
        "PANEL_HI":   "#262626",
        "WELL":       "#020202",
        "WELL_SH":    "#000000",
        "EDGE":       "#303030",
        "INK":        "#FFFFFF",
        "MUT":        "#BABABA",
        "FAINT":      "#787878",
        "GREEN":      "#00E676",
        "AMBER":      "#FFB300",
        "RED":        "#FF3355",
        "GREEN_TXT":  "#00E676",
        "AMBER_TXT":  "#FFB300",
        "RED_TXT":    "#FF3355",
        "MODEL_COLORS": {
            "fable":  "#00D8E8",
            "opus":   "#BF8CFF",
            "sonnet": "#4D9FFF",
            "haiku":  "#00E676",
        },
        "SEAM_LT":    "rgba(255,255,255,22)",
        "WK":         "#909090",
        "CTL_STROKE":    "rgba(255,255,255,20)",
        "CTL_STROKE_HI": "rgba(255,255,255,26)",
        "PANE":       "#141414",
    },
    "light": {
        "BG":         "#F0F0F0",
        "PANEL":      "#FFFFFF",
        "PANEL_HI":   "#F4F4F4",
        "WELL":       "#DEDEDE",
        "WELL_SH":    "#BBBBBB",
        "EDGE":       "#CACACA",
        "INK":        "#000000",
        "MUT":        "#444444",
        "FAINT":      "#6F6F6F",
        "GREEN":      "#009E52",
        "AMBER":      "#E59D00",
        "RED":        "#E60026",
        "GREEN_TXT":  "#007A3F",
        "AMBER_TXT":  "#8F6200",
        "RED_TXT":    "#BF0020",
        "MODEL_COLORS": {
            "fable":  "#0093A0",
            "opus":   "#8040C9",
            "sonnet": "#1A66D9",
            "haiku":  "#009E52",
        },
        "SEAM_LT":    "rgba(0,0,0,15)",
        "WK":         "#5A5A5A",
        "CTL_STROKE":    "rgba(0,0,0,15)",
        "CTL_STROKE_HI": "rgba(0,0,0,41)",
        "PANE":       "#F4F4F4",
        "PRESSED":    "#E2E2E2",
    },
}


class _P:
    """O3 Pitwall tokens — populated by _apply_pitwall_palette()."""
    WELL        = "#020202"
    WELL_SH     = "#000000"
    PANEL_HI    = "#262626"
    SEAM_LT     = "rgba(255,255,255,22)"
    WK          = "#909090"
    CTL_STROKE    = "rgba(255,255,255,20)"
    CTL_STROKE_HI = "rgba(255,255,255,26)"
    PANE        = "#141414"
    GREEN_TXT   = "#00E676"
    AMBER_TXT   = "#FFB300"
    RED_TXT     = "#FF3355"
    PRESSED     = "#E2E2E2"   # light only; dark uses BG
    ACCENT      = "#60CDFF"   # overwritten at apply time (user's Windows accent, §2)
    KEY_TEXT    = "#1B1B1B"


P = _P()


def _alpha(hexcolor, a):
    """Return '#AARRGGBB' from a '#RRGGBB' hex and alpha in [0,1].
    Used for the pill border/background where alpha tracks the live heat color."""
    try:
        h = hexcolor.lstrip("#")
        aa = max(0, min(255, int(round(a * 255))))
        return f"#{aa:02X}{h}"
    except Exception:
        return hexcolor


def _darken(base_hex, t):
    """Blend base_hex toward black by t in [0,1] — whiten()'s twin, for the
    light-theme accent adaptation (O3 spec §2.3)."""
    r, g, b = ts._rgb(base_hex)
    return "#%02x%02x%02x" % (_clamp8(r * (1 - t)),
                              _clamp8(g * (1 - t)),
                              _clamp8(b * (1 - t)))


def _rel_lum(hexcolor):
    """WCAG relative luminance of an #RRGGBB color."""
    def chan(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = ts._rgb(hexcolor)
    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)


def _contrast(hex_a, hex_b):
    """WCAG contrast ratio between two #RRGGBB colors (always >= 1.0)."""
    la, lb = _rel_lum(hex_a), _rel_lum(hex_b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


_ACCENT_FALLBACK = "#0078D4"   # Windows default blue (O3 spec §2.2)


def _windows_accent():
    """The user's own Windows accent color as '#RRGGBB'.

    Reads HKCU\\Software\\Microsoft\\Windows\\DWM → AccentColor (REG_DWORD,
    ABGR layout 0xAABBGGRR — the alpha byte is ignored). READ-ONLY, one value,
    HKCU only. ANY failure (key/value missing, wrong type, access error) falls
    back to Windows default blue — a registry hiccup must never crash or delay
    the widget."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\DWM") as k:
            v, vtype = winreg.QueryValueEx(k, "AccentColor")
        if vtype != winreg.REG_DWORD:
            return _ACCENT_FALLBACK
        v = int(v)
        return "#%02X%02X%02X" % (v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF)
    except Exception:
        return _ACCENT_FALLBACK


def _derive_accent(theme, bg):
    """Theme-adapt the base accent (O3 spec §2.3): step toward white (dark) /
    black (light) in +0.05 increments until it reads >= 4.5:1 against the card
    BG; an accent that already passes is used as-is. Returns (ACCENT, KEY_TEXT)
    where KEY_TEXT is whichever of #1B1B1B / #FFFFFF contrasts harder against
    the final accent — it governs every accent fill (chips, Save key, switch)."""
    base = _windows_accent()
    if base.upper() == _ACCENT_FALLBACK:
        # Default blue: use Windows' own published tints (the mockup's exact
        # values) rather than the derivation — pixel-faithful where the
        # authoritative mockup specified. Both clear 4.5:1 against their BG.
        accent = "#60CDFF" if theme == "dark" else "#005FB8"
    else:
        accent, t = base, 0.0
        step = whiten if theme == "dark" else _darken
        while _contrast(accent, bg) < 4.5 and t < 1.0:
            t += 0.05
            accent = step(base, t)
    key_text = ("#1B1B1B"
                if _contrast("#1B1B1B", accent) >= _contrast("#FFFFFF", accent)
                else "#FFFFFF")
    return accent, key_text


# Tooltips composite on a window background we cannot repaint (it draws black no
# matter what QSS/palette say — proven by the 2026-06-12 orange/purple test). So
# tooltip text is pinned to this light value in BOTH themes; it's the one tooltip
# color Qt actually honors, and it's readable on the black the window forces.
TIP_TEXT = "#EDEDED"


def _apply_pitwall_palette(theme):
    """Overlay the ts module globals with the Pitwall graphite palette for `theme`.
    Call immediately after ts.set_theme() in StewardQt.__init__ — covers System/Dark/Light
    because __init__ is always called fresh on a theme rebuild. The Tk Lite face runs in its
    own process and never sees this overlay — 'one brain, two faces' intact."""
    tok = PITWALL[theme]
    ts.BG    = tok["BG"]
    ts.PANEL = tok["PANEL"]
    ts.EDGE  = tok["EDGE"]
    ts.INK   = tok["INK"]
    ts.MUT   = tok["MUT"]
    ts.FAINT = tok["FAINT"]
    ts.GREEN = tok["GREEN"]
    ts.AMBER = tok["AMBER"]
    ts.RED   = tok["RED"]
    ts.ACCENT = tok["INK"]          # belt-and-braces: every remaining ts.ACCENT → INK
    mc = dict(ts.MODEL_COLORS)
    mc.update(tok["MODEL_COLORS"])
    ts.MODEL_COLORS = mc
    # expose the new O3 tokens in the P namespace
    P.WELL        = tok["WELL"]
    P.WELL_SH     = tok["WELL_SH"]
    P.PANEL_HI    = tok["PANEL_HI"]
    P.SEAM_LT     = tok["SEAM_LT"]
    P.WK          = tok["WK"]
    P.CTL_STROKE    = tok["CTL_STROKE"]
    P.CTL_STROKE_HI = tok["CTL_STROKE_HI"]
    P.PANE        = tok["PANE"]
    P.GREEN_TXT   = tok["GREEN_TXT"]
    P.AMBER_TXT   = tok["AMBER_TXT"]
    P.RED_TXT     = tok["RED_TXT"]
    P.PRESSED     = tok.get("PRESSED", ts.BG)   # light only; dark falls back to BG
    # O3: the user's Windows accent, theme-adapted (lead-built — spec §2).
    # Re-read on every apply: a theme rebuild re-derives the accent for free.
    P.ACCENT, P.KEY_TEXT = _derive_accent(theme, tok["BG"])
    # Tooltips render on a BLACK window background we cannot repaint (proven by
    # the orange/purple diagnostic, 2026-06-12 — see the block below). So the
    # readable contract is LIGHT TEXT on that black, identical in both themes.
    app = QApplication.instance()
    if app:
        app.setStyleSheet(
            "QToolTip { background-color:%s; color:%s; "
            "border:1px solid %s; padding:5px 8px; }"
            # Diagnostic 2026-06-12 (Kevin's orange/purple controllability test)
            # PROVED the tooltip WINDOW renders its background BLACK regardless of
            # QSS or palette — it's a translucent floating window and the bg paint
            # never fills. Only the TEXT color is controllable (rich-text span).
            # So we design FOR the black: light text on black, both themes. The
            # bg value below is honest intent (a near-black) but the window draws
            # pure black anyway; what matters is TIP_TEXT being light.
            % ("#15181C", TIP_TEXT, tok["EDGE"]))
        # The palette roles are set too — belt-and-suspenders, and honest intent
        # if the background ever becomes paintable — but the diagnostic showed the
        # window draws black regardless. TIP_TEXT (light) is what makes it legible.
        pal = app.palette()
        pal.setColor(QPalette.ToolTipBase, QColor("#15181C"))   # honest intent;
        pal.setColor(QPalette.ToolTipText, QColor(TIP_TEXT))    # window draws black
        app.setPalette(pal)


def _rich(text):
    """Escape plain tooltip text into wrap-friendly rich text for the custom hint
    surface (_show_hint). The hint's QLabel carries its own INK color, so unlike
    the old QToolTip path there's no cascade to fight and no color span needed."""
    return "<qt>" + html.escape(text).replace("\n", "<br>") + "</qt>"


def _tip(text):
    """Pass a tooltip string through unchanged. Card tooltips no longer use Qt's native
    QToolTip — its floating window composites to BLACK and ignores all background styling,
    so in the light theme it drew a white-on-black box that also covered the rows it
    described (owner cli details4.mp4). StewardQt.eventFilter now intercepts QEvent.ToolTip
    and shows the custom OPAQUE hint instead (real light PANEL bg + INK text, parked beside
    the card). The hint escapes via _rich, so no span / pre-escaping is needed here. Kept as
    the single sanctioned tooltip-text entry point (the parked lint test asserts no
    setToolTip bypasses it). Any tooltip not on the card still falls back to the app-level
    QToolTip QSS — light-on-black, the old contract ([[tooltip-background-unpaintable]])."""
    return text


def lerp_txt(frac):
    """lerp_color twin for heat-colored text. Lerps over P.GREEN_TXT →
    P.AMBER_TXT → P.RED_TXT. In dark theme the trio equals the base trio, so
    this helper is theme-blind at call sites — one code path (O3 spec §3.4)."""
    frac = max(0.0, min(1.0, frac))
    if frac <= 0.5:
        t = frac * 2.0
        a = ts._rgb(P.GREEN_TXT)
        b = ts._rgb(P.AMBER_TXT)
    else:
        t = (frac - 0.5) * 2.0
        a = ts._rgb(P.AMBER_TXT)
        b = ts._rgb(P.RED_TXT)
    r = int(round(a[0] + (b[0] - a[0]) * t))
    g = int(round(a[1] + (b[1] - a[1]) * t))
    bv = int(round(a[2] + (b[2] - a[2]) * t))
    return "#%02x%02x%02x" % (_clamp8(r), _clamp8(g), _clamp8(bv))


def _well_qss(radius):
    """Well (inset sink) QSS string — flat fill + 1px stroke per O3 spec §3.3.
    The shallow parameter is retired; call sites that passed it are swept."""
    return (f"background:{P.WELL}; border:1px solid {P.WELL_SH}; "
            f"border-radius:{radius}px;")


def _raised_qss(radius=4, checked=False, theme="dark"):
    """Raised touchable face (chip / button / key) QSS — flat per O3 spec §3.3.
    checked=True → flat P.ACCENT fill + P.KEY_TEXT text, no border (sanctioned spots 2/4).
    Elevation border: dark theme uses top-edge bright; light theme uses bottom-edge bright."""
    if checked:
        return (f"background:{P.ACCENT}; color:{P.KEY_TEXT}; "
                f"border:none; border-radius:{radius}px;")
    if theme == "dark":
        return (f"background:{ts.PANEL}; "
                f"border:1px solid {P.CTL_STROKE}; "
                f"border-top-color:{P.CTL_STROKE_HI}; "
                f"border-radius:{radius}px; color:{ts.INK};")
    else:
        return (f"background:{ts.PANEL}; "
                f"border:1px solid {P.CTL_STROKE}; "
                f"border-bottom-color:{P.CTL_STROKE_HI}; "
                f"border-radius:{radius}px; color:{ts.INK};")


class PitwallSwitch(QAbstractButton):
    """B-mockup track-and-knob switch (Settings rebuild, owner, 2026-06-11): inset
    well track, raised knob, GREEN track when on. The knob JUMPS — no slide animation,
    motion stays reserved for the heat pulse. Reads the live ts/P tokens at paint time
    so a theme rebuild restyles it for free. apply_scale() is called by _rescale_dialog
    so the switch grows with the dialog's A−/A+ ladder."""
    BASE_W, BASE_H, BASE_KNOB, BASE_PAD = 30, 17, 13, 2

    def __init__(self, checked=False, scale=1.0, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(bool(checked))
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self.toggled.connect(lambda _on: self.update())
        self.apply_scale(scale)

    def apply_scale(self, s):
        self._s = max(0.5, float(s))
        self.setFixedSize(int(round(self.BASE_W * self._s)),
                          int(round(self.BASE_H * self._s)))
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        r = h / 2.0
        # Track — O3 §6.4: ON = flat P.ACCENT; OFF = transparent + 1px CTL_STROKE_HI stroke
        if self.isChecked():
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(P.ACCENT))
            p.drawRoundedRect(QRectF(0, 0, w, h), r, r)
        else:
            p.setPen(QPen(QColor(P.CTL_STROKE_HI), 1))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), r, r)
        pad = self.BASE_PAD * self._s
        # Knob size: ON = BASE_KNOB; OFF = one step smaller (≈ BASE_KNOB - 2 scaled px)
        k_on = self.BASE_KNOB * self._s
        k_off = max(k_on - 2 * self._s, k_on * 0.85)
        k = k_on if self.isChecked() else k_off
        # Centre the knob vertically regardless of size
        ky = (h - k) / 2.0
        x = (w - pad - k) if self.isChecked() else pad
        # Knob color: ON = ts.BG (mockup .sw::after); OFF = MUT
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(ts.BG if self.isChecked() else ts.MUT))
        p.drawEllipse(QRectF(x, ky, k, k))
        if not self.isEnabled():                     # blocked switch reads dimmed
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(ts.BG))
            p.setOpacity(0.45)
            p.drawRoundedRect(QRectF(0, 0, w, h), r, r)


class _RailButton(QPushButton):
    """Settings rail entry (O3 §6.3). When checked, paints the Win11
    NavigationView selection indicator: a 3×16px full-round P.ACCENT pill,
    vertically centered on the item's LEFT edge — sanctioned accent spot 2.
    Geometry is the spec's contract; QSS carries the rest of the styling."""
    def paintEvent(self, e):
        super().paintEvent(e)
        if self.isChecked():
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(P.ACCENT))
            p.drawRoundedRect(QRectF(0, (self.height() - 16) / 2.0, 3, 16),
                              1.5, 1.5)


class _ElideLabel(QLabel):
    """The session-NAME column. It always fits the REAL width the layout hands it by
    eliding its full text at paint time — so it can NEVER overflow into the model chip
    or the right-aligned state·tokens·$ gutter.

    This is the permanent cure for the recurring session-row OVERLAP bug. Every prior
    fix re-tuned a FEED-FORWARD width guess (name_w computed from the stored card width,
    then setFixedWidth). That guess desynced from the real on-screen width in many states
    (collapsed vs expanded, mid-drag resize, the instant of a state swap, DPI/font-scale
    changes) and overlap came back. Here the name takes the leftover cell via an Expanding
    size policy and re-ellipsizes against its OWN actual width in resizeEvent — feedback,
    not feed-forward — so overlap is impossible by construction, with no width math to keep
    in sync. Stays PlainText: names are transcript-derived (aiTitle/folder) and must never
    render as RichText (Ivan M1)."""

    def __init__(self, text=""):
        super().__init__(text)
        self.setTextFormat(Qt.PlainText)
        self._full = text or ""
        # Expanding horizontally so it soaks up the row's free space; can shrink to
        # nothing (minimumSizeHint width 0) so the fixed gutter always wins the room.
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def setFullText(self, text):
        self._full = text or ""
        self._elide()

    def _elide(self):
        fm = self.fontMetrics()
        super().setText(fm.elidedText(self._full, Qt.ElideRight, max(0, self.width())))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()                       # re-fit whenever the cell width changes

    def changeEvent(self, e):
        super().changeEvent(e)
        if e.type() == QEvent.FontChange:   # A−/A+ rescale: re-fit to the new metrics
            self._elide()

    def sizeHint(self):
        return QSize(0, super().sizeHint().height())

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())


# what the tip line says when a clicked row's window can't be pinned down (also listed
# in _all_tip_texts so the locked tip-box height already fits it)
FLASH_MISS_TIP = ("⚠  Can't tell which window that session is in yet — work in it "
                  "once so Pitwall learns it, then try again.")

# what the tip line says when a clicked row is a BACKGROUND session (no window exists
# to flash — also listed in _all_tip_texts so the locked tip-box height fits it)
BG_FLASH_TIP = ("\U0001f4a1  That session runs in the background — there's no window "
                "to flash. Reach it from the Claude phone app or claude.ai/code.")


class _WindowFlash(QWidget):
    """Click a session row → this heat-throbs the terminal window the session lives
    in (owner vision, 2026-06-10: same throb language as Pitwall's own heat ring).
    A click-through, no-focus translucent window glued over the target's frame,
    slotted into the z-order DIRECTLY above the target (NOT topmost — windows
    covering the target cover the ring too, so it reads as attached; owner test, flash6):
    a glow band in the SESSION'S heat colour that breathes via the same pulse_color
    whitening as the main ring, until the user clicks into the target (or 10s pass).
    Pure pointer — it never moves, raises or focuses the target."""

    MARGIN = 10       # physical px of air around the target's frame (room for the glow)
    LIFE_S = 10.0
    TICK_MS = 40

    def __init__(self):
        super().__init__(None, Qt.FramelessWindowHint |
                         Qt.Tool | Qt.WindowTransparentForInput |
                         Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._hwnd = 0
        self._t0 = 0.0
        self._armed = False          # has the target been seen NOT in the foreground?
        self._frac = 0.0             # the session's heat fraction (colour + throb rate)
        self._base = ts.GREEN
        self._freq, self._amp = 0.8, 0.5
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def start(self, hwnd, frac=0.0):
        self._hwnd = hwnd
        self._t0 = time.monotonic()
        # heat colour + throb rate mirror Pitwall's main ring for this session's heat —
        # but floored so even a stone-cold session still visibly throbs (this is an
        # attention signal; the main ring's "perfectly still when fresh" rule would
        # make the flash invisible exactly when it's needed).
        self._frac = max(0.0, min(1.0, frac))
        self._base = ts.lerp_color(self._frac)
        self._freq, self._amp = pulse_params(max(self._frac, 0.45))
        # if the target is already focused, a click INSIDE it (pointer check below) is
        # the only dismiss — a foreground test alone would kill the flash instantly
        self._armed = not ts.window_focused(hwnd)
        self.winId()                 # force the native window so _place can pin it
        if not self._place():
            return
        self.show()
        self._place()                # Qt's show() can disturb z-order — re-slot above target
        self._timer.start(self.TICK_MS)

    def stop(self):
        self._timer.stop()
        self.hide()
        self._hwnd = 0

    def _place(self):
        r = ts.window_rect(self._hwnd)
        if not r:
            return False
        m = self.MARGIN
        ts.place_window_above(int(self.winId()), r[0] - m, r[1] - m,
                              r[2] + 2 * m, r[3] + 2 * m, target=self._hwnd)
        return True

    def _tick(self):
        done = (time.monotonic() - self._t0 > self.LIFE_S
                or ts.window_minimized(self._hwnd)
                or ts.pointer_pressed_in(self._hwnd))
        if not ts.window_focused(self._hwnd):
            self._armed = True
        elif self._armed:
            done = True              # target just came to the front — job done
        if done or not self._place():
            self.stop()
            return
        self.update()

    def paintEvent(self, _e):
        if not self._hwnd:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        t = time.monotonic() - self._t0
        level = self._amp * math.sin(2 * math.pi * self._freq * t)
        beat = 0.5 + 0.5 * math.sin(2 * math.pi * self._freq * t)   # 0..1 swell
        col = QColor(pulse_color(self._base, level, self._frac))
        # every stroke is centred ON the frame line (the overlay rect minus MARGIN is
        # exactly the target's DWM frame) — owner test, 2026-06-10 flash5: a ring drawn at the
        # overlay's own edge floats MARGIN-3 px off the window and reads as "not hugging".
        # The wide translucent strokes straddle the edge like light spilling off it.
        m = self.MARGIN
        frame = self.rect().adjusted(m, m, -m, -m)
        # Win11 windows have ~8px rounded corners — match them so the corners hug too
        for pw, a in ((16, 0.10), (11, 0.20), (7, 0.32)):
            c = QColor(col)
            c.setAlphaF(a * (0.35 + 0.65 * beat))
            p.setPen(QPen(c, pw))
            p.drawRoundedRect(frame, 9, 9)
        # the core band — solid, the same 3px weight as Pitwall's own heat ring
        c = QColor(col)
        c.setAlphaF(0.95)
        p.setPen(QPen(c, 3))
        p.drawRoundedRect(frame, 9, 9)


# ---------------------------------------------------------------------------
class StewardQt(QWidget):
    def __init__(self):
        super().__init__()
        self.cfg = ts.load_config()
        # Apply the colour theme BEFORE building any widget — every stylesheet bakes in the
        # active palette at construction time. "system" follows the OS light/dark setting
        # (re-checked each refresh so it tracks live); "dark"/"light" pin it.
        self._applied_theme = ts.set_theme(ts.resolve_mode(self.cfg))
        _apply_pitwall_palette(self._applied_theme)   # §0.2: overlay ts globals before _build()
        # §4.1: always_on_top — absent ⇒ True (preserves today's hard-coded behaviour)
        _aot = self.cfg.get("always_on_top", True)
        _flags = Qt.FramelessWindowHint | Qt.Tool
        if _aot:
            _flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(_flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # Qt suppresses tooltips on INACTIVE windows by default — and this card
        # almost never activates (floating tool window the user hovers without
        # clicking). Without this, every setToolTip on the card is dead on
        # arrival (owner's eyeball, 2026-06-12: pill/seam hover "does nothing").
        self.setAttribute(Qt.WA_AlwaysShowToolTips)
        self.setMouseTracking(True)

        # --- text-size ladder: A-/A+ move ONE rung so each jump is an even visual
        #     step. Stored as an int 'size_level' (1..N on SIZE_LADDER); migrate an
        #     older free-float ui_scale to the nearest rung. (Same model as Tkinter.)
        try:
            lvl = int(self.cfg.get("size_level"))
        except (TypeError, ValueError):
            lvl = self._scale_to_level(self.cfg.get("ui_scale", 1.15))
        self.size_level = max(1, min(len(ts.SIZE_LADDER), lvl))

        # state
        self._fonts = []              # (label, base, bold, semibold, mono) to rescale
        self._wrap = []               # word-wrap labels (reflow on width change)
        self._snap = None             # last (cfg, d, ceiling, noun) for the 1s tick
        self._reset_anchor = None     # stabilised "Resets in" target (monotonic; see ts.stable_reset)
        self.collapsed = bool(self.cfg.get("collapsed", False))
        self.heat = ts.GREEN          # base heat colour (set each refresh)
        self._ring_col = ts.GREEN     # CURRENT (pulsed) ring colour, for paintEvent
        self.heat_frac = 0.0          # CLI-window fullness fraction (drives the pulse)
        self.phase = 0.0              # pulse phase accumulator
        self._learned = None          # cached learned "usual" ceiling
        self._ceiling_ctr = 0
        self._drift = None            # drift summary (for 'auto' correction mode)
        # Auto real-usage sync (the off-screen /usage capture; ts.AutoUsageScheduler).
        # Pure scheduler shares this cfg; the blocking capture runs on a daemon worker
        # thread and parks its result in _auto_pending for the UI thread to apply (so the
        # cfg-mutate + re-render can't race the worker). Default OFF — auto_usage.enabled
        # (Settings) is the on switch + kill switch.
        self._auto = ts.AutoUsageScheduler(self.cfg)
        self._auto_pending = None     # a finished capture dict awaiting UI-thread apply
        self._sync_now_result = None  # a finished "Sync now" capture awaiting UI apply
        self._auto_lock = threading.Lock()
        self._auto_ctr = 0            # 1s-tick counter (throttles the decide step ~10s)
        self._diag = None             # the single Usage-capture troubleshooting window
        self._diag_result = None      # a finished diagnostic capture awaiting UI display
        self._details = None          # the single Token Details window
        self._settings = None         # the single Settings dialog
        self._tip_i = 0
        self._resizing = False
        self._focus = ts.FocusTracker()   # follows which CLI window you're looking at
        self._sessions = []               # last rollup, reused by the fast focus poll
        self._win_flash = None            # lazy _WindowFlash (row-click window ring)
        self._follow_key = None           # gate: re-label only when the followed session changes
        self._resize_edge = None      # 'left'/'right' edge being dragged (None = not resizing)
        self._drag = None
        # width tracking — each view owns its OWN width (owner, 2026-06-06: separate
        # collapsed vs expanded). auto = scales with the font ladder until the user
        # drags that view's border to set it, after which it persists & is honoured.
        self._w_auto = self.cfg.get("w") is None             # expanded card width
        self._w = int(self.cfg.get("w") or round(DEFAULT_BASE_W * self._scale()))
        self._cw_auto = self.cfg.get("collapsed_w") is None  # collapsed strip width
        self._cw = self.cfg.get("collapsed_w")               # None -> fit content ONCE (then own it)
        if self._cw is not None:
            self._cw = int(self._cw)
        self._hover_edge = None      # 'left'/'right' edge under the pointer (paints a brighten)
        self._hover_row = None        # the open session row whose ↻ overlay is revealed
        # --- "Nudge me" shoulder-tap (Mode 2, BUILD #3; DESIGN_NOTES §10) ---
        self._cur_session = None      # the session the tap reasons about (set in _apply_follow)
        self._tap = None              # the docked tap surface (built lazily on first tap)
        self._tap_shown = False       # is the tap currently on screen?
        self._tap_anim = None         # keep the enter/leave animation alive
        self._nudge_anchor = None     # the SAVE NUDGES caption (scroll target from the tap)
        self._settings_area = None    # the Settings scroll area (for ensureWidgetVisible)
        self._hint = None             # custom hint popup (replaces QToolTip for the pills)
        self._hint_anchor = None      # the pill the hint is currently shown for

        # hidden verification arg:  --demo 0.92  forces the heat fraction
        self.demo_frac = None
        if "--demo" in sys.argv:
            try:
                self.demo_frac = float(sys.argv[sys.argv.index("--demo") + 1])
            except (ValueError, IndexError):
                pass

        # demo MODE (Settings → Diagnostics → Open demo): a fully isolated showcase.
        # It reads NOTHING real — gather() returns a synthetic snapshot driven by the
        # demo slider — and ts.DEMO_READONLY (set in main) blocks every config write.
        # _demo_frac is the live slider fraction (0..1); demo_frac mirrors it so the
        # existing heat-override path paints the ring/pill/this-chat line to match.
        self.demo_mode = _DEMO_MODE
        self._demo_frac = 0.5
        self._demo_termwin = None      # the spawned fake Claude Code terminal window
        if self.demo_mode:
            if self.demo_frac is not None:
                self._demo_frac = max(0.0, min(1.0, self.demo_frac))
            self.demo_frac = self._demo_frac

        self._build()
        self._install_edge_tracking()   # make the resize edges hover-discoverable (↔ cursor)
        # honour a collapsed state saved from either face (shared config)
        self.full.setVisible(not self.collapsed)
        self.strip.setVisible(self.collapsed)
        self.toggle.setText("▸" if self.collapsed else "▾")
        self._apply_header_mode()
        self._apply_collapse_metrics()
        self._update_size_buttons()
        try:
            self._drift = ts.drift_summary(ts.load_corrections())
        except Exception:
            self._drift = None
        self.refresh()
        self.rotate_tip()          # show a tip immediately, don't wait for the timer
        self._place()

        # timers: slow data re-read, 1s countdown tick, 30fps pulse
        self.t_refresh = QTimer(self)
        self.t_refresh.timeout.connect(self.refresh)
        self.t_refresh.start(self.cfg.get("refresh_seconds", 15) * 1000)
        self.t_tick = QTimer(self)
        self.t_tick.timeout.connect(self.tick)
        self.t_tick.start(1000)
        self.t_pulse = QTimer(self)
        self.t_pulse.timeout.connect(self._pulse)
        self.t_pulse.start(33)
        self.t_tip = QTimer(self)
        self.t_tip.timeout.connect(self.rotate_tip)
        self.t_tip.start(self.cfg.get("tip_seconds", 20) * 1000)
        # Fast focus poll: switching the followed window must feel instant, so check the
        # foreground window a few times a second and re-label off the CACHED rollup (no
        # transcript re-read). The slow t_refresh above still owns the expensive pull.
        self.t_follow = QTimer(self)
        self.t_follow.timeout.connect(self._follow_tick)
        self.t_follow.start(self.cfg.get("focus_poll_ms", 400))
        # Registry watch (owner, 2026-06-12): a freshly spawned CLI gets its row
        # within ~2s instead of waiting out refresh_seconds — the post-pitstop
        # blind spot where the new session was "nowhere to be found". The stamp is
        # just the registry dir's filenames, so it changes exactly when a session
        # process appears or exits; it cannot storm refresh() during normal turns.
        self._reg_stamp = ts.registry_stamp()
        self.t_registry = QTimer(self)
        self.t_registry.timeout.connect(self._registry_watch)
        self.t_registry.start(2000)
        # Topmost guard (2026-06-12): the intermittent "widget no longer on
        # top" (Kevin's hint: after a pitstop spawns a new CLI). Conditional —
        # ts.topmost_anomaly reasserts ONLY on real band corruption, so open
        # context menus (same topmost band) are never stomped. 21s, off the
        # other timers' beat; the win32 walk costs microseconds.
        self.t_topmost = QTimer(self)
        self.t_topmost.timeout.connect(self._topmost_guard)
        if _aot:
            self.t_topmost.start(21000)

    # === theme (light / dark / follow-OS) ==================================
    def apply_theme_choice(self, mode):
        """Settings picked System/Dark/Light: persist it, then repaint if it changed the
        concrete theme. Deferred a tick so the Settings click handler returns first."""
        self.cfg["theme"] = mode
        ts.save_config(self.cfg)
        if ts.resolve_mode(self.cfg) != self._applied_theme:
            QTimer.singleShot(0, self._recreate_for_theme)

    def _maybe_follow_os(self):
        """In 'system' theme, repaint when the Windows light/dark setting flips while we're
        open. Called from refresh() (≈15s); a no-op when the theme is pinned."""
        if (self.cfg.get("theme", "system") not in ("dark", "light")
                and ts.os_theme() != self._applied_theme):
            QTimer.singleShot(0, self._recreate_for_theme)

    def _recreate_for_theme(self, reopen_settings=None):
        """Rebuild the whole card under the freshly-chosen theme. The palette is baked into
        ~240 setStyleSheet calls at construction with no per-widget restyle path, so a clean
        rebuild of the top-level widget is the reliable repaint. Stop our timers, persist
        position, hand off to a fresh StewardQt at the same spot, and retire self. Any open
        Settings/Details popup is closed (it would otherwise be orphaned on the old card).
        reopen_settings: a stash dict from the live theme flip — the new card reopens
        Settings restyled with the user's unsaved state carried over, so the flip feels
        in-place instead of slamming the dialog shut."""
        if getattr(self, "_dying", False):
            return
        self._dying = True
        for t in (self.t_refresh, self.t_tick, self.t_pulse, self.t_tip,
                  self.t_follow, self.t_registry, self.t_topmost):
            t.stop()
        self.cfg["x"], self.cfg["y"] = self.x(), self.y()
        ts.save_config(self.cfg)
        global _card
        _card = StewardQt()          # __init__ re-reads cfg + re-applies set_theme()
        if reopen_settings:
            # Build the restored Settings BEFORE anything shows, then show card and
            # dialog back-to-back below — both surfaces land in the same paint pass.
            # (Showing the card first left it flipping a beat ahead of Settings while
            # the heavy dialog built — owner eyeball, 2026-06-11 round 3.)
            _card.open_settings(restore=reopen_settings, defer_show=True)
        _card.show()
        _card.raise_()
        if reopen_settings and _card._settings is not None:
            _card._settings.show()   # at the old geometry, restyled
        # Retire the old surfaces only AFTER the new ones are up and painted: the new
        # card (and restored Settings) sit exactly on top of the old, so the flip
        # reads as an in-place repaint instead of a close→gap→reopen flash (owner
        # eyeball, 2026-06-11). 60ms is invisible — the old window is covered.
        _old = [w for w in (self._settings, self._details, self._diag,
                            getattr(self, "_sess_details", None))
                if w is not None]

        def _retire():
            for w in _old:
                try:
                    w.close()
                except Exception:
                    pass
            self.close()
            self.deleteLater()
        QTimer.singleShot(60, _retire)

    # === build ==============================================================
    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # ring QFrame is now a transparent HALO_PX-margin container; the visible ring
        # hairline is drawn in paintEvent so the halo glow can live outside the widget tree.
        self.ring = QFrame(self)
        self.ring.setObjectName("ring")
        self.ring.setStyleSheet("#ring{background:transparent;}")
        outer.addWidget(self.ring)
        ringlay = QVBoxLayout(self.ring)
        ringlay.setContentsMargins(HALO_PX, HALO_PX, HALO_PX, HALO_PX)
        self.card = QFrame(self.ring)
        self.card.setObjectName("card")
        # Card material: flat fill per O3 §3.1
        self.card.setStyleSheet(
            f"#card{{background:{ts.BG}; border-radius:{CARD_RADIUS}px;}}")
        ringlay.addWidget(self.card)

        lay = QVBoxLayout(self.card)
        self._card_lay = lay
        lay.setContentsMargins(16, 14, 16, 15)
        lay.setSpacing(0)

        self._build_header(lay)
        # Gap below the header. Collapses to 0 in the compact strip (where the header
        # row is empty — its ▸/✕ ride the countdown row instead), stays 12 in the
        # expanded card (§1.4 header→caption rhythm — this spacer IS that gap; blockA
        # must not add its own). Stored so the collapse toggle can flip it.
        self._head_gap = QSpacerItem(0, 12, QSizePolicy.Minimum, QSizePolicy.Fixed)
        lay.addItem(self._head_gap)

        # everything below the header lives in `full` (hidden when collapsed) and
        # `strip` (the compact view, hidden when expanded).
        self.full = QWidget()
        full = QVBoxLayout(self.full)
        full.setContentsMargins(0, 0, 0, 0)
        full.setSpacing(0)
        self._full_lay = full         # stored so _apply_tip_visibility can invalidate it
        self._build_blockA(full)
        self._build_blockB(full)      # minimal sync line only (editor deferred)
        self._build_blockC(full)
        lay.addWidget(self.full)

        self.strip = QWidget()
        self._build_strip(self.strip)
        self.strip.setVisible(False)
        lay.addWidget(self.strip)

        # The demo showcase controls live INSIDE the card (one window, no companion to
        # position/track), shown only in the demo construct.
        if self.demo_mode:
            self._build_demo_section(lay)

    def _build_demo_section(self, lay):
        """The demo construct's control surface: a slider that drives the WHOLE card
        from synthetic numbers (read out in millions of tokens), a SIMULATED 'Fire
        pitstop' button, and a fake Claude Code terminal that runs a sample pitstop
        hand-off. Nothing here is real — the slider is the only input, and the Fire
        button is a preview, never the real pitstop machinery."""
        lay.addSpacing(12)
        # a hairline divider so the demo controls read as a separate surface
        rule = QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"background:{ts.EDGE};")
        lay.addWidget(rule)
        lay.addSpacing(10)

        lay.addWidget(self._mk("TRY IT", 7, ts.FAINT, semibold=True))
        lay.addSpacing(4)
        _lede = self._mk(
            "Drag to fill a pretend 5-hour window and watch the whole card react — "
            "the dollars, the bar, the heat ring, the save pill.", 8, ts.MUT)
        _lede.setWordWrap(True)
        self._wrap.append(_lede)
        lay.addWidget(_lede)

        # slider 0..1000 -> 0..16M tokens (fine-grained; 1000 steps reads smooth)
        srow = QHBoxLayout()
        srow.setSpacing(8)
        self._demo_slider = QSlider(Qt.Horizontal)
        self._demo_slider.setMinimum(0)
        self._demo_slider.setMaximum(1000)
        self._demo_slider.setValue(int(round(self._demo_frac * 1000)))
        self._demo_slider.setCursor(QCursor(Qt.PointingHandCursor))
        self._demo_slider.valueChanged.connect(self._demo_on_slider)
        srow.addWidget(self._demo_slider, 1)
        # the readout — tokens in millions (M)
        self._demo_readout = self._mk("", 9, ts.INK, mono=True, semibold=True)
        self._demo_readout.setAlignment(Qt.AlignRight)
        self._demo_readout.setMinimumWidth(round(64 * self._scale()))
        srow.addWidget(self._demo_readout, 0, Qt.AlignVCenter)
        lay.addSpacing(6)
        lay.addLayout(srow)

        # Fire pitstop (SIMULATION) + the fake terminal
        lay.addSpacing(10)
        frow = QHBoxLayout()
        frow.setSpacing(8)
        frow.addWidget(self._mk("Hand off to a fresh session", 8, ts.MUT))
        frow.addStretch(1)
        self._demo_fire = QPushButton("Fire pitstop")
        self._demo_fire.setCursor(QCursor(Qt.PointingHandCursor))
        self._demo_fire._ts_fontspec = (8, False, True, False)
        self._demo_fire.setFont(self._font(8, semibold=True))
        self._demo_fire.setStyleSheet(
            f"QPushButton{{background:{ts.AMBER}; color:#1B1B1B; border:none;"
            f" border-radius:4px; padding:4px 12px; font-weight:600;}}"
            f"QPushButton:disabled{{background:{ts.EDGE}; color:{ts.FAINT};}}")
        self._demo_fire.clicked.connect(self._demo_fire_pitstop)
        frow.addWidget(self._demo_fire)
        lay.addLayout(frow)

        lay.addSpacing(2)
        _thint = self._mk(
            "Opens a full Claude Code terminal and runs a sample pitstop — "
            "a terse representation of what actually happens.", 8, ts.FAINT)
        _thint.setWordWrap(True)
        self._wrap.append(_thint)
        lay.addWidget(_thint)
        self._demo_update_readout()

    def _build_header(self, lay):
        head = QHBoxLayout()
        head.setSpacing(0)
        # E's header is the name alone — no glyph prefix (§3.1.1)
        self.brand = self._mk(self.cfg.get("name", "Pitwall"),
                              11, ts.INK, semibold=True)
        head.addWidget(self.brand)
        _tag = self.cfg.get("tagline", "")
        self.tag = self._mk(f"  {_tag}" if _tag else "", 8, ts.FAINT)
        head.addWidget(self.tag)
        # DEMO badge — only in the showcase construct, so it can never be mistaken for
        # the real widget (the data behind it is fake).
        if self.demo_mode:
            self.demo_badge = self._mk(" DEMO ", 7, "#1B1B1B", semibold=True)
            self.demo_badge.setStyleSheet(
                f"background:{ts.AMBER}; color:#1B1B1B; border-radius:3px;"
                f" padding:1px 5px; font-weight:600;")
            head.addSpacing(6)
            head.addWidget(self.demo_badge, 0, Qt.AlignVCenter)
        head.addStretch(1)
        # right cluster: A-  A+  |  collapse  |  gear  |  close
        # All controls: FAINT at rest, hover INK (§3.1.1)
        self.a_dn = self._ctl(self._mk("A−", 8, ts.FAINT, semibold=True),
                              lambda: self._card_restep(-1, self.a_dn))
        self._hover(self.a_dn, ts.INK, ts.FAINT)
        head.addWidget(self.a_dn)
        head.addSpacing(5)
        self.a_up = self._ctl(self._mk("A+", 8, ts.FAINT, semibold=True),
                              lambda: self._card_restep(+1, self.a_up))
        self._hover(self.a_up, ts.INK, ts.FAINT)
        head.addWidget(self.a_up)
        head.addSpacing(9)
        self.toggle = self._ctl(self._mk("▾", 10, ts.FAINT), self.toggle_collapsed)
        self._hover(self.toggle, ts.INK, ts.FAINT)
        head.addWidget(self.toggle)
        head.addSpacing(9)
        # The bell: the momentary mute (owner, 2026-06-12 — "blasted with
        # notifications"). Quiets BOTH the fresh-start popup and the pitstop phone
        # pushes for a chosen short while; everything un-mutes itself. Only shown
        # where the pitstop toolchain exists — on a vanilla machine there are no
        # pushes to mute and the popup has its own snooze row.
        self.bell = self._ctl(self._mk("🔔", 10, ts.FAINT), self._bell_menu)
        self._hover(self.bell, ts.INK, ts.FAINT)
        self.bell.setVisible(ts.pitstop_available())
        head.addWidget(self.bell)
        if ts.pitstop_available():
            head.addSpacing(9)
        self.gear = self._ctl(self._mk("⚙", 11, ts.FAINT), self.open_settings)
        self._hover(self.gear, ts.INK, ts.FAINT)
        head.addWidget(self.gear)
        head.addSpacing(9)
        self.close_btn = self._ctl(self._mk("✕", 11, ts.FAINT),
                                   lambda: QApplication.quit())
        self._hover(self.close_btn, ts.INK, ts.FAINT)
        head.addWidget(self.close_btn)
        lay.addLayout(head)

    def _build_blockA(self, lay):
        # Caption — FAINT, 600, caps (§3.1.2). The header→caption 12px rhythm is
        # carried by _head_gap upstream — no extra spacing here.
        self.caption = self._mk("This 5-hour window", 7, ts.FAINT, semibold=True)
        lay.addWidget(self.caption)

        # Dial well — milled-in well containing $ + pill + sub row (§3.1.3)
        lay.addSpacing(7)    # caption → dial rhythm
        self.dial = QFrame()
        self.dial.setObjectName("dial")
        self.dial.setStyleSheet(f"QFrame#{self.dial.objectName()}{{{_well_qss(4)}}}")
        dial_lay = QVBoxLayout(self.dial)
        dial_lay.setContentsMargins(12, 10, 12, 11)
        dial_lay.setSpacing(0)

        # big $ row, with the save pill right-pinned on the same line
        drow = QHBoxLayout()
        drow.setSpacing(0)
        self.dollars = self._mk("—", 26, ts.INK, mono=True, bold=True)  # mono 700, E's hero
        drow.addWidget(self.dollars)
        drow.addStretch(1)
        self.pill = self._mk("", 9, None, semibold=True)
        self.pill.setAlignment(Qt.AlignCenter)
        self.pill.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        drow.addWidget(self.pill, 0, Qt.AlignVCenter)
        dial_lay.addLayout(drow)

        # sub row: token line + Token Details link (INK 600, hover underline — §3.1.3)
        srow = QHBoxLayout()
        srow.setSpacing(0)
        self.sub = self._mk("value used · at pay-per-use prices", 8, ts.MUT)
        # sweep item 21 (owner, 2026-06-12): the $ figure is a work-done gauge, not a bill
        _usage_tip = ("What this usage would cost at Anthropic's pay-per-token "
                      "prices. On your plan you don't pay this — it's a measure "
                      "of how much work you've used.")
        self.dollars.setToolTip(_tip(_usage_tip))
        self.sub.setToolTip(_tip(_usage_tip))
        srow.addWidget(self.sub)
        srow.addStretch(1)
        self.details_link = self._ctl(
            self._mk("Token Details ↗", 8, ts.INK, semibold=True), self.show_token_details)
        # hover = underline only (no color swap — §3.1.3)
        def _dl_enter(e): self.details_link.setStyleSheet(
            f"color:{ts.INK}; text-decoration:underline;")
        def _dl_leave(e): self.details_link.setStyleSheet(f"color:{ts.INK};")
        self.details_link.enterEvent = _dl_enter
        self.details_link.leaveEvent = _dl_leave
        srow.addWidget(self.details_link)
        dial_lay.addLayout(srow)

        lay.addWidget(self.dial)

        # per-model split (rich text one-liner) — §3.1.4 — with the pitstop
        # verification chip right-pinned (owner, 2026-06-12: the chip crowded
        # the "this chat" row once it grew the overage — moved up to the model
        # line, just under Token Details). Quiet outline pill — state words,
        # never data, so no solid heat fill. It still reports on the session
        # the heat trio follows; _set_ps_pill is unchanged.
        mrow = QHBoxLayout()
        mrow.setSpacing(5)
        self.models = self._mk("", 8, None)
        self.models.setTextFormat(Qt.RichText)
        mrow.addWidget(self.models)
        mrow.addStretch(1)
        self.ps_pill = self._mk("", 8, ts.MUT)
        self.ps_pill.setVisible(False)
        self.ps_pill.installEventFilter(self)   # custom hint on hover (not QToolTip)
        mrow.addWidget(self.ps_pill)
        lay.addSpacing(11)   # dial → models rhythm
        lay.addLayout(mrow)

        # "this chat" line — static dot (de-pulsed in _apply_follow), rich-text (§3.1.5)
        crow = QHBoxLayout()
        crow.setSpacing(5)
        self.conv_dot = self._mk("●", 9, ts.GREEN)
        crow.addWidget(self.conv_dot)
        self.conv = self._mk("", 8, None)
        self.conv.setTextFormat(Qt.RichText)
        crow.addWidget(self.conv)
        crow.addStretch(1)
        # seam chip (owner, 2026-06-12): right-pinned directly under the
        # pitstop chip — is the followed session at a clean break (a seam,
        # where a pitstop can fire) or mid-task?
        self.seam_pill = self._mk("", 8, ts.MUT)
        self.seam_pill.setVisible(False)
        self.seam_pill.installEventFilter(self)  # custom hint on hover (not QToolTip)
        crow.addWidget(self.seam_pill)
        lay.addSpacing(7)    # models → chat rhythm
        lay.addLayout(crow)

        # pace row: STATIC pace-coloured dot + pace text (§3.1.6)
        prow = QHBoxLayout()
        prow.setSpacing(5)
        self.dot = self._mk("●", 10, ts.GREEN)
        prow.addWidget(self.dot)
        self.pace = self._mk("", 9, ts.MUT)
        # RichText so the $/hr values can sit in INK 600 mono spans (§3.1.6); the
        # MUT stylesheet above stays the default color for unstyled scaffold text.
        self.pace.setTextFormat(Qt.RichText)
        prow.addWidget(self.pace)
        prow.addStretch(1)
        lay.addSpacing(7)    # chat → pace rhythm
        lay.addLayout(prow)

        # 5-hour allowance bar — h 6 r 3 shallow groove (§3.1.7). Borderless: a
        # 1px ring on a 6px bar ate a third of the height and muted the heat
        # color (owner eyeball 2026-06-12); the fill now bleeds the full height.
        self.bar_track = QFrame()
        self.bar_track.setFixedHeight(6)
        self.bar_track.setStyleSheet(
            f"background:{P.WELL}; border:none; border-radius:3px;")
        bl = QHBoxLayout(self.bar_track)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        self.bar_fill = QFrame()
        self.bar_fill.setStyleSheet(f"background:{ts.GREEN}; border-radius:3px;")
        bl.addWidget(self.bar_fill)
        self.bar_spacer = QWidget()
        bl.addWidget(self.bar_spacer)
        lay.addSpacing(10)   # pace → bar rhythm
        lay.addWidget(self.bar_track)

        # §3.1.8 barline, restored minus the reassurance: "NN% of your 5-hour limit
        # used" mirrors the weekly line below it; the "· on track to last until it
        # resets" clause stays gone (on_track_tail=False), warning clauses still show.
        # (Owner eyeball 2026-06-11: removing the whole line also lost the 5h %.)
        lay.addSpacing(5)    # bar → barline rhythm (matches wkline → wkbar)
        # plain text, NOT a control — clicking it opened Settings by surprise
        # (owner eyeball 2026-06-11); same for the weekly line in blockB
        self.proj = self._mk("", 8, ts.MUT)
        self.proj.setWordWrap(True)
        self._wrap.append(self.proj)
        lay.addWidget(self.proj)

    def _build_blockB(self, lay):
        # Weekly limit line — plain text (de-clicked with the 5-hour line, owner
        # eyeball 2026-06-11). Zero height until synced. (§3.1.9)
        lay.addSpacing(12)   # barline → weekly rhythm
        self.weekly = self._mk("", 8, ts.MUT)
        self.weekly.setWordWrap(True)
        self._wrap.append(self.weekly)
        lay.addWidget(self.weekly)

        # Weekly bar — SAME geometry as the 5-hour bar (h 6 r 3) and the SAME heat-ramp
        # fill, so the two allowances read as siblings (owner punch list, 2026-06-11;
        # supersedes the §3.1.9 grey gradient). Color is applied in _set_weekly_line
        # where the synced % is known.
        lay.addSpacing(5)    # wkline → wkbar rhythm
        self.wk_track = QFrame()
        self.wk_track.setFixedHeight(6)
        self.wk_track.setStyleSheet(
            f"background:{P.WELL}; border:none; border-radius:3px;")
        wkl = QHBoxLayout(self.wk_track)
        wkl.setContentsMargins(0, 0, 0, 0)
        wkl.setSpacing(0)
        self.wk_fill = QFrame()
        wkl.addWidget(self.wk_fill)
        self.wk_spacer = QWidget()
        wkl.addWidget(self.wk_spacer)
        self.wk_track.setVisible(False)
        lay.addWidget(self.wk_track)

        # Freshness stamp — faint absolute clock ("as of 11:40 AM"), hidden until the
        # first sync — with a "sync now" control on the far right (owner ask 2026-06-11):
        # one-shot /usage read, same worker as Settings' Sync now button.
        self.lsync = self._mk("", 7, ts.FAINT)
        self.lsync.setVisible(False)
        self.sync_link = self._ctl(self._mk("sync now", 7, ts.INK), self._card_sync_now)
        srow = QHBoxLayout()
        srow.setContentsMargins(0, 0, 0, 0)
        srow.setSpacing(8)
        srow.addWidget(self.lsync)
        srow.addStretch(1)
        srow.addWidget(self.sync_link)
        lay.addSpacing(2)
        lay.addLayout(srow)

    def _build_blockC(self, lay):
        lay.addSpacing(14)   # seam above (§1.4)
        lay.addWidget(self._rule())
        lay.addSpacing(12)   # seam below (§1.4)
        self.sess_cap = self._mk("Claude sessions", 7, ts.FAINT, semibold=True)
        lay.addWidget(self.sess_cap)
        self.sess_box = QVBoxLayout()
        self.sess_box.setSpacing(2)
        lay.addSpacing(3)
        lay.addLayout(self.sess_box)
        self._sess_pool = []
        self._sess_empty = self._mk("no recent Claude sessions", 8, ts.FAINT)
        self._sess_empty.setVisible(False)
        self.sess_box.addWidget(self._sess_empty)
        # "+N more" expander — the full list shows max_sessions rows by default; clicking
        # this reveals the rest (and collapses again). It lives in the OUTER column below
        # the row box, so the dynamic row-insert logic above is untouched (owner 2026-06-20).
        self._sessions_expanded = False
        self._sess_more = self._mk("", 8, ts.MUT, semibold=True)
        self._sess_more.setVisible(False)
        self._sess_more.setCursor(Qt.PointingHandCursor)

        def _more_click(e):
            if e.button() == Qt.LeftButton:
                self._sessions_expanded = not self._sessions_expanded
                self.refresh()
                return
            e.ignore()
        self._sess_more.mousePressEvent = _more_click
        lay.addSpacing(2)
        lay.addWidget(self._sess_more)

        lay.addSpacing(14)   # seam above
        lay.addWidget(self._rule())
        lay.addSpacing(12)   # seam below
        rrow = QHBoxLayout()
        rrow.setSpacing(0)
        self.reset_lead = self._mk("Resets in", 9, ts.MUT)
        rrow.addWidget(self.reset_lead)
        rrow.addStretch(1)
        self.countdown = self._mk("—", 13, ts.INK, mono=True, bold=True)  # INK, was ACCENT
        rrow.addWidget(self.countdown)
        lay.addLayout(rrow)

        self.tip = self._mk("", 8, ts.FAINT)
        self.tip.setWordWrap(True)
        self.tip.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        # click-to-copy (stranger-test rule 3, owner 2026-06-12): when the showing tip
        # quotes text to type, _tip_copy holds the payload and a click copies it.
        self._tip_copy = None
        self.tip.mousePressEvent = self._tip_clicked
        self._wrap.append(self.tip)
        self._tip_gap = QSpacerItem(0, 12, QSizePolicy.Minimum, QSizePolicy.Fixed)
        lay.addItem(self._tip_gap)   # reset → tip 12 rhythm
        lay.addWidget(self.tip)

    def _build_strip(self, parent):
        lay = QVBoxLayout(parent)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        top = QHBoxLayout()
        top.setSpacing(0)
        self.c_dot = self._mk("●", 10, ts.GREEN)   # de-pulsed; static heat in _apply_follow
        top.addWidget(self.c_dot)
        top.addSpacing(6)
        self.c_reset = self._mk("—", 15, ts.INK, mono=True, bold=True)  # INK (was ACCENT)
        top.addWidget(self.c_reset)
        top.addSpacing(8)
        self.c_sep = self._mk("·", 9, ts.FAINT)
        top.addWidget(self.c_sep)
        top.addSpacing(6)
        _tok_fm = QFontMetrics(self._font(9, mono=True))
        self.c_tok = self._mk("—", 9, ts.MUT, mono=True)
        self.c_tok.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.c_tok.setFixedWidth(_tok_fm.horizontalAdvance("000.00M") + 2)
        top.addWidget(self.c_tok)
        top.addSpacing(6)
        self.c_sep2 = self._mk("·", 9, ts.FAINT)
        top.addWidget(self.c_sep2)
        top.addSpacing(6)
        self.c_usd = self._mk("—", 9, ts.MUT, mono=True)
        self.c_usd.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.c_usd.setFixedWidth(_tok_fm.horizontalAdvance("$000.00") + 2)
        top.addWidget(self.c_usd)
        top.addStretch(1)
        # §4.2: strip top line "5h 38% · wk 21%" — four labels replacing the old single c_pct.
        # Fixed mono widths per "100%" worst case + 2px, right-aligned; zero jitter on refresh.
        _pct_w = _tok_fm.horizontalAdvance("100%") + 2
        self.c_5h_lbl = self._mk("5h ", 9, ts.FAINT)
        top.addWidget(self.c_5h_lbl)
        self.c_5h_pct = self._mk("", 9, ts.INK, mono=True, bold=True)
        self.c_5h_pct.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.c_5h_pct.setFixedWidth(_pct_w)
        top.addWidget(self.c_5h_pct)
        self.c_wk_lbl = self._mk(" · wk ", 9, ts.FAINT)
        top.addWidget(self.c_wk_lbl)
        self.c_wk_pct = self._mk("", 9, ts.INK, mono=True, bold=True)
        self.c_wk_pct.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.c_wk_pct.setFixedWidth(_pct_w)
        top.addWidget(self.c_wk_pct)
        # hide all four until there's a ceiling; wk pair hides additionally when not synced
        for _w in (self.c_5h_lbl, self.c_5h_pct, self.c_wk_lbl, self.c_wk_pct):
            _w.setVisible(False)
        top.addSpacing(10)
        self.c_toggle = self._ctl(self._mk("▸", 10, ts.FAINT), self.toggle_collapsed)
        self._hover(self.c_toggle, ts.INK, ts.FAINT)
        top.addWidget(self.c_toggle)
        top.addSpacing(8)
        self.c_close = self._ctl(self._mk("✕", 11, ts.FAINT),
                                 lambda: QApplication.quit())
        self._hover(self.c_close, ts.INK, ts.FAINT)
        top.addWidget(self.c_close)
        lay.addLayout(top)
        self.c_sess = QVBoxLayout()
        self.c_sess.setSpacing(1)
        lay.addSpacing(4)
        lay.addLayout(self.c_sess)
        self._strip_pool = []

    # === small builders / helpers ==========================================
    # The "*_at(level, …)" variants take an EXPLICIT ladder level so a child
    # window (Token Details / Settings) can carry its OWN font size, independent
    # of the main card and of each other (#7). The plain _scale/_sz/_font are
    # thin wrappers on the card's shared self.size_level.
    def _scale_at(self, level):
        i = max(1, min(len(ts.SIZE_LADDER), int(level))) - 1
        return ts.SIZE_LADDER[i]

    def _scale(self):
        return self._scale_at(self.size_level)

    def _screen_geo(self, ref=None):
        """availableGeometry of the screen the widget (or `ref` widget) is on.
        Multi-monitor safe: primaryScreen() would clamp/position panels against the
        WRONG display when the widget lives on a second monitor, so sub-panels could
        straddle the seam between monitors or fall off-screen. (2026-06-06)"""
        w = ref if ref is not None else self
        try:
            scr = QApplication.screenAt(w.frameGeometry().center())
            if scr is None:
                scr = (w.screen() if hasattr(w, "screen") else None) \
                    or QApplication.primaryScreen()
            return scr.availableGeometry()
        except Exception:
            # last resort: a sane fixed rect — primaryScreen() can itself be None
            # during a headless/monitor-unplug transition (Ivan L1, 2026-06-06)
            prim = QApplication.primaryScreen()
            return prim.availableGeometry() if prim else QRect(0, 0, 1280, 720)

    def _sz_at(self, level, base):
        return max(6, int(round(base * self._scale_at(level))))

    def _sz(self, base):
        return self._sz_at(self.size_level, base)

    def _font_at(self, level, base, bold=False, semibold=False, mono=False, caps=False):
        # O3 §0.1: Segoe UI Variable for all text and figures.
        # mono=True → "figure/display role" (hero $, countdown, tokens, %, $/hr, session
        # gutters, strip %) — uses the Display optical cut which is tighter for large numerals.
        # mono=False → body/UI role — uses the Text optical cut.
        # Weight law: 400 regular, 600 (DemiBold) for bold or semibold — no Bold (700) anywhere.
        # caps parameter kept for arity compat (_ts_fontspec 5-tuples); letter-spacing dies.
        if mono:
            f = QFont("Segoe UI Variable Display", self._sz_at(level, base))
            f.setFamilies(["Segoe UI Variable Display",
                           "Segoe UI Variable Text", "Segoe UI"])
        else:
            f = QFont("Segoe UI Variable Text", self._sz_at(level, base))
            f.setFamilies(["Segoe UI Variable Text", "Segoe UI"])
        if bold or semibold:
            f.setWeight(QFont.DemiBold)   # 600 only — no Bold (700) in this skin
        # PreferFullHinting removed (was Inter/Plex-specific; Segoe UI Variable is ClearType-tuned)
        return f

    def _font(self, base, bold=False, semibold=False, mono=False, caps=False):
        return self._font_at(self.size_level, base, bold, semibold, mono, caps)

    def _mk(self, text, base, color, bold=False, semibold=False, mono=False,
            caps=False, cls=QLabel):
        """A QLabel registered for font-rescaling. `color` None = caller styles it.
        `cls` lets a caller register a QLabel SUBCLASS (e.g. _ElideLabel) so it still
        rescales on A−/A+ via the shared self._fonts loop."""
        lbl = cls(text)
        lbl.setTextFormat(Qt.PlainText)  # never auto-promote HTML-looking text (M1)
        lbl.setFont(self._font(base, bold, semibold, mono, caps))
        if color:
            lbl.setStyleSheet(f"color:{color};")
        self._fonts.append((lbl, base, bold, semibold, mono, caps))
        return lbl

    def _ctl(self, lbl, fn):
        """Mark a label as a control: it runs `fn` on click and never starts a
        window drag (it swallows its own press)."""
        lbl.setCursor(QCursor(Qt.PointingHandCursor))
        lbl._ts_nodrag = True

        def press(e):
            if e.button() == Qt.LeftButton:
                fn()
                e.accept()
        lbl.mousePressEvent = press
        return lbl

    def _hover(self, lbl, enter_color, leave_color):
        def en(e):
            lbl.setStyleSheet(f"color:{enter_color};")
        def lv(e):
            lbl.setStyleSheet(f"color:{leave_color};")
        lbl.enterEvent = en
        lbl.leaveEvent = lv

    def _rule(self):
        r = QFrame()
        r.setFixedHeight(1)
        r.setStyleSheet(f"QFrame{{background:{P.SEAM_LT};}}")
        return r

    def _scale_to_level(self, scale):
        try:
            scale = float(scale)
        except (TypeError, ValueError):
            scale = ts.SIZE_LADDER[ts.SIZE_DEFAULT_LEVEL - 1]
        best_i, best_d = ts.SIZE_DEFAULT_LEVEL - 1, 1e9
        for i, v in enumerate(ts.SIZE_LADDER):
            if abs(v - scale) < best_d:
                best_i, best_d = i, abs(v - scale)
        return best_i + 1

    # === A- / A+ : text ladder =============================================
    def step_scale(self, delta):
        new = max(1, min(len(ts.SIZE_LADDER), self.size_level + delta))
        if new == self.size_level:
            self._update_size_buttons()
            return
        old_scale = self._scale()                        # capture BEFORE the level changes
        self.size_level = new
        self.cfg["size_level"] = new
        self.cfg["ui_scale"] = ts.SIZE_LADDER[new - 1]   # kept in sync for back-compat
        ts.save_config(self.cfg)
        for lbl, base, bold, semibold, mono, caps in self._fonts:
            lbl.setFont(self._font(base, bold, semibold, mono, caps))
        # The card width must track the text ladder, else bigger fonts overflow the
        # fixed session columns and the rows overlap. Auto width snaps to the base;
        # a user-resized width grows/shrinks proportionally so it stays readable. (2026-06-06)
        if self._w_auto:
            self._w = round(DEFAULT_BASE_W * self._scale())
        elif old_scale > 0:
            self._w = round(self._w * self._scale() / old_scale)
        # the collapsed strip width tracks the ladder the same way (its clamps scale too);
        # when it's still auto, _relayout refits it to content so nothing to do here.
        if not self._cw_auto and self._cw and old_scale > 0:
            self._cw = round(self._cw * self._scale() / old_scale)
        self._update_size_buttons()
        self.refresh()

    def _card_restep(self, delta, btn):
        """Main-card A−/A+ press: FADE-PULSE the resize, then (#8) slide the card so the
        clicked button stays under the cursor for repeated clicks.

        A−/A+ is a discrete font-zoom: the text jumps to the next size in one step, so a
        smooth box-glide just desyncs the frame from its text (the frame slides while the
        text has already snapped — owner test, v2 video, 2026-06-08, "not even close"). So
        instead of animating size we mask the jump: DIM the card to 35%, do the whole
        resize in the SAME frame while it's dimmed (the size change is hidden under the
        dim), then fade back up over a short pulse. The resize is synchronous, so rapid
        A−/A+ clicks never drop a step — each just re-dims and the fade-up restarts once
        you stop. At a size rail (already min/max) nothing resizes. (owner, 2026-06-08.)"""
        before = self.size_level
        if max(1, min(len(ts.SIZE_LADDER), before + delta)) == before:
            self.step_scale(delta)            # rail: refresh the +/- enabled state only
            return
        anchor = QCursor.pos()
        fade = getattr(self, "_pulse_anim", None)
        if fade is not None:
            fade.stop()
        self.setWindowOpacity(0.35)           # dim FIRST so the font-size jump is masked
        self.step_scale(delta)                # resize while dimmed (same frame, no present)
        self._keep_under_cursor(self, btn, anchor)
        fade = QPropertyAnimation(self, b"windowOpacity", self)
        fade.setDuration(140)                 # fast — a pulse, not a slow fade
        fade.setStartValue(0.35)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)
        self._pulse_anim = fade
        fade.start()

    def _keep_under_cursor(self, win, btn, anchor):
        """#8 — after a rescale, move `win` so `btn`'s new centre sits back under the
        cursor (`anchor`, a global QPoint), clamped on-screen. The pointer never moves;
        the window slides, so A−/A+ can be clicked repeatedly without chasing the button.
        (The Tkinter face did this as `_keep_under_cursor`; the Qt face had nothing.)

        Converges in a short loop: the Token Details A−/A+ row lives inside a
        QScrollArea, whose vertical scrollbar can toggle on a DEFERRED layout pass after
        a font bump, shifting the button by the scrollbar width. After each move we flush
        pending layout (EXCLUDING user input, so a rapid second click can't re-enter) and
        re-measure; once the button is on the anchor we stop. Move-only (never teardown),
        so this can't reintroduce the issue-#1 crash. A re-entrancy guard drops nested
        calls outright."""
        if anchor is None or getattr(self, "_anchoring", False):
            return
        self._anchoring = True
        try:
            screen = QApplication.screenAt(anchor) or QApplication.primaryScreen()
            scr = screen.availableGeometry()
            for _ in range(4):
                lay = win.layout()
                if lay is not None:
                    lay.activate()         # force a synchronous layout so geometry is current
                # Flush the DEFERRED layout (e.g. a scrollbar toggling on after a font bump)
                # WITHOUT spinning the event loop. The old processEvents() pumped the loop,
                # which let the compositor PRESENT the half-resized window mid-slide — the
                # flicker the owner still saw. sendPostedEvents delivers Qt's queued layout
                # events only; nothing is presented until we re-enable updates + repaint().
                QApplication.sendPostedEvents(win, 0)
                new_g = btn.mapToGlobal(btn.rect().center())
                dx, dy = anchor.x() - new_g.x(), anchor.y() - new_g.y()
                if abs(dx) <= 1 and abs(dy) <= 1:
                    break                  # button is under the cursor — settled
                target = win.pos() + (anchor - new_g)
                x = max(scr.left(), min(target.x(), scr.right() - win.width()))
                y = max(scr.top(), min(target.y(), scr.bottom() - win.height()))
                win.move(int(x), int(y))
        except RuntimeError:
            pass                            # window torn down mid-press — nothing to anchor
        finally:
            self._anchoring = False

    # === A- / A+ inside the child windows (Token Details, Settings) =========
    # These step the SHARED size ladder (so the main card rescales too) and then
    # rescale the dialog's own widgets IN PLACE — NO teardown/reopen. The old code
    # close()+deleteLater()'d the dialog and rebuilt it on every keypress; destroying
    # the top-level window that owns the just-clicked A−/A+ button crashed the app the
    # instant Qt routed the next mouse event to the freed window (confirmed by a live
    # faulthandler trace, 2026-06-06: crash landed right after the rebuilt reopen, with
    # no Python traceback — a C++ use-after-free of the destroyed dialog). Scalable
    # widgets are tagged `_ts_fontspec` (and optionally `_ts_widthbase`) at build time;
    # `_rescale_dialog` walks them. A dialog sets `_ts_resize` to re-fit its window.
    def _restep_dialog(self, delta, dlg, btn=None):
        # #7: step ONLY this dialog's own level — never the shared card level, never
        # persist. So A−/A+ in Token Details leaves Settings and the main card alone.
        cur = getattr(dlg, "_ts_level", self.size_level)
        new = max(1, min(len(ts.SIZE_LADDER), cur + delta))
        if new == cur:                     # sitting at a rail — nothing resizes
            return
        anchor = QCursor.pos() if btn is not None else None   # #8: before resize
        dlg._ts_level = new
        self._rescale_dialog(dlg)          # in place — the window is never destroyed
        if btn is not None:
            self._keep_under_cursor(dlg, btn, anchor)         # #8: button back under cursor

    def _rescale_dialog(self, dlg):
        lvl = getattr(dlg, "_ts_level", self.size_level)
        scale = self._scale_at(lvl)
        for wdg in dlg.findChildren(QWidget):
            spec = getattr(wdg, "_ts_fontspec", None)
            if spec is not None:
                wdg.setFont(self._font_at(lvl, *spec))
            wbase = getattr(wdg, "_ts_widthbase", None)
            if wbase is not None:
                wdg.setFixedWidth(int(wbase * scale))
            if isinstance(wdg, PitwallSwitch):
                wdg.apply_scale(scale)
        for btn, delta in getattr(dlg, "_ts_sizebtns", ()):
            rail = ((delta < 0 and lvl <= 1)
                    or (delta > 0 and lvl >= len(ts.SIZE_LADDER)))
            btn.setStyleSheet(f"color:{ts.FAINT if rail else ts.MUT};")
        resize = getattr(dlg, "_ts_resize", None)
        if resize is not None:
            resize()

    def _dialog_font_buttons(self, dlg):
        """An 'A−  A+' control pair for a child window's title row. Steps the shared
        size ladder and rescales `dlg` IN PLACE (no teardown). Tag scalable widgets in
        the dialog with `_ts_fontspec`; set `dlg._ts_resize` to re-fit the window."""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(9)
        dlg._ts_sizebtns = []

        lvl0 = getattr(dlg, "_ts_level", self.size_level)

        def mkbtn(text, delta):
            rail = ((delta < 0 and lvl0 <= 1)
                    or (delta > 0 and lvl0 >= len(ts.SIZE_LADDER)))
            b = QLabel(text)
            b.setTextFormat(Qt.PlainText)
            b._ts_fontspec = (9, False, True, False)
            b.setFont(self._font_at(lvl0, 9, semibold=True))
            b.setStyleSheet(f"color:{ts.FAINT if rail else ts.MUT};")
            b.setCursor(QCursor(Qt.PointingHandCursor))

            def press(e, d=delta, btn=b):
                if e.button() == Qt.LeftButton:
                    self._restep_dialog(d, dlg, btn)   # #8: pass clicked btn for anchoring
                    e.accept()
            b.mousePressEvent = press
            dlg._ts_sizebtns.append((b, delta))
            return b

        row.addWidget(mkbtn("A−", -1))
        row.addWidget(mkbtn("A+", +1))
        return row

    def _update_size_buttons(self):
        self.a_dn.setStyleSheet(
            f"color:{ts.FAINT if self.size_level <= 1 else ts.MUT};")
        self.a_up.setStyleSheet(
            f"color:{ts.FAINT if self.size_level >= len(ts.SIZE_LADDER) else ts.MUT};")

    # === collapse / expand =================================================
    def _apply_header_mode(self):
        """Compact = a glance chip with NO header band: EVERY header control is hidden in
        the strip (the ▾/✕ live on the countdown row instead — see _build_strip), so the
        whole header row collapses to zero height and the dead space above the countdown
        is gone. Expanded keeps the full header. (#5, Sarah 2026-06-06.)"""
        full = not self.collapsed
        for w in (self.brand, self.tag, self.a_dn, self.a_up,
                  self.toggle, self.gear, self.close_btn):
            w.setVisible(full)
        self.bell.setVisible(full and ts.pitstop_available())

    def _apply_collapse_metrics(self):
        """Tighten the card to a glance chip when collapsed: drop the header gap to 0 and
        snug the top/bottom margins. Expanded uses the O3 mockup 16/14/16/15 margins +
        12px header gap. (#5, Sarah 2026-06-06; O3 reskin 2026-06-11.)"""
        if self.collapsed:
            self._card_lay.setContentsMargins(16, 10, 16, 12)
            self._head_gap.changeSize(0, 0, QSizePolicy.Minimum, QSizePolicy.Fixed)
        else:
            self._card_lay.setContentsMargins(16, 14, 16, 15)
            self._head_gap.changeSize(0, 12, QSizePolicy.Minimum, QSizePolicy.Fixed)
        self._card_lay.invalidate()

    def toggle_collapsed(self):
        self.collapsed = not self.collapsed
        self.cfg["collapsed"] = self.collapsed
        ts.save_config(self.cfg)
        self.toggle.setText("▸" if self.collapsed else "▾")
        self.full.setVisible(not self.collapsed)
        self.strip.setVisible(self.collapsed)
        self._apply_header_mode()
        self._apply_collapse_metrics()
        self.refresh()

    # === data ===============================================================
    def gather(self):
        if self.demo_mode:
            return self._demo_gather()
        cfg = ts.load_config()
        wh = cfg.get("window_hours", 5)
        entries = ts.collect_entries(wh)
        ov = ts.valid_override(cfg, wh)          # rejects stale/impossible pinned resets
        if ov:
            d = ts.window_for_reset(entries, ov, wh)
        else:
            d = ts.active_window(entries, wh)
        # the learned "usual limit" is a wide file scan - only when no plan is set,
        # and only occasionally (cache it ~every 40 refreshes like the Tkinter face).
        if not cfg.get("plan") and (self._learned is None or self._ceiling_ctr % 40 == 0):
            try:
                self._learned = ts.learned_ceiling(wh, cfg["limit_lookback_days"])
            except Exception:
                pass
        self._ceiling_ctr += 1
        ceiling, noun = ts.active_ceiling(cfg, self._learned)
        try:
            sessions = ts.collect_sessions(wh)
        except Exception:
            sessions = []
        return cfg, d, ceiling, noun, sessions

    # === demo construct =====================================================
    # The demo is a real StewardQt — the SAME render path the live widget uses — but
    # fed a synthetic snapshot built purely from the slider. It never reads transcripts,
    # the session registry, or the /usage capture, and (via ts.DEMO_READONLY) never
    # writes config. So a newcomer can drive the whole card and see exactly how it
    # behaves, with zero risk to their real state.
    DEMO_MAX_TOKENS = 16_000_000     # the slider's top end (a heavy 5-hour window)
    DEMO_CEILING_USD = 40.0          # synthetic 5-hour $ budget the bar measures against

    def _demo_gather(self):
        """Build a synthetic (cfg, d, ceiling, noun, sessions) tuple from the slider —
        shaped exactly like the real gather() so refresh() drives the whole card."""
        frac = self._demo_frac
        # use the user's REAL look (theme/size) but strip every field that would pull
        # in real numbers, so the bar falls to the clean spend÷ceiling estimate path.
        cfg = dict(self.cfg)
        for k in ("calibration", "use_calibrated_ceiling", "reset_override", "plan"):
            cfg.pop(k, None)
        tok = int(round(frac * self.DEMO_MAX_TOKENS))
        usd = frac * self.DEMO_CEILING_USD
        ceiling = self.DEMO_CEILING_USD
        active = tok > 0
        # pace rises with the fill so the pace dot walks green→amber→red as you drag
        pace = round((ceiling / cfg.get("window_hours", 5)) * (0.6 + frac * 1.6), 1)
        reset = datetime.now(timezone.utc) + timedelta(hours=3)
        by_model = {"opus": {"tok": int(tok * 0.6), "usd": usd * 0.6},
                    "sonnet": {"tok": int(tok * 0.4), "usd": usd * 0.4}}
        d = {"active": active, "tok": tok, "usd": usd,
             "reset": reset if active else None,
             "pace": pace if active else 0.0, "by_model": by_model if active else {}}
        sessions = self._demo_sessions(frac, tok, usd)
        return cfg, d, ceiling, "demo", sessions

    def _demo_sessions(self, frac, tok, usd):
        """A small synthetic session list. The one OPEN session carries the slider's
        heat (its ctx ÷ ctx_red == frac), so the ring + this-chat line track the drag."""
        ctx_red = self.cfg.get("ctx_red", 220000) or 220000
        now = datetime.now(timezone.utc)
        base = dict(in_=0, out=0, cw=0, cr=0, bg=False, daemon=False, stuck=False,
                    branch="main", entrypoint="cli", pid=0)
        rows = [
            {"sid": "demo-active", "open": True, "status": "busy",
             "ctx": int(round(frac * ctx_red)), "cwd": r"C:\dev\my-project",
             "label": "building the demo feature", "model": "opus",
             "first": "build the demo mode showcase",
             "tok": int(tok * 0.5), "usd": usd * 0.5, "last": now,
             "path": "demo://active", **base},
            {"sid": "demo-idle", "open": True, "status": "idle",
             "ctx": int(round(0.25 * ctx_red)), "cwd": r"C:\dev\my-api",
             "label": "reviewing the export bug", "model": "sonnet",
             "first": "look at the csv export",
             "tok": int(tok * 0.3), "usd": usd * 0.3,
             "last": now - timedelta(minutes=6), "path": "demo://idle", **base},
            {"sid": "demo-done", "open": False, "status": "",
             "ctx": int(round(0.6 * ctx_red)), "cwd": r"C:\dev\my-site",
             "label": "fixed the dashboard", "model": "haiku",
             "first": "patch the fleet view",
             "tok": int(tok * 0.2), "usd": usd * 0.2,
             "last": now - timedelta(hours=1, minutes=20), "path": "demo://done",
             **base},
        ]
        # rename the in_ key to the real "in" field name the rollup uses
        for r in rows:
            r["in"] = r.pop("in_")
        return rows

    def _demo_on_slider(self, value):
        self._demo_frac = max(0.0, min(1.0, value / 1000.0))
        self.demo_frac = self._demo_frac          # keep the heat-override path in sync
        self._follow_key = None                   # force _apply_follow to repaint
        self._demo_update_readout()
        self.refresh()

    def _demo_update_readout(self):
        self._demo_readout.setText(
            f"{ts.fmt_tokens(int(round(self._demo_frac * self.DEMO_MAX_TOKENS)))}")

    def _demo_fire_pitstop(self):
        """SIMULATION ONLY. Spawns a large fake Claude Code terminal window and plays a
        sample /pitstop run in it — exactly what a REAL pitstop does: bank a checkpoint
        to memory, emit the ■□-framed paste-ready resume block, then spawn the fresh
        session. Pure theatre — touches NONE of the real pitstop machinery (no
        subprocess, no files, no handoff scripts)."""
        if self._demo_termwin is None:
            self._demo_build_termwin()
        win = self._demo_termwin
        win.show()
        win.raise_()
        win.activateWindow()
        # the real pitstop ritual, faithfully: token watch → bank to memory → the ■□
        # paste block (Kevin's copy-boundary cue) → auto-spawn the fresh session.
        lines = [
            "✻ Claude Code  ·  C:\\dev\\my-project   (demo)",
            "",
            "> /pitstop auto",
            "",
            "● Token watch hit the pitstop mark — saving your place.",
            "● Banking checkpoint to memory…",
            "  └─ resume_pitwall.txt written",
            "  └─ LATEST_RESUME.txt written",
            "● Banked. You're clear to restart — handing off ↓",
            "",
            "PASTE THIS INTO THE NEW SESSION",
            "■□■□■□■□■□■□■□■□■□■□■□■□■□■□■□■□■□",
            "Resume my-project, session 12. Start this session in C:/dev/my-project.",
            "Step 1 - read the project memory and the open-items list.",
            "Step 2 - run cli_window.py restore, then relaunch the server.",
            "Step 3 - image-verify the running widget before any relaunch.",
            "Step 4 - pick up the open work line.",
            "Step 5 - confirm the handoff so the old window closes itself.",
            "Step 6 - send the \"Pitstop complete\" push.",
            "■□■□■□■□■□■□■□■□■□■□■□■□■□■□■□■□■□",
            "END OF PASTE",
            "",
            "● Spawning the fresh session…   ✔ new window up.",
            "✔ Pitstop complete — the new session picks up right where you left off.",
            "",
            "> _",
        ]
        self._demo_termtext.setPlainText("")
        self._demo_anim_lines = lines
        self._demo_anim_i = 0
        self._demo_fire.setText("Firing…")
        # type the lines in one at a time so it reads like a live session
        self._demo_anim_timer = QTimer(self)
        self._demo_anim_timer.timeout.connect(self._demo_anim_step)
        self._demo_anim_timer.start(170)

    def _demo_anim_step(self):
        if self._demo_anim_i >= len(self._demo_anim_lines):
            self._demo_anim_timer.stop()
            self._demo_fire.setText("Fire pitstop")
            return
        te = self._demo_termtext
        cur = te.toPlainText()
        line = self._demo_anim_lines[self._demo_anim_i]
        te.setPlainText((cur + "\n" + line) if cur else line)
        sb = te.verticalScrollBar()
        sb.setValue(sb.maximum())
        self._demo_anim_i += 1

    def _demo_build_termwin(self):
        """Build the large fake Claude Code terminal as a separate frameless window.
        Pure display — a styled monospace QPlainTextEdit; nothing here runs or writes."""
        win = QWidget(None, Qt.Window | Qt.FramelessWindowHint)
        win.setWindowTitle("Claude Code — demo")
        scr = (QApplication.screenAt(self.pos())
               or QApplication.primaryScreen()).availableGeometry()
        w = min(round(940 * self._scale()), scr.width() - 80)
        h = min(round(640 * self._scale()), scr.height() - 80)
        win.resize(w, h)
        win.move(scr.center().x() - w // 2, scr.center().y() - h // 2)
        win.setStyleSheet("QWidget{background:#0A0A0A;}")

        outer = QVBoxLayout(win)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # title strip — traffic lights + label + close, so it reads as a terminal window
        bar = QWidget()
        bar.setStyleSheet("background:#161616;")
        bar.setFixedHeight(round(30 * self._scale()))
        blay = QHBoxLayout(bar)
        blay.setContentsMargins(10, 0, 8, 0)
        blay.setSpacing(6)
        for col in ("#FF5F56", "#FFBD2E", "#27C93F"):
            dot = QLabel("●")
            dot.setStyleSheet(f"color:{col};")
            dot.setFont(self._font(8))
            blay.addWidget(dot)
        title = QLabel("Claude Code — demo")
        title.setStyleSheet("color:#9A9A9A;")
        title.setFont(self._font(8, semibold=True))
        blay.addSpacing(8)
        blay.addWidget(title)
        blay.addStretch(1)
        close = QPushButton("✕")
        close.setCursor(QCursor(Qt.PointingHandCursor))
        close.setFont(self._font(9, semibold=True))
        close.setStyleSheet(
            "QPushButton{color:#9A9A9A;background:transparent;border:none;padding:2px 8px;}"
            "QPushButton:hover{color:#FFFFFF;}")
        close.clicked.connect(self._demo_close_termwin)
        blay.addWidget(close)
        outer.addWidget(bar)

        # the terminal body — black panel, green REAL-monospace text, large + readable
        te = QPlainTextEdit()
        te.setReadOnly(True)
        te.setFrameShape(QFrame.NoFrame)
        te.setLineWrapMode(QPlainTextEdit.NoWrap)
        te.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        tf = QFont("Consolas", self._sz(11))
        tf.setStyleHint(QFont.Monospace)
        tf.setFamilies(["Cascadia Mono", "Consolas", "Courier New"])
        te.setFont(tf)
        te.setStyleSheet(
            "QPlainTextEdit{background:#0A0A0A; color:#00E676; border:none; padding:14px;}")
        outer.addWidget(te, 1)
        self._demo_termtext = te

        # drag the window by its title strip; Esc closes it
        def _press(e, _w=win):
            if e.button() == Qt.LeftButton:
                _w._drag = e.globalPosition().toPoint() - _w.frameGeometry().topLeft()
        def _move(e, _w=win):
            if (e.buttons() & Qt.LeftButton) and getattr(_w, "_drag", None) is not None:
                _w.move(e.globalPosition().toPoint() - _w._drag)
        bar.mousePressEvent = _press
        bar.mouseMoveEvent = _move

        def _key(e):
            if e.key() == Qt.Key_Escape:
                self._demo_close_termwin()
        win.keyPressEvent = _key
        self._demo_termwin = win

    def _demo_close_termwin(self):
        t = getattr(self, "_demo_anim_timer", None)
        if t is not None:
            t.stop()
        self._demo_fire.setText("Fire pitstop")
        if self._demo_termwin is not None:
            self._demo_termwin.close()
            self._demo_termwin = None

    def _launch_demo(self):
        """Launch the isolated demo construct as a SECOND process (its own mutex, fake
        data, ts.DEMO_READONLY blocks every config write). Fixed argv with no user
        input, so there's no injection surface. A demo already running blocks itself
        via its own single-instance guard."""
        if getattr(sys, "frozen", False):
            program, argv = sys.executable, ["--demo-mode"]
        else:
            program, argv = sys.executable, [os.path.abspath(__file__), "--demo-mode"]
        try:
            QProcess.startDetached(program, argv)
        except Exception:
            pass

    def refresh(self):
        try:
            cfg, d, ceiling, noun, sessions = self.gather()
        except Exception as e:
            self.sub.setText(f"(read error: {e})")
            return
        self.cfg = cfg
        wh = cfg.get("window_hours", 5)
        # Keep the "Resets in" countdown monotonic: the guessed reset can jitter
        # between refreshes (block-start slides as transcripts age) and would tick
        # upward. A pinned override is authoritative + already stable, so adopt it
        # directly; only the guess needs stabilising. (Timer-jump/backwards bug.)
        ov = ts.valid_override(cfg, wh)
        if ov:
            self._reset_anchor = d.get("reset")
        else:
            self._reset_anchor = ts.stable_reset(d.get("reset"), self._reset_anchor)
        d["reset"] = self._reset_anchor
        self._snap = (cfg, d, ceiling, noun)
        drift = self._drift

        # --- headline $ + token line ---
        # A live session can be open while its tokens aren't on disk yet — current
        # Claude Code flushes the session transcript at the END of a turn, so the
        # window total below genuinely can't include the chat you're typing in.
        live_unmeasured = any(s.get("open") and not s.get("usd")
                              for s in sessions)
        if d.get("active") and d.get("tok"):
            self.caption.setText(f"This {wh}-hour window")
            self.dollars.setText(f"${d['usd']:.2f}")
            self.sub.setText(f"{ts.fmt_tokens(d['tok'])} tokens · value at "
                             f"pay-per-use prices")
        elif live_unmeasured:
            # Don't reassure with "nothing used yet" while a live session is spending
            # tokens we can't read yet — say plainly that it'll fill in.
            self.caption.setText(f"This {wh}-hour window")
            self.dollars.setText(f"${d.get('usd', 0.0):.2f}")
            self.sub.setText("live session not counted yet · syncs after each turn")
        else:
            self.caption.setText("Window open")
            self.dollars.setText("$0.00")
            self.sub.setText("nothing used yet · full budget")

        # --- TWO distinct signals -------------------------------------------
        # (A) the 5-hour ALLOWANCE -> the flat bar + the "% used"/projection line.
        #     Prefer the REAL synced session % (like the weekly line); fall back to the
        #     local spend÷ceiling estimate when nothing's been synced yet.
        frac5, pct5, _adj, _synced = ts.displayed_session_pct(
            cfg, d.get("usd", 0.0), ceiling, drift)
        # (B) THIS conversation's heat -> ring + pill + this-chat line. Extracted into
        #     _apply_follow so the fast focus-poll timer can re-render it the instant you
        #     switch windows, reusing this rollup (no transcript re-read); the slow
        #     refresh passes force=True to repaint with freshly-read numbers.
        self._sessions = sessions
        self._apply_follow(sessions, force=True)

        # --- pace row (static pace colour) ---
        if d.get("active"):
            pace = d["pace"]
            color, sustainable = ts.pace_state(cfg, d, pace, ceiling)
            self.dot.setStyleSheet(f"color:{color};")
            # §3.1.6: values INK 600 mono, scaffold stays MUT (the label's own color).
            # &nbsp; preserves the double-space rhythm RichText would otherwise collapse.
            _mono = "font-weight:600;"
            _v = f'<span style="{_mono} color:{ts.INK};">${pace:.0f}/hr</span>'
            if sustainable:
                # "lasts it" read as a typo to a stranger (sweep item 23) — say it plainly
                self.pace.setText(
                    f'pace&nbsp;&nbsp;{_v}&nbsp;&nbsp;·&nbsp;&nbsp;safe up to ~'
                    f'<span style="{_mono} color:{ts.INK};">'
                    f'${sustainable:.0f}/hr</span>')
            else:
                self.pace.setText(f'pace&nbsp;&nbsp;{_v}')
        else:
            self.dot.setStyleSheet(f"color:{ts.GREEN};")
            self.pace.setText("idle")

        # --- the allowance bar + barline (§3.1.7 + §3.1.8) ---
        c5 = ts.lerp_color(frac5)
        self.bar_fill.setStyleSheet(
            f"background:{ts.lerp_color(frac5)}; border-radius:3px;")
        self._set_bar(frac5)
        # barline: % of the 5-hour limit (+ warning clause when pace says so),
        # no on-track reassurance — see _build_blockA
        _pace5 = d.get("pace", 0) if d.get("active") else 0
        _, ptext, pcolor = ts.projection_text(
            cfg, d, _pace5, ceiling, drift, on_track_tail=False)
        if ptext == "tap to set your plan limit":
            # this label is no longer clickable — point at Settings instead
            ptext = "set your plan limit in Settings"
        self.proj.setText(ptext)
        self.proj.setStyleSheet(f"color:{pcolor};")

        self._set_models(d.get("by_model", {}))
        self._set_weekly_line(cfg)
        self._set_sessions(cfg, sessions, ceiling, pct5)
        self.tick()
        self._pulse()
        self._relayout()
        self._update_nudge()
        self._maybe_follow_os()    # in "system" theme, track a live OS light/dark flip

    def _apply_follow(self, sessions, force=False):
        """Render the conversation-heat trio (ring + save pill + this-chat line) for the
        session in the FOCUSED window. Cheap by design: it reuses the already-collected
        `sessions` and only touches Win32 focus calls, so the fast poll can run it a few
        times a second for a near-instant window switch while t_refresh owns the
        expensive data pull. Returns True when the followed session changed (so the
        caller repaints the ring)."""
        cfg = self.cfg
        ctx_red = cfg.get("ctx_red", 220000) or 220000
        # Pick the session this heat reflects. With focus-follow on, that's the session
        # in the window you're looking at; otherwise (and as the fallback) the
        # most-recently-active open one. `following` drives the "which session" label.
        opens = [s for s in sessions if s.get("open")]
        foc_sid = self._focus.focused_sid(sessions, cfg.get("focus_follow", "window"))
        cur = next((s for s in opens if s["sid"] == foc_sid), None) if foc_sid else None
        following = cur is not None and len(opens) > 1
        if cur is None:
            cur = opens[0] if opens else None

        # Gate: skip the relabel when nothing about the followed session changed, so the
        # fast poll is free until you actually switch windows. force=True (slow refresh)
        # always repaints because the underlying numbers may have moved.
        key = (self.demo_frac, foc_sid, cur["sid"] if cur else None, following)
        if not force and key == self._follow_key:
            return False
        self._follow_key = key
        self._cur_session = cur        # the session the shoulder-tap reasons about

        cli_frac = min(1.0, (cur.get("ctx", 0) or 0) / ctx_red) if cur else 0.0
        if self.demo_frac is not None:
            cli_frac = self.demo_frac
        self.heat = ts.lerp_color(cli_frac)
        self.heat_frac = cli_frac

        # save pill — STATIC (de-pulsed), solid InfoBadge chip (O3 Amendment 1 §3):
        # fill = the BASE heat (same color as conv_dot, owner 2026-06-12: "the pill
        # needs to follow the This Chat dot" — the earlier lerp_txt fill split from
        # the dot in light theme), text = the §2.3 contrast-chooser against the
        # RESOLVED fill (not P.KEY_TEXT — that one tracks the accent), no border.
        # The old 12%-tint + 45%-stroke outline read grey-green at pill size
        # (Kevin's eyeball, 2026-06-11).
        word = ts.save_reco(cli_frac)
        if self.demo_frac is not None:
            word += "  (demo)"
        self.pill.setText(f" {word} ")
        heat = self.heat
        pill_h = max(1, self.pill.sizeHint().height() // 2)
        pill_text = ("#1B1B1B"
                     if _contrast("#1B1B1B", heat) >= _contrast("#FFFFFF", heat)
                     else "#FFFFFF")
        self.pill.setStyleSheet(
            f"color:{pill_text}; background:{heat}; "
            f"border:none; border-radius:{pill_h}px; "
            f"padding:2px 9px; font-weight:600;")
        # conv_dot — static base heat (§3.1.5); c_dot mirrors it (§3.2)
        self.conv_dot.setStyleSheet(f"color:{heat};")
        self.c_dot.setStyleSheet(f"color:{heat};")

        # "this chat" line: fullness word in the (static base) heat colour
        if self.demo_frac is not None:
            _mono = "font-weight:600;"
            self.conv.setText(
                f'<span style="color:{ts.FAINT};">this chat · </span>'
                f'<span style="color:{heat};">{ts.fullness_word(cli_frac)}</span>'
                f'<span style="color:{ts.FAINT};"> · demo heat </span>'
                f'<span style="{_mono} color:{ts.INK};">'
                f'{int(round(cli_frac * 100))}%</span>')
            self.conv_dot.setVisible(True)
        elif cur and cur.get("open") and not cur.get("usd"):
            # The focused window is open but has no measured in-window spend: current
            # Claude Code doesn't write the live session's token usage to disk until the
            # turn finishes, so this chat genuinely can't be measured right now. Say so
            # plainly instead of showing a fabricated "0 chat size · $0.00".
            self.conv.setText(
                f'<span style="color:{ts.FAINT};">this chat · </span>'
                f'<span style="color:{ts.MUT};">live — counts sync after each turn</span>')
            self.conv.setToolTip(_tip(
                "This session is open, but Claude Code hasn't written its token usage to "
                "disk yet — it does that when the turn finishes, and the numbers fill in "
                "then."))
            self.conv_dot.setVisible(True)
        elif cur:
            # Lead is always the stable "this chat" role label. When focus-follow is
            # actively tracking across >1 open window, append " → <session name>" so you
            # can see WHICH window the heat now reflects (name in MUT, brighter than the
            # FAINT scaffold so the *change* is what catches the eye). The name is an
            # untrusted aiTitle/folder going into a RichText label + tooltip, so it's
            # html-escaped at both sinks, and elided to the face's pixel budget (mirrors
            # the session rows) rather than a raw character slice.  [Sarah + Ivan, 06-07]
            # sweep item 22 (owner, 2026-06-12): "ctx↑" was pure jargon — the value is
            # the chat's size, and the tooltip explains why size costs money.
            size_tip = (f"The whole chat is re-read every reply — "
                        f"{ts.fmt_tokens(cur.get('ctx', 0))} tokens' worth. "
                        f"Bigger chat, pricier replies.")
            if following:
                fm = self.conv.fontMetrics()
                budget = round(130 * self.cfg.get("ui_scale", 1.15))
                nm = html.escape(fm.elidedText(ts.session_name(cur), Qt.ElideRight, budget))
                lead = (f'<span style="color:{ts.FAINT};">this chat → </span>'
                        f'<span style="color:{ts.MUT};">{nm}</span>')
                self.conv.setToolTip(_tip("Heat is following the focused window: "
                                          + ts.session_name(cur) + "\n" + size_tip))
            else:
                lead = f'<span style="color:{ts.FAINT};">this chat</span>'
                self.conv.setToolTip(_tip(size_tip))
            # §3.1.5: scaffold FAINT, numeric values INK 600 mono
            _mono = "font-weight:600;"
            self.conv.setText(
                f'{lead}'
                f'<span style="color:{ts.FAINT};"> · </span>'
                f'<span style="color:{heat};">{ts.fullness_word(cli_frac)}</span>'
                f'<span style="color:{ts.FAINT};"> · </span>'
                f'<span style="{_mono} color:{ts.INK};">'
                f'{ts.fmt_tokens(cur.get("ctx", 0))}</span>'
                f'<span style="color:{ts.FAINT};"> chat size · </span>'
                f'<span style="{_mono} color:{ts.INK};">'
                f'${cur.get("usd", 0.0):.2f}</span>')
            self.conv_dot.setVisible(True)
        else:
            self.conv.setText(
                '<span style="color:%s;">no open conversation — heat is calm</span>'
                % ts.FAINT)
            self.conv_dot.setVisible(False)
        self._set_ps_pill(cur)
        return True

    def _set_ps_pill(self, cur):
        """The pitstop verification chip on the 'this chat' row (owner's order,
        2026-06-12): shows the token-watch's ACTUAL state for the followed
        session — armed / due / fired / off / hook-missing — so 'why hasn't
        pitstop fired?' is answered by a glance, not an archaeology dig."""
        try:
            w = ts.pitstop_watch(cur) if (cur and self.demo_frac is None) else None
        except Exception:
            w = None
        if not w:
            self.ps_pill.setVisible(False)
            self.seam_pill.setVisible(False)
            self._hide_hint()
            return
        color = {"armed": ts.MUT,
                 "due": ts.AMBER,
                 "fired": ts.GREEN if w["full_auto"] else ts.AMBER,
                 "off": ts.FAINT,
                 "unarmed": ts.RED}[w["state"]]
        if w["state"] == "due" and w.get("due_heat") is not None:
            # due rides the shared heat ramp like every other heat surface
            # (owner, 2026-06-12): amber at the mark, red by +half a threshold.
            color = ts.lerp_color(w["due_heat"])
        self.ps_pill.setText(w["label"])
        h = max(1, self.ps_pill.sizeHint().height() // 2)
        self.ps_pill.setStyleSheet(
            f"color:{color}; background:transparent; "
            f"border:1px solid {color}; border-radius:{h}px; padding:1px 7px;")
        # The hint shows on hover via the custom _show_hint surface (NOT QToolTip,
        # whose window renders black on this app). Stored escaped+wrapped; the
        # hint label carries its own INK color so the pill's state color can't
        # cascade in, and word-wrap handles the ~200-char first sentence.
        self.ps_pill._hint_text = w["tip"]      # raw; _show_hint sizes + escapes
        self.ps_pill.setVisible(True)
        # seam chip, directly below: green when at a clean break, quiet when
        # mid-task or while the chip is only counting down to the mark ("Seam
        # in xxM" — informational, the pitstop chip already carries urgency).
        if not w["seam_label"]:        # belt: no label -> no chip
            self.seam_pill.setVisible(False)
            if self._hint_anchor is self.seam_pill:
                self._hide_hint()
            return
        s_color = ts.GREEN if (w["seam"] and not w["seam_upcoming"]) else ts.MUT
        self.seam_pill.setText(w["seam_label"])
        sh = max(1, self.seam_pill.sizeHint().height() // 2)
        self.seam_pill.setStyleSheet(
            f"color:{s_color}; background:transparent; "
            f"border:1px solid {s_color}; border-radius:{sh}px; "
            f"padding:1px 7px;")
        self.seam_pill._hint_text = w["seam_tip"]   # raw; _show_hint sizes + escapes
        self.seam_pill.setVisible(True)

    # === custom hint surface (replaces QToolTip for the pills) =============
    # Qt's own tooltip (QTipLabel) renders its window BLACK on this app and
    # ignores every background QSS/palette (proven by Kevin's orange/purple
    # controllability test, 2026-06-12 — the text turned purple, the background
    # stayed black). So the pitstop + seam pills show THIS surface on hover:
    # the same frameless Qt.Tool pattern as the shoulder-tap, which paints its
    # styled background correctly on screen. Light PANEL bg, INK text, base-10
    # (the old tooltip read too small), restyled every show so theme flips are
    # free, and rescaled by A−/A+ via the shared _fonts/_wrap registries.
    def _ensure_hint(self):
        h = self._hint
        if h is not None:
            return h
        h = QWidget(self)
        h.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        # Translucent frameless Tool window — the SAME construction as the shoulder-tap
        # (#tap) and the hand-off dialog (#hpanel), both of which paint a solid ROUNDED
        # panel correctly on this app (owner confirmed #hpanel "fixed … full solid bg").
        # The old black-compositing bug was specific to Qt's BUILT-IN QToolTip window,
        # not a custom Tool window, so the earlier "opaque, square corners are the price"
        # workaround isn't needed — and rounding the corners to match the app is exactly
        # what the owner asked for (cli details5.mp4: square/flat read "ugly, out of place").
        h.setAttribute(Qt.WA_TranslucentBackground)
        h.setAttribute(Qt.WA_ShowWithoutActivating)
        wrap = QVBoxLayout(h)
        wrap.setContentsMargins(0, 0, 0, 0)
        self._hint_frame = QFrame(h)
        self._hint_frame.setObjectName("hint")
        wrap.addWidget(self._hint_frame)
        flay = QVBoxLayout(self._hint_frame)
        flay.setContentsMargins(12, 9, 12, 10)
        self._hint_label = QLabel("")
        self._hint_label.setTextFormat(Qt.RichText)
        self._hint_label.setWordWrap(True)   # only the long opening sentence wraps
        self._hint_label.setFont(self._font(10))
        self._fonts.append((self._hint_label, 10, False, False, False, False))
        self._wrap.append(self._hint_label)
        flay.addWidget(self._hint_label)
        self._hint = h
        return h

    def _show_hint(self, anchor, raw, beside=False):
        if not raw:
            return
        h = self._ensure_hint()
        # Match the app's floating-panel language exactly (#tap / #hpanel): solid BG fill,
        # 1px EDGE border, CARD_RADIUS rounded corners. The translucent window lets the
        # corners outside the radius read as the desktop, so the rounding is real.
        self._hint_frame.setStyleSheet(
            f"#hint{{background:{ts.BG}; border:1px solid {ts.EDGE}; "
            f"border-radius:{CARD_RADIUS}px;}}")
        self._hint_label.setStyleSheet(f"color:{ts.INK};")
        # Width = the widest \n-line so the legend entries sit one-per-row and DON'T
        # word-wrap (owner, 2026-06-12). Capped at 60% of the screen so the long
        # opening sentence wraps instead of drawing a monitor-wide strip.
        tl = anchor.mapToGlobal(QPoint(0, 0))
        scr = QApplication.screenAt(tl) or QApplication.primaryScreen()
        geo = scr.availableGeometry()
        fm = QFontMetrics(self._hint_label.font())
        widest = max((fm.horizontalAdvance(ln) for ln in raw.split("\n")), default=200)
        cap = min(int(geo.width() * 0.60), 720)
        self._hint_label.setFixedWidth(max(180, min(widest + 2, cap)))
        self._hint_label.setText(_rich(raw))
        h.adjustSize()
        if beside:
            # Tooltips: park the hint to the SIDE of the card, vertically aligned to the
            # anchor — so it never covers the row it describes (the cursor-anchored native
            # tooltip overlapped the rows: owner "inconsistent with hitting the line",
            # cli details4.mp4). Roomier side, clamped fully onto the anchor's screen.
            g = self.frameGeometry()
            x = g.right() + 10
            if x + h.width() > geo.right() - 4:
                x = g.left() - 10 - h.width()
            x = max(geo.left() + 4, min(x, geo.right() - h.width() - 4))
            y = max(geo.top() + 4, min(tl.y(), geo.bottom() - h.height() - 4))
        else:
            # Pills: just below the pill, clamped to the pill's own screen.
            x = max(geo.left() + 4, min(tl.x(), geo.right() - h.width() - 4))
            y = tl.y() + anchor.height() + 6
            if y + h.height() > geo.bottom() - 4:      # no room below -> flip above
                y = tl.y() - h.height() - 6
        h.move(x, y)
        h.show()
        h.raise_()
        self._hint_anchor = anchor

    def _hide_hint(self):
        if self._hint is not None and self._hint.isVisible():
            self._hint.hide()
        self._hint_anchor = None

    def _follow_tick(self):
        """Fast timer: re-pick the focused session off the CACHED rollup and, if it
        changed, relabel + repaint the ring at once. No transcript reads happen here."""
        if self.demo_frac is not None or not self._sessions:
            return
        try:
            if self._apply_follow(self._sessions):
                self._pulse()
                self._relayout()
            self._update_nudge()
        except Exception:
            pass

    def _set_bar(self, frac):
        frac = max(0.0, min(1.0, frac))
        lay = self.bar_track.layout()
        lay.setStretch(0, max(1, int(round(frac * 1000))))
        lay.setStretch(1, max(0, int(round((1 - frac) * 1000))))

    def _set_models(self, by_model):
        split = ts.model_split(by_model or {})
        if not split:
            self.models.setText("")
            self.models.setVisible(False)
            return
        self.models.setVisible(True)
        parts = []
        for fam, usd in split:
            col = ts.MODEL_COLORS.get(fam, ts.MUT)
            # escape the label: it falls back to the raw family string, which is
            # transcript-derived. RichText would otherwise render embedded markup
            # (incl. remote <img> -> a network fetch). Ivan M1, 2026-06-06.
            label = html.escape(ts.MODEL_LABEL.get(fam, fam))
            parts.append(f'<span style="color:{col};">{label} ${usd:.2f}</span>')
        sep = f'<span style="color:{ts.FAINT};"> · </span>'
        self.models.setText(sep.join(parts))

    def _set_weekly_line(self, cfg):
        """Block B: weekly-allowance line + wk_bar. Hidden until synced. (§3.1.9)"""
        line = ts.weekly_line(cfg)
        wk_pct = (cfg.get("calibration") or {}).get("weekly_all_pct")
        if not line:
            self.weekly.setVisible(False)
            self.wk_track.setVisible(False)
        else:
            self.weekly.setText(line)
            self.weekly.setStyleSheet(f"color:{ts.MUT};")
            self.weekly.setVisible(True)
            # update wk_bar width + heat color (same ramp + gradient recipe as the
            # 5-hour bar_fill, so the two bars read identically)
            if wk_pct is not None:
                frac = max(0.0, min(1.0, wk_pct / 100.0))
                cw = ts.lerp_color(frac)
                self.wk_fill.setStyleSheet(
                    f"background:{ts.lerp_color(frac)}; border-radius:3px;")
                wkl = self.wk_track.layout()
                wkl.setStretch(0, max(1, int(round(frac * 1000))))
                wkl.setStretch(1, max(0, int(round((1 - frac) * 1000))))
            self.wk_track.setVisible(True)
        self._paint_lsync()

    def _paint_lsync(self):
        """Paint the freshness stamp under the weekly line. While a real-usage capture is
        running, show 'syncing…' (ACCENT) so the auto-sync-on-launch is visible; otherwise
        the absolute 'Synced as of …' (MUT), or hide it until the first sync. Shared by the
        weekly-line render and the 1s tick so the stamp tracks the in-flight state live."""
        if self._auto.in_flight:
            self.lsync.setText("syncing…")
            self.lsync.setStyleSheet(f"color:{ts.INK};")   # INK (was ACCENT — §3.1.10)
            self.lsync.setVisible(True)
            self.sync_link.setVisible(False)   # one capture at a time
            return
        self.sync_link.setVisible(True)
        clock = ts.last_synced_clock(self.cfg)
        if clock:
            self.lsync.setText(f"Synced as of {clock}")
            self.lsync.setStyleSheet(f"color:{ts.MUT};")
            self.lsync.setVisible(True)
        else:
            self.lsync.setVisible(False)

    def _card_sync_now(self):
        """The card's 'sync now' control: one-shot /usage capture on the SAME worker +
        apply path as Settings' Sync now, sharing the scheduler's in_flight gate so it
        can never double-run. Feedback is the freshness stamp itself — _paint_lsync
        shows 'syncing…' while in flight, then the new clock."""
        if self._auto.in_flight:
            return
        self._auto.mark_started()
        self._paint_lsync()                    # flip to "syncing…" immediately
        threading.Thread(target=self._sync_now_worker, daemon=True).start()
        QTimer.singleShot(500, self._card_sync_poll)

    def _card_sync_poll(self):
        """UI-thread poll for the card-initiated sync (the card twin of
        _sync_now_poll, minus the dialog widgets)."""
        with self._auto_lock:
            cap = self._sync_now_result
        if cap is None:
            QTimer.singleShot(400, self._card_sync_poll)
            return
        with self._auto_lock:
            self._sync_now_result = None
        res = ts.inject_real_usage(self.cfg, capture=cap)
        if res.get("ok") and res.get("changed"):
            self._reset_anchor = None      # let the new reset re-anchor the countdown
            try:
                self._drift = ts.drift_summary(ts.load_corrections())
            except Exception:
                self._drift = None
            self.refresh()
        if not res.get("ok"):
            self.lsync.setText("couldn't read /usage — try again")
            self.lsync.setStyleSheet(f"color:{ts.AMBER};")
            self.lsync.setVisible(True)
            self.sync_link.setVisible(True)
            QTimer.singleShot(4000, self._paint_lsync)   # 4s, like the ack flashes
            return
        self._paint_lsync()

    def _recolor(self, lbl, color):
        lbl.setStyleSheet(f"color:{color};")

    def _set_sessions(self, cfg, sessions, ceiling, pct5):
        # IN-PLACE update — the rows are PERSISTENT widgets, never torn down. Tearing
        # rows out (setParent(None)) on a frameless translucent window momentarily
        # collapses the sessions box; the layout yanks the reset row + tip UP, and the
        # just-vacated bottom strip shows the DESKTOP for one frame (the "white flash").
        # Reusing the same row widgets and only mutating text/colour/visibility keeps the
        # layout height rock-steady every refresh, so no transparent gap can ever open.
        # Rows are appended only when the session COUNT grows (rare); surplus rows hide.
        now = datetime.now(timezone.utc)
        n_open = sum(1 for s in sessions if s.get("open"))
        self.sess_cap.setText(f"Claude sessions · {n_open} open")
        ctx_red = cfg.get("ctx_red", 220000) or 220000

        # full-card rows — capped at max_sessions, expandable on demand (+N more)
        max_n = cfg.get("max_sessions", 5) or 5
        total = len(sessions)
        shown = sessions if self._sessions_expanded else sessions[:max_n]
        # Right-gutter column widths are FIT to their widest realistic content (font-metric-
        # derived, not magic px) so the state·tokens·$ cluster reads tight with no dead
        # whitespace, while staying fixed-width + right-aligned so columns still line up down
        # every row. Auto-tracks DPI and the A−/A+ font scale. (Sarah spec 2026-06-07.)
        sc = self._scale()
        state_fm = QFontMetrics(self._font(8))
        mono_fm = QFontMetrics(self._font(9, mono=True))
        sw = state_fm.horizontalAdvance("closed") + round(6 * sc)    # widest state word
        # Mono font ⇒ width = character COUNT. Size tokens/$ to the 7-cell worst case
        # (100M+ tokens "148.20M", $100+ "$148.00") so a heavy session can never overflow
        # the column again — the one extra cell just trims the eliding name's budget by a
        # glyph (invisible), vs a visible repeat of the overlap bug. (Sarah whole-window
        # review 2026-06-07.) "000.00M"/"$000.00" are width SAMPLES, not displayed.
        tw = mono_fm.horizontalAdvance("000.00M") + round(4 * sc)    # 7-cell token max
        mw = mono_fm.horizontalAdvance("$000.00") + round(4 * sc)    # 7-cell $ max
        # The name column self-elides to its OWN real width (_ElideLabel), so there is no
        # longer any card-width math to pre-compute here — the layout stretch sizes it and
        # the fixed gutter (state · tokens · $) always wins its room. (Overlap fix 06-07.)
        while len(self._sess_pool) < len(shown):
            self._sess_pool.append(self._build_session_row())
        for i, s in enumerate(shown):
            self._fill_session_row(self._sess_pool[i], s, ctx_red, now, sw, tw, mw)
            self._sess_pool[i].setVisible(True)
        for i in range(len(shown), len(self._sess_pool)):
            self._sess_pool[i].setVisible(False)
        self._sess_empty.setVisible(not shown)
        # the "+N more" / "show fewer" expander, only when the list overflows the cap
        if total > max_n:
            self._sess_more.setText("show fewer ▲" if self._sessions_expanded
                                    else f"+{total - max_n} more ▼")
            self._sess_more.setVisible(True)
        else:
            self._sess_more.setVisible(False)

        # compact strip rows (open windows only)
        opens = [s for s in sessions if s.get("open")]
        cw = sw                                   # strip state column matches the card
        while len(self._strip_pool) < len(opens):
            self._strip_pool.append(self._build_strip_row())
        for i, s in enumerate(opens):
            self._fill_strip_row(self._strip_pool[i], s, ctx_red, now, cw, tw, mw)
            self._strip_pool[i].setVisible(True)
        for i in range(len(opens), len(self._strip_pool)):
            self._strip_pool[i].setVisible(False)

        # newly-appended rows need the edge-hover tracking too (idempotent; no-op if none)
        self._install_edge_tracking()

        # §4.2: strip top line "5h 38% · wk 21%" — four labels, shown only when synced
        if ceiling:
            self.c_5h_pct.setText(f"{pct5}%")
            self.c_5h_lbl.setVisible(True)
            self.c_5h_pct.setVisible(True)
            wk_pct_val = (cfg.get("calibration") or {}).get("weekly_all_pct")
            if wk_pct_val is not None:
                self.c_wk_pct.setText(f"{int(round(wk_pct_val))}%")
                self.c_wk_lbl.setVisible(True)
                self.c_wk_pct.setVisible(True)
            else:
                self.c_wk_lbl.setVisible(False)
                self.c_wk_pct.setVisible(False)
        else:
            for _w in (self.c_5h_lbl, self.c_5h_pct, self.c_wk_lbl, self.c_wk_pct):
                _w.setVisible(False)

    def _build_session_row(self):
        # Build ONE full-card row with the FULL set of children (chip + restart always
        # exist, toggled by visibility) so a refresh only ever mutates text/colour/
        # visibility — never reparents — keeping the bottom of the card from moving.
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)              # tight numeric gutter; chip gets +4 right margin below
        holder._dot = self._mk("●", 8, ts.FAINT)
        row.addWidget(holder._dot)
        # The name is a SELF-ELIDING column (drops the old feed-forward setFixedWidth
        # width math that caused the recurring overlap). It Expands to take the row's
        # leftover space and elides to its REAL width, so it can never overflow into the
        # chip or gutter. The model chip therefore sits next to the right-hand cluster
        # (owner choice, 2026-06-07).
        holder._name = self._mk("", 9, ts.INK, cls=_ElideLabel)
        # left-click an OPEN row's name -> flash the terminal window that session
        # lives in; right-click ANY row -> the session-details popup (owner,
        # 2026-06-12); anything else falls through (e.ignore) so window-drag works
        def _nclick(e, h=holder):
            s = h._session
            if e.button() == Qt.RightButton and s:
                self._show_session_details(s)
                return
            if e.button() == Qt.LeftButton and s and s.get("open"):
                self._flash_session_window(s)
                return
            e.ignore()
        holder._name.mousePressEvent = _nclick
        row.addWidget(holder._name, 1)
        # 'bg' chip — marks a background-job session (real process, NO terminal window),
        # so the user isn't sent hunting for a window that doesn't exist (owner, 2026-06-12)
        holder._bg = self._mk("bg", 8, ts.FAINT)
        holder._bg.setContentsMargins(0, 0, 4, 0)
        holder._bg.setVisible(False)
        row.addWidget(holder._bg)
        # 'stuck' chip — a background job whose own state file says it's blocked
        # waiting on something nobody can see (owner, 2026-06-12 zombie job)
        holder._stuck = self._mk("stuck", 8, ts.AMBER)
        holder._stuck.setContentsMargins(0, 0, 4, 0)
        holder._stuck.setVisible(False)
        holder._stuck.setToolTip(_tip("This background job reports it is stuck and needs "
                                      "attention. Right-click the name for details."))
        row.addWidget(holder._stuck)
        holder._chip = self._mk("", 8, ts.MUT)
        holder._chip.setContentsMargins(0, 0, 4, 0)   # chip↔state reads as 8px (4 margin +
        row.addWidget(holder._chip)                   # 4 spacing) — wider than the numeric gaps
        # fixed-width right gutter (state | tokens | $ | restart) so columns align
        holder._state = self._mk("", 8, ts.FAINT)
        holder._state.setAlignment(Qt.AlignRight)
        row.addWidget(holder._state)
        holder._tok = self._mk("", 9, ts.MUT, mono=True)
        holder._tok.setAlignment(Qt.AlignRight)
        row.addWidget(holder._tok)
        holder._money = self._mk("", 9, ts.MUT, mono=True)
        holder._money.setAlignment(Qt.AlignRight)
        row.addWidget(holder._money)
        holder._session = None
        # The ↻ hand-off button is an OVERLAY child, NOT in the row layout — so the
        # state·tokens·$ columns line up identically on OPEN and CLOSED rows. It is
        # hover-revealed over the right edge of open rows only (driven by the window
        # eventFilter, _update_row_hover), with a BG backing chip masking the $ beneath;
        # instant show/hide, no fade — motion is reserved for the heat pulse (Sarah 2026-06-06).
        holder._restart = self._ctl(self._mk("↻", 10, ts.INK),
                                    lambda h=holder: self.restart_session(h._session))
        holder._restart.setParent(holder)
        holder._restart.setStyleSheet(self._restart_qss(selected=False))
        holder._restart.setVisible(False)
        def _ren(e, b=holder._restart):
            b.setStyleSheet(self._restart_qss(selected=True))
        def _rlv(e, b=holder._restart):
            b.setStyleSheet(self._restart_qss(selected=False))
        holder._restart.enterEvent = _ren
        holder._restart.leaveEvent = _rlv
        def _hres(e, h=holder):
            if h._restart.isVisible():
                self._position_restart(h)
            QWidget.resizeEvent(h, e)
        holder.resizeEvent = _hres
        # keep the persistent "no recent CLI sessions" label last in the box
        self.sess_box.insertWidget(self.sess_box.count() - 1, holder)
        return holder

    def _restart_qss(self, selected=False):
        # ↻ overlay — raised key (hover reveals it). O3: radius 4, flat recipe.
        # selected=True → raised hover fill (not accent — refresh is not selection, §6.1.9)
        theme = getattr(self, "_applied_theme", "dark")
        if selected:
            return (f"background:{P.PANEL_HI}; color:{ts.INK};"
                    f" border:1px solid {P.CTL_STROKE}; border-radius:4px; padding:0 4px;")
        return (_raised_qss(4, checked=False, theme=theme)
                + f" color:{ts.INK}; padding:0 4px;")

    def _position_restart(self, h):
        b = h._restart
        b.adjustSize()                           # shrink to the glyph (overlay, no layout)
        b.move(max(0, h.width() - b.width() - 2),
               max(0, (h.height() - b.height()) // 2))

    def _fill_session_row(self, holder, s, ctx_red, now, state_w, tok_w, money_w):
        is_open = bool(s.get("open"))
        # dot + state word = this session's CHAT FULLNESS, so an open row matches the
        # "this chat" dot + the save pill (owner 2026-06-12: one heat for the chat)
        frac = ts.session_row_frac(s, ctx_red)
        col = ts.lerp_color(frac) if is_open else ts.FAINT
        holder._session = s
        self._recolor(holder._dot, col)
        # model chip first, so we can measure it before sizing the flexible name column
        mname = ts.MODEL_LABEL.get(s.get("model"), "")
        if mname:
            holder._chip.setText(mname)
            self._recolor(holder._chip, ts.MODEL_COLORS.get(s.get("model"), ts.MUT)
                          if is_open else ts.FAINT)
            holder._chip.setVisible(True)
        else:
            holder._chip.setVisible(False)
        holder._bg.setVisible(bool(s.get("bg")))
        self._recolor(holder._bg, ts.MUT if is_open else ts.FAINT)
        holder._stuck.setVisible(bool(s.get("stuck")))
        self._recolor(holder._stuck, ts.AMBER if is_open else ts.FAINT)
        # name (+ entrypoint tag): a self-eliding column (_ElideLabel) that fits its OWN
        # real allocated width — no width math, no setFixedWidth. The Expanding policy
        # hands it the row's leftover space and it ellipsizes if that's too small, so it
        # physically cannot overflow the chip or the right gutter in ANY width / DPI /
        # font-size / collapsed / mid-drag state. (Permanent overlap fix, 2026-06-07.)
        ep = s.get("entrypoint") or ""
        tag = "" if ep in ("", "cli") else "·desktop" if "desktop" in ep else "·" + ep[:7]
        self._recolor(holder._name, ts.INK if is_open else ts.FAINT)  # §3.1.13: closed→FAINT
        holder._name.setFullText(ts.session_name(s) + tag)
        holder._name.setCursor(Qt.PointingHandCursor if is_open else Qt.ArrowCursor)
        if not is_open:
            name_tip = "Right-click for session details"
        elif s.get("bg"):
            # the resume COMMAND moved into right-click details (Copy all works there;
            # a tooltip can't hold a copy button) — sweep item 17, owner 2026-06-12
            name_tip = ("Background session — it has no window. Right-click for "
                        "details and ways to reach it.")
        else:
            name_tip = ("Click to flash this session's window. "
                        "Right-click for details.")
        holder._name.setToolTip(_tip(name_tip))
        holder._state.setFixedWidth(state_w)
        holder._state.setText(ts.session_state_word(s, now))
        self._recolor(holder._state, col)   # base heat — matches the row dot (owner 2026-06-12)
        holder._tok.setFixedWidth(tok_w)
        holder._money.setFixedWidth(money_w)
        if is_open and not s.get("usd"):
            # Open window with no measured in-window spend — either a live session whose
            # transcript isn't on disk yet (Claude Code flushes it at turn end), a
            # synthesised open-window row, or one idle since before this 5h window. A
            # measured row always has usd > 0 (the scan requires it), so a zero here was
            # never actually counted: show "—", never a fabricated "$0.00".
            holder._tok.setText("—")
            holder._money.setText("—")
        else:
            holder._tok.setText(ts.fmt_tokens(s.get("tok", 0)))
            holder._money.setText(f"${s.get('usd', 0.0):.2f}")
        self._recolor(holder._tok, ts.MUT if is_open else ts.FAINT)
        if not is_open:                                   # closed rows never reveal ↻
            holder._restart.setVisible(False)
            if self._hover_row is holder:
                self._hover_row = None

    def _build_strip_row(self):
        holder = QWidget()
        row = QHBoxLayout(holder)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        holder._dot = self._mk("●", 9, ts.GREEN)
        row.addWidget(holder._dot)
        # self-eliding name (same overlap-proof column as the full card) — Expands to the
        # strip's leftover width and ellipsizes; the fixed state column always fits.
        holder._name = self._mk("", 9, ts.INK, cls=_ElideLabel)
        # strip rows are all OPEN sessions — same click-to-flash as the full card,
        # same right-click details (owner, 2026-06-12)
        holder._session = None
        def _nclick(e, h=holder):
            if e.button() == Qt.RightButton and h._session:
                self._show_session_details(h._session)
                return
            if e.button() == Qt.LeftButton and h._session:
                self._flash_session_window(h._session)
                return
            e.ignore()
        holder._name.mousePressEvent = _nclick
        holder._name.setCursor(Qt.PointingHandCursor)
        row.addWidget(holder._name, 1)
        holder._bg = self._mk("bg", 8, ts.FAINT)          # background-session marker
        holder._bg.setContentsMargins(0, 0, 4, 0)
        holder._bg.setVisible(False)
        row.addWidget(holder._bg)
        holder._stuck = self._mk("stuck", 8, ts.AMBER)    # blocked background job
        holder._stuck.setContentsMargins(0, 0, 4, 0)
        holder._stuck.setVisible(False)
        holder._stuck.setToolTip(_tip("This background job reports it is stuck and needs "
                                      "attention. Right-click the name for details."))
        row.addWidget(holder._stuck)
        holder._chip = self._mk("", 8, ts.MUT)
        holder._chip.setContentsMargins(0, 0, 4, 0)
        row.addWidget(holder._chip)
        holder._state = self._mk("", 8, ts.FAINT)
        holder._state.setAlignment(Qt.AlignRight)
        row.addWidget(holder._state)
        # per-row tokens + $ (owner, 2026-06-11) — same fixed-width right-aligned gutter
        # as the full card, so the columns line up down the strip; right-alignment leaves
        # the width slack on the left, which doubles as the column gap (row spacing is 0)
        holder._tok = self._mk("", 9, ts.MUT, mono=True)
        holder._tok.setAlignment(Qt.AlignRight)
        row.addWidget(holder._tok)
        holder._money = self._mk("", 9, ts.MUT, mono=True)
        holder._money.setAlignment(Qt.AlignRight)
        row.addWidget(holder._money)
        self.c_sess.addWidget(holder)
        return holder

    def _fill_strip_row(self, holder, s, ctx_red, now, state_w, tok_w, money_w):
        # same chat-fullness heat as the card rows (owner 2026-06-12)
        frac = ts.session_row_frac(s, ctx_red)
        col = ts.lerp_color(frac)
        holder._session = s
        self._recolor(holder._dot, col)
        holder._name.setFullText(" " + ts.session_name(s))
        mname = ts.MODEL_LABEL.get(s.get("model"), "")
        holder._chip.setText(mname)
        self._recolor(holder._chip, ts.MODEL_COLORS.get(s.get("model"), ts.MUT))
        holder._chip.setVisible(bool(mname))
        holder._bg.setVisible(bool(s.get("bg")))
        self._recolor(holder._bg, ts.MUT)
        holder._stuck.setVisible(bool(s.get("stuck")))
        self._recolor(holder._stuck, ts.AMBER)
        holder._state.setFixedWidth(state_w)
        holder._state.setText(ts.session_state_word(s, now))
        self._recolor(holder._state, col)   # base heat — matches the row dot (owner 2026-06-12)
        holder._tok.setFixedWidth(tok_w)
        holder._tok.setText(ts.fmt_tokens(s.get("tok", 0)))
        holder._money.setFixedWidth(money_w)
        holder._money.setText(f"${s.get('usd', 0.0):.2f}")

    def _flash_session_window(self, s):
        """Row-click: ring the terminal window this session lives in (owner, 2026-06-10 —
        with several CLIs open the row labels alone can be too vague to tell apart).
        Never guesses: when the window can't be pinned down, it says so in the tip line
        instead of flashing the wrong window. Minimized targets get the standard
        taskbar-button flash (no frame on screen to ring — owner's pick)."""
        if s.get("bg"):                  # background session: no window EXISTS to flash
            self._set_tip(BG_FLASH_TIP)
            self._apply_tip_visibility()   # re-show tip row if tips_off (must be seen)
            self._relayout()
            return
        # The folder→tab match (collect_sessions) gives a live window directly, which
        # the sid-based lookup can't for a synthesised idle row (fake/stale sid). Trust
        # it only if it's still a live, sized window; else fall back to the evidence chain.
        hwnd = s.get("whwnd")
        if not (hwnd and ts.window_rect(hwnd)):
            hwnd = self._focus.window_for_sid(s["sid"], self._sessions)
        if not hwnd:
            self._set_tip(FLASH_MISS_TIP)
            self._apply_tip_visibility()   # re-show tip row if tips_off (FLASH_MISS must show)
            self._relayout()
            return
        if ts.window_minimized(hwnd):
            ts.flash_taskbar(hwnd)
            return
        if self._win_flash is None:
            self._win_flash = _WindowFlash()
        frac = min(1.0, (s.get("ctx", 0) or 0) /
                   max(self.cfg.get("ctx_red", 220000), 1))
        self._win_flash.start(hwnd, frac)

    def _show_session_details(self, s):
        """Right-click on a session row: the troubleshooting card (owner,
        2026-06-12 — 'detailed info on the cli that Pitwall shows, PID, name etc').
        Everything Pitwall knows about that CLI — verified pid + image, the window
        it would flash and WHY, job state, transcript path — with a copy-all so
        the user can paste it into a chat or a bug report. Values are re-checked
        at open time, because this view exists for the moments a row looks wrong."""
        old = getattr(self, "_sess_details", None)
        if old is not None:
            try:
                old.close()
            except Exception:
                pass
        rows = ts.session_details(s, self._focus, self._sessions)
        text = ts.details_text(rows)
        dlg = QDialog(self)
        dlg.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        dlg.setAttribute(Qt.WA_TranslucentBackground)   # rounded frameless corners
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.setObjectName("ddlg")
        self._sess_details = dlg
        dlg.destroyed.connect(lambda *_: setattr(self, "_sess_details", None))
        dlg.rejected.connect(dlg.close)   # ESC reject()s = HIDES; route it to close
        # A QFrame child paints the FULL panel — background + border + rounded
        # corners INCLUDING the layout margins — exactly like the hand-off dialog
        # (#hpanel). The old *{background} approach painted child labels only, so the
        # dialog's own transparent margins let the black active CLI show through
        # (owner, 2026-06-12 "cli details.mp4").
        shell = QVBoxLayout(dlg)
        shell.setContentsMargins(0, 0, 0, 0)
        panel = QFrame(dlg)
        panel.setObjectName("dpanel")
        panel.setStyleSheet(f"#dpanel{{background:{ts.BG}; border:1px solid {ts.EDGE};"
                            f" border-radius:{CARD_RADIUS}px;}}")
        shell.addWidget(panel)
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(12, 8, 12, 10)
        outer.setSpacing(4)
        head = QHBoxLayout()
        head.setSpacing(10)
        title = self._mk("Session details", 9, ts.MUT, semibold=True)
        # Pin the header labels to their natural height: the wrapped value labels
        # below report inflated sizeHints, and without this the header soaks up the
        # slack into a tall empty band (lead probe, 2026-06-12).
        title.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        head.addWidget(title)
        head.addStretch(1)
        # ⧉ copy-all — same affordance as the pitstop tap's copy (owner ask:
        # "like Switchboard has for the texts in chat")
        cp = self._mk("⧉ copy all", 9, ts.MUT)
        cp.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        def _copy():
            QApplication.clipboard().setText(text)   # copy ONLY — never executes
            cp.setText("copied ✓")
            cp.setStyleSheet(f"color:{ts.GREEN};")
            QTimer.singleShot(1500, lambda: (
                cp.setText("⧉ copy all"), cp.setStyleSheet(f"color:{ts.MUT};")))
        self._ctl(cp, _copy)
        self._hover(cp, ts.INK, ts.MUT)
        head.addWidget(cp)
        x = self._ctl(self._mk("✕", 10, ts.MUT), dlg.close)
        x.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._hover(x, ts.INK, ts.MUT)
        head.addWidget(x)
        outer.addLayout(head)
        # drag anywhere on the title to move the popup (frameless)
        def _drag(e):
            if e.button() == Qt.LeftButton and dlg.windowHandle():
                dlg.windowHandle().startSystemMove()
        title.mousePressEvent = _drag
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(3)
        grid.setColumnStretch(1, 1)
        for i, (k, v) in enumerate(rows):
            kl = self._mk(k, 8, ts.FAINT)
            kl.setAlignment(Qt.AlignRight | Qt.AlignTop)
            grid.addWidget(kl, i, 0)
            vl = self._mk(v, 9, ts.INK)
            vl.setWordWrap(True)
            vl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(vl, i, 1)
        outer.addLayout(grid)
        # Size by TRUE wrapped height, not adjustSize: the value labels word-wrap and
        # report inflated sizeHints, so adjustSize oversizes the window and the slack
        # inflates the header band (lead probe, 2026-06-12 — same unstable-wordwrap
        # class as the edge-drag fix). Fix the width, activate the layout, then pin the
        # height to heightForWidth (the real wrapped height) when available.
        W = int(460 * self._scale_at(self.size_level))
        dlg.setFixedWidth(W)
        # Measure the PANEL's own layout, not dlg.layout(): the 0-margin shell wraps the
        # panel in a QWidgetItem that doesn't propagate heightForWidth, so measuring the
        # shell fell back to a too-short sizeHint — the popup opened short then resized
        # after show (the "paints twice / jerky" jank, owner cli details2.mp4). The panel
        # is the full dialog width (shell has no margins), so its height IS the dialog's.
        lay = panel.layout()
        lay.activate()
        hfw = lay.heightForWidth(W) if lay.hasHeightForWidth() else 0
        dlg.setFixedHeight(hfw if hfw > 0 else panel.sizeHint().height())
        # open near the cursor, clamped onto the row's screen
        pos = QCursor.pos()
        geo = self._screen_geo(self)
        dlg.move(min(max(pos.x() - 30, geo.left() + 8),
                     geo.right() - dlg.width() - 8),
                 min(max(pos.y() + 12, geo.top() + 8),
                     geo.bottom() - dlg.height() - 8))
        dlg.show()

    def _registry_watch(self):
        """2s poll: when a session process appears or exits (registry filename set
        changed), pull the full refresh immediately — the new/dead row shows now,
        not at the next refresh_seconds boundary (owner, 2026-06-12)."""
        try:
            stamp = ts.registry_stamp()
        except Exception:
            return
        if stamp != self._reg_stamp:
            self._reg_stamp = stamp
            self.refresh()

    def _topmost_guard(self):
        """21s poll while always-on-top is on: detect + heal z-band corruption
        (the widget under normal windows despite WS_EX_TOPMOST). Evidence of
        what sat above us lands in topmost_guard.log for root-causing."""
        try:
            bad, evidence = ts.topmost_anomaly(int(self.winId()))
            if bad:
                ts.topmost_reassert(int(self.winId()), evidence)
        except Exception:
            pass                  # the guard must never take the widget down

    def tick(self):
        self._auto_step()
        if not self._snap:
            return
        _, d, _, _ = self._snap
        cd = ts.fmt_countdown(d.get("reset"))
        self.countdown.setText(cd)
        self.c_reset.setText(cd)
        self.c_tok.setText(ts.fmt_tokens(d.get("tok", 0)) if d.get("active") else "—")
        self.c_usd.setText(f"${d.get('usd', 0.0):.2f}" if d.get("active") else "—")
        self._paint_lsync()      # flip the stamp to 'syncing…' / 'as of HH:MM' live

    # --- auto real-usage sync ------------------------------------------------
    def _live_family(self):
        """Effective model family for the Fable resync default. Prefer 'fable' when ANY
        live session is running it (Fable burns faster, so the faster cadence should win),
        else the most-recently-active session's family. (Owner bug report 2026-06-11: the
        old most-recent-only read showed the generic 30-min default right after a restart.)"""
        fams = [s.get("model") for s in (self._sessions or [])]
        return "fable" if "fable" in fams else (fams[0] if fams else None)

    def _auto_step(self):
        """Drive the background auto-sync of the REAL /usage numbers, on the 1s tick.
        Every tick, cheaply apply a finished capture (if one is waiting) on THIS (UI)
        thread — so the cfg mutate + save + re-render can never race the worker. Throttled
        to ~10s, ask the scheduler whether a capture is due; if so, spawn a daemon thread
        to run the BLOCKING capture off the UI thread and park its result."""
        self._auto_apply_pending()
        self._auto_ctr += 1
        if self._auto_ctr % 10 != 0 or self._auto.in_flight:
            return
        try:
            reason = self._auto.tick(ts.latest_activity_ts(),
                                     active_family=self._live_family())
        except Exception:
            reason = None
        if not reason:
            return
        self._auto.mark_started()
        threading.Thread(target=self._auto_capture_worker, daemon=True).start()

    def _auto_capture_worker(self):
        """OFF the UI thread: run the blocking /usage capture, park the result for the
        next tick to apply, then clear the in-flight flag. Never raises (the capture
        already turns every failure into an ok=False dict)."""
        try:
            cap = ts.capture_real_usage()
        except Exception as e:
            cap = {"ok": False, "error": "worker: %s" % e}
        with self._auto_lock:
            self._auto_pending = cap
        self._auto.mark_done()

    def _auto_apply_pending(self):
        """If the worker parked a finished capture, fold it into cfg on THIS thread
        (ts.inject_real_usage — the SAME write path as the manual Sync) and, when it
        moved the reset/ceiling, re-read so the window + countdown lock onto the truth."""
        with self._auto_lock:
            cap = self._auto_pending
            self._auto_pending = None
        if cap is None:
            return
        res = ts.inject_real_usage(self.cfg, capture=cap)
        if res.get("ok") and res.get("changed"):
            self._reset_anchor = None      # let the new reset re-anchor the countdown
            try:
                self._drift = ts.drift_summary(ts.load_corrections())
            except Exception:
                self._drift = None
            self.refresh()

    def _sync_now_worker(self):
        """OFF the UI thread: the one-shot 'Sync now' capture. Parks its result in a
        SEPARATE slot from the auto path (so the auto tick can't consume it first), then
        clears the in-flight gate. Never raises (capture turns failures into ok=False)."""
        try:
            cap = ts.capture_real_usage()
        except Exception as e:
            cap = {"ok": False, "error": "worker: %s" % e}
        with self._auto_lock:
            self._sync_now_result = cap
        self._auto.mark_done()

    def _lsync_text(self):
        """The 'last synced …' line for the auto-sync block — shared by the dialog build
        and the live Sync-now refresh so they always read the same way. Shows the ABSOLUTE
        clock time ('Last synced at 9:40 AM'): the owner found the relative 'just now' ambiguous
        (2026-06-08), so Settings now matches the main card's clock. Falls back to relative
        age only if the clock can't be parsed."""
        au = self.cfg.get("auto_usage") or {}
        ls = au.get("last_sync")
        if not ls:
            return "not synced yet"
        clock = ts.last_synced_clock(self.cfg)
        head = ("Synced as of " + clock) if clock else ("Synced " + ts.age_str(ls))
        return head + ("" if au.get("last_ok") else " · last attempt failed")

    def _sync_now_poll(self, status, btn, last=None):
        """UI-thread poll (QTimer, survives the Settings dialog closing): wait for the
        worker's capture, apply it on THIS thread via the SAME write path as the manual
        Sync, re-render, report Synced ✓ / couldn't-read on the dialog's status line, and
        refresh the 'last synced' line — guarded against a deleted dialog (RuntimeError)."""
        with self._auto_lock:
            cap = self._sync_now_result
        if cap is None:
            QTimer.singleShot(400, lambda: self._sync_now_poll(status, btn, last))
            return
        with self._auto_lock:
            self._sync_now_result = None
        res = ts.inject_real_usage(self.cfg, capture=cap)
        if res.get("ok") and res.get("changed"):
            self._reset_anchor = None      # let the new reset re-anchor the countdown
            try:
                self._drift = ts.drift_summary(ts.load_corrections())
            except Exception:
                self._drift = None
            self.refresh()
        try:
            if res.get("ok"):
                sp = res.get("session_pct")
                status.setStyleSheet(f"color:{ts.GREEN};")
                status.setText("Synced ✓" +
                               (" — session %.0f%%" % sp if sp is not None else ""))
            else:
                status.setStyleSheet(f"color:{ts.AMBER};")
                status.setText("Couldn't read /usage — try again")
            btn.setEnabled(True)
            if last is not None:
                last.setText(self._lsync_text())
        except RuntimeError:
            pass

    def _diag_worker(self):
        """OFF the UI thread: run the read-only diagnostic capture (the SAME /usage read
        the sync uses) and park it for the poll. Never raises."""
        try:
            cap = ts.capture_real_usage()
        except Exception as e:
            cap = {"ok": False, "error": "worker: %s" % e}
        with self._auto_lock:
            self._diag_result = cap
        self._auto.mark_done()

    def _diag_poll(self, dlg):
        """UI-thread poll: show the capture's parsed numbers + raw panel text. READ-ONLY —
        it never writes cfg (this view is for verifying, not syncing). Guarded against the
        window being closed mid-capture."""
        with self._auto_lock:
            cap = self._diag_result
        if cap is None:
            QTimer.singleShot(400, lambda: self._diag_poll(dlg))
            return
        with self._auto_lock:
            self._diag_result = None
        try:
            ok = bool(cap.get("ok"))

            def put(w, v, suffix=""):
                w.setText("—" if v in (None, "") else (str(v) + suffix))
            put(dlg._v_sp, cap.get("session_pct"), "%")
            put(dlg._v_sr, cap.get("session_reset"))
            put(dlg._v_wa, cap.get("weekall_pct"), "%")
            put(dlg._v_war, cap.get("weekall_reset"))
            put(dlg._v_so, cap.get("sonnet_pct"), "%")
            raw = cap.get("raw")
            if raw:
                # the console buffer leaves control chars (e.g. STX) around the bar glyphs
                # — blank them so the panel reads cleanly. Keep newlines + real text.
                clean = "".join(c if (c == "\n" or c >= " ") else " " for c in raw)
                dlg._raw.setPlainText(clean)
            elif not ok:
                dlg._raw.setPlainText("(capture failed: %s)" % cap.get("error", "unknown"))
            if ok:
                dlg._status.setStyleSheet(f"color:{ts.GREEN};")
                dlg._status.setText("Captured at "
                                    + datetime.now().strftime("%I:%M:%S %p").lstrip("0"))
            else:
                dlg._status.setStyleSheet(f"color:{ts.AMBER};")
                dlg._status.setText("Couldn't read /usage — try again")
            dlg._btn.setEnabled(True)
        except RuntimeError:
            pass

    def show_usage_diagnostics(self):
        """A small read-only window that runs the SAME /usage capture and shows EXACTLY
        what came back — the parsed numbers Pitwall would use plus the raw panel text — so the
        capture is verifiable in-app, not behind the scenes. Never changes Pitwall's numbers."""
        if self._diag is not None:
            try:
                self._diag.raise_()
                self._diag.activateWindow()
                return
            except RuntimeError:
                self._diag = None
        dlg = QDialog()
        self._diag = dlg
        dlg.setWindowTitle("Usage capture — troubleshooting")
        dlg.setStyleSheet(f"background:{ts.BG};")
        s = self._scale()
        dlg.resize(int(560 * s), int(560 * s))
        dlg.setMinimumSize(380, 360)

        def lab(text, base, color, bold=False, semibold=False, wrap_=False, mono=False):
            l = QLabel(text)
            l.setTextFormat(Qt.PlainText)
            l._ts_fontspec = (base, bold, semibold, mono)
            l.setFont(self._font(base, bold, semibold, mono))
            l.setStyleSheet(f"color:{color};")
            if wrap_:
                l.setWordWrap(True)
            return l

        root = QVBoxLayout(dlg)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(0)
        root.addWidget(lab("Usage capture — troubleshooting", 14, ts.INK, bold=True))
        root.addSpacing(4)
        root.addWidget(lab("Reads Claude's /usage panel directly as text (no OCR). Use it "
                           "to see exactly what Pitwall pulls in. This view only checks — it "
                           "does not change Pitwall's numbers.", 9, ts.MUT, wrap_=True))
        root.addSpacing(12)

        crow = QHBoxLayout()
        crow.setContentsMargins(0, 0, 0, 0)
        crow.setSpacing(8)
        btn = QPushButton("Capture now")
        btn.setCursor(QCursor(Qt.PointingHandCursor))
        btn._ts_fontspec = (9, False, True, False)
        btn.setFont(self._font(9, semibold=True))
        btn.setStyleSheet(f"QPushButton{{{_raised_qss(radius=4)}padding:5px 14px;}}"
                          f"QPushButton:hover{{background:{P.PANEL_HI}; color:{ts.INK};"
                          f" border:1px solid {P.CTL_STROKE};}}"
                          f"QPushButton:disabled{{color:{ts.FAINT};}}")
        status = lab("", 9, ts.MUT, wrap_=True)
        crow.addWidget(btn)
        crow.addWidget(status, 1)
        root.addLayout(crow)
        root.addSpacing(12)

        root.addWidget(lab("What Pitwall read", 7, ts.FAINT, semibold=True))
        root.addSpacing(5)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(3)

        # Sarah S11.4: bare aligned figures go through mono (Consolas); the two word-bearing
        # reset values stay proportional. Left-aligned (only 3 of 5 rows are figures).
        def vrow(r, name, mono=False):
            grid.addWidget(lab(name, 9, ts.MUT), r, 0)
            v = lab("—", 9, ts.INK, semibold=True, mono=mono)
            grid.addWidget(v, r, 1)
            return v
        dlg._v_sp = vrow(0, "Current session", mono=True)
        dlg._v_sr = vrow(1, "Session resets")
        dlg._v_wa = vrow(2, "Weekly (all models)", mono=True)
        dlg._v_war = vrow(3, "Weekly resets")
        dlg._v_so = vrow(4, "Weekly (Sonnet)", mono=True)
        grid.setColumnStretch(1, 1)
        root.addLayout(grid)
        root.addSpacing(12)

        root.addWidget(lab("Raw panel text", 7, ts.FAINT, semibold=True))
        root.addSpacing(5)
        raw = QPlainTextEdit()
        raw.setReadOnly(True)
        raw.setLineWrapMode(QPlainTextEdit.NoWrap)
        raw.setPlaceholderText("Press “Capture now” to read Claude's /usage panel.")
        raw.setFont(self._font(8, mono=True))
        raw.setStyleSheet(f"QPlainTextEdit{{background:#000000;color:{ts.MUT};"
                          f"border:1px solid {ts.EDGE};border-radius:4px;padding:6px;}}")
        root.addWidget(raw, 1)
        dlg._raw = raw
        dlg._status = status
        dlg._btn = btn

        def capture():
            if self._auto.in_flight:
                status.setStyleSheet(f"color:{ts.AMBER};")
                status.setText("A sync is already running…")
                return
            self._auto.mark_started()
            btn.setEnabled(False)
            status.setStyleSheet(f"color:{ts.MUT};")
            status.setText("Capturing…")
            threading.Thread(target=self._diag_worker, daemon=True).start()
            QTimer.singleShot(500, lambda: self._diag_poll(dlg))
        btn.clicked.connect(lambda _=False: capture())

        def _closed(e):
            self._diag = None
            QDialog.closeEvent(dlg, e)
        dlg.closeEvent = _closed

        try:
            dlg.move(self.x() + 36, self.y() + 36)
        except Exception:
            pass
        dlg.show()
        capture()       # kick off a first read so the window opens with live data

    def _set_tip(self, text, copy=None):
        """The ONE door to the tip label: sets the text, arms/disarms the
        click-to-copy payload, and flips the cursor so a copyable tip looks
        clickable. Every site that writes the tip goes through here, so a stale
        payload can never ride along under an unrelated message."""
        self._tip_copy = copy
        self.tip.setCursor(Qt.PointingHandCursor if copy else Qt.ArrowCursor)
        self.tip.setText(text)

    def _tip_clicked(self, _e):
        if not self._tip_copy:
            return
        QApplication.clipboard().setText(self._tip_copy)
        self._set_tip("✓ Copied — paste it where you need it.")

    def rotate_tip(self):
        # when tips are silenced, blank the box and return — FLASH_MISS still
        # shows via its own direct call, which bypasses this method
        if self.cfg.get("tips_off"):
            self._set_tip("")
            return
        # surface the highest-impact nudge when burning hot, else the next tip
        snap = self._snap
        hot = False
        if snap:
            cfg, d, ceiling, _ = snap
            if d.get("active"):
                color, _ = ts.pace_state(cfg, d, d.get("pace", 0.0), ceiling)
                hot = (color == ts.RED)
        if hot:
            self._set_tip(ts.hot_nudge_text(self._snap[1]["pace"]),
                          copy=ts.HANDOFF_SUMMARY_ASK)
        else:
            t = ts.TIPS[self._tip_i % len(ts.TIPS)]
            self._set_tip("\U0001f4a1  " + t, copy=ts.TIP_COPY.get(t))
            self._tip_i += 1

    def _all_tip_texts(self):
        """Every string the tip line can show — the rotating ts.TIPS plus the dynamic
        'burning $/hr' nudge (with a worst-case wide number). Used to size the tip box
        to its tallest member so a new tip never changes the layout."""
        return ([ts.hot_nudge_text(88888), FLASH_MISS_TIP, BG_FLASH_TIP,
                 ts.TERMINAL_FAIL_TIP] + ["\U0001f4a1  " + t for t in ts.TIPS])

    def _lock_tip_height(self, win_w):
        """Pin the tip label's height to the TALLEST tip at this width+font. Tips wrap
        to different line counts, so without this a new tip resizes the whole window —
        and that resize repaint is what flashed white. Locking the box kills both. Must
        be re-run whenever the width or font changes (it is, from _relayout)."""
        avail = max(40, int(win_w) - 2 * HALO_PX - 36)   # halo 10+10, card h-margins 18+18
        fm = QFontMetrics(self.tip.font())
        h = 0
        for t in self._all_tip_texts():
            r = fm.boundingRect(0, 0, avail, 100000, Qt.TextWordWrap, t)
            h = max(h, r.height())
        self.tip.setFixedHeight(h)

    def _apply_tip_visibility(self):
        """Show/hide the tip row and its preceding gap. With tips_off the row collapses
        to zero height — UNLESS a FLASH_MISS notice is currently showing, in which case
        the strip stays visible until rotate_tip() blanks it on the next timer fire."""
        visible = not self.cfg.get("tips_off") or bool(self.tip.text())
        self.tip.setVisible(visible)
        h = 11 if visible else 0
        self._tip_gap.changeSize(0, h, QSizePolicy.Minimum, QSizePolicy.Fixed)
        self._full_lay.invalidate()

    # === the pulse: ring hairline + halo breathe (§2.4) ===
    # Ring and halo are the ONLY animated pixels now — pill and conv_dot are static.
    # _pulse updates _ring_col and calls self.update() only while amp > 0 or color changed.
    def _pulse(self):
        f = self.heat_frac
        base = self.heat
        freq, amp = pulse_params(f)
        old_col = self._ring_col
        if amp <= 0.0:
            level = 0.0
        else:
            self.phase += 2 * math.pi * freq * 0.033
            if self.phase > 1e7:
                self.phase = math.fmod(self.phase, 2 * math.pi)
            level = amp * math.sin(self.phase)
        col = pulse_color(base, level, f)
        self._ring_col = col
        # repaint only while the pulse is active or the color just changed (fresh chat stays
        # perfectly still AND repaint-free when f < 0.08, per the spec §2.4)
        if amp > 0 or col != old_col:
            self.update()

    # === Token Details window ==============================================
    def show_token_details(self):
        if self._details is not None:
            try:
                self._details.raise_()
                self._details.activateWindow()
                return
            except RuntimeError:
                self._details = None
        snap = self._snap
        if not snap:
            return
        cfg, d, ceiling, noun = snap
        wh = cfg.get("window_hours", 5)
        dlg = QDialog()                       # native titlebar - a window you read/resize
        dlg._ts_level = self.size_level       # #7: this window's OWN font level (diverges)
        self._details = dlg
        dlg.setWindowTitle("Token Details — Pitwall")
        dlg.setStyleSheet(f"background:{ts.BG};")
        scr = self._screen_geo()
        # #2: provisional size only — the real size is computed by _fit_details() once the
        # content exists (measure-and-fit HEIGHT; WIDTH driven by the tables, which don't
        # word-wrap). The scroll area is the clipping FLOOR for pathological screen-bound
        # sizes, not the everyday layout mechanism (Sarah S1).
        dlg.resize(min(int(600 * self._scale()), scr.width() - 24),
                   min(int(640 * self._scale()), scr.height() - 24))
        dlg.setMinimumSize(360, 260)
        tables = []        # _detail_table widgets — drive the fitted width (no word-wrap)

        wrap = QVBoxLayout(dlg)
        wrap.setContentsMargins(0, 0, 0, 0)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.NoFrame)
        area.setStyleSheet(f"background:{ts.BG};")
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)   # dormant; the floor
        area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        wrap.addWidget(area)
        body_w = QWidget()
        body_w.setStyleSheet(f"background:{ts.BG};")
        body = QVBoxLayout(body_w)
        body.setContentsMargins(16, 14, 16, 14)
        body.setSpacing(0)
        area.setWidget(body_w)

        def lab(text, base, color, bold=False, semibold=False, wrap_=False, mono=False):
            l = QLabel(text)
            l.setTextFormat(Qt.PlainText)  # never auto-promote HTML-looking text (M1)
            l._ts_fontspec = (base, bold, semibold, mono)   # rescale in place on A−/A+
            l.setFont(self._font(base, bold, semibold, mono))
            l.setStyleSheet(f"color:{color};")
            if wrap_:
                l.setWordWrap(True)
            return l

        # S5 density: section top 16->13, caption gap 5->4 (fonts unchanged: 15/9/8).
        # §3.4: captions get caps letter-spacing + a seam underline 8px below the text
        # (same treatment as the Settings caption helper). The 5-element _ts_fontspec
        # carries the caps flag through _rescale_dialog.
        def section(text, top=13):
            body.addSpacing(top)
            cap = lab(text, 8, ts.FAINT, semibold=True)
            cap._ts_fontspec = (8, False, True, False, False)
            cap.setFont(self._font(8, semibold=True))
            body.addWidget(cap)
            body.addSpacing(8)
            body.addWidget(self._rule())
            body.addSpacing(4)

        def para(text, color=ts.MUT):
            body.addWidget(lab(text, 9, color, wrap_=True))
            body.addSpacing(2)

        # S3: the teaching prose is collapsible (the ? toggle). Each block lives in its
        # own container so hiding it removes ALL of its vertical space (no orphan gaps).
        # Tables, captions and the §7 summary are NOT collapsible.
        explain = []

        def explain_box():
            c = QWidget()
            c.setStyleSheet(f"background:{ts.BG};")
            cv = QVBoxLayout(c)
            cv.setContentsMargins(0, 0, 0, 0)
            cv.setSpacing(0)
            explain.append(c)
            body.addWidget(c)
            return cv

        # --- pinned header: title + ? + A−/A+ live OUTSIDE the scroll area so they can
        #     never scroll off (Sarah — same principle as the Settings pinned footer).
        header = QWidget()
        header.setStyleSheet(f"background:{ts.BG};")
        hwrap = QVBoxLayout(header)
        hwrap.setContentsMargins(16, 14, 16, 8)
        hwrap.setSpacing(0)
        trow = QHBoxLayout()
        trow.setContentsMargins(0, 0, 0, 0)
        trow.setSpacing(0)
        trow.addWidget(lab("Token Details", 15, ts.INK, bold=True))
        trow.addStretch(1)
        qtoggle = lab("?", 11, ts.MUT, bold=True)
        qtoggle.setCursor(QCursor(Qt.PointingHandCursor))
        qtoggle.setToolTip(_tip("Show / hide the explanations"))
        trow.addWidget(qtoggle)
        trow.addSpacing(12)
        trow.addLayout(self._dialog_font_buttons(dlg))
        hwrap.addLayout(trow)
        wrap.insertWidget(0, header)

        def _fit_details():
            # S1: WIDTH from the widest table (tables don't word-wrap, so their sizeHint
            # width is honest); HEIGHT measured from the body at that width. Clamp to the
            # screen — only then does the scroll floor engage. Re-run on build, ? toggle
            # and A−/A+ rescale, so big fonts re-measure instead of clipping.
            g = self._screen_geo(dlg)
            s = self._scale_at(dlg._ts_level)
            base_w = max([t.sizeHint().width() for t in tables], default=int(560 * s))
            W = max(int(420 * s), min(base_w + 32 + 20, g.width() - 24, int(720 * s)))
            dlg.resize(W, dlg.height())
            lay = dlg.layout()
            if lay is not None:
                lay.activate()
            inner = max(120, body_w.width() or (W - 32))
            blay = body_w.layout()
            if blay is not None and blay.hasHeightForWidth():
                bh = blay.heightForWidth(inner)
            else:
                bh = body_w.sizeHint().height()
            H = min(header.sizeHint().height() + bh + 8, g.height() - 24)
            dlg.resize(W, H)
        dlg._ts_resize = _fit_details

        def _set_explain(on):
            dlg._explain_on = on
            for c in explain:
                c.setVisible(on)
            qtoggle.setStyleSheet(f"color:{ts.INK if on else ts.MUT};")
            _fit_details()

        def _qtoggle_press(e):
            if e.button() == Qt.LeftButton:
                _set_explain(not getattr(dlg, "_explain_on", False))
                e.accept()
        qtoggle.mousePressEvent = _qtoggle_press

        if not d.get("active"):
            body.addSpacing(4)
            para("No active window right now — nothing has been used since the last "
                 "reset, so there are no tokens to break down yet.")
            body.addStretch(1)
            qtoggle.hide()                    # nothing to explain on an empty window
            _fit_details()
            self._finish_details(dlg)
            return
        body.addSpacing(1)
        body.addWidget(lab(f"this {wh}-hour window · ${d['usd']:.2f} est · "
                           f"{ts.fmt_tokens(d['tok'])} tokens total", 9, ts.MUT))

        # ===== What's a token? (Don's approved primer, 2026-06-08) ==============
        # An always-visible 3-sentence answer to the question a reader staring at a big
        # token number actually has — "what IS a token, is it eating my disk?" — with the
        # longer "About tokens" read behind its OWN [About tokens ▾] expander (default
        # collapsed; independent of the ? teaching toggle; no tooltip — owner's call).
        section("What's a token?")
        para("A token is a small chunk of text — very roughly three-quarters of a "
             "word — and it's the unit Claude reads and writes in. They take up almost "
             "no space (a million of them is only about 4 MB, a tiny file); what makes "
             "them costly is that every token in the conversation has to be re-read and "
             "held in mind each time Claude replies. So the real cost is the length of "
             "the chat, not the storage — which is exactly why starting fresh after a "
             "long session saves you so much.")
        about_more = QWidget()
        about_more.setStyleSheet(f"background:{ts.BG};")
        aml = QVBoxLayout(about_more)
        aml.setContentsMargins(0, 0, 0, 0)
        aml.setSpacing(0)
        for _p in (
            "A token is a small piece of text — on average about three-quarters of a "
            "word, or roughly four letters. It's simply the unit Claude uses to read "
            "what you write and to write back to you.",
            "Tokens are surprisingly small to store. A million of them is only around "
            "750,000 words, which saves to disk as about 4 MB — smaller than a single "
            "phone photo. So when you see a big token number, it isn't filling up your "
            "drive.",
            "What tokens actually cost is attention. Every token in the conversation so "
            "far has to be re-read and kept in mind each time Claude answers, and that "
            "takes processing time and working memory. The longer a chat runs, the more "
            "there is to re-read every single turn — so the cost grows with the length "
            "of the conversation, not with any file on your computer.",
            "Small on disk, heavy to think about. That's the whole idea behind this "
            "widget: it watches how heavy your current chat has gotten, so you know "
            "when it's worth saving your progress and starting a fresh one.",
        ):
            aml.addWidget(lab(_p, 9, ts.FAINT, wrap_=True))
            aml.addSpacing(6)
        about_more.setVisible(False)
        # Quiet teaching text, not a loud accent (owner, 2026-06-08): the ▾ caret + the
        # pointing-hand cursor carry the "expandable" affordance, so this stays muted gray.
        about_link = lab("About tokens  ▾", 9, ts.MUT)
        about_link.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)  # text-only click zone
        about_link.setCursor(QCursor(Qt.PointingHandCursor))

        def _about_toggle(e, _w=about_more, _lnk=about_link):
            if e.button() == Qt.LeftButton:
                show = not _w.isVisible()
                _w.setVisible(show)
                _lnk.setText("About tokens  ▴" if show else "About tokens  ▾")
                dlg._ts_resize()                 # re-fit the panel to the new height
                e.accept()
        about_link.mousePressEvent = _about_toggle
        body.addSpacing(2)
        body.addWidget(about_link)
        body.addWidget(about_more)

        # ===== HERO: where the words go (Sarah §9, accurate 5-way cut) =========
        # Every word the model processed this window, split into its REAL sources and
        # colour-ramped cool→warm (you / files / Claude = small & real; saved + re-read
        # = the machine overhead that dwarfs them). The stacked bar keeps TRUE
        # proportions (no fake minimums — the asymmetry is the lesson); the swatched
        # legend carries the colour identity + exact numbers. Computed here because it
        # leads the page; the precise per-kind token table below reuses `comp`.
        start = d["reset"] - timedelta(hours=wh) if d.get("reset") else None
        comp = ts.collect_components(wh, start)
        # collective plan-allowance % used this window — the SAME number the front card
        # leads with (corrected_used + the same drift correction), surfaced here so the
        # words view also answers "how much of my 5-hour limit is that?" (Task 2).
        frac5, pct5, _adj5 = ts.corrected_used(cfg, d.get("usd", 0.0), ceiling, self._drift)
        ctot = comp["in"] + comp["out"] + comp["cw"] + comp["cr"]
        usd_tot = comp["in_usd"] + comp["out_usd"] + comp["cw_usd"] + comp["cr_usd"]
        typed = ts.collect_typed_words(wh, start)
        typed_w = typed["words"]                     # EXACT — your keystrokes
        in_w = ts.words_of(comp["in"])
        files_w = max(0, in_w - typed_w)             # the new input that ISN'T your typing
        cats = [
            ("You typed",                typed_w,                  ts.W_YOU),
            ("Files & output read in",   files_w,                  ts.W_FILES),
            ("Claude wrote",             ts.words_of(comp["out"]), ts.W_CLAUDE),
            ("Saved to memory (cached)", ts.words_of(comp["cw"]),  ts.W_SAVED),
            ("The chat, re-read",        ts.words_of(comp["cr"]),  ts.W_REREAD),
        ]
        tot_w = sum(w for _l, w, _c in cats) or 1
        s = self._scale()

        def _pct(p):
            return f"{p:.2f}%" if 0 < p < 1 else (f"{p:.1f}%" if p < 10 else f"{p:.0f}%")

        body.addSpacing(13)
        body.addWidget(lab("Where the words go  ·  this 5-hour window", 8, ts.FAINT,
                           semibold=True))
        body.addSpacing(7)
        stack = QFrame()                             # the true-proportion stacked bar
        # Sarah 2026-06-07: a slim ~6px capsule rule (was a bulky 11px block), on a
        # faint EDGE track so the thin bar reads as a deliberate gauge. No inner gaps
        # and square inner edges so the tiny green/teal/blue/amber slivers keep their
        # full proportional width; only the two outer ends are rounded (pill caps).
        stack.setFixedHeight(max(4, int(6 * s)))
        stack.setStyleSheet(f"background:{ts.EDGE}; border-radius:3px;")
        sbl = QHBoxLayout(stack)
        sbl.setContentsMargins(0, 0, 0, 0)
        sbl.setSpacing(0)
        n = len(cats)
        for i, (_l, w, col) in enumerate(cats):
            seg = QFrame()
            if i == 0:
                rad = "border-top-left-radius:3px; border-bottom-left-radius:3px;"
            elif i == n - 1:
                rad = "border-top-right-radius:3px; border-bottom-right-radius:3px;"
            else:
                rad = ""
            seg.setStyleSheet(f"background:{col}; {rad}")
            sbl.addWidget(seg)
            sbl.setStretch(sbl.count() - 1, max(1, int(round(w / tot_w * 4000))))
        body.addWidget(stack)
        body.addSpacing(10)

        wnum = max(70, int(96 * s))                  # right-aligned mono number columns
        wpct = max(42, int(58 * s))
        for _l, w, col in cats:
            pct = w / tot_w * 100
            r = QHBoxLayout()
            r.setContentsMargins(0, 0, 0, 0)
            r.setSpacing(0)
            sw = QFrame()
            sw.setFixedSize(max(8, int(9 * s)), max(8, int(9 * s)))
            sw.setStyleSheet(f"background:{col}; border-radius:2px;")
            r.addWidget(sw, 0, Qt.AlignVCenter)
            r.addSpacing(9)
            r.addWidget(lab(_l, 9, col))
            r.addStretch(1)
            wl = lab(f"{w:,}", 9, ts.MUT, mono=True)
            wl.setFixedWidth(wnum)
            wl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            r.addWidget(wl)
            r.addSpacing(12)
            pl = lab(_pct(pct), 9, col, semibold=True, mono=True)
            pl.setFixedWidth(wpct)
            pl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            r.addWidget(pl)
            body.addLayout(r)
            body.addSpacing(5)

        # Collective plan-allowance figure for the whole window (all sessions together),
        # echoing the front card's spend heat colour so it reads as the same number.
        if ceiling and ceiling > 0:
            body.addSpacing(9)
            cr = QHBoxLayout()
            cr.setContentsMargins(0, 0, 0, 0)
            cr.setSpacing(0)
            cr.addWidget(lab("Across every session this window", 9, ts.MUT))
            cr.addStretch(1)
            cr.addWidget(lab(f"{pct5}%", 9, ts.lerp_color(frac5), bold=True, mono=True))
            cr.addSpacing(7)
            cr.addWidget(lab("of your 5-hour limit", 9, ts.MUT))
            body.addLayout(cr)

        body.addSpacing(4)
        body.addWidget(lab(f"You typed about {typed_w:,} of the {tot_w:,} words the "
                           f"model processed this window — roughly {_pct(typed_w / tot_w * 100)}. "
                           f"Almost everything else is Claude's own output and the same "
                           f"chat re-read every turn to keep its memory — which is why a "
                           f"long chat costs more than a short one.", 9, ts.MUT,
                           wrap_=True))
        body.addSpacing(3)
        body.addWidget(lab("What you typed is an exact count; the rest is a word-estimate "
                           "(about ¾ of a word per token).", 8, ts.FAINT))

        # S3 collapsible §3 + S2 tightened copy.
        c3 = explain_box()
        c3.addSpacing(13)
        c3.addWidget(lab("What the  ↑ / ↓  in your terminal mean", 8, ts.FAINT, semibold=True))
        c3.addSpacing(4)
        c3.addWidget(lab("↑  is how BIG your conversation is right now — the whole chat "
                         "re-read for this turn, plus what you just typed. It grows as "
                         "the chat gets longer and drops near zero when you /clear or "
                         "start fresh. It's NOT money or your plan limit — just \"how "
                         "full is this chat.\"", 9, ts.MUT, wrap_=True))
        c3.addSpacing(2)
        c3.addWidget(lab("↓  is just the size of Claude's latest reply. Small, less "
                         "interesting.", 9, ts.MUT, wrap_=True))
        c3.addSpacing(2)
        c3.addWidget(lab("The bigger ↑ gets, the more every new turn costs — the whole "
                         "chat is re-read each time. That's the one number worth "
                         "watching.", 9, ts.FAINT, wrap_=True))

        section(f"Where this window's tokens went  ·  {comp['turns']} turns")
        comp_rows = [
            ("New input you typed", comp["in"], comp["in_usd"]),
            ("Claude's replies  (↓)", comp["out"], comp["out_usd"]),
            ("Cache writes  (saving the chat)", comp["cw"], comp["cw_usd"]),
            ("Cache re-reads  (↑ the chat, re-sent)", comp["cr"], comp["cr_usd"]),
        ]
        rows = [[label, ts.fmt_tokens(tok),
                 f"{(tok / ctot * 100 if ctot else 0):.1f}%", f"${usd:.2f}"]
                for label, tok, usd in comp_rows]
        rows.append(["Total", ts.fmt_tokens(ctot), "100%", f"${usd_tot:.2f}"])
        t_where = self._detail_table(
            ["", "Tokens", "Share", "$ est"], rows,
            colcfg=[("w", ts.INK), ("e", ts.MUT), ("e", ts.MUT), ("e", ts.MUT)],
            last_bold=True,
            # tint each row label to match the "Where the words go" legend above
            # (input→green, replies→blue, cache-write→amber, re-read→orange);
            # TOTAL row has no entry → stays INK bold. — Sarah 2026-06-07
            label_colors=[ts.W_YOU, ts.W_CLAUDE, ts.W_SAVED, ts.W_REREAD])
        tables.append(t_where)
        body.addWidget(t_where)
        # S3 collapsible §4 + S2 tightened tail (anti-"blown quota" sentence kept).
        c4 = explain_box()
        c4.addSpacing(2)
        c4.addWidget(lab("Cache re-reads dominate — the same chat re-sent every turn "
                         "— which is why this total dwarfs the real work above.",
                         9, ts.FAINT, wrap_=True))

        split = sorted(((f, v) for f, v in (d.get("by_model") or {}).items()
                        if v.get("usd", 0) > 0), key=lambda kv: -kv[1]["usd"])
        if split:
            section("By model")
            t_model = self._detail_table(
                ["Model", "Tokens", "$ est"],
                [[ts.MODEL_LABEL.get(f, f), ts.fmt_tokens(v["tok"]), f"${v['usd']:.2f}"]
                 for f, v in split],
                colcfg=[("w", ts.INK), ("e", ts.MUT), ("e", ts.MUT)])
            tables.append(t_model)
            body.addWidget(t_model)

        all_sess = self._all_sessions()
        n_open = sum(1 for s in all_sess if s.get("open"))
        section(f"Each CLI session  ·  {n_open} open")
        now = datetime.now(timezone.utc)
        # S4: open sessions first, then biggest by tokens; cap the visible table at 8 rows
        # and note the remainder (the header's "· N open" count stays honest).
        ordered = sorted(all_sess,
                         key=lambda s: (0 if s.get("open") else 1, -s.get("tok", 0)))
        SESS_CAP = 8
        shown = ordered[:SESS_CAP]
        hidden = len(ordered) - len(shown)
        srows = []
        for s in shown:
            frac = min(s["ctx"] / max(cfg["ctx_red"], 1), 1.0)
            # this session's OWN re-read share (Task 1) — the orange of the hero bar:
            # how much of THIS session is the chat re-sent every turn (the rest is new
            # work). High = a long chat worth handing off. "—" until it has tokens.
            _new_pct, reread_pct = ts.session_split(s)
            has_split = (_new_pct + reread_pct) > 0
            srows.append([
                self._trunc(ts.session_name(s), 26),
                ts.MODEL_LABEL.get(s.get("model"), "—"),
                ts.fmt_tokens(s["ctx"]),
                ts.fullness_word(frac) if s.get("open") else "—",
                f"${s['usd']:.2f}",
                ts.fmt_tokens(s["tok"]),
                f"{reread_pct:.0f}%" if has_split else "—",
                ts.session_state_word(s, now),
            ])
        if srows:
            t_sess = self._detail_table(
                ["Session", "Model", "Context ↑", "Fullness", "Spent", "Tokens",
                 "Re-read", "State"], srows,
                colcfg=[("w", ts.INK), ("w", ts.MUT), ("e", ts.MUT), ("w", ts.MUT),
                        ("e", ts.MUT), ("e", ts.MUT), ("e", ts.W_REREAD),
                        ("e", ts.MUT)])
            tables.append(t_sess)
            body.addWidget(t_sess)
            if hidden > 0:
                body.addSpacing(3)
                body.addWidget(lab(f"+{hidden} more session"
                                   f"{'s' if hidden != 1 else ''} (not shown)",
                                   8, ts.FAINT))
        else:
            para("No Claude sessions active in this window.")
        # S3 collapsible §6 session explainer.
        c6 = explain_box()
        c6.addWidget(lab("\"Context ↑\" is that session's current chat size — the number "
                         "its own terminal shows as ↑. \"Tokens\" is everything it has "
                         "run this window (mostly cache re-reads). \"Re-read\" is how much "
                         "of that — the orange in the bar up top — is the same chat re-sent "
                         "every turn; the rest is new work (your typing, files, Claude's "
                         "replies). A high Re-read % on an OPEN session means a long chat "
                         "worth handing off. "
                         "Fullness: Light → Filling → Heavy → Hand off (start a fresh "
                         "session).", 9, ts.FAINT, wrap_=True))

        body.addSpacing(12)                   # S5: pre-rule 16 -> 12
        body.addWidget(self._rule())
        body.addSpacing(8)                    # S5: post-rule 10 -> 8
        para("In one line:  the terminal's ↑ is THIS one turn's size · the "
             "totals above are the SUM of every turn this window. Neither is wrong "
             "— they measure different things. Your true \"how much plan is left\" "
             "lives only on Claude's Settings → Usage screen.", ts.FAINT)
        body.addStretch(1)

        # Teaching prose starts COLLAPSED — except the very first open ever, so a new
        # user sees it once. Only the "seen" flag persists; the ? toggle flips it live.
        first = not self.cfg.get("details_explain_seen")
        if first:
            self.cfg["details_explain_seen"] = True
            ts.save_config(self.cfg)
        _set_explain(first)
        self._finish_details(dlg)

    def _all_sessions(self):
        try:
            return ts.collect_sessions(self.cfg.get("window_hours", 5))
        except Exception:
            return []

    def _beside_xy(self, w, h):
        """(x, y) to open a w×h window beside the widget on the roomier side,
        clamped fully onto the widget's OWN screen (never straddle monitors)."""
        g = self.frameGeometry()
        scr = self._screen_geo()
        x = g.right() + 10
        if x + w > scr.right():
            x = max(scr.left(), g.left() - 10 - w)
        # final clamp so the panel is always fully on this screen
        x = max(scr.left(), min(x, scr.right() - w))
        y = max(scr.top(), min(g.top(), scr.bottom() - h))
        return x, y

    def _place_beside(self, dlg):
        """Open a dialog beside the widget on the roomier side, clamped on-screen.
        Shared by Token Details and Settings (DRY, Sarah's spec §8.3)."""
        dlg.move(*self._beside_xy(dlg.width(), dlg.height()))
        dlg.show()

    def _finish_details(self, dlg):
        def closed(e):
            self._details = None
            QDialog.closeEvent(dlg, e)
        dlg.closeEvent = closed
        self._place_beside(dlg)

    def _trunc(self, text, n):
        return text if len(text) <= n else text[: n - 1] + "…"

    def _detail_table(self, headers, rows, colcfg=None, last_bold=False,
                      label_colors=None):
        # label_colors: optional list parallel to `rows` (one hex per row, or None)
        # to tint the col-0 LABEL a category colour (mirrors the words legend:
        # coloured label, calm numbers). The bold TOTAL row ignores it -> INK bold.
        colcfg = colcfg or [("w", ts.MUT)] * len(headers)
        tbl = QWidget()
        tbl.setStyleSheet(f"background:{ts.BG};")
        grid = QGridLayout(tbl)
        grid.setContentsMargins(0, 2, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(1)
        minw = int(58 * self._scale())
        for c, h in enumerate(headers):
            lbl = QLabel(h)
            lbl._ts_fontspec = (8, True, False, False)   # rescale in place on A−/A+
            lbl.setFont(self._font(8, bold=True))
            lbl.setStyleSheet(f"color:{ts.FAINT};")
            align = Qt.AlignLeft if colcfg[c][0] == "w" else Qt.AlignRight
            grid.addWidget(lbl, 0, c, align | Qt.AlignVCenter)
            if c == 0:
                grid.setColumnStretch(c, 1)
            else:
                grid.setColumnMinimumWidth(c, minw)
        for r, row in enumerate(rows, start=1):
            bold = last_bold and r == len(rows)
            for c, val in enumerate(row):
                anch, fg = colcfg[c]
                lbl = QLabel(str(val))
                lbl.setTextFormat(Qt.PlainText)  # never auto-promote HTML-looking text (M1)
                if bold or c == 0:
                    lbl._ts_fontspec = (9, bold, False, False)   # rescale in place
                    lbl.setFont(self._font(9, bold=bold))
                    lc = ts.INK
                    if c == 0 and not bold and label_colors and r - 1 < len(label_colors):
                        lc = label_colors[r - 1] or ts.INK
                    lbl.setStyleSheet(f"color:{lc};")
                else:
                    lbl._ts_fontspec = (9, False, False, True)   # mono; rescale in place
                    lbl.setFont(self._font(9, mono=True))
                    lbl.setStyleSheet(f"color:{fg};")
                align = Qt.AlignLeft if anch == "w" else Qt.AlignRight
                grid.addWidget(lbl, r, c, align | Qt.AlignVCenter)
        return tbl

    # === restart / hand-off =================================================
    def restart_session(self, s):
        # Stranger-test rewrite (owner, 2026-06-12, item 1 — his worked example):
        # plain words, says exactly what to do, and the text we ask the user to TYPE
        # comes with a real Copy button. A QMessageBox can't hold an inline copy
        # button, so this is a small custom dialog in the house frameless style.
        dlg = QDialog(self)
        dlg.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        dlg.setAttribute(Qt.WA_TranslucentBackground)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.setObjectName("hdlg")
        dlg.rejected.connect(dlg.close)
        # A QFrame child paints the FULL panel — background + border + rounded
        # corners INCLUDING the layout margins — exactly like the card and the
        # shoulder-tap. The old approach painted background only on the child
        # labels (*{background}), so the dialog's own margins stayed transparent;
        # invisible while it opened over the card, dark gaps once it opened beside
        # it on the desktop (owner, 2026-06-12 popups.mp4).
        shell = QVBoxLayout(dlg)
        shell.setContentsMargins(0, 0, 0, 0)
        panel = QFrame(dlg)
        panel.setObjectName("hpanel")
        panel.setStyleSheet(f"#hpanel{{background:{ts.BG}; border:1px solid {ts.EDGE};"
                            f" border-radius:{CARD_RADIUS}px;}}")
        shell.addWidget(panel)
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(16, 12, 16, 14)
        outer.setSpacing(6)

        title = self._mk("Start a fresh session", 10, ts.INK, semibold=True)
        title.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        outer.addWidget(title)
        # drag anywhere on the title to move the dialog (frameless)
        def _drag(e):
            if e.button() == Qt.LeftButton and dlg.windowHandle():
                dlg.windowHandle().startSystemMove()
        title.mousePressEvent = _drag

        # The session name comes from the transcript (aiTitle / cwd); these labels are
        # PlainText, so nothing in it can render as markup (Ivan M1 stays honored).
        # Body text reads in INK (the session-row text color) so it stays legible
        # on the panel in both themes — the heat ramp (ts.lerp_color) drew as a
        # low-contrast teal on the light panel (owner, 2026-06-12).
        head = self._mk(
            f"{ts.session_name(s)} — ${s.get('usd', 0.0):.2f} so far. This chat has "
            f"grown large, so every reply now costs more than it should.",
            9, ts.INK)
        head.setWordWrap(True)
        outer.addWidget(head)
        outer.addSpacing(2)

        intro = self._mk(
            "A fresh session is cheaper because it starts empty — but that also "
            "means it won't know anything about this conversation. To carry your "
            "work over:", 9, ts.MUT)
        intro.setWordWrap(True)
        outer.addWidget(intro)

        srow = QHBoxLayout()
        srow.setSpacing(8)
        step1 = self._mk(
            f"1.  In the old window, type:  “{ts.HANDOFF_SUMMARY_ASK}”", 9, ts.MUT)
        step1.setWordWrap(True)
        srow.addWidget(step1, 1)
        cp = self._mk("⧉ Copy", 9, ts.INK)
        cp.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        def _copy():
            QApplication.clipboard().setText(ts.HANDOFF_SUMMARY_ASK)  # copy ONLY
            cp.setText("copied ✓")
            cp.setStyleSheet(f"color:{ts.GREEN};")
            QTimer.singleShot(1500, lambda: (
                cp.setText("⧉ Copy"), cp.setStyleSheet(f"color:{ts.INK};")))
        self._ctl(cp, _copy)
        self._hover(cp, ts.GREEN, ts.INK)
        srow.addWidget(cp, 0, Qt.AlignTop)
        outer.addLayout(srow)

        steps23 = self._mk(
            "2.  Copy the summary it gives you.\n"
            "3.  Click the button below, then paste the summary into the new window.",
            9, ts.MUT)
        steps23.setWordWrap(True)
        outer.addWidget(steps23)
        outer.addSpacing(6)

        brow = QHBoxLayout()
        brow.setSpacing(8)
        go_b = QPushButton("Open a fresh session")
        go_b.setCursor(QCursor(Qt.PointingHandCursor))
        go_b._ts_fontspec = (9, False, True, False)
        go_b.setFont(self._font(9, semibold=True))
        go_b.setStyleSheet(
            f"QPushButton{{background:{P.ACCENT}; color:{P.KEY_TEXT}; border:none;"
            f" border-radius:4px; padding:6px 16px;}}")
        cancel_b = QPushButton("Cancel")
        cancel_b.setCursor(QCursor(Qt.PointingHandCursor))
        cancel_b._ts_fontspec = (9, False, False, False)
        cancel_b.setFont(self._font(9))
        cancel_b.setStyleSheet(
            f"QPushButton{{background:transparent; color:{ts.MUT}; border:none;"
            f" padding:6px 10px;}}QPushButton:hover{{color:{ts.INK};}}")
        brow.addWidget(go_b)
        brow.addWidget(cancel_b)
        brow.addStretch(1)
        outer.addLayout(brow)

        opened = []
        go_b.clicked.connect(lambda: (opened.append(True), dlg.close()))
        cancel_b.clicked.connect(dlg.close)
        # Open BESIDE the card, not on top of it (owner, 2026-06-12 popups.mp4:
        # the old self.x()+36 offset dropped the dialog over the main app).
        dlg.adjustSize()                       # final size before we place a frameless modal
        try:
            dlg.move(*self._beside_xy(dlg.width(), dlg.height()))
        except Exception:
            pass
        dlg.exec()
        if opened:
            if not self._open_new_cli(s.get("cwd")):
                self._set_tip(ts.TERMINAL_FAIL_TIP, copy="claude")
                self._apply_tip_visibility()
                self._relayout()

    def _open_new_cli(self, cwd):
        import shutil
        import subprocess
        cwd = cwd if (cwd and os.path.isdir(cwd)) else os.path.expanduser("~")
        has_claude = shutil.which("claude") is not None
        # Name the tab after the project folder (the "app") so you can tell which
        # terminal is which at a glance — Claude Code otherwise titles the tab with
        # the session summary, which doesn't say which app it belongs to. Claude
        # sets that title via escape sequences, so on Windows Terminal we must pass
        # --suppressApplicationTitle to stop it overwriting ours (verified: WT docs).
        app = os.path.basename(os.path.normpath(cwd))
        title = f"{app} — Claude" if app else "Claude"
        try:
            if shutil.which("wt"):
                cmd = (["wt", "-d", cwd, "--title", title, "--suppressApplicationTitle",
                        "cmd", "/k"] + (["claude"] if has_claude else []))
                subprocess.Popen(cmd)
            else:
                # Legacy console (no Windows Terminal): best-effort title via cmd's
                # `title` builtin. There's no suppress outside WT, so Claude may
                # later override it — but at least it opens correctly labelled.
                inner = f"title {app}" + (" & claude" if has_claude else "")
                subprocess.Popen(["cmd", "/k", inner], cwd=cwd,
                                 creationflags=0x00000010)
            return True
        except Exception:
            return False

    # === settings ==========================================================
    # The reset/calibration helpers below (_override_dt / effective_reset /
    # log_correction) are pure config/transcript logic kept as private methods here.
    # FOLLOW-UP (tidy-up): they could be lifted into ts_core alongside the other
    # shared helpers (active_ceiling/corrected_used/pace_state) so the widget just
    # delegates — but there is only one face now, so it is not urgent.
    def _override_dt(self):
        """The pinned reset as a datetime if set and still in the future, else None."""
        iso = self.cfg.get("reset_override")
        if not iso:
            return None
        try:
            r = datetime.fromisoformat(iso)
            return r if r > datetime.now(timezone.utc) else None
        except Exception:
            return None

    def _effective_reset(self):
        """Manual override wins while future; else the transcript guess. Clears an
        expired/bad override (and persists) exactly like the Tkinter face."""
        iso = self.cfg.get("reset_override")
        if iso:
            try:
                r = datetime.fromisoformat(iso)
                if r > datetime.now(timezone.utc):
                    return r
            except Exception:
                pass
            self.cfg["reset_override"] = None
            ts.save_config(self.cfg)
        d = self._snap[1] if self._snap else {}
        return (d or {}).get("reset")

    def _log_correction(self, real_pct, real_reset, guess, est_before):
        """Record one correction (typed truth vs what the widget thought) so the drift
        report can separate a reset-clock error from an amount error. Mirrors the
        Tkinter log_correction; uses ts_core for the active ceiling + append."""
        ceil, _ = ts.active_ceiling(self.cfg, self._learned)
        est_usd = (est_before or {}).get("usd", 0.0)
        est_pct = (est_usd / ceil * 100.0) if ceil else None
        guess_reset = (guess or {}).get("reset")
        reset_gap_min = ((guess_reset - real_reset).total_seconds() / 60.0
                         if (guess_reset is not None and real_reset is not None) else None)
        ts.append_correction({
            "at": datetime.now(timezone.utc).isoformat(),
            "real_pct": real_pct,
            "est_pct": round(est_pct, 1) if est_pct is not None else None,
            "pct_gap": round(est_pct - real_pct, 1) if est_pct is not None else None,
            "guess_reset": guess_reset.isoformat() if guess_reset else None,
            "real_reset": real_reset.isoformat() if real_reset else None,
            "reset_gap_min": round(reset_gap_min, 1) if reset_gap_min is not None else None,
            "est_usd": round(est_usd, 2),
            "ceiling": round(ceil, 1) if ceil else None,
        })
        try:
            self._drift = ts.drift_summary(ts.load_corrections())
        except Exception:
            self._drift = None

    def _rates_checked_text(self):
        """The 'Last checked: <date>' line for the Rates section — local date, or
        'never' if the user has not checked/entered rates yet."""
        iso = self.cfg.get("rates_last_checked")
        if not iso:
            return "Last checked: never"
        try:
            dt = datetime.fromisoformat(iso).astimezone()
            return f"Last checked: {dt.strftime('%b')} {dt.day}, {dt.year}"
        except Exception:
            return "Last checked: never"

    def _refresh_rates_lc(self):
        """Update the Accuracy pane's 'Last checked' label in place (if Settings is still
        open) after the Rates dialog stamps a check/update."""
        lbl = getattr(self, "_rates_lc_lbl", None)
        if lbl is not None:
            try:
                lbl.setText(self._rates_checked_text())
            except RuntimeError:        # the label was destroyed with a closed dialog
                self._rates_lc_lbl = None

    def open_rates(self):
        """Settings → Accuracy → Rates. Shows the prices Pitwall turns tokens into dollars
        with (ts.DEFAULT_RATES plus any manual overrides), lets the user read Anthropic's
        current prices in the browser and type them back in, and stamps when they last
        checked. Pitwall makes NO network request itself — 'Check' just opens the pricing
        page (owner's explicit choice). Input/output per 1M tokens are editable; the cache
        prices derive from input. Overrides + the last-checked stamp persist to
        pitwall_config.json and fold into the active RATES via ts.apply_rate_overrides."""
        fams = ["fable", "opus", "sonnet", "haiku"]

        def _fmt_rate(per_tok):       # per-token (internal) -> trimmed $ per 1M (shown)
            return "%g" % round(per_tok * 1e6, 6)

        dlg = QDialog(self)                       # modal child of the widget
        dlg._ts_level = self.size_level
        dlg.setWindowTitle("Rates — Pitwall")
        dlg.setStyleSheet(f"background:{ts.BG};")
        sc = self._scale_at(dlg._ts_level)
        dlg.setMinimumWidth(int(360 * sc))

        def lab(text, base, color, bold=False, semibold=False, wrap_=False):
            l = QLabel(text)
            l.setTextFormat(Qt.PlainText)
            l._ts_fontspec = (base, bold, semibold, False)
            l.setFont(self._font(base, bold, semibold))
            l.setStyleSheet(f"color:{color};")
            if wrap_:
                l.setWordWrap(True)
            return l

        body = QVBoxLayout(dlg)
        body.setContentsMargins(18, 16, 18, 14)
        body.setSpacing(0)
        body.addWidget(lab("Rates", 13, ts.INK, semibold=True))
        body.addSpacing(3)
        body.addWidget(lab(
            "These are the prices Pitwall uses to turn tokens into dollars. Claude logs how "
            "many tokens you use but not the prices, so Pitwall keeps its own. When "
            "Anthropic changes prices, check their page and type the new numbers here.",
            8, ts.FAINT, wrap_=True))

        # --- the editable table: model rows × (Input, Output), $ per 1M tokens -------
        body.addSpacing(12)
        body.addWidget(lab("$ per 1 million tokens", 8, ts.MUT, semibold=True))
        body.addSpacing(6)
        line_qss = (f"QLineEdit{{{_well_qss(4)}color:{ts.INK};padding:4px 8px;"
                    f"border-bottom:1px solid {P.CTL_STROKE_HI};"
                    f"selection-background-color:{ts.INK};selection-color:{ts.BG};}}"
                    f"QLineEdit:focus{{border-bottom:2px solid {P.ACCENT};padding-bottom:3px;}}")
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        grid.addWidget(lab("Input", 8, ts.FAINT, semibold=True), 0, 1, Qt.AlignHCenter)
        grid.addWidget(lab("Output", 8, ts.FAINT, semibold=True), 0, 2, Qt.AlignHCenter)
        edits = {}
        for r, fam in enumerate(fams, start=1):
            grid.addWidget(lab(ts.MODEL_LABEL.get(fam, fam), 9, ts.INK), r, 0)
            for c, kind in ((1, "in"), (2, "out")):
                e = QLineEdit()
                e._ts_fontspec = (9, False, False, False)
                e.setFont(self._font(9))
                e.setStyleSheet(line_qss)
                e.setAlignment(Qt.AlignRight)
                e.setFixedWidth(int(84 * sc))
                e.setText(_fmt_rate(ts.RATES[fam][kind]))
                grid.addWidget(e, r, c)
                edits[f"{fam}_{kind}"] = e
        grid.setColumnStretch(0, 1)
        body.addLayout(grid)
        body.addSpacing(6)
        body.addWidget(lab("Cache prices follow the input price automatically — you only "
                           "type these two.", 8, ts.FAINT, wrap_=True))

        # --- check (opens the browser; Pitwall makes no request) ---------------------
        body.addSpacing(12)
        check_lk = lab("Check Anthropic's current prices ↗", 8, ts.INK)
        check_lk.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        check_lk.setCursor(QCursor(Qt.PointingHandCursor))
        body.addWidget(check_lk)
        body.addSpacing(6)
        lc_lbl = lab(self._rates_checked_text(), 8, ts.MUT)
        body.addWidget(lc_lbl)
        status = lab("", 8, ts.MUT, wrap_=True)
        body.addSpacing(4)
        body.addWidget(status)

        def _stamp_check():
            self.cfg["rates_last_checked"] = datetime.now(timezone.utc).isoformat()
            ts.save_config(self.cfg)
            lc_lbl.setText(self._rates_checked_text())
            self._refresh_rates_lc()

        def check_now(_e=None):
            webbrowser.open(ts.ANTHROPIC_PRICING_URL)
            _stamp_check()
            status.setStyleSheet(f"color:{ts.MUT};")
            status.setText("Opened the prices in your browser. Type any changes above, "
                           "then Save.")
        check_lk.mousePressEvent = check_now

        # --- footer: Reset · Cancel · Save ------------------------------------------
        _th = self._applied_theme
        raised = (f"QPushButton{{{_raised_qss(4, theme=_th)}padding:5px 14px;}}"
                  f"QPushButton:hover{{background:{P.PANEL_HI}; color:{ts.INK};"
                  f" border:1px solid {P.CTL_STROKE};}}")
        key_qss = (f"QPushButton{{background:{P.ACCENT}; color:{P.KEY_TEXT}; border:none;"
                   f" border-radius:4px; padding:5px 16px; font-weight:600;}}"
                   f"QPushButton:hover{{background:{P.ACCENT};}}")
        ghost = (f"QPushButton{{background:transparent;color:{ts.MUT};border:none;"
                 f"padding:5px 10px;}}QPushButton:hover{{color:{ts.INK};}}")
        foot = QHBoxLayout()
        foot.setContentsMargins(0, 0, 0, 0)
        foot.setSpacing(8)
        reset_b = QPushButton("Reset to built-in")
        cancel_b = QPushButton("Cancel")
        save_b = QPushButton("Save")
        for b, qss in ((reset_b, raised), (cancel_b, ghost), (save_b, key_qss)):
            b.setCursor(QCursor(Qt.PointingHandCursor))
            b._ts_fontspec = (8, False, True, False)
            b.setFont(self._font(8, semibold=True))
            b.setStyleSheet(qss)
        foot.addWidget(reset_b)
        foot.addStretch(1)
        foot.addWidget(cancel_b)
        foot.addWidget(save_b)
        body.addSpacing(14)
        body.addLayout(foot)

        def reset_fields(_=False):
            # refill the fields with the built-in defaults; commit happens on Save
            for fam in fams:
                edits[f"{fam}_in"].setText(_fmt_rate(ts.DEFAULT_RATES[fam]["in"]))
                edits[f"{fam}_out"].setText(_fmt_rate(ts.DEFAULT_RATES[fam]["out"]))
            status.setStyleSheet(f"color:{ts.MUT};")
            status.setText("Built-in prices filled in. Save to apply.")

        def apply_rates(_=False):
            new_ov = {}
            for fam in fams:
                rin = ts.parse_rate(edits[f"{fam}_in"].text())
                rout = ts.parse_rate(edits[f"{fam}_out"].text())
                if rin is None or rout is None:
                    status.setStyleSheet(f"color:{ts.RED};")
                    status.setText(f"{ts.MODEL_LABEL.get(fam, fam)}: enter a price above 0 "
                                   f"for both Input and Output.")
                    return
                # store an override only where it differs from the built-in default, so the
                # config stays clean and "Reset to built-in" is a true reset
                din = ts.DEFAULT_RATES[fam]["in"] * 1e6
                dout = ts.DEFAULT_RATES[fam]["out"] * 1e6
                if abs(rin - din) > 1e-9 or abs(rout - dout) > 1e-9:
                    new_ov[fam] = {"in": rin, "out": rout}
            self.cfg["rate_overrides"] = new_ov
            self.cfg["rates_last_checked"] = datetime.now(timezone.utc).isoformat()
            ts.save_config(self.cfg)
            ts.apply_rate_overrides(self.cfg)
            self.refresh()              # re-price every number behind the dialog now
            self._refresh_rates_lc()
            dlg.accept()

        reset_b.clicked.connect(reset_fields)
        cancel_b.clicked.connect(dlg.reject)
        save_b.clicked.connect(apply_rates)
        save_b.setDefault(True)
        save_b.setAutoDefault(True)
        dlg.exec()

    def open_settings(self, scroll_to_nudge=False, restore=None, defer_show=False):
        """The full Settings dialog — B-mockup rail-and-panes (owner, 2026-06-11): a recessed left rail (Identity / Accuracy / Attention /
        Pitstop) selects one pane at a time in a QStackedWidget; each pane scrolls
        independently; the footer (status + Cancel/Clear/Save) stays pinned. Same
        fields, config keys, validation and apply() logic as the Tkinter `calibrate`
        port — only the scaffold and the toggle control (chips → PitwallSwitch) changed.
        NOTE: dialog widgets use LOCAL builders (not self._mk), so they are NOT
        registered in self._fonts (that would crash step_scale with setFont on deleted
        C++ objects). Instead each scalable widget is tagged `_ts_fontspec` so A−/A+
        rescales them IN PLACE via _rescale_dialog — no teardown, no reopen.
        scroll_to_nudge=True (the tap's 'Don't show these' path, §10.4) switches to the
        Attention pane and scrolls to the SAVE NUDGES switch so disarming stays a
        deliberate act at the one real switch.
        restore: the stash a live theme flip captured before rebuilding the card —
        unsaved field text, switch positions, pane, geometry and font level are put
        back so the dialog reappears exactly as the user left it, restyled."""
        if self._settings is not None:
            try:
                if self._settings.isVisible():
                    self._settings.raise_()
                    self._settings.activateWindow()
                    if scroll_to_nudge:
                        self._scroll_to_nudge()
                    return
                # hidden but never closed (a dismiss path that skipped closeEvent) —
                # discard it and build fresh instead of raising an invisible window
                self._settings.deleteLater()
            except RuntimeError:
                pass
            self._settings = None
        if not self._snap:
            return
        cfg, d, ceiling, noun = self._snap
        cal = cfg.get("calibration") or {}
        sc = self._scale()
        # the live estimate the front page leads with, so the dialog mirrors it
        est_now = (round(d.get("usd", 0.0) / ceiling * 100)
                   if (ceiling and ceiling > 0) else None)

        # --- dialog scaffold: rail + stacked panes + pinned footer ----------
        scr = self._screen_geo()
        base_w = 620                          # rail (~120) + one pane (~500)
        W = min(int(base_w * sc), scr.width() - 24)
        H = min(int(620 * sc), scr.height() - 24)
        dlg = QDialog()
        # Frameless like the main card (owner punch list 2026-06-11): no native title
        # bar — the card-style header row below carries title / A−/A+ / close, and a
        # thin bezel hands edge-resizing to the OS (so resize still works).
        dlg.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        # Rounded corners to match the card (owner, 2026-06-11: "the settings page is
        # 90 degree corners"): the window itself goes transparent and #sdlg paints the
        # rounded slab. Win11 only auto-rounds native-framed windows, not frameless.
        dlg.setAttribute(Qt.WA_TranslucentBackground)
        dlg._ts_level = self.size_level       # #7: this window's OWN font level (diverges)
        self._settings = dlg
        dlg.setWindowTitle("Settings — Pitwall")   # alt-tab/taskbar label only
        dlg.setObjectName("sdlg")
        # `*` = the old bare-declaration cascade (same semantics); #sdlg adds the
        # hairline window border a frameless window otherwise loses.
        dlg.setStyleSheet(f"*{{background:{ts.BG};}}"
                          f"#sdlg{{background:{ts.BG}; border:1px solid {ts.EDGE};"
                          f" border-radius:8px;}}")
        dlg.resize(W, H)
        dlg.setMinimumSize(min(380, W), min(340, H))

        # Full-bleed background slab. A translucent top-level window does NOT reliably
        # paint its OWN stylesheet background (#sdlg), so the 4px resize bezel let the
        # black active CLI composite through (owner, 2026-06-12 "cli details.mp4" — same
        # class as the hand-off dialog #hpanel fix). A lowered child QFrame paints the
        # panel (bg + border + rounded corners) across the whole window, behind all
        # content; _place_grips keeps it sized + at the bottom of the z-order.
        spanel = QFrame(dlg)
        spanel.setObjectName("spanel")
        spanel.setStyleSheet(f"#spanel{{background:{ts.BG}; border:1px solid {ts.EDGE};"
                             f" border-radius:8px;}}")

        def _resize_settings():      # re-fit the window when A−/A+ rescales in place
            g = self._screen_geo(dlg)
            s = self._scale_at(dlg._ts_level)    # #7: this dialog's own level
            dlg.resize(min(int(base_w * s), g.width() - 24),
                       min(int(620 * s), g.height() - 24))
        dlg._ts_resize = _resize_settings

        outer = QVBoxLayout(dlg)
        outer.setSpacing(0)

        # --- frameless edge-resize: thin grip widgets OWN the bezel strips, so the
        # ↔/↕ cursor lives only on them and Qt resets it the moment the mouse moves
        # onto content. (The old dialog-level setCursor cascaded to every child and
        # stuck on — entering the window crossed the bezel, children then ate the
        # move events, unsetCursor never ran. Owner eyeball 2026-06-11.) A press
        # hands off to the native system resize (Windows snap behavior included).
        _RM = 4     # bezel width = edge-grip thickness
        _CG = 10    # corner grip square; transparent + only 6px over content
        outer.setContentsMargins(_RM, _RM, _RM, _RM)

        def _grip(edges, cursor):
            g = QWidget(dlg)
            g.setStyleSheet("background:transparent;")  # beat the dialog-wide BG rule
            g.setCursor(cursor)

            def _press(e, _ed=edges):
                if e.button() == Qt.LeftButton and dlg.windowHandle():
                    dlg.windowHandle().startSystemResize(_ed)
            g.mousePressEvent = _press
            return g

        _grips = {
            "l":  _grip(Qt.LeftEdge,                  Qt.SizeHorCursor),
            "r":  _grip(Qt.RightEdge,                 Qt.SizeHorCursor),
            "t":  _grip(Qt.TopEdge,                   Qt.SizeVerCursor),
            "b":  _grip(Qt.BottomEdge,                Qt.SizeVerCursor),
            "tl": _grip(Qt.TopEdge | Qt.LeftEdge,     Qt.SizeFDiagCursor),
            "br": _grip(Qt.BottomEdge | Qt.RightEdge, Qt.SizeFDiagCursor),
            "tr": _grip(Qt.TopEdge | Qt.RightEdge,    Qt.SizeBDiagCursor),
            "bl": _grip(Qt.BottomEdge | Qt.LeftEdge,  Qt.SizeBDiagCursor),
        }

        def _place_grips():
            w, h = dlg.width(), dlg.height()
            spanel.setGeometry(0, 0, w, h)
            spanel.lower()            # full-bleed slab, behind every layout child
            _grips["l"].setGeometry(0, _CG, _RM, h - 2 * _CG)
            _grips["r"].setGeometry(w - _RM, _CG, _RM, h - 2 * _CG)
            _grips["t"].setGeometry(_CG, 0, w - 2 * _CG, _RM)
            _grips["b"].setGeometry(_CG, h - _RM, w - 2 * _CG, _RM)
            _grips["tl"].setGeometry(0, 0, _CG, _CG)
            _grips["tr"].setGeometry(w - _CG, 0, _CG, _CG)
            _grips["bl"].setGeometry(0, h - _CG, _CG, _CG)
            _grips["br"].setGeometry(w - _CG, h - _CG, _CG, _CG)
            for g in _grips.values():
                g.raise_()   # above the layout children, which are added after us

        def _dlg_resize(e):
            QDialog.resizeEvent(dlg, e)
            _place_grips()
        dlg.resizeEvent = _dlg_resize
        _place_grips()

        # --- local builders (NOT registered in self._fonts; see docstring) ---
        def lab(text, base, color, bold=False, semibold=False, wrap_=False):
            l = QLabel(text)
            l.setTextFormat(Qt.PlainText)   # never auto-promote HTML-looking text (M1)
            l._ts_fontspec = (base, bold, semibold, False)   # rescale in place on A−/A+
            l.setFont(self._font(base, bold, semibold))
            l.setStyleSheet(f"color:{color};")
            if wrap_:
                l.setWordWrap(True)
                # body copy is selectable so the owner can copy it out to rewrite;
                # ONLY wrapped text — single-line labels include the header title,
                # whose mouse press must keep falling through to the window drag
                l.setTextInteractionFlags(Qt.TextSelectableByMouse)
            return l

        def caption(host, text, top=14):
            host.addSpacing(top)
            cap_lbl = QLabel(text)
            cap_lbl.setTextFormat(Qt.PlainText)
            # 5-element spec: caps must survive A−/A+ rescale (_rescale_dialog *spec)
            cap_lbl._ts_fontspec = (8, False, True, False, False)
            f = self._font(8, semibold=True)
            cap_lbl.setFont(f)
            cap_lbl.setStyleSheet(f"color:{ts.FAINT};")
            host.addWidget(cap_lbl)
            host.addSpacing(8)             # seam 8px below the text (§3.3)
            host.addWidget(self._rule())   # seam underline (§3.3)
            host.addSpacing(4)
            return cap_lbl                 # anchor for scroll-to (SAVE NUDGES)

        # §3.3 Settings restyle: well inputs, ink-inverted selection, MUT focus border
        line_qss = (
            f"QLineEdit{{{_well_qss(4)}color:{ts.INK};"
            f"padding:4px 8px;"
            f"border-bottom:1px solid {P.CTL_STROKE_HI};"
            f"selection-background-color:{ts.INK};selection-color:{ts.BG};}}"
            f"QLineEdit:focus{{border-bottom:2px solid {P.ACCENT};"
            f"padding-bottom:3px;}}")

        edits = {}

        def field(host, key, label, init, placeholder=""):
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            lb = lab(label, 8, ts.MUT)
            lb._ts_widthbase = 96        # keep the label column scaling with the text
            lb.setFixedWidth(int(96 * sc))
            row.addWidget(lb)
            e = QLineEdit()
            # 9, was 10: typed numbers towered over their 8pt labels
            # (owner eyeball 2026-06-11 "the numbers are too big")
            e._ts_fontspec = (9, False, False, False)    # rescale in place on A−/A+
            e.setFont(self._font(9))
            e.setStyleSheet(line_qss)
            if placeholder:
                e.setPlaceholderText(placeholder)
                pal = e.palette()
                pal.setColor(QPalette.PlaceholderText, QColor(ts.FAINT))
                e.setPalette(pal)
            if init not in (None, ""):
                e.setText(str(init))
            row.addWidget(e, 1)
            host.addSpacing(4)
            host.addLayout(row)
            edits[key] = e
            return e

        # §3.3 chip: raised face at rest, ink-inverted key when checked (§2.3)
        _th = getattr(self, "_applied_theme", "dark")
        chip_qss = (
            f"QPushButton{{{_raised_qss(4, theme=_th)}padding:4px 11px;}}"
            f"QPushButton:hover:!checked{{background:{P.PANEL_HI}; "
            f"border:1px solid {P.CTL_STROKE};}}"
            f"QPushButton:checked{{{_raised_qss(4, checked=True, theme=_th)}"
            f"font-weight:600;}}"
            f"QPushButton:focus:!checked{{border:1px solid {P.ACCENT};}}")

        def chiprow(host, items, selected):
            """items = [(value, label)]; returns a {'val': ...} state dict with the
            current selection (single-select), matching the Tkinter set_plan closure."""
            state = {"val": selected, "chips": {}}
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(5)

            def select(v):
                state["val"] = v
                for cv, btn in state["chips"].items():
                    btn.setChecked(cv == v)
            state["select"] = select

            for val, label in items:
                b = QPushButton(label)
                b.setCheckable(True)
                b.setCursor(QCursor(Qt.PointingHandCursor))
                b._ts_fontspec = (8, False, True, False)   # rescale in place on A−/A+
                b.setFont(self._font(8, semibold=True))
                b.setStyleSheet(chip_qss)
                b.clicked.connect(lambda _=False, v=val: select(v))
                row.addWidget(b)
                state["chips"][val] = b
            row.addStretch(1)
            select(selected)
            host.addSpacing(2)
            host.addLayout(row)
            return state

        # === switch row (B trow): INK label + FAINT detail left, switch right ===
        def switch_row(host, title, init_on, detail=None, on_toggle=None,
                       label_size=8):
            """Returns the same {'val': 'on'/'off'} state dict the chip rows used,
            plus 'sw' (the PitwallSwitch) so callers can disable/inspect it."""
            state = {"val": "on" if init_on else "off"}
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(10)
            txt = QVBoxLayout()
            txt.setContentsMargins(0, 0, 0, 0)
            txt.setSpacing(2)
            txt.addWidget(lab(title, label_size, ts.INK))
            if detail:
                txt.addWidget(lab(detail, 8, ts.FAINT, wrap_=True))
            row.addLayout(txt, 1)
            sw = PitwallSwitch(init_on, self._scale_at(dlg._ts_level))
            state["sw"] = sw

            def _tg(on):
                state["val"] = "on" if on else "off"
                if on_toggle:
                    on_toggle(on)
            sw.toggled.connect(_tg)
            row.addWidget(sw, 0, Qt.AlignVCenter)
            host.addSpacing(8)
            host.addLayout(row)
            return state

        # === card-style header: title left, A−/A+ + ✕ right (frameless chrome) ===
        # Mirrors the main card's hand-drawn header row; dragging it moves the window
        # (native system move, so Windows snap keeps working).
        head_w = QWidget()
        head_w.setObjectName("shead")
        # top radii = CARD_RADIUS - 4px bezel: the header sits inset in the now-rounded
        # window, so its own square corners must not poke into the transparent corners
        head_w.setStyleSheet(f"#shead{{background:{ts.BG};"
                             f" border-top-left-radius:{8 - _RM}px;"
                             f" border-top-right-radius:{8 - _RM}px;}}"
                             f"#shead QLabel{{background:transparent;}}")
        head = QHBoxLayout(head_w)
        head.setContentsMargins(16, 8, 12, 8)
        head.setSpacing(10)
        head.addWidget(lab("Settings", 11, ts.INK, semibold=True))
        head.addStretch(1)
        head.addLayout(self._dialog_font_buttons(dlg))
        x_btn = lab("✕", 10, ts.FAINT)
        x_btn.setCursor(QCursor(Qt.PointingHandCursor))
        x_btn.mousePressEvent = lambda _e: dlg.close()
        head.addWidget(x_btn)
        outer.addWidget(head_w)

        def _head_press(e):
            if e.button() == Qt.LeftButton and dlg.windowHandle():
                dlg.windowHandle().startSystemMove()
        head_w.mousePressEvent = _head_press

        head_sep = QFrame()
        head_sep.setFixedHeight(1)
        head_sep.setStyleSheet(f"background:{ts.EDGE};")
        outer.addWidget(head_sep)

        # === rail + stacked panes (B dialog chrome) ==========================
        body_row = QHBoxLayout()
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.setSpacing(0)
        outer.addLayout(body_row, 1)

        # The rail is a recessed tray: floor a step darker than the card, hairline
        # seam against the pane. Entries are raised faces when active (§2.3).
        rail_w = QWidget()
        rail_w.setObjectName("srail")
        # O3: rail floor = BG (the recessed tray is deleted; pane fill step + inset
        # stroke carry the boundary per spec §6.3)
        rail_w.setStyleSheet(
            f"#srail{{background:{ts.BG};}}"
            f"#srail QLabel{{background:transparent;}}")
        rail_w._ts_widthbase = 122
        rail_w.setFixedWidth(int(122 * sc))
        rail = QVBoxLayout(rail_w)
        rail.setContentsMargins(10, 12, 10, 12)
        rail.setSpacing(2)
        # (the "Settings" title and the A−/A+ pair moved to the frameless header row)
        body_row.addWidget(rail_w)

        stack = QStackedWidget()
        stack.setStyleSheet(f"background:{ts.BG};")
        body_row.addWidget(stack, 1)

        panes = {}        # key -> the pane's QScrollArea (stack page)

        def make_pane(key, title, lede):
            page = QScrollArea()
            page.setWidgetResizable(True)
            page.setFrameShape(QFrame.NoFrame)
            page.setStyleSheet(f"background:{P.PANE};")
            pw = QWidget()
            pw.setStyleSheet(f"background:{P.PANE}; border-top-left-radius:8px;"
                             f" border:1px solid {P.CTL_STROKE};")
            v = QVBoxLayout(pw)
            v.setContentsMargins(16, 12, 16, 14)
            v.setSpacing(0)
            v.addWidget(lab(title, 12, ts.INK, semibold=True))
            if lede:
                v.addSpacing(3)
                v.addWidget(lab(lede, 8, ts.FAINT, wrap_=True))
            page.setWidget(pw)
            panes[key] = page
            stack.addWidget(page)
            return v

        rail_btns = {}

        def select_pane(key):
            dlg._ts_pane = key               # the live theme flip restores this pane
            for k, b in rail_btns.items():
                b.setChecked(k == key)
            stack.setCurrentWidget(panes[key])
        dlg._select_pane = select_pane       # _scroll_to_nudge jumps panes with this

        rail_qss = (
            f"QPushButton{{background:transparent;color:{ts.MUT};border:none;"
            f"border-radius:4px;padding:5px 8px;text-align:left;}}"
            f"QPushButton:hover:!checked{{background:{ts.PANEL}; color:{ts.INK};}}"
            f"QPushButton:checked{{background:{P.PANEL_HI};color:{ts.INK};"
            f"font-weight:600; border:1px solid {P.CTL_STROKE};}}")
        _ps_avail = ts.pitstop_available()
        _rail_entries = [("identity", "Identity"), ("accuracy", "Accuracy"),
                         ("attention", "Attention"), ("diagnostics", "Diagnostics")]
        if _ps_avail:
            _rail_entries.append(("pitstop", "Pitstop"))
        for _rk, _rl in _rail_entries:
            rb = _RailButton(_rl)
            rb.setCheckable(True)
            rb.setCursor(QCursor(Qt.PointingHandCursor))
            rb._ts_fontspec = (9, False, False, False)
            rb.setFont(self._font(9))
            rb.setStyleSheet(rail_qss)
            rb.clicked.connect(lambda _=False, k=_rk: select_pane(k))
            rail.addWidget(rb)
            rail_btns[_rk] = rb
        rail.addStretch(1)
        # sweep item 37 (owner, 2026-06-12: "we should have a version number for
        # pitwall somewhere"): shown ONCE, quietly, at the bottom of the rail. The
        # number itself lives in ts_core.APP_VERSION (one brain, both faces).
        ver = lab(f"Pitwall {ts.APP_VERSION}", 7, ts.FAINT)
        rail.addWidget(ver, 0, Qt.AlignHCenter)

        ident = make_pane("identity", "Identity",
                          "What the widget calls itself and how it sits on "
                          "your desktop.")
        acc = make_pane("accuracy", "Accuracy",
                        "How Pitwall checks its numbers against Claude's own "
                        "instead of estimating.")
        att = make_pane("attention", "Attention",
                        "When the widget speaks up — save nudges and rotating tips.")
        diag = make_pane("diagnostics", "Diagnostics",
                         "A safe place to see how Pitwall behaves, plus the off-screen "
                         "/usage capture that keeps the numbers honest.")
        # === DIAGNOSTICS pane (top: Demo; the capture controls move in below) ====
        caption(diag, "Demo", top=12)
        diag.addWidget(lab(
            "Open a self-contained demo: a second Pitwall, clearly badged DEMO, driven "
            "by a slider instead of your real usage. Drag it to watch the whole card "
            "react. It changes nothing on your machine.", 8, ts.FAINT, wrap_=True))
        demo_btn = QPushButton("Open demo")
        demo_btn.setCursor(QCursor(Qt.PointingHandCursor))
        demo_btn._ts_fontspec = (8, False, True, False)
        demo_btn.setFont(self._font(8, semibold=True))
        demo_btn.setStyleSheet(
            f"QPushButton{{{_raised_qss(radius=4, checked=False, theme=self._applied_theme)}"
            f"padding:4px 14px;}}"
            f"QPushButton:hover{{background:{P.PANEL_HI}; color:{ts.INK};"
            f" border:1px solid {P.CTL_STROKE};}}")
        demo_btn.clicked.connect(lambda _=False: self._launch_demo())
        _drow = QHBoxLayout()
        _drow.setContentsMargins(0, 0, 0, 0)
        _drow.addWidget(demo_btn)
        _drow.addStretch(1)
        diag.addSpacing(6)
        diag.addLayout(_drow)
        # === IDENTITY pane ===================================================
        # 1. DISPLAY NAME
        caption(ident, "Display name", top=12)
        field(ident, "name", "name", cfg.get("name", "Pitwall"),
              placeholder="Pitwall")
        field(ident, "tagline", "tagline", cfg.get("tagline", ""),
              placeholder="optional subtitle")
        ident.addSpacing(2)
        ident.addWidget(lab("The name shown at the top of the widget. Leave it "
                            "blank to use Pitwall. Leave the tagline blank for "
                            "none.", 8, ts.FAINT, wrap_=True))

        # 1.5 THEME
        caption(ident, "Theme", top=14)
        theme_state = chiprow(ident, [("system", "System"),
                                      ("dark", "Dark"), ("light", "Light")],
                              cfg.get("theme", "system"))
        ident.addSpacing(2)
        ident.addWidget(lab("Dark and Light pin that look permanently. System has no "
                            "look of its own — it copies whatever Windows is set to "
                            "and follows along whenever Windows changes. Example: if "
                            "Windows is scheduled to switch itself to dark mode at "
                            "night, this window flips with it automatically. "
                            "Clicking applies it right away.",
                            8, ts.FAINT, wrap_=True))

        # 1.7 WINDOW — Always stay on top (§4.1), now a track-and-knob switch
        caption(ident, "Window", top=14)
        aot_state = switch_row(
            ident, "Always stay on top", self.cfg.get("always_on_top", True),
            detail="The widget floats above every other window so the numbers "
                   "stay in view. Turn off to let it stack like a normal window.")
        # Start with Windows — login autostart. The HKCU Run key is the source of
        # truth (read live, written on Save), so it can't drift from a Task Manager
        # change. Not stored in config; see ts.startup_enabled / ts.set_startup.
        start_state = switch_row(
            ident, "Start with Windows", ts.startup_enabled(),
            detail="Launch Pitwall automatically when you sign in to Windows, so "
                   "the widget is always waiting for you. Turn off to start it "
                   "yourself.")

        # === ACCURACY pane ===================================================
        # 2. YOUR PLAN
        caption(acc, "Your plan", top=12)
        plan_items = [(k, ts.PLANS[k]["label"]) for k in ("free", "pro", "max5", "max20")]
        plan_state = chiprow(acc, plan_items, cfg.get("plan"))
        acc.addSpacing(2)
        acc.addWidget(lab("Pick the Claude plan you pay for. Pitwall estimates "
                          "your limits from it until a sync brings the real "
                          "numbers — then the real numbers win.",
                          8, ts.FAINT, wrap_=True))

        # 2.5 RATES — the prices Pitwall turns tokens into dollars with. Claude Code's
        # transcripts log token COUNTS but not the $ rates, so Pitwall ships its own price
        # table; this is where the user keeps it current (the dialog opens Anthropic's
        # pricing page in the browser — Pitwall makes no network request — and lets them
        # type the numbers back in). The button + last-checked line live here; the editing
        # happens in self.open_rates().
        caption(acc, "Rates", top=14)
        acc.addWidget(lab("The prices Pitwall uses to turn tokens into dollars. Claude "
                          "doesn't report the dollar rates, so Pitwall keeps its own — "
                          "open this to check Anthropic's current prices and update them.",
                          8, ts.FAINT, wrap_=True))
        rates_btn = QPushButton("View & update rates ↗")
        rates_btn.setCursor(QCursor(Qt.PointingHandCursor))
        rates_btn._ts_fontspec = (8, False, True, False)
        rates_btn.setFont(self._font(8, semibold=True))
        rates_btn.setStyleSheet(
            f"QPushButton{{{_raised_qss(radius=4, checked=False, theme=self._applied_theme)}"
            f"padding:4px 14px;}}"
            f"QPushButton:hover{{background:{P.PANEL_HI}; color:{ts.INK};"
            f" border:1px solid {P.CTL_STROKE};}}")
        rates_btn.clicked.connect(lambda _=False: self.open_rates())
        _rrow = QHBoxLayout()
        _rrow.setContentsMargins(0, 0, 0, 0)
        _rrow.addWidget(rates_btn)
        _rrow.addStretch(1)
        acc.addSpacing(6)
        acc.addLayout(_rrow)
        # last-checked line — a referenced label so open_rates() can refresh it live when
        # the user checks/updates without reopening Settings.
        self._rates_lc_lbl = lab(self._rates_checked_text(), 8, ts.MUT)
        acc.addSpacing(4)
        acc.addWidget(self._rates_lc_lbl)

        # 3.6 AUTO-SYNC REAL USAGE — the headline path (manual set moved BELOW as the
        # fallback — owner punch list 2026-06-11: auto first, then by-hand). When On,
        # Pitwall runs `claude /usage` off-screen on a schedule, reads Claude's OWN %s + reset,
        # and pins them for you. Default OFF; the flag is also the kill switch
        # (ts.AutoUsageScheduler reads it live).
        au = cfg.get("auto_usage") or {}
        caption(diag, "Auto-sync real usage", top=14)
        try:
            imin = int(au.get("interval_min", 30))
        except (TypeError, ValueError):
            imin = 30
        _imin_user_set = au.get("interval_min_user_set") is True
        # sense the model NOW from the live sessions (don't wait for a scheduler tick —
        # right after a restart the cached value read as non-Fable and showed 30)
        _eff_fam = self._live_family()
        _eff_min = imin if _imin_user_set else (10 if _eff_fam == "fable" else 30)
        AUTO_HELP = (f"Auto-sync asks Claude itself: about every {_eff_min} min, Pitwall runs "
                     f"Anthropic's own /usage check in the background and re-pins the real "
                     f"percentages and reset times. It runs completely off-screen — you "
                     f"never see a window. Off by default.")
        astatus = lab("", 8, ts.MUT, wrap_=True)

        def _auto_flash(on):
            if on:
                astatus.setStyleSheet(f"color:{ts.GREEN};")
                astatus.setText("On — I'll keep the numbers synced to Claude's own.")
                # 4s hold to match the nudge ack (same read-speed complaint applies)
                QTimer.singleShot(4000, lambda: (
                    astatus.setStyleSheet(f"color:{ts.MUT};"),
                    astatus.setText(AUTO_HELP)))
            else:
                astatus.setStyleSheet(f"color:{ts.MUT};")
                astatus.setText(AUTO_HELP)

        astate = switch_row(
            diag, "Sync the real numbers automatically", bool(au.get("enabled")),
            detail="Reads Claude's own /usage off-screen — no typing, no window.",
            on_toggle=_auto_flash)

        # Resync interval — how often the auto-sync fires. Placeholder shows the effective
        # default (10 min if Fable is live, else 30) so the Fable rule is visible.
        _imin_ph = f"{10 if _eff_fam == 'fable' else 30} min"
        _imin_init = str(imin) if _imin_user_set else ""
        field(diag, "imin", "resync every", _imin_init, placeholder=_imin_ph)
        diag.addSpacing(2)
        diag.addWidget(lab("Minutes between background syncs (2–240). Leave blank to use "
                          "the default (10 min when Fable is active, else 30).",
                          8, ts.FAINT, wrap_=True))

        # "Sync now" — one-shot manual trigger of the SAME off-screen /usage capture, on
        # demand (works even when the toggle is Off). The blocking capture runs on a daemon
        # worker; a QTimer poll applies the result on the UI thread and survives this dialog
        # closing (guarded against deleted widgets). Shares the scheduler's in_flight gate
        # so it can't double-run alongside an auto capture.
        _theme = self._applied_theme
        _raised_rest = _raised_qss(radius=4, checked=False, theme=_theme)
        sync_qss = (f"QPushButton{{{_raised_rest}padding:4px 12px;}}"
                    f"QPushButton:hover{{background:{P.PANEL_HI}; color:{ts.INK};"
                    f" border:1px solid {P.CTL_STROKE};}}"
                    f"QPushButton:disabled{{color:{ts.FAINT};}}")
        srow2 = QHBoxLayout()
        srow2.setContentsMargins(0, 0, 0, 0)
        srow2.setSpacing(8)
        srow2.addWidget(lab("Read Claude's /usage right now", 8, ts.MUT))
        srow2.addStretch(1)
        sync_btn = QPushButton("Sync now")
        sync_btn.setCursor(QCursor(Qt.PointingHandCursor))
        sync_btn._ts_fontspec = (8, False, True, False)   # rescale in place on A−/A+
        sync_btn.setFont(self._font(8, semibold=True))
        sync_btn.setStyleSheet(sync_qss)

        def sync_now():
            if self._auto.in_flight:
                astatus.setStyleSheet(f"color:{ts.AMBER};")
                astatus.setText("A sync is already running…")
                return
            dlg._manual_sync = True      # this status is owned by "Sync now", not the poll
            self._auto.mark_started()
            sync_btn.setEnabled(False)
            astatus.setStyleSheet(f"color:{ts.MUT};")
            astatus.setText("Syncing…")
            threading.Thread(target=self._sync_now_worker, daemon=True).start()
            QTimer.singleShot(500, lambda: self._sync_now_poll(astatus, sync_btn, async_last))

        sync_btn.clicked.connect(lambda _=False: sync_now())
        srow2.addWidget(sync_btn)
        diag.addSpacing(6)
        diag.addLayout(srow2)

        astatus.setText(AUTO_HELP)
        diag.addSpacing(4)
        diag.addWidget(astatus)
        # last-synced line: when a sync (auto OR "Sync now") last landed. Always shown,
        # as a referenced label so "Sync now" can refresh it live (it would otherwise be
        # stuck at the value from when this dialog opened). Driven by auto_usage.last_sync.
        async_last = lab(self._lsync_text(), 8, ts.MUT, wrap_=True)
        diag.addWidget(async_last)
        # While ANY capture runs — the auto-sync that fires on launch, the scheduler, or a
        # manual "Sync now" — gray out "Sync now" and show "Syncing…" so a click can never
        # land on the "already running" guard. A 300ms poll watches the shared in_flight
        # flag; on the busy→idle edge it restores the help text + refreshes the last-synced
        # line, unless a manual "Sync now" owns the status (then its "Synced ✓" is kept).
        dlg._was_busy = False
        dlg._manual_sync = False

        def _poll_auto_ui():
            try:
                busy = self._auto.in_flight
                sync_btn.setEnabled(not busy)
                if busy:
                    if not dlg._manual_sync:
                        astatus.setStyleSheet(f"color:{ts.MUT};")
                        astatus.setText("Syncing…")
                    dlg._was_busy = True
                elif dlg._was_busy:
                    dlg._was_busy = False
                    if dlg._manual_sync:
                        dlg._manual_sync = False      # leave the poll's "Synced ✓"
                    else:
                        astatus.setStyleSheet(f"color:{ts.MUT};")
                        astatus.setText(AUTO_HELP)
                        async_last.setText(self._lsync_text())
            except RuntimeError:
                pass

        _auto_ui_timer = QTimer(dlg)
        _auto_ui_timer.timeout.connect(_poll_auto_ui)
        _auto_ui_timer.start(300)
        _poll_auto_ui()      # set the initial button/status state without waiting 300ms
        # "Troubleshoot capture" — opens a read-only window showing exactly what the
        # /usage read pulled in (raw panel text + parsed numbers), so a wrong number can
        # be diagnosed in-app instead of behind the scenes.
        diag_link = lab("Troubleshoot capture ↗", 8, ts.INK)
        # clamp the click zone to the text — a QLabel in a VBox stretches the full
        # row width, making the whole row clickable (owner eyeball 2026-06-11)
        diag_link.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        diag_link.setCursor(QCursor(Qt.PointingHandCursor))
        diag_link.mousePressEvent = lambda _e: self.show_usage_diagnostics()
        diag.addSpacing(3)
        diag.addWidget(diag_link)

        # MANUAL SET — type the numbers from Claude's own Usage page by hand. Renamed
        # from "SYNC FROM SETTINGS → USAGE" (owner punch list 2026-06-11: this is
        # setting, not syncing) and sequenced BELOW auto-sync as the fallback path.
        caption(acc, "Manual set", top=14)
        acc.addWidget(lab(
            "Pitwall's percentages come from Anthropic's own /usage report, so they "
            "can drift in the stretch between syncs. To set them by hand, open Usage "
            "in the Claude desktop or web app (Settings → Usage), then type the "
            "numbers it shows into the fields below — Pitwall re-pins to those.",
            8, ts.MUT, wrap_=True))
        caption(acc, "Current session", top=8)
        field(acc, "spct", "% used", "" if est_now is None else f"{est_now:.0f}")
        # Same guarded equation as the main card's "Resets in" (gather() → valid_override),
        # so the two can't diverge on an impossible/stale pinned reset (owner, 2026-06-08).
        _ov = ts.valid_override(cfg, cfg.get("window_hours", 5))
        field(acc, "rin", "resets in", ts.fmt_countdown(_ov) if _ov else "")

        # WEEKLY LIMITS — lives with the other "type-from-the-Usage-page" fields
        def pct_init(key):
            v = cal.get(key)
            return "" if v is None else f"{v:.0f}"

        caption(acc, "Weekly limits", top=14)
        field(acc, "wall", "all models %", pct_init("weekly_all_pct"))
        field(acc, "wreset", "weekly resets", cal.get("weekly_all_reset") or "")
        field(acc, "wson", "sonnet %", pct_init("weekly_sonnet_pct"))

        # reconciliation note — what the % is currently measured against
        cal_on = (cfg.get("use_calibrated_ceiling")
                  and (cfg.get("calibration") or {}).get("derived_ceiling"))
        if cal_on and ceiling:
            meas = f"now measuring against your synced usage (~${ceiling:.0f})"
        else:
            meas = ("now measuring against your "
                    f"{ts.PLANS.get(cfg.get('plan'), {}).get('label', 'plan')} plan")
        acc.addSpacing(10)
        acc.addWidget(lab(meas, 8, ts.FAINT, wrap_=True))
        # Privacy statement — moved off the rail bottom and made TRUE again (owner punch
        # list 2026-06-11: the old "reads your local logs only" predates auto-sync, which
        # asks the user's own Claude app for /usage).
        acc.addSpacing(8)
        acc.addWidget(lab("Private by design: Pitwall reads your local Claude Code "
                          "logs and asks your own Claude app for /usage. Nothing is "
                          "sent anywhere else.", 7, ts.FAINT, wrap_=True))

        # === ATTENTION pane ==================================================
        # 3.5 SAVE NUDGES — "Nudge me" arm switch + F-N2 "can't arm" banner (§10.2/10.3).
        # Off by default; while a metered/off-subscription env var is set the switch is
        # disabled and an AMBER banner says exactly which var to clear (F12).
        block_reason = ts.nudge_arm_block_reason()
        self._nudge_anchor = caption(att, "Save nudges", top=12)
        NUDGE_HELP = ("When a chat gets expensive enough that starting fresh would "
                      "save real money, Pitwall shows a tip with the exact text to "
                      "type (and a way to copy it). It never types or runs anything "
                      "itself — you do. Off by default.")
        nstatus = lab("", 8, ts.MUT, wrap_=True)

        def _nudge_flash(on):
            if on:
                nstatus.setStyleSheet(f"color:{ts.GREEN};")
                nstatus.setText("On — I'll tap you when a fresh start would pay off.")
                # 4s hold — 1.5s then 2.5s were both gone before they could be read
                # (owner, 2026-06-11 twice)
                QTimer.singleShot(4000, lambda: (
                    nstatus.setStyleSheet(f"color:{ts.MUT};"),
                    nstatus.setText(NUDGE_HELP)))
            else:
                nstatus.setStyleSheet(f"color:{ts.MUT};")
                nstatus.setText(NUDGE_HELP)

        nstate = switch_row(
            att, "Tell me when a fresh session would save money",
            bool(cfg.get("nudge_armed")) and not block_reason,
            on_toggle=_nudge_flash if not block_reason else None)

        if block_reason:
            # F-N2: disable the switch (anti-affordance) and say why, in plain English.
            # (apply() also refuses to arm while blocked — belt and braces.)
            nstate["sw"].setChecked(False)
            nstate["sw"].setEnabled(False)
            nstate["val"] = "off"
            armblock = QFrame()
            armblock.setObjectName("armblock")
            armblock.setStyleSheet(
                f"#armblock{{background:{ts.PANEL}; "
                f"border-left:3px solid {ts.AMBER}; border-radius:4px;}}")
            bl = QVBoxLayout(armblock)
            bl.setContentsMargins(11, 9, 12, 10)
            bl.setSpacing(0)
            brow = QHBoxLayout()
            brow.setSpacing(6)
            brow.addWidget(lab("⚠", 9, ts.AMBER))
            brow.addWidget(lab("Can't arm yet", 8, ts.INK, semibold=True))
            brow.addStretch(1)
            bl.addLayout(brow)
            bl.addSpacing(4)
            bl.addWidget(lab(block_reason, 8, ts.MUT, wrap_=True))  # verbatim, names the var
            att.addSpacing(6)
            att.addWidget(armblock)
        else:
            nstatus.setText(NUDGE_HELP)
            att.addSpacing(4)
            att.addWidget(nstatus)

        # 3.8 ROTATING TIPS
        caption(att, "Rotating tips", top=14)
        tips_state = switch_row(
            att, "Show rotating tips", not cfg.get("tips_off"),
            detail="Hides the rotating tips at the bottom of the card when off. "
                   "The session-flash warning still shows when needed.")
        # accuracy stamp (owner, 2026-06-12) — the date lives in ts_core.TIPS_VERIFIED,
        # re-stamped whenever the fact-check manifest walk passes
        att.addSpacing(4)
        att.addWidget(lab(ts.TIPS_VERIFIED_NOTE, 8, ts.FAINT, wrap_=True))

        # Drift Correction was removed 2026-06-08 — Sync from Settings → Usage is now the
        # single source of truth, so the manual correction knob is gone (it implied the
        # numbers were unreliable when we're only ~30 min out).

        # === PITSTOP pane — the CLI save-and-restart ritual ==================
        # The toolchain is per-machine (hooks/scripts under ~/.claude/pitstop); on a
        # machine without it the pane AND its rail entry don't exist (_ps_avail above).
        ps_cfg = ts.load_pitstop_config() if _ps_avail else None
        ps_states = {}
        if ps_cfg is not None:
            pit = make_pane(
                "pitstop", "Pitstop — save & restart",
                "A long Claude CLI session gets expensive to continue. Pitstop is the "
                "save-and-restart move: type pitstop in a session and it saves a "
                "checkpoint plus a resume file, so a fresh window can pick up exactly "
                "where the old one left off. pitstop auto opens that new window for "
                "you — and the old window only closes after the new session confirms "
                "the handoff landed. The switches below control how the new window "
                "starts.")

            # sweep item 33 (owner, 2026-06-12): "pitstop" is text we ask the user
            # to type, so the pane offers it with a real copy affordance.
            ps_copy = lab("⧉  Copy the word to type: pitstop", 8, ts.INK)
            ps_copy.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            ps_copy.setCursor(QCursor(Qt.PointingHandCursor))

            def _ps_copy(_e):
                QApplication.clipboard().setText("pitstop")   # copy ONLY
                ps_copy.setText("✓  Copied — type it into the session")
                ps_copy.setStyleSheet(f"color:{ts.GREEN};")
                QTimer.singleShot(1800, lambda: (
                    ps_copy.setText("⧉  Copy the word to type: pitstop"),
                    ps_copy.setStyleSheet(f"color:{ts.INK};")))
            ps_copy.mousePressEvent = _ps_copy
            pit.addSpacing(4)
            pit.addWidget(ps_copy)

            caption(pit, "The new window", top=12)
            ps_states["rc"] = switch_row(
                pit, "Phone control", ps_cfg["remote_control"],
                detail="The relaunched session starts with Remote Control on, so you "
                       "can keep driving it from your phone — it appears as “pitstop "
                       "<track>” under Code in the Claude phone app, or at "
                       "claude.ai/code. One-time setup first: sign in to Claude Code "
                       "with your claude.ai account (type /login in the CLI), then "
                       "sign in to the Claude app on your phone with the same "
                       "account. Full walkthrough in the documentation below.")
            ps_states["auto"] = switch_row(
                pit, "Auto mode", ps_cfg["auto_mode"],
                detail="The new window starts working immediately and never stops "
                       "to ask permission. Convenient, but nothing asks before it "
                       "acts — your call.")
            ps_states["full_auto"] = switch_row(
                pit, "Full auto", ps_cfg["full_auto"],
                detail="When the token nudge fires, the whole pitstop runs itself — "
                       "save, checkpoint, open the new window, close the old one — "
                       "with no questions asked. Leave off unless you're sure you "
                       "want that.")

            caption(pit, "Nudge", top=14)
            field(pit, "ps_thresh", "nudge at",
                  ts.fmt_token_amount(ts.pitstop_threshold()))
            pit.addSpacing(2)
            pit.addWidget(lab(
                "Once a CLI session crosses this many tokens, Pitwall suggests a "
                "pitstop (and reminds you again every extra 1M). Type it like 3M, "
                "2.5M, 300k, or a plain number.", 8, ts.FAINT, wrap_=True))
            ps_last = ts.pitstop_last_handoffs(limit=2)
            if ps_last:
                caption(pit, "Recent handoffs", top=14)
                for r in ps_last:
                    pit.addWidget(lab(
                        "Last handoff (%s): %s · %s" % (r["track"], r["text"], r["when"]),
                        8, ts.FAINT, wrap_=True))
            # Documentation link (owner punch list 2026-06-11: the pane must link out).
            ps_doc = lab("How Pitstop works — full documentation ↗", 8, ts.INK)
            ps_doc.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)  # text-only click zone
            ps_doc.setCursor(QCursor(Qt.PointingHandCursor))
            ps_doc.mousePressEvent = (
                lambda _e: webbrowser.open(PITSTOP_DOC_URL))
            pit.addSpacing(12)
            pit.addWidget(ps_doc)
            pit.addStretch(1)

        # bottom-anchor every pane's content
        ident.addStretch(1)
        acc.addStretch(1)
        att.addStretch(1)

        # the Attention pane's scroll area is the scroll-to-nudge target (§10.4)
        self._settings_area = panes["attention"]

        # --- pinned footer (never scrolls) ----------------------------------
        # Seam across top (§2.5) + flat WELL band (§3.3)
        foot_sep = QFrame()
        foot_sep.setFixedHeight(1)
        foot_sep.setStyleSheet(f"QFrame{{background:{P.SEAM_LT};}}")
        outer.addWidget(foot_sep)
        foot_w = QWidget()
        foot_w.setObjectName("sfoot")
        # bottom radii mirror the header's top radii (rounded-window fix); the scoped
        # selector drops the old bare-declaration cascade, so the QLabel rule keeps
        # the message label from picking the dialog-wide BG box back up
        foot_w.setStyleSheet(f"#sfoot{{background:{P.WELL};"
                             f" border-top:1px solid {P.WELL_SH};"
                             f" border-bottom-left-radius:{8 - _RM}px;"
                             f" border-bottom-right-radius:{8 - _RM}px;}}"
                             f"#sfoot QLabel{{background:transparent;}}")
        foot = QHBoxLayout(foot_w)
        foot.setContentsMargins(16, 10, 16, 12)
        foot.setSpacing(8)
        msg = lab("", 8, ts.RED, wrap_=True)
        foot.addWidget(msg)
        foot.addStretch(1)
        outer.addWidget(foot_w)

        _key_qss = _raised_qss(radius=4, checked=True, theme=_theme)
        _accent_hover = whiten(P.ACCENT, 0.08) if _theme == "dark" else _darken(P.ACCENT, 0.08)
        save_qss = (f"QPushButton{{{_key_qss}padding:6px 16px;}}"
                    f"QPushButton:hover{{background:{_accent_hover}; color:{P.KEY_TEXT};"
                    f" border:none;}}"
                    f"QPushButton:disabled{{color:{ts.FAINT};}}")
        clear_qss = (f"QPushButton{{{_raised_qss(4, theme=_theme)}padding:6px 12px;}}"
                     f"QPushButton:hover{{background:{P.PANEL_HI}; color:{ts.INK};"
                     f" border:1px solid {P.CTL_STROKE};}}"
                     f"QPushButton:disabled{{color:{ts.FAINT};}}")
        cancel_qss = (f"QPushButton{{background:transparent;color:{ts.MUT};border:none;"
                      f"padding:6px 10px;}}QPushButton:hover{{color:{ts.INK};}}")

        b_save = QPushButton("Save")
        b_save.setCursor(QCursor(Qt.PointingHandCursor))
        b_save._ts_fontspec = (9, False, True, False)   # rescale in place on A−/A+
        b_save.setFont(self._font(9, semibold=True))
        b_save.setStyleSheet(save_qss)
        b_clear = QPushButton("Clear")
        b_clear.setCursor(QCursor(Qt.PointingHandCursor))
        b_clear._ts_fontspec = (9, False, False, False)   # rescale in place
        b_clear.setFont(self._font(9))
        b_clear.setStyleSheet(clear_qss)
        b_cancel = QPushButton("Cancel")
        b_cancel.setCursor(QCursor(Qt.PointingHandCursor))
        b_cancel._ts_fontspec = (9, False, False, False)   # rescale in place
        b_cancel.setFont(self._font(9))
        b_cancel.setStyleSheet(cancel_qss)
        foot.addWidget(b_cancel)     # B-mockup order: ghost · raised · primary key
        foot.addWidget(b_clear)
        foot.addWidget(b_save)

        # --- apply / clear (faithful port of Tkinter calibrate.apply) -------
        def apply():
            def err(text):    # validation message — force RED in the shared slot
                msg.setStyleSheet(f"color:{ts.RED};")
                msg.setText(text)
            sp = ts.parse_pct(edits["spct"].text())
            wa = ts.parse_pct(edits["wall"].text())
            ws = ts.parse_pct(edits["wson"].text())
            rin = edits["rin"].text().strip()
            # pitstop nudge point: validate BEFORE anything writes (a bad value must
            # not half-apply the dialog). Empty field = leave the setting alone.
            ps_thresh = None
            if ps_cfg is not None:
                t_txt = edits["ps_thresh"].text().strip()
                if t_txt:
                    ps_thresh = ts.parse_token_amount(t_txt)
                    if ps_thresh is None or not (ts.PITSTOP_THRESHOLD_MIN
                                                 <= ps_thresh <= ts.PITSTOP_THRESHOLD_MAX):
                        err("Pitstop nudge point: try 3M or 2.5M (100k–50M).")
                        return
            # the reset ANCHORS the window; without it a % can't become a ceiling
            if sp is not None and not rin and self._override_dt() is None:
                err("Add “resets in” too — it pins the window so the % is right.")
                return
            if rin:
                res = ts.parse_reset_input(rin, datetime.now().astimezone())
                if res is None:
                    err("Couldn't read the reset — try 2h22m or 9:15pm.")
                    return
                self.cfg["reset_override"] = (
                    None if res == "CLEAR"
                    else res.astimezone(timezone.utc).isoformat())
            new = dict(self.cfg.get("calibration") or {})
            new["at"] = datetime.now(timezone.utc).isoformat()
            if sp is not None:
                ov = self._override_dt()
                if ov is None:
                    err("Add “resets in” too — it pins the window so the % is right.")
                    return
                wh = self.cfg.get("window_hours", 5)
                est_before, guess = (self._snap[1] if self._snap else {}), {}
                try:
                    entries = ts.collect_entries(wh)
                    guess = ts.active_window(entries, wh)
                    measured = ts.window_for_reset(entries, ov, wh)
                except Exception:
                    measured = est_before
                # only log a real correction when the typed % differs from the estimate
                if est_now is None or abs(sp - est_now) >= 1:
                    self._log_correction(sp, ov, guess, est_before)
                usd = measured.get("usd", 0.0)
                new["session_pct"] = sp
                new["session_usd"] = usd
                new["derived_ceiling"] = ((usd / (sp / 100.0))
                                          if (usd > 0 and sp > 0) else None)
                if new["derived_ceiling"]:
                    self.cfg["use_calibrated_ceiling"] = True
            if wa is not None:
                new["weekly_all_pct"] = wa
            if ws is not None:
                new["weekly_sonnet_pct"] = ws
            wr = edits["wreset"].text().strip()
            if wr:
                new["weekly_all_reset"] = wr
            # name / tagline / plan apply regardless of the usage fields
            self.cfg["name"] = edits["name"].text().strip() or "Pitwall"
            self.cfg["tagline"] = edits["tagline"].text().strip()
            self.cfg["plan"] = plan_state["val"]
            self.cfg["theme"] = theme_state["val"]
            # Arm/disarm "Nudge me" — but NEVER while blocked (leave the prior armed
            # state intact so clearing the env var later doesn't silently lose it).
            if not block_reason:
                self.cfg["nudge_armed"] = (nstate["val"] == "on")
            # auto-sync real usage on/off (default OFF; this flag is also the kill switch).
            # Preserve the rest of the auto_usage dict (interval/idle/last_sync/last_ok).
            _au = dict(self.cfg.get("auto_usage") or {})
            _au["enabled"] = (astate["val"] == "on")
            # resync interval: empty → clear user-set flag (restore default); valid number
            # in 2..240 → apply and set flag; unparseable → leave unchanged.
            _imin_txt = edits["imin"].text().strip()
            if _imin_txt == "":
                _au["interval_min_user_set"] = False
            else:
                try:
                    _imin_val = max(2, min(240, int(_imin_txt)))
                    _au["interval_min"] = _imin_val
                    _au["interval_min_user_set"] = True
                except (TypeError, ValueError):
                    pass  # unparseable — leave config unchanged
            self.cfg["auto_usage"] = _au
            self.cfg["tips_off"] = (tips_state["val"] == "off")
            # always_on_top (§4.1): save + apply immediately
            new_aot = (aot_state["val"] == "on")
            if new_aot != self.cfg.get("always_on_top", True):
                self.cfg["always_on_top"] = new_aot
                _flags = Qt.FramelessWindowHint | Qt.Tool
                if new_aot:
                    _flags |= Qt.WindowStaysOnTopHint
                self.setWindowFlags(_flags)
                self.show()      # mandatory after setWindowFlags
                # keep the guard in step with the switch
                (self.t_topmost.start(21000) if new_aot
                 else self.t_topmost.stop())
            # start with Windows: the registry is the source of truth, so write only
            # on a real change (avoids re-stamping the Run key with this run's path
            # every Save — harmless, but pointless churn).
            want_startup = (start_state["val"] == "on")
            if want_startup != ts.startup_enabled():
                ts.set_startup(want_startup)
            self.cfg["calibration"] = new
            # pitstop launch options + nudge point live OUTSIDE Pitwall's config (the CLI
            # toolchain reads them from ~/.claude/pitstop) — write them on Save too.
            if ps_cfg is not None:
                ts.save_pitstop_config(ps_states["rc"]["val"] == "on",
                                       ps_states["auto"]["val"] == "on",
                                       ps_states["full_auto"]["val"] == "on")
                if ps_thresh is not None:
                    ts.save_pitstop_threshold(ps_thresh)
            ts.save_config(self.cfg)
            # reflect header rename + re-render everything behind the dialog now,
            # so the card is already current when the dialog dismisses
            self.brand.setText(self.cfg["name"])
            self.tag.setText(self.cfg["tagline"])
            self.rotate_tip()   # tips toggled off? blank the stale tip NOW so the
                                # refresh below shrinks the window on this same Save
            self.refresh()
            # Theme changed the concrete light/dark? Rebuild the whole card now (no live
            # restyle path) — the rebuild supersedes the green-flash/close below.
            if ts.resolve_mode(self.cfg) != self._applied_theme:
                QTimer.singleShot(0, self._recreate_for_theme)
                return
            # confirm in place, then close after a beat (Sarah's ruling): a green
            # "Saved ✓" in the shared footer status slot — green overwrites any
            # prior red error in the same label, so the two valences can't collide.
            msg.setStyleSheet(f"color:{ts.GREEN};")
            msg.setText("Saved ✓")
            b_save.setEnabled(False)       # no double-fire during the hold window
            b_clear.setEnabled(False)
            QTimer.singleShot(700, dlg.close)

        def clear():
            self.cfg["calibration"] = None
            self.cfg["use_calibrated_ceiling"] = False
            ts.save_config(self.cfg)
            self.refresh()
            dlg.close()

        b_save.clicked.connect(apply)
        b_clear.clicked.connect(clear)
        b_cancel.clicked.connect(dlg.close)

        # --- live theme flip (owner, 2026-06-11): clicking a THEME chip applies it
        # NOW — persist the choice, rebuild the card (there is no per-widget restyle
        # path), and reopen this dialog restyled with every unsaved edit carried
        # over. Only the theme is saved at this point; everything else still waits
        # for Save/Cancel as before.
        def _settings_stash():
            return {
                "pane": getattr(dlg, "_ts_pane", "identity"),
                "geo": (dlg.x(), dlg.y(), dlg.width(), dlg.height()),
                "level": dlg._ts_level,
                "edits": {k: e.text() for k, e in edits.items()},
                "plan": plan_state["val"],
                "switches": dict(
                    [("aot", aot_state["val"]), ("start", start_state["val"]),
                     ("auto", astate["val"]),
                     ("nudge", nstate["val"]), ("tips", tips_state["val"])]
                    + [("ps_" + k, s["val"]) for k, s in ps_states.items()]),
            }

        def _live_theme(v):
            self.cfg["theme"] = v
            ts.save_config(self.cfg)
            if ts.resolve_mode(self.cfg) == self._applied_theme:
                return        # same concrete look (e.g. system→dark on a dark OS)
            stash = _settings_stash()
            QTimer.singleShot(
                0, lambda: self._recreate_for_theme(reopen_settings=stash))

        for _tv, _tb in theme_state["chips"].items():
            _tb.clicked.connect(lambda _=False, v=_tv: _live_theme(v))

        # Return = save, Esc = close (Esc is QDialog's default reject)
        b_save.setDefault(True)
        b_save.setAutoDefault(True)

        def closed(e):
            self._settings = None
            QDialog.closeEvent(dlg, e)
        dlg.closeEvent = closed

        # Esc fires QDialog.reject(), which HIDES the dialog WITHOUT a closeEvent —
        # without this hook the guard above kept pointing at an invisible dialog and
        # the gear could never reopen Settings (owner bug report, 2026-06-11).
        def _rejected():
            self._settings = None
            dlg.deleteLater()
        dlg.rejected.connect(_rejected)

        if restore:
            # A live theme flip rebuilt the card — put everything back the way the
            # user had it, restyled. Switches restore silently (signals blocked) so
            # the "On — …" ack flashes don't fire on what isn't a user toggle.
            for k, v in restore["edits"].items():
                if k in edits:
                    edits[k].setText(v)
            plan_state["select"](restore["plan"])
            _sw = {"aot": aot_state, "start": start_state, "auto": astate,
                   "nudge": nstate, "tips": tips_state}
            _sw.update({"ps_" + k: s for k, s in ps_states.items()})
            for k, v in restore["switches"].items():
                s = _sw.get(k)
                if s and s["sw"].isEnabled():   # env-blocked nudge switch stays put
                    s["sw"].blockSignals(True)
                    s["sw"].setChecked(v == "on")
                    s["sw"].blockSignals(False)
                    s["sw"].update()
                    s["val"] = v
            if restore["level"] != dlg._ts_level:
                dlg._ts_level = restore["level"]
                self._rescale_dialog(dlg)
            _rx, _ry, _rw, _rh = restore["geo"]
            dlg.move(_rx, _ry)
            dlg.resize(_rw, _rh)
            select_pane(restore["pane"])
            if not defer_show:
                dlg.show()    # _place_beside (skipped here) is what normally shows
            # defer_show: _recreate_for_theme shows us together with the new card
        else:
            self._place_beside(dlg)
            if scroll_to_nudge:
                self._scroll_to_nudge()
            else:
                # Open on Identity (owner punch list 2026-06-11 — supersedes the old
                # "Accuracy + % cursor" muscle memory). No field gets focus, so a stray
                # keystroke can't silently edit the name.
                select_pane("identity")

    def _scroll_to_nudge(self):
        """Switch the open Settings dialog to the Attention pane and scroll to the
        SAVE NUDGES switch (§10.4)."""
        try:
            sel = getattr(self._settings, "_select_pane", None)
            if sel:
                sel("attention")
            if self._settings_area and self._nudge_anchor:
                self._settings_area.ensureWidgetVisible(self._nudge_anchor)
        except RuntimeError:
            pass

    # === window sizing / placement / drag ==================================
    # === "Nudge me" shoulder-tap (Mode 2, DESIGN_NOTES §10) =================
    # A SEPARATE top-level surface docked 8px under the card (never inside it, so it
    # can't disturb the protected session gutter). ACCENT keyline, never the heat ramp
    # — it's a save TIP, not a spend alarm. It shows the engine's headline/detail and
    # the two commands to type as COPYABLE text; it never runs anything (constraint #1).
    TAP_GAP = 8                 # px between the card's ring and the tap
    TAP_MIN_BASE_W = 260        # min tap width at 100% so the command lines don't wrap

    def _build_tap(self):
        """Build the docked tap surface once (lazily, on first tap). Parented to the
        card so it's cleaned up with the window and never keeps the app alive on its
        own; the Tool window flags keep it a separate, independently-positioned surface."""
        tap = QWidget(self)
        tap.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        tap.setAttribute(Qt.WA_TranslucentBackground)
        # same inactive-window tooltip suppression as the card (✕ / snooze tips)
        tap.setAttribute(Qt.WA_AlwaysShowToolTips)
        tlay = QVBoxLayout(tap)
        tlay.setContentsMargins(0, 0, 0, 0)

        frame = QFrame(tap)
        frame.setObjectName("tap")
        frame.setStyleSheet(
            f"#tap{{background:{ts.BG}; border:1px solid {ts.EDGE}; "
            f"border-radius:8px;}}")
        tlay.addWidget(frame)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(14, 11, 14, 12)
        lay.setSpacing(0)

        # 1) tip glyph + headline + dismiss
        row = QHBoxLayout(); row.setSpacing(6)
        glyph = self._mk("◆", 10, ts.INK, semibold=True)
        row.addWidget(glyph)
        self.tap_headline = self._mk("", 9, ts.INK, semibold=True)
        row.addWidget(self.tap_headline)
        row.addStretch(1)
        x = self._mk("✕", 9, ts.MUT)
        self._ctl(x, self._tap_dismiss); self._hover(x, ts.INK, ts.MUT)
        x.setToolTip(_tip("Hide this for 10 minutes."))
        row.addWidget(x)
        lay.addLayout(row)

        # 2) the load-bearing detail sentence (carries the ctx number in prose)
        lay.addSpacing(5)
        self.tap_detail = self._mk("", 8, ts.MUT, cls=QLabel)
        self.tap_detail.setWordWrap(True)
        self._wrap.append(self.tap_detail)
        lay.addWidget(self.tap_detail)

        # 3) caption + the two copyable commands ("type these — not run from here")
        lay.addSpacing(8)
        cap = self._mk("type these at your terminal — they're not run from here",
                       7, ts.FAINT)
        cap.setWordWrap(True); self._wrap.append(cap)
        lay.addWidget(cap)
        lay.addSpacing(2)
        cmdblock = QFrame(frame)
        cmdblock.setObjectName("cmdblock")
        cmdblock.setStyleSheet(
            f"#cmdblock{{background:{ts.PANEL}; border-radius:6px;}}")
        cb = QVBoxLayout(cmdblock)
        cb.setContentsMargins(9, 6, 9, 6); cb.setSpacing(3)
        self._tap_copy_labels = []
        for cmd in ("/pitstop", "/clear"):
            cr = QHBoxLayout(); cr.setSpacing(6)
            cr.addWidget(self._mk("$", 9, ts.FAINT, mono=True))
            cr.addWidget(self._mk(cmd, 9, ts.INK, mono=True))
            cr.addStretch(1)
            cp = self._mk("⧉ copy", 8, ts.MUT)
            self._ctl(cp, lambda c=cmd, l=None: self._tap_copy(c))
            self._hover(cp, ts.INK, ts.MUT)
            cp._ts_cmd = cmd
            self._tap_copy_labels.append(cp)
            cr.addWidget(cp)
            cb.addLayout(cr)
        lay.addWidget(cmdblock)

        # 4) footer: Snooze: 5m 15m 30m 1h ............ Don't show these
        # One-click momentary choices (owner, 2026-06-12) — this row quiets the
        # POPUP only; the header bell is the wider mute (popup + phone pushes).
        lay.addSpacing(9)
        foot = QHBoxLayout(); foot.setSpacing(6)
        self.tap_snooze_caption = self._mk("Snooze:", 8, ts.FAINT)
        foot.addWidget(self.tap_snooze_caption)
        self._tap_snooze_btns = []
        for secs, label in ts.NUDGE_SNOOZE_CHOICES:
            b = self._mk(label, 8, ts.MUT)
            self._ctl(b, lambda s=secs, t=label: self._tap_snooze_click(s, t))
            self._hover(b, ts.INK, ts.MUT)
            b.setToolTip(_tip("Hide this pop-up for "
                              + label.replace("m", " minutes").replace("1h", "1 hour")
                              + ", then show it again if a fresh start would still "
                              "save money."))
            foot.addWidget(b)
            self._tap_snooze_btns.append(b)
        foot.addStretch(1)
        dont = self._mk("Don't show these", 8, ts.FAINT)
        self._ctl(dont, self._tap_dont_show); self._hover(dont, ts.MUT, ts.FAINT)
        foot.addWidget(dont)
        lay.addLayout(foot)

        self._tap = tap
        return tap

    def _tap_width(self):
        return max(self.width(), round(self.TAP_MIN_BASE_W * self._scale()))

    def _reposition_tap(self):
        """Glue the tap to the card, left-aligned, 8px gap. Docks UNDER the card by
        default; if the card sits too low for the tap to fit below (it would run off
        the screen / be clamped back up over the card's session gutter), dock it ABOVE
        the card instead — same left edge. Recomputed on every card move, never latched
        (§10.1b: the tap must never overlap the card)."""
        if not self._tap or not self._tap_shown:
            return
        self._tap.setFixedWidth(self._tap_width())
        self._tap.layout().activate()
        self._tap.adjustSize()
        g = self.frameGeometry()
        tap_h = self._tap.frameGeometry().height()
        scr = self._screen_geo()
        # dock to the VISIBLE card edge (HALO_PX inside the window frame — §2.4 knock-on)
        card_bottom = g.bottom() - HALO_PX
        card_top    = g.top()    + HALO_PX
        if card_bottom + self.TAP_GAP + tap_h <= scr.bottom():
            y = card_bottom + self.TAP_GAP
        else:
            y = card_top - self.TAP_GAP - tap_h
        self._tap.move(g.left() + HALO_PX, y)

    def _update_nudge(self):
        """Ask the engine whether to tap about the current session, and show / update /
        hide the docked tap to match. The engine owns ALL timing (armed, tier,
        break-even, snooze); the face only reflects its verdict. The bell's momentary
        mute overrides everything — while it's on, the tap stays hidden too."""
        try:
            quiet, qlabel = ts.push_quiet_state()
        except Exception:
            quiet, qlabel = False, ""
        self._set_bell(quiet, qlabel)
        try:
            payload = ts.nudge_decision(self._cur_session or {}, self.cfg)
        except Exception:
            payload = None
        if quiet:
            payload = None
        if payload is None:
            if self._tap_shown:
                self._hide_tap(animate=True)   # condition cleared → it leaves on its own
            return
        if self._tap is None:
            self._build_tap()
        self.tap_headline.setText(payload["headline"])
        self.tap_detail.setText(payload["detail"])
        if not self._tap_shown:
            self._show_tap()
        else:
            self._reposition_tap()

    def _show_tap(self):
        self._tap_shown = True
        self._reset_copy_labels()
        self.tap_snooze_caption.setText("Snooze:")
        for b in self._tap_snooze_btns:
            b.show()
        self._reposition_tap()
        # one-time gentle slide-up + fade-in (160ms ease-out); never repeats.
        g = self._tap.frameGeometry()
        end = QPoint(g.left(), g.top())
        start = QPoint(g.left(), g.top() + 10)
        self._tap.move(start)
        self._tap.setWindowOpacity(0.0)
        self._tap.show()
        self._tap.raise_()
        self._tap_anim = self._slide(self._tap, start, end, 0.0, 1.0, 160,
                                     QEasingCurve.OutCubic)

    def _hide_tap(self, animate=True):
        if not self._tap or not self._tap_shown:
            return
        self._tap_shown = False
        if not animate:
            self._tap.hide()
            return
        g = self._tap.frameGeometry()
        start = QPoint(g.left(), g.top())
        end = QPoint(g.left(), g.top() + 10)
        self._tap_anim = self._slide(self._tap, start, end, 1.0, 0.0, 140,
                                     QEasingCurve.InCubic, on_done=self._tap.hide)

    def _slide(self, w, p0, p1, o0, o1, ms, curve, on_done=None):
        grp = QParallelAnimationGroup(w)
        a = QPropertyAnimation(w, b"pos"); a.setDuration(ms)
        a.setStartValue(p0); a.setEndValue(p1); a.setEasingCurve(curve)
        b = QPropertyAnimation(w, b"windowOpacity"); b.setDuration(ms)
        b.setStartValue(o0); b.setEndValue(o1)
        grp.addAnimation(a); grp.addAnimation(b)
        if on_done:
            grp.finished.connect(on_done)
        grp.start()
        return grp

    def _reset_copy_labels(self):
        for cp in getattr(self, "_tap_copy_labels", []):
            cp.setText("⧉ copy")
            cp.setStyleSheet(f"color:{ts.MUT};")

    def _tap_copy(self, cmd):
        QApplication.clipboard().setText(cmd)   # copy ONLY — never executes (constraint #1)
        for cp in self._tap_copy_labels:
            if getattr(cp, "_ts_cmd", None) == cmd:
                cp.setText("copied ✓")
                cp.setStyleSheet(f"color:{ts.GREEN};")
                QTimer.singleShot(1200, lambda l=cp: (
                    l.setText("⧉ copy"), l.setStyleSheet(f"color:{ts.MUT};")))

    def _tap_dismiss(self):
        """✕ = a real 'not now': quiet for 10 minutes. (Before 2026-06-12 this only
        hid the popup until the next 1-second re-check put it straight back — the
        'blasted with notifications' video.)"""
        ts.nudge_snooze(self.cfg, seconds=ts.NUDGE_DISMISS_SECONDS)
        ts.save_config(self.cfg)
        self._hide_tap(animate=True)

    def _tap_snooze_click(self, seconds, label):
        """One-click momentary snooze (5m/15m/30m/1h): quiet the POPUP for that long;
        phone pushes are the bell's lane (deliberate split — predictable controls).
        Brief inline confirm, persist the window, then slide away."""
        ts.nudge_snooze(self.cfg, seconds=seconds)
        ts.save_config(self.cfg)
        for b in self._tap_snooze_btns:
            b.hide()
        self.tap_snooze_caption.setText("snoozed — back in " + label)
        QTimer.singleShot(900, lambda: self._hide_tap(animate=True))

    def _tap_dont_show(self):
        """Never disarms in place — opens Settings at the toggle so disarming is
        deliberate and visible (one source of truth for armed-state, §10.4)."""
        self._hide_tap(animate=False)
        self.open_settings(scroll_to_nudge=True)

    # === the bell: momentary mute (owner, 2026-06-12) ========================

    def _set_bell(self, active, label):
        """Reflect the mute state on the header bell: 🔔 when live, 🔕 + the
        plain-English remainder while muted. Called every nudge tick so the
        countdown stays current; only touches the widget when the text changes."""
        if not hasattr(self, "bell"):
            return
        if active:
            txt = "🔕 " + ("next pitstop" if label.startswith("until")
                           else label.replace(" left", ""))
            tip = ("Notifications are muted (" + label + "). The fresh-start "
                   "pop-up and the pitstop phone pushes are both quiet; they "
                   "turn themselves back on. Click to unmute now or pick a "
                   "different time.")
        else:
            txt = "🔔"
            tip = ("Mute notifications for a little while — the fresh-start "
                   "pop-up and the pitstop phone pushes both go quiet, then "
                   "turn themselves back on. Click for choices.")
        if self.bell.text() != txt:
            self.bell.setText(txt)
        self.bell.setToolTip(_tip(tip))

    def _bell_menu(self):
        """The bell's picker. Muted: lead with 'Unmute now'; either way offer the
        short quiet windows plus 'until next pitstop' (the handoff confirm step
        clears that one, so it really ends at the next pitstop)."""
        m = QMenu(self)
        m.setStyleSheet(
            f"QMenu{{background:{ts.PANEL}; color:{ts.INK}; "
            f"border:1px solid {ts.EDGE}; border-radius:6px; padding:4px;}}"
            f"QMenu::item{{padding:4px 14px; background:transparent;}}"
            f"QMenu::item:selected{{background:{P.PANEL_HI}; color:{ts.INK};}}"
            f"QMenu::item:disabled{{color:{ts.FAINT};}}"
            f"QMenu::separator{{height:1px; background:{ts.EDGE}; margin:4px 8px;}}")
        try:
            quiet, qlabel = ts.push_quiet_state()
        except Exception:
            quiet, qlabel = False, ""
        if quiet:
            hdr = m.addAction("Muted — " + qlabel)
            hdr.setEnabled(False)
            m.addAction("Unmute now", lambda: self._bell_pick())
            m.addSeparator()
        # say WHAT the choices mute — the pop-up AND the phone pushes (owner's
        # eyeball, 2026-06-12; the tooltip said it, the menu itself didn't)
        what = m.addAction("Mute the pop-up and phone pushes for:")
        what.setEnabled(False)
        for secs, label in ts.NUDGE_SNOOZE_CHOICES:
            m.addAction("Quiet " + label.replace("m", " min").replace("1h", "1 hour"),
                        lambda s=secs: self._bell_pick(seconds=s))
        m.addAction("Quiet until next pitstop",
                    lambda: self._bell_pick(until_pitstop=True))
        m.exec(QCursor.pos())

    def _bell_pick(self, seconds=None, until_pitstop=False):
        """Write the pick (only the push block's two quiet keys change — never the
        launch switches) and reflect it immediately."""
        try:
            ts.set_push_quiet(seconds=seconds, until_pitstop=until_pitstop)
        except Exception:
            return
        self._update_nudge()

    def paintEvent(self, e):
        # Window-level paint (O3 §3.2): neutral shadow → flat card body → 1px heat ring.
        # Prevents the "white flash" on resize; ring QFrame is transparent.
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r_card = QRectF(self.rect().adjusted(HALO_PX, HALO_PX, -HALO_PX, -HALO_PX))

        # 1. Window shadow — static neutral black, concentric strokes in the apron.
        # Not heat-coloured, not animated (O3 §3.2). S = strength constant.
        S = 0.22 if self._applied_theme == "dark" else 0.12
        p.setBrush(Qt.NoBrush)
        for i in range(1, 7):
            a = S * ((1 - i / 7) ** 2)
            c = QColor(0, 0, 0)
            c.setAlphaF(a)
            p.setPen(QPen(c, 2))
            adj = r_card.adjusted(-i, -i, i, i)
            p.drawRoundedRect(adj, CARD_RADIUS + i, CARD_RADIUS + i)

        # 2. Card body: flat fill
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(ts.BG))
        p.drawRoundedRect(r_card, CARD_RADIUS, CARD_RADIUS)

        # 3. Heat ring: heat-coloured stroke centred on the card edge, pulsing (RING_W px;
        #    3px restored 2026-06-12 — see RING_W). Centred on r_card so half the weight
        #    sits over the shadow apron and half over the card, reading as a frame hugging
        #    the card. (Was a 0.5px-inset hairline rect when RING_W was 1px.)
        ring_rect = r_card
        ring_pen = QPen(QColor(self._ring_col), RING_W)
        ring_pen.setCosmetic(True)
        p.setBrush(Qt.NoBrush)
        p.setPen(ring_pen)
        p.drawRoundedRect(ring_rect, CARD_RADIUS, CARD_RADIUS)

        # 4. Grip hover: re-stroke the hairline with a whitened color in the grab band
        if self._hover_edge:
            r = self.rect()
            band = QRect(r)
            if self._hover_edge == "left":
                band.setWidth(HALO_PX + LEFT_RESIZE_MARGIN)
            else:
                band.setLeft(r.right() - (HALO_PX + RESIZE_MARGIN))
            p.save()
            p.setClipRect(band)
            p.setPen(QPen(QColor(whiten(self._ring_col, 0.22)), RING_W))
            p.drawRoundedRect(ring_rect, CARD_RADIUS, CARD_RADIUS)
            p.restore()

    def resizeEvent(self, e):
        # On the rare genuine resize (session count changed, A−/A+, drag) repaint
        # SYNCHRONOUSLY. update() only POSTS a paint for the next event-loop pass, so
        # the compositor can present the newly-exposed (transparent) strip of this
        # translucent window for one frame first -> desktop bleed. repaint() fills the
        # opaque backdrop before we return, closing that gap. Steady refreshes don't
        # resize (rows update in place), so this almost never fires.
        super().resizeEvent(e)
        self.repaint()
        self._reposition_tap()

    def moveEvent(self, e):
        super().moveEvent(e)
        self._reposition_tap()   # keep the shoulder-tap docked under the card on drag

    def _grip_edge(self, x):
        """Which width-resize edge (if any) is local x over? BOTH edges are grabbable in
        BOTH views (owner, 2026-06-06): a left-edge drag pins the right edge, a right-edge
        drag pins the left, so you can pull either side to widen or narrow this view."""
        if x <= LEFT_RESIZE_MARGIN:
            return "left"
        if x >= self.width() - RESIZE_MARGIN:
            return "right"
        return None

    def _min_expanded_w(self):
        """Smallest expanded card width that still fits the FIXED session gutter
        (state·tokens·$) plus the dot, a minimal elided name and the model chip — so the
        $ column can never be clipped off the card edge. The name self-elides and the chip
        is short, but the gutter is fixed-width, so the card simply must not go narrower
        than this. The OTHER half of the permanent overlap fix: the eliding name stops the
        NAME overrunning; this floor stops the fixed GUTTER overrunning. Scales with the
        text ladder, and mirrors the column metrics in _set_sessions."""
        sc = self._scale()
        gutter = round(48 * sc) + 2 * round(54 * sc)   # state · tokens · $ (= sw + tw + tw)
        dot = round(14 * sc)
        name_min = round(56 * sc)                       # a short name + the "…"
        chip = round(52 * sc)                           # widest model label (e.g. "Sonnet")
        spacing = round(30 * sc)                         # 6 inter-widget gaps of 5px
        chrome = 2 * HALO_PX + 36                        # halo 10+10, card margins 18+18
        return chrome + dot + name_min + chip + spacing + gutter

    def _clamp_view_width(self, w):
        """Clamp a dragged width to the ACTIVE view's bounds: the collapsed glance-chip
        range, or the expanded card's minimum .. screen width."""
        if self.collapsed:
            return self._clamp_collapsed_w(w)
        scr = self._screen_geo()
        return max(self._min_expanded_w(), min(int(w), scr.width() - 16))

    def _strip_min_content_w(self):
        """Smallest collapsed width whose top line cannot clip the countdown. The §4.2
        pct cluster ('5h 38% · wk 21%') made the flat COLLAPSED_MIN_W floor too small —
        a strip width saved before the cluster existed would squeeze the countdown
        label (the one widget without a fixed width). Measured against the WORST case
        (both pct pairs visible) so the floor never moves at runtime."""
        fm15 = QFontMetrics(self._font(15, mono=True, bold=True))
        fm10 = QFontMetrics(self._font(10))
        fm11 = QFontMetrics(self._font(11))
        fm9m = QFontMetrics(self._font(9, mono=True))
        fm9 = QFontMetrics(self._font(9))
        pct_w = fm9m.horizontalAdvance("100%") + 2
        need = (fm10.horizontalAdvance("●") + 6                     # dot
                + fm15.horizontalAdvance("0:00:00") + 8             # countdown
                + fm9.horizontalAdvance("·") + 6                    # sep
                + fm9m.horizontalAdvance("000.00M") + 2 + 6         # tokens
                + fm9.horizontalAdvance("·") + 6                    # sep2
                + fm9m.horizontalAdvance("$000.00") + 2             # dollars
                + fm9.horizontalAdvance("5h ") + pct_w              # 5h pair
                + fm9.horizontalAdvance(" · wk ") + pct_w           # wk pair
                + 10 + fm10.horizontalAdvance("▸")                  # re-expand
                + 8 + fm11.horizontalAdvance("✕"))                  # quit
        return 2 * HALO_PX + 36 + need      # halo 10+10, card h-margins 18+18

    def _clamp_collapsed_w(self, w):
        """Clamp a collapsed-strip width to Sarah's glance-chip bounds (scaled with the
        text ladder) and the screen, so it can't shrink under the countdown or stretch
        into a ragged gutter."""
        scr = self._screen_geo()
        lo = max(round(COLLAPSED_MIN_W * self._scale()),
                 self._strip_min_content_w())
        hi = min(round(COLLAPSED_MAX_W * self._scale()), scr.width() - 16)
        return max(lo, min(int(w), max(lo, hi)))

    def _fit_height(self):
        """Resize to the layout's exact height for the CURRENT fixed width.
        sizeHint()/adjustSize() are the wrong tool here: the card holds
        word-wrapped labels (proj/weekly), and a wrapped QLabel's sizeHint
        depends on its last layout pass — so the window's hint drifts from its
        true height, a bare adjustSize() snaps to the stale value, and the next
        refresh snaps it back (the edge-drag jump, owner eyeball 2026-06-11).
        totalHeightForWidth(w) is deterministic for a given width + content."""
        lay = self.layout()
        lay.activate()
        w = self.width()
        h = (lay.totalHeightForWidth(w) if lay.hasHeightForWidth()
             else lay.totalSizeHint().height())
        if h > 0 and h != self.height():
            self.resize(w, h)

    def _relayout(self):
        if self.collapsed:
            # Width is USER-owned — never shrink-to-fit (that was the wide->narrow flash,
            # #4). First run with no saved strip width: fit content ONCE, then own it.
            if self._cw is None:
                self.setMinimumWidth(0)
                self.setMaximumWidth(16777215)
                self.layout().activate()
                self.adjustSize()
                self._cw = self._clamp_collapsed_w(self.width())
                self.cfg["collapsed_w"] = self._cw
                ts.save_config(self.cfg)
            w = self._clamp_collapsed_w(self._cw)
            if self.maximumWidth() != w or self.minimumWidth() != w:
                self.setFixedWidth(w)
            # height still auto-fits the strip content (width-only resize)
            self._fit_height()
        else:
            scr = self._screen_geo()
            w = max(self._min_expanded_w(), min(self._w, scr.width() - 16))
            if self.maximumWidth() != w or self.minimumWidth() != w:
                self.setFixedWidth(w)
            self._apply_tip_visibility()   # show/hide tip row before measuring height
            self._lock_tip_height(w)       # size the tip box BEFORE the height auto-fit
            # Only resize when the height actually needs to change. A steady refresh
            # (same rows, just new numbers) keeps the same size, so we DON'T resize —
            # which is what was repainting/flashing every cycle.
            self._fit_height()
        self._reposition_tap()

    def _place(self):
        scr = self._screen_geo()
        x, y = self.cfg.get("x"), self.cfg.get("y")
        if x is None or y is None:
            # align the VISIBLE card edge (HALO_PX inside the window rect) — §2.4 knock-on
            x = scr.right() - self.width() + HALO_PX - 24
            y = scr.bottom() - self.height() + HALO_PX - 70
        self.move(int(x), int(y))

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        edge = self._grip_edge(e.position().x())
        if edge:
            self._resizing = True
            self._resize_edge = edge
            self._rx = e.globalPosition().x()
            self._rw = self.width()
            self._right_anchor = self.frameGeometry().right()  # pin point for a left-edge drag
        else:
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._resizing and (e.buttons() & Qt.LeftButton):
            dx = e.globalPosition().x() - self._rx
            # LEFT-edge drag pulls left (dx<0) to widen and pins the RIGHT edge; RIGHT-edge
            # drag pushes right (dx>0) to widen and pins the LEFT edge. Both views, both edges.
            raw = self._rw - dx if self._resize_edge == "left" else self._rw + dx
            neww = self._clamp_view_width(int(raw))
            if self.collapsed:
                self._cw, self._cw_auto = neww, False
            else:
                self._w, self._w_auto = neww, False
                self._lock_tip_height(neww)      # re-fit the tip box to the new width
            self.setFixedWidth(neww)
            if self._resize_edge == "left":
                self.move(self._right_anchor - neww + 1, self.y())  # keep the right edge fixed
            self._fit_height()
        elif self._drag is not None and (e.buttons() & Qt.LeftButton):
            self.move(e.globalPosition().toPoint() - self._drag)
        else:
            self._update_grip_hover(e.position().x())
            self._update_row_hover(e.globalPosition().toPoint())

    def _update_row_hover(self, gpos):
        """Reveal the ↻ overlay on the OPEN session row under the pointer (and hide it
        on every other row). Driven from the same move-event funnel as the resize grip,
        so the row's opaque child widgets can't swallow the hover. Instant, no fade."""
        if self._resizing:
            return
        hovered = None
        for h in self._sess_pool:
            if (h.isVisible() and h._session and h._session.get("open")
                    and h.rect().contains(h.mapFromGlobal(gpos))):
                hovered = h
                break
        if hovered is self._hover_row:
            return
        if self._hover_row is not None:
            try:
                self._hover_row._restart.setVisible(False)
            except RuntimeError:
                pass                                  # row widget already gone
        self._hover_row = hovered
        if hovered is not None:
            self._position_restart(hovered)
            hovered._restart.setVisible(True)
            hovered._restart.raise_()

    def _update_grip_hover(self, x):
        """Show the ↔ cursor and brighten the hovered grab edge. Driven from BOTH the
        window's own move events AND child widgets' (via eventFilter): the opaque ring/card
        children cover the edge band, so a plain hover otherwise never reaches the window and
        the grip looked undraggable even though it worked (owner, 2026-06-06)."""
        if self._resizing:
            return
        edge = self._grip_edge(x)
        self.setCursor(QCursor(Qt.SizeHorCursor if edge else Qt.ArrowCursor))
        if edge != self._hover_edge:             # repaint so the grab edge brightens/dims
            self._hover_edge = edge
            self.update()

    def _install_edge_tracking(self):
        """The resize edges sit under opaque child widgets (the ring/card fill the whole
        window rect). Children don't report plain hovers, so enable mouse tracking on every
        descendant and funnel their move events to one handler — without this the ↔ cursor
        never appears, which read as 'can't drag' (owner, 2026-06-06). Idempotent so it can
        re-run after a refresh appends new session rows (each widget filtered exactly once)."""
        for w in self.findChildren(QWidget):
            if w.property("_ts_edge_tracked"):
                continue
            w.setMouseTracking(True)
            w.installEventFilter(self)
            w.setProperty("_ts_edge_tracked", True)

    def eventFilter(self, obj, ev):
        t = ev.type()
        # custom opaque hint on the pitstop / seam pills (not QToolTip) — folded in
        # here because this is the LIVE eventFilter; a second def shadows any other.
        if obj is self.ps_pill or obj is self.seam_pill:
            if t == QEvent.Enter:
                if obj.isVisible():
                    self._show_hint(obj, getattr(obj, "_hint_text", ""))
            elif t in (QEvent.Leave, QEvent.Hide):
                self._hide_hint()
        elif t == QEvent.ToolTip:
            # Every OTHER card tooltip: swallow Qt's native tooltip (its floating window
            # composites to BLACK and ignores bg styling — wrong in the light theme; owner
            # cli details4.mp4) and show the same opaque light hint instead, beside the card.
            tip = obj.toolTip()
            if tip:
                self._show_hint(obj, tip, beside=True)
                return True
        elif t in (QEvent.Leave, QEvent.Hide) and obj is self._hint_anchor:
            self._hide_hint()
        if t == QEvent.MouseMove and not (ev.buttons() & Qt.LeftButton):
            gpos = ev.globalPosition().toPoint()
            self._update_grip_hover(self.mapFromGlobal(gpos).x())
            self._update_row_hover(gpos)
        return super().eventFilter(obj, ev)

    def leaveEvent(self, e):
        if self._hover_edge is not None:         # drop the grip brighten when the pointer leaves
            self._hover_edge = None
            self.update()
        # Drop the ↻ overlay only when the pointer TRULY left the window — entering a
        # child row also fires leaveEvent on the parent, and we must not hide it then.
        if self._hover_row is not None and not self.geometry().contains(QCursor.pos()):
            try:
                self._hover_row._restart.setVisible(False)
            except RuntimeError:
                pass
            self._hover_row = None
        if not self.geometry().contains(QCursor.pos()):   # belt-and-braces: kill any open hint
            self._hide_hint()
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._resizing:
            if self.collapsed:
                self.cfg["collapsed_w"] = self._cw
            else:
                self.cfg["w"] = self._w
            ts.save_config(self.cfg)
        elif self._drag is not None:
            self.cfg["x"], self.cfg["y"] = self.x(), self.y()
            ts.save_config(self.cfg)
        self._resizing = False
        self._resize_edge = None
        self._drag = None

    def closeEvent(self, e):
        ts.save_config(self.cfg)
        super().closeEvent(e)


def show_already_running():
    app = QApplication.instance() or QApplication(sys.argv)
    QMessageBox.information(
        None, "Pitwall",
        "Pitwall is already running.\n\n"
        "Look for the ⚖ widget — it may be hidden behind another window "
        "or sitting on your other screen.")


def main():
    # Demo construct: a fully isolated showcase (Settings → Diagnostics → Open demo).
    # Set the process-wide write-lock BEFORE anything loads so no code path can touch
    # the user's real config/corrections, and take a SEPARATE single-instance lock so
    # the demo coexists beside the real widget (and only one demo runs at a time).
    global _DEMO_MODE, _instance_lock
    _DEMO_MODE = "--demo-mode" in sys.argv
    if _DEMO_MODE:
        ts.DEMO_READONLY = True
    # Hold the lock in a module-level name so it lives for the whole process.
    _instance_lock = ts.acquire_single_instance_lock(
        ts.DEMO_INSTANCE_MUTEX if _DEMO_MODE else ts.SINGLE_INSTANCE_MUTEX)
    if _instance_lock is None:
        show_already_running()
        sys.exit(0)
    app = QApplication(sys.argv)
    # O3 §0.1: Segoe UI Variable is used for all text and figures — no bundled font
    # files to load. The Inter/IBMPlexMono files stay on disk (they belong to the kept
    # Apple skin) but are not loaded in this skin.
    # The card is a Qt.Tool window; Qt's default quitOnLastWindowClosed would treat
    # a child popup (Token Details / Settings) as the only "real" window and quit the
    # whole app when it closes. Turn that off — the ONLY quit path is the card's own
    # ✕ button, which calls QApplication.quit() explicitly.
    app.setQuitOnLastWindowClosed(False)
    # Held in a module global so a theme rebuild (_recreate_for_theme swaps in a fresh
    # StewardQt) keeps a live reference and the old one can be retired.
    global _card
    _card = StewardQt()
    _card.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
