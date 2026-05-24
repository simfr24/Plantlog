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
import qrcode
import qrcode.constants


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
# Markdown parsing
#   Supports: # / ## / ### headings, paragraphs with inline **bold** *italic*,
#             - / * bullet lists, and | pipe | tables.
# ---------------------------------------------------------------------------

_INLINE_RE = re.compile(r'\*\*(.+?)\*\*|\*(.+?)\*|`(.+?)`')
_LINK_RE   = re.compile(r'\[([^\]]+)\]\([^)\s]+\)')

def _parse_inline(s):
    """Split a single line into styled runs: [(text, 'regular'|'bold'|'italic')]."""
    s = _LINK_RE.sub(r'\1', s)  # [label](url) → label
    out = []
    pos = 0
    for m in _INLINE_RE.finditer(s):
        if m.start() > pos:
            out.append((s[pos:m.start()], "regular"))
        if m.group(1) is not None:
            out.append((m.group(1), "bold"))
        elif m.group(2) is not None:
            out.append((m.group(2), "italic"))
        else:
            out.append((m.group(3), "regular"))
        pos = m.end()
    if pos < len(s):
        out.append((s[pos:], "regular"))
    return out or [("", "regular")]


def _parse_md(text):
    """Parse markdown into a list of block dicts.

    Block shapes:
      {"type": "h1"|"h2"|"h3", "runs": [...]}
      {"type": "para",         "runs": [...]}
      {"type": "bullet",       "runs": [...]}
      {"type": "table",        "rows": [[runs, runs, ...], ...]}
    """
    blocks = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            i += 1
            continue

        m = re.match(r'^(#{1,3})\s+(.*)$', line)
        if m:
            level = len(m.group(1))
            blocks.append({"type": f"h{level}", "runs": _parse_inline(m.group(2).strip())})
            i += 1
            continue

        if re.match(r'^\s*[-*]\s+', line):
            content = re.sub(r'^\s*[-*]\s+', '', line)
            blocks.append({"type": "bullet", "runs": _parse_inline(content)})
            i += 1
            continue

        # Blockquote → render as italic paragraph
        if line.lstrip().startswith(">"):
            content = re.sub(r'^\s*>\s?', '', line)
            blocks.append({"type": "para", "runs": [(content.strip(), "italic")]})
            i += 1
            continue

        # Tables → flattened to compact "**Key** : Value" paragraphs (skip header row)
        if line.lstrip().startswith("|") and "|" in line.lstrip()[1:]:
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                raw = lines[i].strip().strip("|")
                cells = [c.strip() for c in raw.split("|")]
                if all(re.fullmatch(r'[-:\s]+', c) for c in cells):
                    i += 1
                    continue
                rows.append(cells)
                i += 1
            # Drop the header row if there are at least two rows
            data_rows = rows[1:] if len(rows) >= 2 else rows
            for row in data_rows:
                if not row:
                    continue
                if len(row) == 1:
                    blocks.append({"type": "para", "runs": _parse_inline(row[0])})
                    continue
                key  = row[0]
                rest = " — ".join(c for c in row[1:] if c)
                runs = [(key, "bold"), (" : ", "regular")] + _parse_inline(rest)
                blocks.append({"type": "para", "runs": runs})
            continue

        para = [line]
        i += 1
        while i < len(lines) and lines[i].strip() and not re.match(
                r'^(\s*#{1,3}\s|\s*[-*]\s|\s*\||\s*>)', lines[i]):
            para.append(lines[i].rstrip())
            i += 1
        joined = " ".join(s.strip() for s in para)
        blocks.append({"type": "para", "runs": _parse_inline(joined)})

    return blocks


def _build_md_fonts(body_size=15):
    """Return a font dict suitable for the new MD renderer."""
    return {
        "h1":      _get_font("bold",   max(18, int(body_size * 1.7))),
        "h2":      _get_font("bold",   max(16, int(body_size * 1.35))),
        "h3":      _get_font("bold",   max(15, int(body_size * 1.15))),
        "regular": _get_font("regular", body_size),
        "bold":    _get_font("bold",    body_size),
        "italic":  _get_font("italic",  body_size),
        "bullet":  _get_font("regular", body_size),
    }


_TOKEN_RE = re.compile(r'\S+|\s+')

