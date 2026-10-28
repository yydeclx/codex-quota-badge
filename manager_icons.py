"""Use a separate taskbar group and real DPI-sized Codex icons for this manager."""
import ctypes as C
from ctypes import wintypes as W
from sidebar_data import ROOT

def prepare_identity():
    shell=C.windll.shell32
    shell.SetCurrentProcessExplicitAppUserModelID.argtypes=[W.LPCWSTR]
    shell.SetCurrentProcessExplicitAppUserModelID.restype=C.c_long
    result=shell.SetCurrentProcessExplicitAppUserModelID('Local.CodexSidebar.Manager')
    if result:raise OSError('Cannot set sidebar manager application identity')

def set_titlebar_colors(root,hwnd=None):
    """Use the client theme for native captions; older Windows keeps its default."""
    u=C.windll.user32
    u.GetAncestor.argtypes=[W.HWND,W.UINT];u.GetAncestor.restype=W.HWND
    hwnd=hwnd or u.GetAncestor(root.winfo_id(),2)
    dwm=C.WinDLL('dwmapi')
    dwm.DwmSetWindowAttribute.argtypes=[W.HWND,W.DWORD,C.c_void_p,W.DWORD]
    dwm.DwmSetWindowAttribute.restype=C.c_long
    def colorref(color):
        r,g,b=root.winfo_rgb(color)
        return W.DWORD((r//257)|((g//257)<<8)|((b//257)<<16))
    results=[]
    for attribute,color in ((35,root.cget('bg')),(36,'#292927')):
        value=colorref(color)
        results.append(dwm.DwmSetWindowAttribute(hwnd,attribute,C.byref(value),C.sizeof(value)))
    return all(result==0 for result in results)

def set_window_icons(root):
    u=C.windll.user32
    u.LoadImageW.argtypes=[W.HINSTANCE,W.LPCWSTR,W.UINT,C.c_int,C.c_int,W.UINT];u.LoadImageW.restype=W.HANDLE
    u.SendMessageW.argtypes=[W.HWND,W.UINT,W.WPARAM,W.LPARAM];u.SendMessageW.restype=C.c_ssize_t
    u.GetAncestor.argtypes=[W.HWND,W.UINT];u.GetAncestor.restype=W.HWND
    u.GetDpiForWindow.argtypes=[W.HWND];u.GetDpiForWindow.restype=W.UINT
    u.DestroyIcon.argtypes=[W.HANDLE]
    root.update_idletasks();hwnd=u.GetAncestor(root.winfo_id(),2);dpi=u.GetDpiForWindow(hwnd) or 96
    icons=[]
    for kind,size in ((0,round(16*dpi/96)),(1,round(32*dpi/96))):
        icon=u.LoadImageW(None,str(ROOT/'codex-monochrome.ico'),1,size,size,0x10)
        if not icon:raise C.WinError()
        icons.append(icon);u.SendMessageW(hwnd,0x80,kind,icon)
    for previous in getattr(root,'codex_icon_handles',[]):u.DestroyIcon(previous)
    root.codex_icon_handles=icons
    set_titlebar_colors(root,hwnd)
    return hwnd
