"""
Label generation and Bluetooth printing for the YHK-835E thermal printer.
"""

import io
import re
import struct
from pathlib import Path

import PIL.Image
import PIL.ImageDraw
import PIL.ImageFont
import PIL.ImageOps


PRINTER_WIDTH = 384
_ICON_DIR = Path(__file__).parent / "icons"


# ---------------------------------------------------------------------------
# Font helpers
# ---------------------------------------------------------------------------

def _find_font(variant="regular"):
    paths = {
        "regular": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
        ],
        "bold": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
        ],
        "italic": [
            "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
        ],
    }
    for f in paths.get(variant, paths["regular"]):
        try:
            PIL.ImageFont.truetype(f, 12)
            return f
        except OSError:
            continue
    for f in ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/freefont/FreeSans.ttf"]:
        try:
            PIL.ImageFont.truetype(f, 12)
            return f
        except OSError:
            continue
    return None


def _get_font(variant, size):
    path = _find_font(variant)
    if path:
        return PIL.ImageFont.truetype(path, size)
    return PIL.ImageFont.load_default()


# ---------------------------------------------------------------------------
# Measurement helpers
# ---------------------------------------------------------------------------

def _text_h(draw, text, font):
    b = draw.textbbox((0, 0), text, font=font)
    return b[3] - b[1]

def _text_w(draw, text, font):
    b = draw.textbbox((0, 0), text, font=font)
    return b[2] - b[0]

def _ml_h(draw, text, font):
    b = draw.multiline_textbbox((0, 0), text, font=font)
    return b[3] - b[1]

def _ml_w(draw, text, font):
    b = draw.multiline_textbbox((0, 0), text, font=font)
    return b[2] - b[0]


def _wrap(text, font, max_w, max_lines=None):
    """Word-wrap, truncate with … if max_lines exceeded."""
    words = text.split()
    lines = [""]
    for word in words:
        test = f"{lines[-1]} {word}".strip()
        if font.getlength(test) <= max_w:
            lines[-1] = test
        else:
            lines.append(word)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip() + "…"
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Markdown parsing (subset: bullets, **bold**, *italic*, line breaks)
# ---------------------------------------------------------------------------

def _parse_md(text):
    """
    Return list of (content, style) per logical line.
    style: 'regular' | 'bold' | 'italic' | 'bullet'
    """
    def _strip_inline(s):
        s = re.sub(r'\*\*(.*?)\*\*', r'\1', s)
        s = re.sub(r'\*(.*?)\*',     r'\1', s)
        s = re.sub(r'`(.*?)`',       r'\1', s)
        return s

    result = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if line.startswith(("- ", "* ")):
            result.append(("• " + _strip_inline(line[2:]), "bullet"))
            continue
        m = re.match(r'^\*\*(.*)\*\*$', line)
        if m:
            result.append((_strip_inline(m.group(1)), "bold"))
            continue
        m = re.match(r'^\*(.*)\*$', line)
        if m:
            result.append((_strip_inline(m.group(1)), "italic"))
            continue
        result.append((_strip_inline(line), "regular"))
    return result


def _md_height(td, segments, fonts, max_w, gap=4):
    total = 0
    for i, (text, style) in enumerate(segments):
        font = fonts.get(style, fonts["regular"])
        total += _ml_h(td, _wrap(text, font, max_w), font)
        if i < len(segments) - 1:
            total += gap
    return total


def _render_md(d, segments, fonts, x, y, max_w, gap=4):
    """Render markdown segments starting at (x, y). Returns new y."""
    for i, (text, style) in enumerate(segments):
        font = fonts.get(style, fonts["regular"])
        wrapped = _wrap(text, font, max_w)
        d.multiline_text((x, y), wrapped, font=font, fill=0)
        y += _ml_h(d, wrapped, font)
        if i < len(segments) - 1:
            y += gap
    return y


