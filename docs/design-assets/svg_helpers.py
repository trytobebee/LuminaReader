"""SVG drawing helpers + design tokens for design-doc skill.

USAGE in your make_figs.py:

    from svg_helpers import *

    def fig_my_diagram():
        W, H = 1500, 800
        out = [svg_header(W, H)]
        out.append(text(W/2, 50, '我的图标题', fs=22, weight='700',
                        color=FG, anchor='middle'))
        out.append(card(40, 100, W - 80, 200, *INDIGO))
        out.append(number_badge(82, 130, '1', INDIGO[0]))
        out.append(text(110, 130, '资源层', fs=17, weight='700', color=FG))
        out.append(svg_footer())
        Path(f'{OUT}/fig_my.svg').write_text(''.join(out))

CRITICAL RULES:
  - All text content goes through esc() to escape & < >  (done automatically by text/pill/badge)
  - Font family inside SVG style attribute uses SINGLE QUOTES around font names
  - One numbered badge per logical card / section
  - Soft pastel fills (token[2] very-light), thin colored borders (token[0]),
    subtle drop shadows via filter=url(#card-shadow)
"""
import os
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape


def esc(s):
    """Escape &, <, > for safe SVG/XML text content."""
    return xml_escape(str(s))


# ====================================================================
# Design tokens — each tuple is (main, light, very-light)
# Use these consistently. Don't introduce new colors without good reason.
# ====================================================================
INDIGO  = ('#4f46e5', '#e0e7ff', '#eef2ff')
CYAN    = ('#0891b2', '#cffafe', '#ecfeff')
EMERALD = ('#059669', '#d1fae5', '#ecfdf5')
AMBER   = ('#d97706', '#fef3c7', '#fffbeb')
BLUE    = ('#2563eb', '#dbeafe', '#eff6ff')
ROSE    = ('#e11d48', '#ffe4e6', '#fff1f2')
SLATE   = ('#475569', '#cbd5e1', '#f1f5f9')

FG       = '#0f172a'
FG_BODY  = '#334155'
FG_MUTED = '#64748b'
BG       = '#ffffff'

FONT_STACK = ("-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', "
              "'Hiragino Sans GB', 'Microsoft YaHei', sans-serif")


# ====================================================================
# SVG header / footer (with defs: shadow filter + 4 arrow markers)
# ====================================================================

def svg_header(w, h):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
  viewBox="0 0 {w} {h}" width="100%" style="font-family: {FONT_STACK}; max-width: {w}px;">
<defs>
  <filter id="card-shadow" x="-5%" y="-5%" width="110%" height="115%">
    <feGaussianBlur in="SourceAlpha" stdDeviation="2"/>
    <feOffset dx="0" dy="1" result="offsetblur"/>
    <feComponentTransfer><feFuncA type="linear" slope="0.08"/></feComponentTransfer>
    <feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
  <marker id="arrow-slate" viewBox="0 0 10 10" refX="8" refY="5"
          markerWidth="6" markerHeight="6" orient="auto">
    <path d="M 0 1 L 8 5 L 0 9 z" fill="{SLATE[0]}"/></marker>
  <marker id="arrow-slate-thin" viewBox="0 0 10 10" refX="8" refY="5"
          markerWidth="5" markerHeight="5" orient="auto">
    <path d="M 0 2 L 7 5 L 0 8 z" fill="{SLATE[1]}"/></marker>
  <marker id="arrow-indigo" viewBox="0 0 10 10" refX="8" refY="5"
          markerWidth="6" markerHeight="6" orient="auto">
    <path d="M 0 1 L 8 5 L 0 9 z" fill="{INDIGO[0]}"/></marker>
  <marker id="arrow-amber" viewBox="0 0 10 10" refX="8" refY="5"
          markerWidth="6" markerHeight="6" orient="auto">
    <path d="M 0 1 L 8 5 L 0 9 z" fill="{AMBER[0]}"/></marker>
  <marker id="arrow-emerald" viewBox="0 0 10 10" refX="8" refY="5"
          markerWidth="6" markerHeight="6" orient="auto">
    <path d="M 0 1 L 8 5 L 0 9 z" fill="{EMERALD[0]}"/></marker>
