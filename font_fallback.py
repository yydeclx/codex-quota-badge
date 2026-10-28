"""Cross-platform UI font selection with an explicit MiSans fallback chain."""
import tkinter as tk
from functools import lru_cache

FONT_PREFERENCE = ('MiSans', 'Microsoft YaHei', '微软雅黑', 'Segoe UI', 'Arial')

@lru_cache(maxsize=1)
def available_families():
    root = tk._default_root
    owned = False
    if root is None:
        root = tk.Tcl()
        owned = True
    try:
        families = root.call('font', 'families')
        return frozenset(str(item) for item in families)
    except tk.TclError:
        return frozenset()
    finally:
        if owned:
            root.destroy()

def ui_family():
    families = available_families()
    for name in FONT_PREFERENCE:
        if name in families:
            return name
    return 'TkDefaultFont'
