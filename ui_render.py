"""Bounded raster/font caches shared by native UI surfaces."""
from functools import lru_cache,wraps
from collections import OrderedDict
from pathlib import Path
import json,os
from PIL import Image, ImageDraw, ImageFilter, ImageFont


def popup_frame(p, width, height, bottom, *, dark=False):
    """Shared chrome for the composer and sidebar popups; leave content and hit areas in place."""
    image = Image.new('RGBA', (p(width), p(height)))
    shadow = Image.new('RGBA', image.size)
    ImageDraw.Draw(shadow).rounded_rectangle((p(8), p(10), p(width - 8), p(bottom + 2)),
                                             p(12), fill=(0, 0, 0, 88 if dark else 39))
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(p(4))))
    ImageDraw.Draw(image).rounded_rectangle((p(7), p(6), p(width - 7), p(bottom)), p(12),
        fill='#20242b' if dark else '#ffffff', outline='#404751' if dark else '#e3e6eb', width=max(1, p(.6)))
    return image

def _font_candidates(path):
    name=Path(path).name.lower()
    local=Path(os.environ.get('LOCALAPPDATA',''))/'Microsoft/Windows/Fonts'
    if name in ('msyh.ttc','msyhbd.ttc'):
        yield local/('MiSans-Demibold.ttf' if name=='msyhbd.ttc' else 'MiSans-Regular.ttf')
        yield Path('C:/Windows/Fonts')/name
        yield Path('/System/Library/Fonts/Hiragino Sans GB.ttc')
        yield Path('/System/Library/Fonts/Hiragino Sans W3.ttc')
        yield Path('/Library/Fonts/Arial Unicode.ttf')
    yield Path(path)

@lru_cache(maxsize=96)
def font(path,size):
    name=Path(path).name.lower()
    for candidate in _font_candidates(path):
        if candidate.exists():
            return ImageFont.truetype(str(candidate),size)
    return ImageFont.load_default(size=max(8,int(size)))

def raster_cache(fn):
    cache=OrderedDict();used=0
    @wraps(fn)
    def draw(*args,**kwargs):
        nonlocal used
        key=json.dumps([args,kwargs],ensure_ascii=False,sort_keys=True)
        if key in cache:
            value,size=cache.pop(key);cache[key]=(value,size);return value
        value=fn(*args,**kwargs);image=value[0] if isinstance(value,tuple) else value
        size=image.width*image.height*4
        if size<=4*1024*1024:
            while cache and (used+size>4*1024*1024 or len(cache)>=12):
                _,(_,old)=cache.popitem(last=False);used-=old
            cache[key]=(value,size);used+=size
        return value
    return draw