</defs>
<rect width="100%" height="100%" fill="white"/>
'''


def svg_footer():
    return '</svg>\n'


# ====================================================================
# Primitives
# ====================================================================

def card(x, y, w, h, color_main, color_light, color_vlight,
         radius=12, stroke_w=1, shadow=True):
    """A pastel-filled rounded rect with thin colored border + optional shadow."""
    f = ' filter="url(#card-shadow)"' if shadow else ''
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" '
            f'ry="{radius}" fill="{color_vlight}" stroke="{color_main}" '
            f'stroke-width="{stroke_w}"{f}/>\n')


def white_card(x, y, w, h, border_color, radius=10, stroke_w=1, shadow=True):
    """A clean white card with thin colored border (used inside soft-box sections)."""
    f = ' filter="url(#card-shadow)"' if shadow else ''
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" '
            f'ry="{radius}" fill="white" stroke="{border_color}" '
            f'stroke-width="{stroke_w}"{f}/>\n')


def accent_stripe(x, y, w, color_main, h=4):
    """A short colored stripe at the top of a card (visual anchor)."""
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="2" '
            f'ry="2" fill="{color_main}"/>\n')


def number_badge(cx, cy, n, color_main, r=14, fs=12):
    """A filled circle with white number — the canonical 'section' marker."""
    return (f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{color_main}"/>\n'
            f'<text x="{cx}" y="{cy}" text-anchor="middle" '
            f'dominant-baseline="central" font-size="{fs}" font-weight="700" '
            f'fill="white">{esc(n)}</text>\n')


def text(x, y, txt, fs=12, color=FG, weight='400', anchor='start',
         baseline='central', letter_spacing='normal'):
    """A text element — automatically XML-escaped."""
    return (f'<text x="{x}" y="{y}" font-size="{fs}" fill="{color}" '
            f'font-weight="{weight}" text-anchor="{anchor}" '
            f'dominant-baseline="{baseline}" letter-spacing="{letter_spacing}">'
            f'{esc(txt)}</text>\n')


def line_arrow(x1, y1, x2, y2, color, marker='arrow-slate', stroke_w=1.5,
               dasharray=None):
    """A line with arrowhead. Color matches one of the marker variants."""
    dash = f' stroke-dasharray="{dasharray}"' if dasharray else ''
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            f'stroke="{color}" stroke-width="{stroke_w}" '
            f'stroke-linecap="round"{dash} marker-end="url(#{marker})"/>\n')


def curved_arrow(x1, y1, x2, y2, control_offset_y, color,
                 marker='arrow-amber', stroke_w=2):
    """A quadratic Bezier curve with arrow at the end. Used for feedback loops."""
    cx = (x1 + x2) / 2
    cy = max(y1, y2) + control_offset_y
    return (f'<path d="M {x1} {y1} Q {cx} {cy} {x2} {y2}" stroke="{color}" '
            f'stroke-width="{stroke_w}" fill="none" stroke-linecap="round" '
            f'marker-end="url(#{marker})"/>\n')


def pill(x, y, w, h, txt, color_main, color_vlight, fs=11, weight='600'):
    """A pill-shaped label, used in footer theme rows."""
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{h/2}" '
            f'ry="{h/2}" fill="{color_vlight}" stroke="{color_main}" '
            f'stroke-width="1"/>\n'
            + text(x + w/2, y + h/2, txt, fs=fs, color=color_main,
                   weight=weight, anchor='middle'))


# ====================================================================
# Composite helpers
# ====================================================================

def section_header_in_fig(x, y, num, title_zh, title_en, token, num_r=15, fs=16):
    """Inside a figure: numbered badge + 中文标题 + 英文 subtitle (slate)."""
    out = number_badge(x + num_r + 5, y, num, token[0], r=num_r, fs=12)
    out += text(x + num_r * 2 + 18, y, title_zh, fs=fs, weight='700', color=FG)
    out += text(x + num_r * 2 + 18 + len(title_zh) * fs + 8, y + 1, title_en,
                fs=12, color=FG_MUTED, weight='500', letter_spacing='0.04em')
    return out


def footer_themes(out_list, themes, fig_w, y, total_w=None, h=38, fs=12):
    """Bottom row of theme pills (like image1's 'Data & Knowledge | ...' bar).

    themes: list of (label, token) tuples
    """
    n = len(themes)
    if total_w is None:
        total_w = fig_w * 0.9
    gap = 14
    pw = (total_w - (n - 1) * gap) / n
    x0 = (fig_w - total_w) / 2
    for i, (label, tok) in enumerate(themes):
        x = x0 + i * (pw + gap)
        out_list.append(pill(x, y, pw, h, label, tok[0], tok[2], fs=fs))
