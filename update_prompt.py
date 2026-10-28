import json,tkinter as tk
from tkinter import messagebox
from pathlib import Path
ROOT=Path(__file__).resolve().parent
try:
    data=json.loads((ROOT/'update-status.json').read_text(encoding='utf-8'))
    if data.get('state')!='update_available':raise SystemExit(0)
    from update_client import download_and_update
    process=download_and_update(data)
    returncode=process.wait()
    if returncode != 0: raise RuntimeError('更新脚本退出码 {}'.format(returncode))
    (ROOT/'update-status.json').write_text(json.dumps({'state':'updated','version':data.get('version'),'updatedAt':__import__('time').time()},ensure_ascii=False),encoding='utf-8')
except Exception as exc:
    detail='{}: {}'.format(type(exc).__name__,exc)
    (ROOT/'update-status.json').write_text(json.dumps({'state':'error','error':detail},ensure_ascii=False),encoding='utf-8')
    try:
        root=tk.Tk();root.withdraw();messagebox.showerror('Codex额度插件更新失败','新版没有应用，旧版本已保留。\n\n{}'.format(detail),parent=root);root.destroy()
    except Exception: pass
