"""Optional per-user logon launcher; no administrator rights or scheduled tasks."""
import sys
import winreg
from pathlib import Path
KEY=r'Software\Microsoft\Windows\CurrentVersion\Run'
NAME='CodexSidebarCompanion'
def command():
    return '"'+str(Path(sys.executable).with_name('pythonw.exe'))+'" "'+str(Path(__file__).with_name('sidebar_supervisor.py'))+'"'
def enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,KEY) as key:return winreg.QueryValueEx(key,NAME)[0]==command()
    except FileNotFoundError:return False
def set_enabled(value):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER,KEY) as key:
        if value:winreg.SetValueEx(key,NAME,0,winreg.REG_SZ,command())
        else:
            try:winreg.DeleteValue(key,NAME)
            except FileNotFoundError:pass
