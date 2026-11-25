"""Build a self-contained HTML preview from the prototype sources."""
import argparse
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('output', type=Path)
args = parser.parse_args()
root = Path(__file__).resolve().parent
html = (root / 'preview.html').read_text(encoding='utf-8')
css = (root / 'preview.css').read_text(encoding='utf-8')
js = (root / 'inline-usage.js').read_text(encoding='utf-8')
html = html.replace('<link rel="stylesheet" href="preview.css">', '<style>\n' + css + '</style>')
html = html.replace('<script src="inline-usage.js"></script>', '<script>\n' + js + '</script>')
with args.output.open('x', encoding='utf-8') as output:
    output.write(html)