def _wrap_runs(runs, fonts, max_w, default_style="regular"):
    """Word-wrap styled runs. Returns lines = [[(text, style), ...], ...]."""
    lines = [[]]
    cur_w = 0
    for text, style in runs:
        font = fonts.get(style, fonts[default_style])
        for tok in _TOKEN_RE.findall(text):
            tw = font.getlength(tok)
            if tok.isspace():
                if not lines[-1]:
                    continue
                if cur_w + tw > max_w:
                    lines.append([])
                    cur_w = 0
                else:
                    lines[-1].append((tok, style))
                    cur_w += tw
            else:
                if cur_w + tw > max_w and lines[-1]:
                    lines.append([])
                    cur_w = 0
                lines[-1].append((tok, style))
                cur_w += tw
    return lines


def _line_h(d, line, fonts, default_style="regular"):
    if not line:
        return _text_h(d, "Ay", fonts[default_style])
    return max(_text_h(d, t if t.strip() else "A", fonts.get(s, fonts[default_style])) for t, s in line)


def _runs_height(d, runs, fonts, max_w, default_style="regular", line_gap=2):
    lines = _wrap_runs(runs, fonts, max_w, default_style)
    h = 0
    for i, line in enumerate(lines):
        h += _line_h(d, line, fonts, default_style)
        if i < len(lines) - 1:
            h += line_gap
    return h


def _draw_runs(d, runs, fonts, x, y, max_w, default_style="regular", line_gap=2):
    lines = _wrap_runs(runs, fonts, max_w, default_style)
    for i, line in enumerate(lines):
        # Coalesce adjacent same-style tokens into one segment so PIL can shape
        # the whole string in one d.text call. Drawing token-by-token and
        # advancing the cursor by font.getlength causes the next token to land
        # inside the previous glyph's right-side bearing, swallowing spaces.
        segments = []
        for t, s in line:
            if segments and segments[-1][1] == s:
                segments[-1][0] += t
            else:
                segments.append([t, s])
        h = _line_h(d, line, fonts, default_style)
        cx = x
        for text, style in segments:
            font = fonts.get(style, fonts[default_style])
            d.text((cx, y), text, font=font, fill=0)
            cx += font.getlength(text)
        y += h
        if i < len(lines) - 1:
            y += line_gap
    return y


def _heading_fonts(fonts, level):
    """Treat heading runs as bold-by-default at the heading size."""
    f = fonts[f"h{level}"]
    return {"regular": f, "bold": f, "italic": f, "bullet": f}


def _block_height(d, block, fonts, max_w, line_gap=2):
    t = block["type"]
    if t in ("h1", "h2", "h3"):
        return _runs_height(d, block["runs"], _heading_fonts(fonts, int(t[1])), max_w, line_gap=line_gap)
    if t == "para":
        return _runs_height(d, block["runs"], fonts, max_w, line_gap=line_gap)
    if t == "bullet":
        indent = _text_w(d, "• ", fonts["regular"])
        return _runs_height(d, block["runs"], fonts, max_w - indent, line_gap=line_gap)
    if t == "table":
        col1_w = max(40, int(max_w * 0.42))
        col2_w = max_w - col1_w - 10
        bold_fonts = {**fonts, "regular": fonts["bold"]}
        h = 0
        for row in block["rows"]:
            if len(row) >= 2:
                h1 = _runs_height(d, row[0], bold_fonts, col1_w, line_gap=line_gap)
                h2 = _runs_height(d, row[1], fonts,      col2_w, line_gap=line_gap)
                h += max(h1, h2) + 4
            elif row:
                h += _runs_height(d, row[0], fonts, max_w, line_gap=line_gap) + 4
        return h
    return 0


def _render_block(d, block, fonts, x, y, max_w, line_gap=2):
    t = block["type"]
    if t in ("h1", "h2", "h3"):
        return _draw_runs(d, block["runs"], _heading_fonts(fonts, int(t[1])), x, y, max_w, line_gap=line_gap)
    if t == "para":
        return _draw_runs(d, block["runs"], fonts, x, y, max_w, line_gap=line_gap)
    if t == "bullet":
        bw = _text_w(d, "• ", fonts["regular"])
        d.text((x, y), "• ", font=fonts["regular"], fill=0)
        return _draw_runs(d, block["runs"], fonts, x + bw, y, max_w - bw, line_gap=line_gap)
    if t == "table":
        col1_w = max(40, int(max_w * 0.42))
        col2_w = max_w - col1_w - 10
        bold_fonts = {**fonts, "regular": fonts["bold"]}
        for row in block["rows"]:
            if len(row) >= 2:
                h1 = _runs_height(d, row[0], bold_fonts, col1_w, line_gap=line_gap)
                h2 = _runs_height(d, row[1], fonts,      col2_w, line_gap=line_gap)
                row_h = max(h1, h2)
                _draw_runs(d, row[0], bold_fonts, x, y, col1_w, line_gap=line_gap)
                _draw_runs(d, row[1], fonts,      x + col1_w + 10, y, col2_w, line_gap=line_gap)
                # Light rule under each row
                d.line([(x, y + row_h + 1), (x + max_w, y + row_h + 1)], fill=0, width=1)
                y += row_h + 4
            elif row:
                y = _draw_runs(d, row[0], fonts, x, y, max_w, line_gap=line_gap) + 4
        return y
    return y


