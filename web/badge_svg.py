"""Renders a custom gym badge (designed in the web app's gym builder) as SVG.

A design is {shape, primary, secondary, emblem}: one of SHAPES, two
"#rrggbb" colors, and one of EMBLEMS. Everything is drawn from whitelisted
building blocks — no user text ever reaches the SVG — so it's safe to serve
as an image. The API validates designs on save; render() re-validates
anyway (defaulting anything unrecognized) so a bad value can never break or
inject into the output.
"""

import math
import re

SHAPES = ["circle", "hexagon", "octagon", "diamond", "shield", "star"]
EMBLEMS = ["star", "bolt", "flame", "drop", "leaf", "snowflake", "heart", "moon", "crown", "gem", "mountain", "wing"]
DEFAULT_DESIGN = {"shape": "hexagon", "primary": "#e03b4a", "secondary": "#ffcb05", "emblem": "star"}
HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def normalize(design: dict | None) -> dict:
    design = design or {}
    out = dict(DEFAULT_DESIGN)
    if design.get("shape") in SHAPES:
        out["shape"] = design["shape"]
    if design.get("emblem") in EMBLEMS:
        out["emblem"] = design["emblem"]
    for key in ("primary", "secondary"):
        value = design.get(key)
        if isinstance(value, str) and HEX_COLOR.match(value):
            out[key] = value.lower()
    return out


def _shade(hex_color: str, factor: float) -> str:
    """factor > 1 lightens toward white, < 1 darkens toward black."""
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    if factor >= 1:
        k = factor - 1
        r, g, b = (round(c + (255 - c) * k) for c in (r, g, b))
    else:
        r, g, b = (round(c * factor) for c in (r, g, b))
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, c)) for c in (r, g, b)))


def _polygon(n: int, radius: float, rotation_deg: float = -90, cx: float = 50, cy: float = 50) -> str:
    pts = []
    for i in range(n):
        a = math.radians(rotation_deg + i * 360 / n)
        pts.append(f"{cx + radius * math.cos(a):.2f},{cy + radius * math.sin(a):.2f}")
    return " ".join(pts)


def _star_points(points: int, outer: float, inner: float, cx: float = 50, cy: float = 50) -> str:
    pts = []
    for i in range(points * 2):
        r = outer if i % 2 == 0 else inner
        a = math.radians(-90 + i * 180 / points)
        pts.append(f"{cx + r * math.cos(a):.2f},{cy + r * math.sin(a):.2f}")
    return " ".join(pts)


def _shape_element(shape: str, scale: float, attrs: str) -> str:
    """The badge body outline at `scale` (1.0 = full size) around the center."""
    r = 46 * scale
    if shape == "circle":
        return f'<circle cx="50" cy="50" r="{r:.2f}" {attrs}/>'
    if shape == "hexagon":
        return f'<polygon points="{_polygon(6, r, -90)}" {attrs}/>'
    if shape == "octagon":
        return f'<polygon points="{_polygon(8, r, -67.5)}" {attrs}/>'
    if shape == "diamond":
        return f'<polygon points="{_polygon(4, r, -90)}" {attrs}/>'
    if shape == "star":
        return f'<polygon points="{_star_points(8, r, r * 0.8)}" {attrs}/>'
    # shield
    s = scale
    t = lambda x, y: f"{50 + (x - 50) * s:.2f} {50 + (y - 50) * s:.2f}"
    d = (f"M{t(50, 5)} L{t(88, 16)} L{t(88, 46)} C{t(88, 70)} {t(70, 86)} {t(50, 96)} "
         f"C{t(30, 86)} {t(12, 70)} {t(12, 46)} L{t(12, 16)} Z")
    return f'<path d="{d}" {attrs}/>'