# ---------------------------------------------------------------------------
# Icon drawing — PNG if available in py/icons/, drawn fallback otherwise
# ---------------------------------------------------------------------------

def _load_png_icon(name, size):
    """
    Load py/icons/<name>.png, resize to size×size, return as 1-bit PIL image.
    Supports transparent PNGs (dark icon on transparent background works best).
    Returns None if the file doesn't exist or fails to load.
    """
    path = _ICON_DIR / f"{name}.png"
    if not path.exists():
        return None
    try:
        src = PIL.Image.open(path).convert("RGBA")
        src = src.resize((size, size), PIL.Image.LANCZOS)
        bg = PIL.Image.new("L", (size, size), 255)
        bg.paste(src.convert("L"), mask=src.split()[3])
        return bg.point(lambda p: 0 if p < 128 else 255).convert("1")
    except Exception:
        return None


def _icon_pin(img, d, x, y, size=15):
    """Location pin icon. Returns width consumed."""
    icon = _load_png_icon("pin", size)
    if icon:
        img.paste(icon, (x, y))
        return size + 6
    # Drawn fallback: circle head + pointed tail
    r = max(3, size * 2 // 5)
    cx = x + r
    d.ellipse([x, y, x + r * 2, y + r * 2], fill=0)
    d.ellipse([cx - 2, y + r - 2, cx + 2, y + r + 2], fill=1)
    d.polygon([(x + 1, y + r * 2 - 2), (x + r * 2 - 1, y + r * 2 - 2), (cx, y + size)], fill=0)
    return r * 2 + 6


def _icon_diamond(img, d, x, y, size=11):
    """Filled diamond bullet. Returns width consumed."""
    icon = _load_png_icon("diamond", size)
    if icon:
        img.paste(icon, (x, y))
        return size + 6
    # Drawn fallback
    cx, cy = x + size // 2, y + size // 2
    r = max(2, size // 2 - 1)
    d.polygon([(cx, cy - r), (cx + r, cy), (cx, cy + r), (cx - r, cy)], fill=0)
    return size + 6


def _icon_pen(img, d, x, y, size=14):
    """Pencil icon. Returns width consumed."""
    icon = _load_png_icon("pen", size)
    if icon:
        img.paste(icon, (x, y))
        return size + 6
    # Drawn fallback: thick diagonal body (width=4) so it reads as a pencil, not a blade
    d.line([(x + 3, y + size - 2), (x + size - 2, y + 3)], fill=0, width=4)
    d.rectangle([x + size - 3, y, x + size + 2, y + 5], fill=0)   # eraser cap
    d.polygon([(x, y + size + 1), (x + 6, y + size - 4), (x + 2, y + size - 5)], fill=0)
    return size + 6


# ---------------------------------------------------------------------------
# Decorative borders
# ---------------------------------------------------------------------------

def _diamond_corners(d, bx0, by0, bx1, by1, r=6):
    """Draw filled diamond ornaments at the four corners of a rectangle."""
    for cx, cy in [(bx0, by0), (bx1, by0), (bx0, by1), (bx1, by1)]:
        d.polygon([(cx, cy - r), (cx + r, cy),
                   (cx, cy + r), (cx - r, cy)], fill=0)


def _square_notches(d, bx0, by0, bx1, by1, s=4):
    """Draw small filled squares at the four corners (classic style accent)."""
    for cx, cy in [(bx0 + 6, by0 + 6), (bx1 - 6, by0 + 6),
                   (bx0 + 6, by1 - 6), (bx1 - 6, by1 - 6)]:
        d.rectangle([cx - s, cy - s, cx + s, cy + s], fill=0)


# ---------------------------------------------------------------------------
# Label image generators
# ---------------------------------------------------------------------------

def create_label_classic(common_name, latin_name, date_str,
                         variety=None, nickname=None, extra_notes=None):
    """Rectangular double-border label with optional nickname and extra notes (MD)."""
    W = PRINTER_WIDTH
    margin, pad_t, pad_b = 20, 32, 28

    name_font  = _get_font("bold",    40)
    sub_font   = _get_font("italic",  22)   # common name when nickname is the hero
    latin_font = _get_font("italic",  22)
    var_font   = _get_font("regular", 17)
    date_font  = _get_font("regular", 18)
    note_r     = _get_font("regular", 16)
    note_b     = _get_font("bold",    16)
    note_i     = _get_font("italic",  16)
    md_fonts   = {"regular": note_r, "bold": note_b, "italic": note_i, "bullet": note_r}

    # Nickname becomes the hero; common name drops to a subtitle
    hero = nickname if nickname else common_name
    sub  = common_name if nickname else None

    tmp = PIL.Image.new("1", (1, 1))
    td  = PIL.ImageDraw.Draw(tmp)

    inner_w    = W - margin * 2 - 24
    hero_lines = _wrap(hero, name_font, inner_w)
    hero_w     = _ml_w(td, hero_lines, name_font)
    hero_h     = _ml_h(td, hero_lines, name_font)
    latin_h    = _text_h(td, latin_name, latin_font)
    date_w     = _text_w(td, date_str, date_font)
    date_h     = _text_h(td, date_str, date_font)

    sub_h = (_text_h(td, sub, sub_font) + 5) if sub else 0
    var_h = (_text_h(td, variety, var_font) + 6) if variety else 0

    md_segs = _parse_md(extra_notes.strip()) if extra_notes else []
    note_h  = (_md_height(td, md_segs, md_fonts, inner_w) + 16) if md_segs else 0

    total_h = pad_t + hero_h + 10 + sub_h + latin_h + var_h + 20 + date_h + note_h + pad_b
    img = PIL.Image.new("1", (W, total_h), 1)
    d   = PIL.ImageDraw.Draw(img)

    bx0, by0, bx1, by1 = margin - 5, 5, W - margin + 5, total_h - 6
    d.rectangle([bx0, by0, bx1, by1], outline=0, width=3)
    d.rectangle([bx0 + 6, by0 + 6, bx1 - 6, by1 - 6], outline=0, width=1)
    _square_notches(d, bx0 + 6, by0 + 6, bx1 - 6, by1 - 6)

    y = pad_t
    d.multiline_text(((W - hero_w) // 2, y), hero_lines, font=name_font, fill=0, align="center")
    y += hero_h + 10

    if sub:
        d.text((W // 2, y), sub, font=sub_font, fill=0, anchor="ma")
        y += sub_h

    d.text((W // 2, y), latin_name, font=latin_font, fill=0, anchor="ma")
    y += latin_h + 5

    if variety:
        d.text((W // 2, y), variety, font=var_font, fill=0, anchor="ma")
        y += var_h

    div_m = margin + 15
    d.line([(div_m, y), (W - div_m, y)], fill=0, width=1)
    d.line([(div_m, y + 2), (W - div_m, y + 2)], fill=0, width=1)
    y += 14

    d.text(((W - date_w) // 2, y), date_str, font=date_font, fill=0)
    y += date_h

    if md_segs:
        y += 8
        d.line([(div_m, y), (W - div_m, y)], fill=0, width=1)
        y += 8
        _render_md(d, md_segs, md_fonts, margin + 15, y, inner_w)

    return img


def create_label_circular(common_name, latin_name, date_str,
                          variety=None, nickname=None, extra_notes=None):
    """Circular label. Nickname is the hero name; extra notes (MD) appear below the circle."""
    diameter  = int(PRINTER_WIDTH * 0.66)
    note_r    = _get_font("regular", 14)
    note_b    = _get_font("bold",    14)
    note_i    = _get_font("italic",  14)
    md_fonts  = {"regular": note_r, "bold": note_b, "italic": note_i, "bullet": note_r}

    md_segs  = _parse_md(extra_notes.strip()) if extra_notes else []
    note_w   = PRINTER_WIDTH - 40

    tmp = PIL.Image.new("1", (1, 1))
    td  = PIL.ImageDraw.Draw(tmp)
    note_h = (_md_height(td, md_segs, md_fonts, note_w) + 14) if md_segs else 0

    total_h = diameter + 20 + note_h
    img = PIL.Image.new("1", (PRINTER_WIDTH, total_h), 1)
    d   = PIL.ImageDraw.Draw(img)

    cx, cy, r = PRINTER_WIDTH // 2, diameter // 2 + 10, diameter // 2
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=0, width=3)
    d.ellipse([cx - r + 6, cy - r + 6, cx + r - 6, cy + r - 6], outline=0, width=1)

    name_font  = _get_font("bold",    28)
    sub_font   = _get_font("italic",  15)
    latin_font = _get_font("italic",  15)
    var_font   = _get_font("regular", 13)
    date_font  = _get_font("regular", 14)

    hero = nickname if nickname else common_name
    sub  = common_name if nickname else None

    max_text_w = int(r * 1.4)
    hero_lines = _wrap(hero, name_font, max_text_w)
    hero_h     = _ml_h(d, hero_lines, name_font)
    latin_h    = _text_h(d, latin_name, latin_font)
    date_h     = _text_h(d, date_str, date_font)
    sub_h      = (_text_h(d, sub, sub_font) + 4) if sub else 0
    var_h      = (_text_h(d, variety, var_font) + 4) if variety else 0

    inner_h = hero_h + 6 + sub_h + latin_h + var_h + 6 + date_h
    y = cy - inner_h // 2

    d.multiline_text((cx, y), hero_lines, font=name_font, fill=0, anchor="ma", align="center")
    y += hero_h + 6

    if sub:
        d.text((cx, y), sub, font=sub_font, fill=0, anchor="ma")
        y += sub_h

    d.text((cx, y), latin_name, font=latin_font, fill=0, anchor="ma")
    y += latin_h + 4

    if variety:
        d.text((cx, y), variety, font=var_font, fill=0, anchor="ma")
        y += var_h + 2

    d.text((cx, y), date_str, font=date_font, fill=0, anchor="ma")

    if md_segs:
        y = diameter + 20 + 8
        _render_md(d, md_segs, md_fonts, 20, y, note_w)

    return img


def create_label_minimal(common_name, latin_name, date_str,
                         variety=None, nickname=None, extra_notes=None):
    """Clean borderless label. Nickname is the hero name if present."""
    W   = PRINTER_WIDTH
    pad = 28

    name_font  = _get_font("bold",    46)
    sub_font   = _get_font("italic",  22)
    latin_font = _get_font("italic",  21)
    var_font   = _get_font("regular", 17)
    date_font  = _get_font("regular", 17)
    note_r     = _get_font("regular", 16)
    note_b     = _get_font("bold",    16)
    note_i     = _get_font("italic",  16)
    md_fonts   = {"regular": note_r, "bold": note_b, "italic": note_i, "bullet": note_r}

    hero = nickname if nickname else common_name
    sub  = common_name if nickname else None

    tmp = PIL.Image.new("1", (1, 1))
    td  = PIL.ImageDraw.Draw(tmp)

    inner_w    = W - 48
    hero_lines = _wrap(hero, name_font, inner_w)
    hero_w     = _ml_w(td, hero_lines, name_font)
    hero_h     = _ml_h(td, hero_lines, name_font)
    latin_h    = _text_h(td, latin_name, latin_font)
    date_w     = _text_w(td, date_str, date_font)
    date_h     = _text_h(td, date_str, date_font)
    sub_h      = (_text_h(td, sub, sub_font) + 4) if sub else 0
    var_h      = (_text_h(td, variety, var_font) + 4) if variety else 0

    md_segs = _parse_md(extra_notes.strip()) if extra_notes else []
    note_h  = (_md_height(td, md_segs, md_fonts, inner_w) + 12) if md_segs else 0

    total_h = pad + hero_h + 10 + sub_h + latin_h + var_h + 16 + date_h + note_h + pad
    img = PIL.Image.new("1", (W, total_h), 1)
    d   = PIL.ImageDraw.Draw(img)

    y = pad
    d.multiline_text(((W - hero_w) // 2, y), hero_lines, font=name_font, fill=0, align="center")
    y += hero_h + 6

    rule_w = W // 2
    d.line([((W - rule_w) // 2, y), ((W + rule_w) // 2, y)], fill=0, width=1)
    y += 10

    if sub:
        d.text((W // 2, y), sub, font=sub_font, fill=0, anchor="ma")
        y += sub_h

    d.text((W // 2, y), latin_name, font=latin_font, fill=0, anchor="ma")
    y += latin_h + 4

    if variety:
        d.text((W // 2, y), variety, font=var_font, fill=0, anchor="ma")
        y += var_h

    y += 12
    d.text(((W - date_w) // 2, y), date_str, font=date_font, fill=0)
    y += date_h

    if md_segs:
        y += 12
        _render_md(d, md_segs, md_fonts, (W - inner_w) // 2, y, inner_w)

    return img


def create_label_detailed(common_name, latin_name, date_str,
                          variety=None, nickname=None,
                          location=None, notes=None, extra_notes=None):
    """Full-info label with icons, location, plant notes, personal notes (MD), date."""
    W       = PRINTER_WIDTH
    margin  = 16
    pad_t   = 24
    pad_b   = 20
    tx      = margin + 12          # text / icon left edge
    inner_w = W - tx - margin - 8  # usable text width

    ICON_S  = 16   # icon size (px), matched to info font

    name_font  = _get_font("bold",    34)
    sub_font   = _get_font("italic",  18)
    latin_font = _get_font("italic",  18)
    var_font   = _get_font("regular", 16)
    info_r     = _get_font("regular", 15)
    note_r     = _get_font("regular", 15)
    note_b     = _get_font("bold",    15)
    note_i     = _get_font("italic",  15)
    date_font  = _get_font("regular", 16)
    md_fonts   = {"regular": note_r, "bold": note_b, "italic": note_i, "bullet": note_r}
    info_fonts = {"regular": info_r}

    tmp = PIL.Image.new("1", (1, 1))
    td  = PIL.ImageDraw.Draw(tmp)

    hero = nickname if nickname else common_name
    sub  = common_name if nickname else None

    hero_lines = _wrap(hero, name_font, inner_w)
    hero_w     = _ml_w(td, hero_lines, name_font)
    hero_h     = _ml_h(td, hero_lines, name_font)
    latin_h    = _text_h(td, latin_name, latin_font)
    date_w     = _text_w(td, date_str, date_font)
    date_h     = _text_h(td, date_str, date_font)
    sub_h      = (_text_h(td, sub, sub_font) + 4) if sub else 0
    var_h      = (_text_h(td, variety, var_font) + 4) if variety else 0

    # Pre-calculate icon row widths
    icon_w_pin     = ICON_S + 6
    icon_w_diamond = ICON_S + 6
    icon_w_pen     = ICON_S + 5

    loc_h = 0
    if location:
        loc_wrapped = _wrap(location, info_r, inner_w - icon_w_pin)
        loc_h = _ml_h(td, loc_wrapped, info_r) + 6

    plant_segs  = _parse_md(notes.strip()) if notes and notes.strip() else []
    plant_note_h = (_md_height(td, plant_segs, info_fonts, inner_w - icon_w_diamond) + 6) if plant_segs else 0

    extra_segs  = _parse_md(extra_notes.strip()) if extra_notes and extra_notes.strip() else []
    extra_note_h = (_md_height(td, extra_segs, md_fonts, inner_w - icon_w_pen) + 6) if extra_segs else 0

    has_info = loc_h or plant_note_h or extra_note_h
    info_gap = 20 if has_info else 0

    total_h = (pad_t + hero_h + 8 + sub_h + latin_h + var_h
               + info_gap + loc_h + plant_note_h + extra_note_h
               + 20 + date_h + pad_b)

    img = PIL.Image.new("1", (W, total_h), 1)
    d   = PIL.ImageDraw.Draw(img)

    # Border: thin rectangle + diamond corner ornaments
    bx0, by0, bx1, by1 = margin - 4, 4, W - margin + 4, total_h - 4
    d.rectangle([bx0, by0, bx1, by1], outline=0, width=1)
    _diamond_corners(d, bx0, by0, bx1, by1, r=6)

    y = pad_t
    d.multiline_text(((W - hero_w) // 2, y), hero_lines, font=name_font, fill=0, align="center")
    y += hero_h + 8

    if sub:
        d.text((W // 2, y), sub, font=sub_font, fill=0, anchor="ma")
        y += sub_h

    d.text((W // 2, y), latin_name, font=latin_font, fill=0, anchor="ma")
    y += latin_h + 4

    if variety:
        d.text((W // 2, y), variety, font=var_font, fill=0, anchor="ma")
        y += var_h

    if has_info:
        div_m = margin + 8
        y += 8
        d.line([(div_m, y), (W - div_m, y)], fill=0, width=1)
        y += 12

        if location:
            loc_wrapped = _wrap(location, info_r, inner_w - icon_w_pin)
            fh = _text_h(d, "A", info_r)
            _icon_pin(img, d, tx, y + max(0, (fh - ICON_S) // 2), ICON_S)
            d.multiline_text((tx + icon_w_pin, y), loc_wrapped, font=info_r, fill=0)
            y += _ml_h(d, loc_wrapped, info_r) + 6

        if plant_segs:
            fh = _text_h(d, "A", info_r)
            _icon_diamond(img, d, tx, y + max(0, (fh - ICON_S) // 2), ICON_S)
            _render_md(d, plant_segs, info_fonts, tx + icon_w_diamond, y,
                       inner_w - icon_w_diamond)
            y += plant_note_h

        if extra_segs:
            fh = _text_h(d, "A", note_r)
            _icon_pen(img, d, tx, y + max(0, (fh - ICON_S) // 2), ICON_S)
            _render_md(d, extra_segs, md_fonts, tx + icon_w_pen, y,
                       inner_w - icon_w_pen)
            y += extra_note_h

    div_m = margin + 8
    y += 8
    d.line([(div_m, y), (W - div_m, y)], fill=0, width=1)
    y += 12

    d.text(((W - date_w) // 2, y), date_str, font=date_font, fill=0)

    return img


# ---------------------------------------------------------------------------
# Rendering to bytes / PNG
# ---------------------------------------------------------------------------

def label_to_printer_bytes(img: PIL.Image.Image) -> bytes:
    """Return raw ESC/POS bytes ready to send to the YHK-835E printer."""
    if img.width < PRINTER_WIDTH:
        padded = PIL.Image.new("1", (PRINTER_WIDTH, img.height), 1)
        padded.paste(img)
        img = padded
    if img.size[0] % 8:
        img2 = PIL.Image.new("1", (img.size[0] + 8 - img.size[0] % 8, img.size[1]), "white")
        img2.paste(img, (0, 0))
        img = img2
    img = PIL.ImageOps.invert(img.convert("L")).convert("1")
    return (
        b"\x1d\x76\x30\x00"
        + struct.pack("2B", img.size[0] // 8 % 256, img.size[0] // 8 // 256)
        + struct.pack("2B", img.size[1] % 256, img.size[1] // 256)
        + img.tobytes()
    )


def label_to_png_bytes(img: PIL.Image.Image) -> bytes:
    """Return PNG bytes from a label PIL image, suitable for HTTP response."""
    preview = img.resize((img.width * 2, img.height * 2), PIL.Image.NEAREST)
    buf = io.BytesIO()
    preview.save(buf, format="PNG")
    return buf.getvalue()
