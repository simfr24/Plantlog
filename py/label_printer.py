"""
Label generation and Bluetooth printing for the YHK-835E thermal printer.

Extracted from plant-label-simple.py for use by the Flask web app.
"""

import io

import PIL.Image
import PIL.ImageDraw
import PIL.ImageFont


PRINTER_WIDTH = 384


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


def _wrap_text(text, font, max_width):
    words = text.split()
    lines = [""]
    for word in words:
        test = f"{lines[-1]} {word}".strip()
        if font.getlength(test) <= max_width:
            lines[-1] = test
        else:
            lines.append(word)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Label image generators
# ---------------------------------------------------------------------------

def create_label_classic(common_name, latin_name, date_str, variety=None):
    """Return a 1-bit PIL Image with a classic rectangular label layout."""
    W = PRINTER_WIDTH
    margin, pad_t, pad_b = 20, 30, 25

    name_font  = _get_font("bold",    40)
    latin_font = _get_font("italic",  22)
    date_font  = _get_font("regular", 16)
    var_font   = _get_font("regular", 14)

    tmp = PIL.Image.new("1", (1, 1))
    td  = PIL.ImageDraw.Draw(tmp)

    name_lines = _wrap_text(common_name, name_font, W - margin * 2 - 20)
    name_bbox  = td.multiline_textbbox((0, 0), name_lines, font=name_font)
    name_w, name_h = name_bbox[2] - name_bbox[0], name_bbox[3] - name_bbox[1]

    latin_bbox = td.textbbox((0, 0), latin_name, font=latin_font)
    latin_h    = latin_bbox[3] - latin_bbox[1]

    date_bbox  = td.textbbox((0, 0), date_str, font=date_font)
    date_w, date_h = date_bbox[2] - date_bbox[0], date_bbox[3] - date_bbox[1]

    var_h = 0
    if variety:
        var_bbox = td.textbbox((0, 0), variety, font=var_font)
        var_h = var_bbox[3] - var_bbox[1] + 6

    total_h = pad_t + name_h + 12 + latin_h + (var_h) + 18 + 10 + date_h + pad_b
    img = PIL.Image.new("1", (W, total_h), 1)
    d   = PIL.ImageDraw.Draw(img)

    bx0, by0, bx1, by1 = margin - 5, 5, W - margin + 5, total_h - 6
    d.rectangle([bx0, by0, bx1, by1], outline=0, width=3)
    d.rectangle([bx0 + 6, by0 + 6, bx1 - 6, by1 - 6], outline=0, width=1)

    y = pad_t
    d.multiline_text(((W - name_w) // 2, y), name_lines, font=name_font, fill=0, align="center")
    y += name_h + 8
    d.text((W // 2, y), latin_name, font=latin_font, fill=0, anchor="ma")
    y += latin_h + 6

    if variety:
        d.text((W // 2, y), variety, font=var_font, fill=0, anchor="ma")
        y += var_h

    div_m = margin + 15
    d.line([(div_m, y - 1), (W - div_m, y - 1)], fill=0, width=1)
    d.line([(div_m, y + 1), (W - div_m, y + 1)], fill=0, width=1)
    y += 15

    d.text(((W - date_w) // 2, y), date_str, font=date_font, fill=0)
    return img


def create_label_circular(common_name, latin_name, date_str, variety=None):
    """Return a 1-bit PIL Image with a circular label layout."""
    diameter = int(PRINTER_WIDTH * 0.66)
    img = PIL.Image.new("1", (PRINTER_WIDTH, diameter + 20), 1)
    d   = PIL.ImageDraw.Draw(img)

    cx, cy, r = PRINTER_WIDTH // 2, diameter // 2 + 10, diameter // 2
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=0, width=2)
    d.ellipse([cx - r + 5, cy - r + 5, cx + r - 5, cy + r - 5], outline=0, width=1)

    name_font  = _get_font("bold",    28)
    latin_font = _get_font("italic",  14)
    date_font  = _get_font("regular", 12)
    var_font   = _get_font("regular", 11)

    max_text_w = int(r * 1.4)
    name_lines = _wrap_text(common_name, name_font, max_text_w)

    name_bbox  = d.multiline_textbbox((0, 0), name_lines, font=name_font)
    name_h     = name_bbox[3] - name_bbox[1]
    latin_bbox = d.textbbox((0, 0), latin_name, font=latin_font)
    date_bbox  = d.textbbox((0, 0), date_str,   font=date_font)

    var_h = 0
    if variety:
        var_bbox = d.textbbox((0, 0), variety, font=var_font)
        var_h = var_bbox[3] - var_bbox[1] + 4

    total_h = (name_h + 8 + (latin_bbox[3] - latin_bbox[1]) +
               var_h + 6 + (date_bbox[3] - date_bbox[1]))
    y = cy - total_h // 2

    d.multiline_text((cx, y), name_lines, font=name_font, fill=0, anchor="ma", align="center")
    y += name_h + 8
    d.text((cx, y), latin_name, font=latin_font, fill=0, anchor="ma")
    y += (latin_bbox[3] - latin_bbox[1]) + 6

    if variety:
        d.text((cx, y), variety, font=var_font, fill=0, anchor="ma")
        y += var_h

    d.text((cx, y), date_str, font=date_font, fill=0, anchor="ma")
    return img


# ---------------------------------------------------------------------------
# Rendering to bytes / PNG
# ---------------------------------------------------------------------------

def label_to_png_bytes(img: PIL.Image.Image) -> bytes:
    """Return PNG bytes from a label PIL image, suitable for HTTP response."""
    # Scale up 2× for readability in browser preview
    preview = img.resize((img.width * 2, img.height * 2), PIL.Image.NEAREST)
    buf = io.BytesIO()
    preview.save(buf, format="PNG")
    return buf.getvalue()