# Emblems drawn in a 100x100 box, roughly within 30..70.
_EMBLEMS = {
    "star": f'<polygon points="{_star_points(5, 21, 9)}"/>',
    "bolt": '<polygon points="55,27 37,53 48,53 43,73 63,45 52,45 58,27"/>',
    "flame": '<path d="M50 27 C58 37 66 44 64 56 C62 66 56 72 50 72 C42 72 36 66 36 57 C36 50 40 46 43 42 C43 48 46 51 49 51 C47 43 48 35 50 27 Z"/>',
    "drop": '<path d="M50 26 C57 37 65 46 65 56 C65 65 58 72 50 72 C42 72 35 65 35 56 C35 46 43 37 50 26 Z"/>',
    "leaf": '<path d="M33 67 C33 44 46 31 69 29 C69 52 56 67 33 67 Z"/><path d="M36 64 L60 38" fill="none" stroke-width="2.2"/>',
    # Stroke-only emblem: drawn twice (dark outline, then the emblem color).
    "snowflake": '<g fill="none" stroke-linecap="round" stroke-linejoin="round">'
                 '<path d="M50 28 V72 M31 39 L69 61 M31 61 L69 39 M44 31 L50 37 L56 31 M44 69 L50 63 L56 69" stroke-width="8"/>'
                 '<path d="M50 28 V72 M31 39 L69 61 M31 61 L69 39 M44 31 L50 37 L56 31 M44 69 L50 63 L56 69" stroke="url(#{id}-emblem)" stroke-width="4.5"/></g>',
    "heart": '<path d="M50 69 C37 60 31 53 31 45 C31 38 36 33 43 33 C47 33 49 36 50 38 C51 36 53 33 57 33 C64 33 69 38 69 45 C69 53 63 60 50 69 Z"/>',
    "moon": '<path d="M57 29 A21 21 0 1 0 62 69 A16 16 0 1 1 57 29 Z"/>',
    "crown": '<polygon points="31,65 33,39 43,52 50,33 57,52 67,39 69,65"/>',
    "gem": '<polygon points="40,32 60,32 69,44 50,70 31,44"/><path d="M31 44 H69 M40 32 L45 44 L50 70 L55 44 L60 32" fill="none" stroke-width="1.6"/>',
    "mountain": '<polygon points="29,67 44,39 51,50 58,35 71,67"/>',
    "wing": '<path d="M32 62 C38 45 51 34 69 31 C63 38 61 42 55 44 C61 44 64 46 66 48 C57 52 51 52 46 54 C50 56 53 58 55 61 C47 63 39 63 32 62 Z"/>',
}


def render(design: dict | None) -> str:
    d = normalize(design)
    primary, secondary = d["primary"], d["secondary"]
    outline = "#15131c"
    # Unique gradient ids per design, so several badges inlined into one
    # page can't pick up each other's gradients.
    gid = "b" + "".join(c for c in d["shape"] + d["emblem"] + primary + secondary if c.isalnum())
    body_attrs = f'fill="url(#{gid}-body)"'
    emblem = _EMBLEMS[d["emblem"]].replace("{id}", gid)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="160" height="160">
<defs>
  <radialGradient id="{gid}-body" cx="40%" cy="35%" r="70%">
    <stop offset="0" stop-color="{_shade(primary, 1.45)}"/>
    <stop offset="0.55" stop-color="{primary}"/>
    <stop offset="1" stop-color="{_shade(primary, 0.55)}"/>
  </radialGradient>
  <linearGradient id="{gid}-rim" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="{_shade(secondary, 1.35)}"/>
    <stop offset="0.5" stop-color="{secondary}"/>
    <stop offset="1" stop-color="{_shade(secondary, 0.6)}"/>
  </linearGradient>
  <linearGradient id="{gid}-emblem" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0" stop-color="{_shade(secondary, 1.3)}"/>
    <stop offset="1" stop-color="{_shade(secondary, 0.8)}"/>
  </linearGradient>
</defs>
{_shape_element(d["shape"], 1.0, f'fill="url(#{gid}-rim)" stroke="{outline}" stroke-width="2.5" stroke-linejoin="round"')}
{_shape_element(d["shape"], 0.8, body_attrs + f' stroke="{_shade(primary, 0.45)}" stroke-width="1.5" stroke-linejoin="round"')}
<g fill="url(#{gid}-emblem)" stroke="{outline}" stroke-width="2" stroke-linejoin="round" transform="translate(50 50) scale(1.15) translate(-50 -50)">{emblem}</g>
<ellipse cx="42" cy="30" rx="18" ry="8" fill="#ffffff" opacity="0.28" transform="rotate(-20 42 30)"/>
</svg>"""
