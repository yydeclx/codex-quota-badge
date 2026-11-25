"""Reuse the installed Codex UI font in this process, with a CJK fallback."""
from functools import lru_cache
from io import BytesIO
import json
from pathlib import Path
import re
import struct

from PIL import ImageFont

from ui_render import font


@lru_cache(maxsize=2)
def _faces(archive):
    if not archive:
        return {}
    try:
        from fontTools.ttLib import TTFont
        with Path(archive).open('rb') as source:
            _, header, _, length = struct.unpack('<IIII', source.read(16))
            if not 0 < length <= header <= 16 * 1024 * 1024:
                return {}
            assets = json.loads(source.read(length))['files']['webview']['files']['assets']['files']
            faces = {}
            for name, entry in assets.items():
                match = re.fullmatch(r'OpenAISans-(Regular|Semibold).[0-9a-f]+.woff2', name)
                if not match or not 0 < entry.get('size', 0) < 256 * 1024 or 'offset' not in entry:
                    continue
                source.seek(8 + header + int(entry['offset']))
                with TTFont(BytesIO(source.read(entry['size']))) as face:
                    supported = frozenset(face.getBestCmap())
                    face.flavor = None
                    result = BytesIO()
                    face.save(result)
                    faces[match[1]] = result.getvalue(), supported
            return faces
    except (OSError, ValueError, TypeError, KeyError, ImportError, struct.error):
        return {}
    except Exception:
        # A changed archive or optional font decoder must not break the widget.
        return {}


@lru_cache(maxsize=48)
def scaled_fonts(archive, size, bold=False):
    fallback = font('C:/Windows/Fonts/' + ('msyhbd.ttc' if bold else 'msyh.ttc'), size)
    contents = _faces(archive).get('Semibold' if bold else 'Regular')
    if not contents:
        return fallback, fallback, frozenset()
    raw, supported = contents
    return ImageFont.truetype(BytesIO(raw), size), fallback, supported


def _runs(text, fonts):
    primary, fallback, supported = fonts
    if primary is fallback:
        yield text, fallback
        return
    start = 0
    use_primary = bool(text and ord(text[0]) in supported)
    for index, char in enumerate(text):
        use_primary_now = ord(char) in supported
        if use_primary_now != use_primary:
            yield text[start:index], primary if use_primary else fallback
            start, use_primary = index, use_primary_now
    if text:
        yield text[start:], primary if use_primary else fallback


def _cells(draw, text, fonts, tabular=False):
    for part, face in _runs(text, fonts):
        if not tabular:
            yield part, face, draw.textlength(part, font=face)
            continue
        digit_width = max(draw.textlength(digit, font=face) for digit in '0123456789')
        for cell in re.findall(r'[0-9]|[^0-9]+', part):
            advance = digit_width if cell in '0123456789' else draw.textlength(cell, font=face)
            yield cell, face, advance


def width(draw, text, fonts, tabular=False):
    return sum(advance for _, _, advance in _cells(draw, text, fonts, tabular))


def fit(draw, text, fonts, limit):
    if width(draw, text, fonts) <= limit:
        return text
    if width(draw, '…', fonts) > limit:
        return ''
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if width(draw, text[:mid] + '…', fonts) <= limit:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + '…'


def write(draw, position, text, fonts, fill, align='left', tabular=False):
    x, center = position
    cells = tuple(_cells(draw, text, fonts, tabular))
    if align != 'left':
        x -= sum(cell[2] for cell in cells) / (2 if align == 'center' else 1)
    reference = fonts[1]
    _, top, _, bottom = reference.getbbox('上下文 15%', anchor='ls')
    baseline = center - (top + bottom) / 2
    for part, face, advance in cells:
        inset = (advance - draw.textlength(part, font=face)) / 2 if tabular and part in '0123456789' else 0
        draw.text((x + inset, baseline), part, font=face, fill=fill, anchor='ls')
        x += advance
