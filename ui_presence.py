"""Identify companion windows without relying on titles or process names."""
import ctypes as C
from ctypes import wintypes as W
PROPERTY='CodexQuotaCompanionWindow'
def mark(root):
    u=C.WinDLL('user32',use_last_error=True)
    u.GetAncestor.argtypes=[W.HWND,W.UINT];u.GetAncestor.restype=W.HWND
    u.SetPropW.argtypes=[W.HWND,W.LPCWSTR,W.HANDLE];u.SetPropW.restype=W.BOOL
    root.update_idletasks()
    hwnd=u.GetAncestor(root.winfo_id(),2)
    if not u.SetPropW(hwnd,PROPERTY,1):raise C.WinError(C.get_last_error())
def is_companion(hwnd):
    u=C.WinDLL('user32',use_last_error=True)
    u.GetPropW.argtypes=[W.HWND,W.LPCWSTR];u.GetPropW.restype=W.HANDLE
    return bool(hwnd and u.GetPropW(hwnd,PROPERTY))
