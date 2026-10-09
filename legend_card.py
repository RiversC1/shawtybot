"""Draws the Hall of Fame card the bot posts to Discord when a trainer beats
True Champion Cynthia (see cogs/pokemon.py's legend announcements). Pure
Pillow: the caller passes already-loaded images, so this never touches the
network."""
import io
import os
from datetime import datetime

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(ROOT, "assets", "fonts")
W, H = 1200, 675
GOLD = (245, 197, 66)
GOLD_SOFT = (255, 226, 140)
CRIMSON = (255, 90, 110)
WHITE = (246, 243, 250)
MUTED = (178, 170, 196)
INK = (14, 10, 18)


def _font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(os.path.join(FONT_DIR, f"Poppins-{weight}.ttf"), size)
    except OSError:
        return ImageFont.load_default(size)


def _fit_font(draw: ImageDraw.ImageDraw, text: str, weight: str, size: int, max_w: int, min_size: int = 26):
    while size > min_size:
        f = _font(weight, size)
        if draw.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return _font(weight, min_size)


def _spaced(draw: ImageDraw.ImageDraw, xy, text: str, font, fill, spacing: float):
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + spacing


def _glow_layer(size, shapes, blur):
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for box, color in shapes:
        d.ellipse(box, fill=color)
    return layer.filter(ImageFilter.GaussianBlur(blur))


def _fit_art(img: Image.Image, max_w: int, max_h: int) -> Image.Image:
    img = img.convert("RGBA")
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
    scale = min(max_w / img.width, max_h / img.height)
    size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
    resample = Image.NEAREST if scale >= 2 else Image.LANCZOS
    return img.resize(size, resample)


def _with_alpha(img: Image.Image, alpha: float) -> Image.Image:
    img = img.copy()
    a = img.getchannel("A").point(lambda v: int(v * alpha))
    img.putalpha(a)
    return img


def _silhouette_glow(img: Image.Image, color, blur: int, pad: int) -> Image.Image:
    """A soft colored halo the shape of `img` (for the hero art)."""
    canvas = Image.new("RGBA", (img.width + pad * 2, img.height + pad * 2), (0, 0, 0, 0))
    solid = Image.new("RGBA", img.size, color + (255,))
    solid.putalpha(img.getchannel("A"))
    canvas.paste(solid, (pad, pad), solid)
    return canvas.filter(ImageFilter.GaussianBlur(blur))


def render_true_champion_card(trainer_name: str, boss_name: str, turns: int, wins: int, when: datetime,
                              team: list[tuple[str, Image.Image | None]],
                              trainer_art: Image.Image | None = None,
                              boss_art: Image.Image | None = None) -> bytes:
    """PNG bytes for the card. `team` is (name, artwork or None) per Pokémon."""
    card = Image.new("RGBA", (W, H), INK + (255,))
    # Crimson and violet light bleeding in from opposite corners.
    card.alpha_composite(_glow_layer((W, H), [
        ((-260, 330, 520, 940), (192, 38, 61, 150)),
        ((760, -320, 1460, 300), (110, 40, 160, 140)),
        ((520, 160, 1180, 760), (245, 197, 66, 40)),
    ], 110))

    # The defeated Champion, faint, filling the right side.
    if boss_art is not None:
        boss = _with_alpha(_fit_art(boss_art, 520, 640), 0.2)
        card.alpha_composite(boss, (W - boss.width - 10, H - boss.height))
    # The new True Champion in front, haloed in gold.
    if trainer_art is not None:
        hero = _fit_art(trainer_art, 300, 460)
        hx, hy = W - 70 - (300 + hero.width) // 2, 80 + (460 - hero.height)
        halo = _silhouette_glow(hero, GOLD, 26, 60)
        card.alpha_composite(_with_alpha(halo, 0.85), (hx - 60, hy - 60))
        card.alpha_composite(hero, (hx, hy))

    # Translucent shapes and the text each get their own layer: drawing them
    # straight onto the card would replace its pixels instead of blending.
    shapes = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shapes)
    text = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(text)
    x = 64
    _spaced(d, (x + 2, 52), "HALL OF FAME  ·  FINAL TRIAL CLEARED", _font("Bold", 20), CRIMSON, 3.2)
    title_font = _font("ExtraBold", 84)
    d.text((x + 3, 82 + 4), "TRUE CHAMPION", font=title_font, fill=(60, 30, 10))
    d.text((x, 82), "TRUE CHAMPION", font=title_font, fill=GOLD)
    name_font = _fit_font(d, trainer_name, "Bold", 62, 680)
    d.text((x, 196), trainer_name, font=name_font, fill=WHITE)
    d.text((x + 2, 286), f"defeated {boss_name}", font=_font("Medium", 28), fill=MUTED)

    # Stat chips.
    chip_font = _font("Bold", 21)
    cx = x
    for label in (f"{turns} turns", "First victory" if wins <= 1 else f"Victory #{wins}",
                  when.strftime("%b %d, %Y").replace(" 0", " ")):
        tw = d.textlength(label, font=chip_font)
        sd.rounded_rectangle((cx, 348, cx + tw + 32, 390), radius=21, fill=(255, 255, 255, 20),
                             outline=GOLD + (150,), width=2)
        d.text((cx + 16, 355), label, font=chip_font, fill=GOLD_SOFT)
        cx += tw + 44

    # The winning team, Hall of Fame style.
    _spaced(d, (x + 2, 430), "THE TEAM", _font("Bold", 17), MUTED, 2.6)
    slot, gap = 104, 14
    arts = []
    for i, (mon_name, art) in enumerate(team[:6]):
        sx, sy = x + i * (slot + gap), 462
        sd.ellipse((sx, sy, sx + slot, sy + slot), fill=(255, 255, 255, 22), outline=GOLD + (210,), width=3)
        if art is not None:
            a = _fit_art(art, slot - 18, slot - 18)
            arts.append((a, (sx + (slot - a.width) // 2, sy + (slot - a.height) // 2)))
        label_font = _fit_font(d, mon_name, "Medium", 16, slot + gap - 4, min_size=11)
        lw = d.textlength(mon_name, font=label_font)
        d.text((sx + (slot - lw) / 2, sy + slot + 8), mon_name, font=label_font, fill=WHITE)

    # Gold frame and footer.
    sd.rounded_rectangle((14, 14, W - 15, H - 15), radius=26, outline=GOLD + (230,), width=3)
    sd.rounded_rectangle((24, 24, W - 25, H - 25), radius=20, outline=GOLD + (70,), width=1)
    foot = _font("Medium", 17)
    footer = "ShawtyBot  ·  Legends"
    d.text((W - 48 - d.textlength(footer, font=foot), H - 54), footer, font=foot, fill=MUTED)

    card.alpha_composite(shapes)
    for a, pos in arts:
        card.alpha_composite(a, pos)
    card.alpha_composite(text)

    out = io.BytesIO()
    card.convert("RGB").save(out, "PNG", optimize=True)
    return out.getvalue()
