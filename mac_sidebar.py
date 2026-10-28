"""Cross-platform macOS sidebar companion using Tk and shared OpenCodex data."""
import json, subprocess, threading, time, tkinter as tk
from pathlib import Path
from sidebar_data import ROOT, CONFIG, load_config, fetch_usage, compact_tokens, sync_provider_items

BG='#f3f2f0'; INK='#4c4b49'; MUTED='#888783'; BORDER='#aaa9a6'
class MacSidebar:
    def __init__(self):
        self.config=load_config();self.usage=None;self.error=None;self.busy=False
        self.root=tk.Tk();self.root.overrideredirect(True);self.root.attributes('-topmost',True);self.root.configure(bg=BG)
        self.frame=tk.Frame(self.root,bg=BG);self.frame.pack(padx=6,pady=6);self.labels=[]
        self.root.bind('<Command-q>',lambda e:self.close());self.root.bind('<Button-3>',lambda e:self.close())
        self.refresh();self.root.after(1000,self.tick)
    def refresh(self):
        if self.busy:return
        self.busy=True
        def worker():
            try:self.result=fetch_usage();self.error=None
            except Exception as exc:self.result=None;self.error=type(exc).__name__
            self.root.after(0,self.apply)
        threading.Thread(target=worker,daemon=True).start()
    def apply(self):
        self.busy=False
        if self.result:
            self.usage=self.result
            try:self.config=sync_provider_items(self.config,self.usage);self.config['items']=[i for i in self.config['items'] if i.get('type')!='weekly_quota'];CONFIG.write_text(json.dumps(self.config,ensure_ascii=False,indent=2),encoding='utf-8')
            except Exception:pass
        self.draw()
    def draw(self):
        for child in self.frame.winfo_children():child.destroy()
        rows=[]
        for item in self.config.get('items',[]):
            if item.get('type')!='opencodex_today' or not item.get('enabled',True):continue
            row=(self.usage or {}).get('providers',{}).get(item.get('provider'));value=compact_tokens(row.get('totalTokens') if row else None)
            rows.append((item.get('label',item.get('provider')),value))
        for label,value in rows:
            box=tk.Frame(self.frame,bg=BG,highlightbackground=BORDER,highlightthickness=1);box.pack(fill='x',pady=2)
            tk.Label(box,text=label,font=('Arial',9),fg=MUTED,bg=BG).pack(pady=(3,0));tk.Label(box,text=value,font=('Arial',15,'bold'),fg=INK,bg=BG).pack(padx=10,pady=(0,3))
        if not rows:
            tk.Label(self.frame,text='OpenCodex 用量暂不可用',font=('Arial',10),fg=MUTED,bg=BG).pack(padx=10,pady=10)
        self.root.update_idletasks();self.root.geometry(f'+{max(0,self.root.winfo_screenwidth()-self.root.winfo_reqwidth()-20)}+100')
    def tick(self):
        if not self.busy:self.refresh()
        self.root.after(60000,self.tick)
    def close(self):self.root.destroy()
if __name__=='__main__':MacSidebar().root.mainloop()
