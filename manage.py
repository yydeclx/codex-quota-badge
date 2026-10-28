"""Local companion management; no Codex package or OpenCodex config changes."""
import argparse
import ctypes as C
from ctypes import wintypes as W
import json
from pathlib import Path
from sidebar_data import ROOT, load_config, save_config

parser=argparse.ArgumentParser(description='Manage the native Codex sidebar companion')
parser.add_argument('action',choices=['list','enable','disable','move','status','stop'])
parser.add_argument('item',nargs='?')
parser.add_argument('position',nargs='?',type=int)
args=parser.parse_args()
if args.action in ('status','stop'):
    from native_badge import Win
    state=json.loads((ROOT/'status.json').read_text(encoding='utf-8'))
    win=Win();pid=W.DWORD();win.u.GetWindowThreadProcessId(state['hwnd'],C.byref(pid))
    alive=pid.value==state['pid'] and win.process_path(state['hwnd']).lower().endswith('pythonw.exe')
    if args.action=='stop':
        if alive:win.u.PostMessageW(state['hwnd'],0x10,0,0)
        print('Stop requested' if alive else 'Not running')
    else:
        print(json.dumps({'running':alive,**state},ensure_ascii=False,indent=2))
else:
    config=load_config()
    if args.action=='list':print(json.dumps(config,ensure_ascii=False,indent=2))
    else:
        item=next((i for i in config['items'] if i['id']==args.item),None)
        if item is None:parser.error('Unknown item id')
        if args.action=='move':
            if args.position is None or not 1<=args.position<=len(config['items']):parser.error('Position must be within item list (one-based)')
            config['items'].remove(item);config['items'].insert(args.position-1,item)
        else:
            item['enabled']=args.action=='enable'
        save_config(config)
        print('Saved; running companion reloads configuration automatically')
