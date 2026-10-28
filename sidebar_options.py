"""Optional compact rows and persistent, once-per-cycle quota alerts."""
import json,time
from pathlib import Path
class CompactRows:
    def __init__(self):self.since={};self.hidden=set()
    def update(self,config,snapshot,now=None):
        now=time.monotonic() if now is None else now
        hidden=set()
        valid=not snapshot.get('taskError') and 0<=time.time()-snapshot.get('updatedAt',0)<=20
        for item in config['items']:
            key=item['id'];kind=item['type']
            empty=config.get('compactMode',False) and valid and kind in ('tasks_running','tasks_done') and not snapshot.get('tasks',{}).get(kind)
            # Pending status is not reliably observable; unknown must remain visible.
            if empty:
                self.since.setdefault(key,now)
                if now-self.since[key]>=5:hidden.add(key)
            else:self.since.pop(key,None)
        changed=hidden!=self.hidden;self.hidden=hidden
        return changed
class QuotaAlert:
    def __init__(self,path):
        self.path=Path(path)
        try:self.sent=json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError,ValueError):self.sent={}
    def due(self,config,data):
        if not config.get('quotaAlertEnabled',False) or not data:return False
        threshold=config.get('quotaAlertThreshold',20)
        return data['remaining']<=threshold and not (abs(self.sent.get('reset',0)-data['resetsAt'])<=2 and self.sent.get('threshold')==threshold)
    def mark(self,config,data):
        from sidebar_modules import atomic_json
        self.sent={'reset':data['resetsAt'],'threshold':config['quotaAlertThreshold']}
        atomic_json(self.path,self.sent)
if __name__=='__main__':
    import sys,tkinter as tk
    from tkinter import messagebox
    root=tk.Tk();root.withdraw()
    messagebox.showinfo('周额度提醒',f'本周剩余额度 {sys.argv[1]}%，已达到你设置的 {sys.argv[2]}% 提醒阈值。',parent=root)
    root.destroy()
