#!/bin/sh
set -eu
command -v python3 >/dev/null || { echo '需要 Python 3.10+，请先安装 Python。'; exit 1; }
python3 -m pip install --user Pillow
chmod +x start-mac.sh
echo '安装完成：./start-mac.sh'