def _md_height(d, blocks, fonts, max_w, block_gap=8):
    if not blocks:
        return 0
    total = 0
    for i, b in enumerate(blocks):
        total += _block_height(d, b, fonts, max_w)
        if i < len(blocks) - 1:
            total += block_gap
    return total


def _md_render(d, blocks, fonts, x, y, max_w, block_gap=8):
    for i, b in enumerate(blocks):
        y = _render_block(d, b, fonts, x, y, max_w)
        if i < len(blocks) - 1:
            y += block_gap
    return y


# Back-compat alias
def _render_md(d, blocks, fonts, x, y, max_w):
    return _md_render(d, blocks, fonts, x, y, max_w)


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
    md_fonts   = _build_md_fonts(16)

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
    md_fonts  = _build_md_fonts(14)

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
    md_fonts   = _build_md_fonts(16)

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


def create_label_detailed_v(common_name, latin_name, date_str,
                            variety=None, nickname=None,
                            location=None, notes=None, extra_notes=None):
    """Vertical full-info label with icons, location, plant notes, personal notes (MD), date."""
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
    date_font  = _get_font("regular", 16)
    md_fonts   = _build_md_fonts(15)
    info_fonts = md_fonts

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

    icon_w_pin = ICON_S + 6

    loc_h = 0
    if location:
        loc_wrapped = _wrap(location, info_r, inner_w - icon_w_pin)
        loc_h = _ml_h(td, loc_wrapped, info_r) + 6

    plant_segs = _parse_md(notes.strip()) if notes and notes.strip() else []
    # Drop the leading H1: the hero name already provides the title.
    if plant_segs and plant_segs[0]["type"] == "h1":
        plant_segs = plant_segs[1:]
    plant_note_h = (_md_height(td, plant_segs, md_fonts, inner_w) + 6) if plant_segs else 0

    extra_segs  = _parse_md(extra_notes.strip()) if extra_notes and extra_notes.strip() else []
    extra_note_h = (_md_height(td, extra_segs, md_fonts, inner_w) + 6) if extra_segs else 0

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
            y = _md_render(d, plant_segs, md_fonts, tx, y, inner_w) + 6

        if extra_segs:
            y = _md_render(d, extra_segs, md_fonts, tx, y, inner_w) + 6

    div_m = margin + 8
    y += 8
    d.line([(div_m, y), (W - div_m, y)], fill=0, width=1)
    y += 12

    d.text(((W - date_w) // 2, y), date_str, font=date_font, fill=0)

    return img


def create_label_detailed_h(common_name, latin_name, date_str,
                            variety=None, nickname=None,
                            location=None, notes=None, extra_notes=None,
                            plant_url=None):
    """Horizontal detailed label. Fixed reading height = PRINTER_WIDTH;
    content flows across as many columns as needed, then the whole image is
    rotated 90° clockwise so the strip prints at the printer's native width.
    Intended to be wrapped sideways around a pot."""
    H        = PRINTER_WIDTH
    pad_l    = 18
    pad_r    = 18
    pad_t    = 14
    pad_b    = 14
    inner_h  = H - pad_t - pad_b

    HEADER_W = 230
    COL_W    = 300
    GUTTER   = 22
    BLOCK_GAP = 8

    name_font  = _get_font("bold",    34)
    sub_font   = _get_font("italic",  17)
    latin_font = _get_font("italic",  18)
    var_font   = _get_font("regular", 15)
    info_r     = _get_font("regular", 14)
    date_font  = _get_font("regular", 15)
    md_fonts   = _build_md_fonts(14)
    ICON_S     = 14

    tmp = PIL.Image.new("1", (1, 1))
    td  = PIL.ImageDraw.Draw(tmp)

    hero = nickname if nickname else common_name
    sub  = common_name if nickname else None

    plant_blocks = _parse_md(notes.strip())       if notes       and notes.strip()       else []
    if plant_blocks and plant_blocks[0]["type"] == "h1":
        plant_blocks = plant_blocks[1:]
    extra_blocks = _parse_md(extra_notes.strip()) if extra_notes and extra_notes.strip() else []
    all_blocks   = plant_blocks + extra_blocks

    # Pack blocks into fixed-height columns
    columns      = [[]]
    col_used_h   = [0]
    for b in all_blocks:
        bh = _block_height(td, b, md_fonts, COL_W)
        gap = BLOCK_GAP if columns[-1] else 0
        if not columns[-1] or col_used_h[-1] + gap + bh <= inner_h:
            columns[-1].append(b)
            col_used_h[-1] += gap + bh
        else:
            columns.append([b])
            col_used_h.append(bh)

    if not all_blocks:
        columns = []

    n_cols = len(columns)
    total_w = pad_l + HEADER_W + pad_r
    if n_cols > 0:
        total_w = pad_l + HEADER_W + GUTTER + n_cols * COL_W + (n_cols - 1) * GUTTER + pad_r

    img = PIL.Image.new("1", (total_w, H), 1)
    d   = PIL.ImageDraw.Draw(img)

    # Border + corner ornaments
    bx0, by0, bx1, by1 = 4, 4, total_w - 4, H - 4
    d.rectangle([bx0, by0, bx1, by1], outline=0, width=1)
    _diamond_corners(d, bx0, by0, bx1, by1, r=6)

    # ─── header column ──────────────────────────────────────────────────
    hx = pad_l
    hy = pad_t + 4

    hero_lines = _wrap(hero, name_font, HEADER_W)
    hero_w     = _ml_w(td, hero_lines, name_font)
    hero_h     = _ml_h(td, hero_lines, name_font)
    d.multiline_text((hx + (HEADER_W - hero_w) // 2, hy),
                     hero_lines, font=name_font, fill=0, align="center")
    hy += hero_h + 6

    if sub:
        sw = _text_w(td, sub, sub_font)
        d.text((hx + (HEADER_W - sw) // 2, hy), sub, font=sub_font, fill=0)
        hy += _text_h(td, sub, sub_font) + 4

    lw = _text_w(td, latin_name, latin_font)
    d.text((hx + (HEADER_W - lw) // 2, hy), latin_name, font=latin_font, fill=0)
    hy += _text_h(td, latin_name, latin_font) + 4

    if variety:
        vw = _text_w(td, variety, var_font)
        d.text((hx + (HEADER_W - vw) // 2, hy), variety, font=var_font, fill=0)
        hy += _text_h(td, variety, var_font) + 4

    hy += 6
    d.line([(hx + 12, hy), (hx + HEADER_W - 12, hy)], fill=0, width=1)
    hy += 10

    if location:
        loc_wrapped = _wrap(location, info_r, HEADER_W - 22)
        _icon_pin(img, d, hx + 2, hy + 1, ICON_S)
        d.multiline_text((hx + 22, hy), loc_wrapped, font=info_r, fill=0)
        hy += _ml_h(td, loc_wrapped, info_r) + 6

    # Date pinned to bottom of header column
    date_w_px = _text_w(td, date_str, date_font)
    date_h_px = _text_h(td, date_str, date_font)
    date_y    = H - pad_b - date_h_px - 2
    d.text((hx + (HEADER_W - date_w_px) // 2, date_y), date_str, font=date_font, fill=0)

    # QR code between location/title block and the date
    if plant_url:
        avail_h = date_y - hy - 12
        qr_size = min(HEADER_W - 30, avail_h)
        qr_size = (max(60, qr_size) // 4) * 4
        if qr_size >= 60 and qr_size <= avail_h:
            qr_y   = hy + (avail_h - qr_size) // 2
            qr_img = _make_qr_image(plant_url, qr_size)
            img.paste(qr_img, (hx + (HEADER_W - qr_size) // 2, qr_y))

    # ─── content columns ────────────────────────────────────────────────
    col_x = pad_l + HEADER_W + GUTTER
    for col_blocks in columns:
        # Vertical divider on the left edge of each column
        div_x = col_x - GUTTER // 2
        d.line([(div_x, pad_t + 6), (div_x, H - pad_b - 6)], fill=0, width=1)

        cy = pad_t + 4
        for bi, b in enumerate(col_blocks):
            if bi > 0:
                cy += BLOCK_GAP
            cy = _render_block(d, b, md_fonts, col_x, cy, COL_W)
        col_x += COL_W + GUTTER

    # Rotate 90° clockwise → printer-native orientation (PRINTER_WIDTH wide,
    # total_w tall). The header ends up at the top of the strip.
    return img.transpose(PIL.Image.ROTATE_270)


# ---------------------------------------------------------------------------
# Stake-wrap label
# ---------------------------------------------------------------------------

def create_label_stake_wrap(common_name, latin_name, date_str,
                            variety=None, nickname=None, extra_notes=None,
                            back_text=None):
    W     = PRINTER_WIDTH
    SEC_W = W // 3
    PAD   = 12

    name_font  = _get_font("bold",    34)
    latin_font = _get_font("italic",  17)
    var_font   = _get_font("italic",  17)
    date_font  = _get_font("regular", 16)

    hero = nickname if nickname else common_name

    tmp = PIL.Image.new("1", (1, 1))
    td  = PIL.ImageDraw.Draw(tmp)

    hero_w  = _text_w(td, hero, name_font)
    latin_w = _text_w(td, latin_name, latin_font)
    date_w  = _text_w(td, date_str, date_font)
    var_w   = (_text_w(td, f"'{variety}'", var_font) + 6) if variety else 0
    # latin + variety on same line
    latin_line_w = latin_w + var_w

    H     = max(hero_w, latin_line_w, date_w) + PAD * 2
    COL_H = SEC_W - PAD * 2

    img = PIL.Image.new("1", (W, H), 1)

    def _draw_rotated_text(section_idx, angle):
        tmp2 = PIL.Image.new("1", (H, COL_H), 1)
        td2  = PIL.ImageDraw.Draw(tmp2)

        main_h  = _text_h(td2, hero, name_font)
        latin_h = _text_h(td2, latin_name, latin_font)
        date_h  = _text_h(td2, date_str, date_font)
        block   = main_h + 4 + latin_h + 4 + date_h
        y0      = (COL_H - block) // 2

        td2.text((H // 2, y0), hero, font=name_font, fill=0, anchor="ma")

        # Latin + variety on same line
        latin_str = latin_name
        if variety:
            latin_str += f"  '{variety}'"
        td2.text((H // 2, y0 + main_h + 4), latin_str, font=latin_font, fill=0, anchor="ma")

        td2.text((H // 2, y0 + main_h + 4 + latin_h + 4), date_str,
                 font=date_font, fill=0, anchor="ma")

        rotated = tmp2.rotate(angle, expand=True)
        sx      = section_idx * SEC_W
        paste_x = sx + (SEC_W - rotated.width) // 2
        paste_y = (H - rotated.height) // 2
        img.paste(rotated, (paste_x, paste_y))

    _draw_rotated_text(1, 270)
    _draw_rotated_text(2, 90)

    return img

# ---------------------------------------------------------------------------
# QR code label
# ---------------------------------------------------------------------------

def _make_qr_image(url, target_size):
    """Generate a QR code as a 1-bit PIL image sized close to target_size px."""
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=max(2, target_size // 33),
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img = img.resize((target_size, target_size), PIL.Image.NEAREST)
    return img.convert("1")


def create_label_qr(common_name, latin_name, date_str, plant_url,
                    variety=None, nickname=None, extra_notes=None):
    """Label with the plant name and a QR code linking to the plant's page."""
    W      = PRINTER_WIDTH
    margin = 18
    pad_t  = 26
    pad_b  = 20

    name_font  = _get_font("bold",    36)
    sub_font   = _get_font("italic",  20)
    latin_font = _get_font("italic",  20)
    var_font   = _get_font("regular", 16)
    date_font  = _get_font("regular", 15)

    hero = nickname if nickname else common_name
    sub  = common_name if nickname else None

    inner_w = W - margin * 2

    tmp = PIL.Image.new("1", (1, 1))
    td  = PIL.ImageDraw.Draw(tmp)

    hero_lines = _wrap(hero, name_font, inner_w)
    hero_h     = _ml_h(td, hero_lines, name_font)
    hero_w     = _ml_w(td, hero_lines, name_font)
    sub_h      = (_text_h(td, sub, sub_font) + 6) if sub else 0
    latin_h    = _text_h(td, latin_name, latin_font)
    var_h      = (_text_h(td, variety, var_font) + 6) if variety else 0
    date_h     = _text_h(td, date_str, date_font)

    qr_size  = int(W * 0.56)
    qr_size  = (qr_size // 4) * 4   # round to multiple of 4 for clean scaling

    total_h = pad_t + hero_h + 8 + sub_h + latin_h + 4 + var_h + 18 + qr_size + 14 + date_h + pad_b

    img = PIL.Image.new("1", (W, total_h), 1)
    d   = PIL.ImageDraw.Draw(img)

    # Border
    bx0, by0, bx1, by1 = margin - 5, 5, W - margin + 5, total_h - 6
    d.rectangle([bx0, by0, bx1, by1], outline=0, width=3)
    d.rectangle([bx0 + 6, by0 + 6, bx1 - 6, by1 - 6], outline=0, width=1)
    _square_notches(d, bx0 + 6, by0 + 6, bx1 - 6, by1 - 6)

    y = pad_t
    d.multiline_text(((W - hero_w) // 2, y), hero_lines, font=name_font, fill=0, align="center")
    y += hero_h + 8

    if sub:
        d.text((W // 2, y), sub, font=sub_font, fill=0, anchor="ma")
        y += sub_h

    d.text((W // 2, y), latin_name, font=latin_font, fill=0, anchor="ma")
    y += latin_h + 4

    if variety:
        d.text((W // 2, y), f"'{variety}'", font=var_font, fill=0, anchor="ma")
        y += var_h

    div_m = margin + 12
    d.line([(div_m, y + 7), (W - div_m, y + 7)], fill=0, width=1)
    d.line([(div_m, y + 9), (W - div_m, y + 9)], fill=0, width=1)
    y += 18

    qr_img = _make_qr_image(plant_url, qr_size)
    img.paste(qr_img, ((W - qr_size) // 2, y))
    y += qr_size + 14

    date_w = _text_w(td, date_str, date_font)
    d.text(((W - date_w) // 2, y), date_str, font=date_font, fill=0)

    return img


# ---------------------------------------------------------------------------
# Free-text label (non-plant)
# ---------------------------------------------------------------------------

def create_label_freetext(title, subtitle=None, body_md=None):
    """Classic double-border label with a title, optional subtitle, and MD body.

    Intended for non-plant uses (gift tags, jar labels, notes, etc.)."""
    W = PRINTER_WIDTH
    margin, pad_t, pad_b = 20, 32, 28

    title_font = _get_font("bold",    40)
    sub_font   = _get_font("italic",  22)
    md_fonts   = _build_md_fonts(16)

    tmp = PIL.Image.new("1", (1, 1))
    td  = PIL.ImageDraw.Draw(tmp)

    inner_w = W - margin * 2 - 24

    title       = (title or "").strip() or " "
    title_lines = _wrap(title, title_font, inner_w)
    title_w     = _ml_w(td, title_lines, title_font)
    title_h     = _ml_h(td, title_lines, title_font)

    sub = (subtitle or "").strip() or None
    if sub:
        sub_wrapped = _wrap(sub, sub_font, inner_w)
        sub_w_px    = _ml_w(td, sub_wrapped, sub_font)
        sub_h_px    = _ml_h(td, sub_wrapped, sub_font)
    else:
        sub_wrapped = None
        sub_w_px = sub_h_px = 0

    md_segs = _parse_md(body_md.strip()) if body_md and body_md.strip() else []
    body_h  = _md_height(td, md_segs, md_fonts, inner_w) if md_segs else 0

    rule_h = 14 if md_segs else 0

    total_h = pad_t + title_h + (10 + sub_h_px if sub else 0) + rule_h + body_h + pad_b
    img = PIL.Image.new("1", (W, total_h), 1)
    d   = PIL.ImageDraw.Draw(img)

    bx0, by0, bx1, by1 = margin - 5, 5, W - margin + 5, total_h - 6
    d.rectangle([bx0, by0, bx1, by1], outline=0, width=3)
    d.rectangle([bx0 + 6, by0 + 6, bx1 - 6, by1 - 6], outline=0, width=1)
    _square_notches(d, bx0 + 6, by0 + 6, bx1 - 6, by1 - 6)

    y = pad_t
    d.multiline_text(((W - title_w) // 2, y), title_lines,
                     font=title_font, fill=0, align="center")
    y += title_h

    if sub:
        y += 10
        d.multiline_text(((W - sub_w_px) // 2, y), sub_wrapped,
                         font=sub_font, fill=0, align="center")
        y += sub_h_px

    if md_segs:
        div_m = margin + 15
        y += 6
        d.line([(div_m, y), (W - div_m, y)], fill=0, width=1)
        d.line([(div_m, y + 2), (W - div_m, y + 2)], fill=0, width=1)
        y += 8
        _render_md(d, md_segs, md_fonts, margin + 15, y, inner_w)

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
