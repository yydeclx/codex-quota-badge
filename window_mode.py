"""Persistent Windows window choice, independent of replaceable program files."""
import argparse
import ast
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parent
MODE_FILE='window-mode.json'
ENTRIES={'child':'sidebar_plugin.py','owned-popup':'sidebar_compat.py'}


def load_mode(root=ROOT):
    path=Path(root)/MODE_FILE
    try:
        with path.open(encoding='utf-8-sig') as stream:
            value=json.loads(stream.read(1025))
    except FileNotFoundError:
        return 'child'
    mode=value.get('mode') if isinstance(value,dict) else None
    if mode not in ENTRIES:
        raise ValueError('Invalid window-mode.json mode; expected child or owned-popup')
    return mode


def child_entry(root=ROOT):
    root=Path(root)
    entry=root/ENTRIES[load_mode(root)]
    if not entry.is_file():
        raise FileNotFoundError('Missing sidebar entry: '+entry.name)
    return entry


def _path_literal(node):
    # Inspect legacy code without importing/executing it. Only a statically
    # selected CHILD filename counts; merely having a compat file is not opt-in.
    if isinstance(node,ast.Constant) and isinstance(node.value,str):
        return node.value.replace('\\','/').rsplit('/',1)[-1]
    if isinstance(node,ast.BinOp) and isinstance(node.op,ast.Div):
        return _path_literal(node.right)
    if (isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute)
            and node.func.attr in ('joinpath','with_name','join') and node.args):
        return _path_literal(node.args[-1])
    return None


def legacy_popup_selected(root):
    path=Path(root)/'sidebar_supervisor.py'
    try:
        with path.open(encoding='utf-8-sig') as stream:
            source=stream.read(131073)
    except FileNotFoundError:
        return False
    if len(source)>131072:
        raise ValueError('Legacy supervisor is too large to inspect')
    tree=ast.parse(source)
    choices=[]
    for statement in tree.body:
        if isinstance(statement,ast.Assign):
            targets=statement.targets
        elif isinstance(statement,ast.AnnAssign):
            targets=[statement.target]
        else:
            continue
        if any(isinstance(t,ast.Name) and t.id=='CHILD' for t in targets):
            choices.append(_path_literal(statement.value))
    return bool(choices and choices[-1]=='sidebar_compat.py')


def migrate(root):
    root=Path(root)
    if (root/MODE_FILE).exists():
        return {'created':False,'mode':load_mode(root)}
    if not legacy_popup_selected(root):
        return {'created':False,'mode':'child'}
    try:
        # Exclusive create: never replace a concurrently selected local mode.
        with (root/MODE_FILE).open('x',encoding='utf-8') as stream:
            stream.write(json.dumps({'mode':'owned-popup'})+'\n')
            stream.flush();os.fsync(stream.fileno())
    except FileExistsError:
        return {'created':False,'mode':load_mode(root)}
    return {'created':True,'mode':'owned-popup'}


def main():
    parser=argparse.ArgumentParser(description='Choose the Windows sidebar window mode (restart sidebar to apply)')
    parser.add_argument('--migrate',type=Path,help='Preserve a legacy supervisor selection during update')
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--set',choices=tuple(ENTRIES))
    args=parser.parse_args()
    if args.migrate:
        if args.set:parser.error('--migrate and --set cannot be combined')
        result=migrate(args.migrate)
    elif args.set:
        from sidebar_modules import atomic_json
        atomic_json(args.root/MODE_FILE,{'mode':args.set})
        result={'mode':load_mode(args.root),'restartRequired':True}
    else:
        result={'mode':load_mode(args.root)}
    print(json.dumps(result))


if __name__=='__main__':
    main()
