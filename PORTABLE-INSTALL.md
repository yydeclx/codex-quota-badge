# Codex 侧栏指标 Windows 可移植包

当前版本：1.5.2

普通安装包为 `codex-quota-badge-1.5.2.zip`，不包含个人配置、历史或网关二进制。新安装默认关闭实时指标补丁；已有安装更新时保留原设置与已有补丁文件

可选附件 `opencodex-telemetry-2.92.0-1.5.0.zip` 仅用于 Windows x64 的 OpenCodex 2.92.0。先安装普通包，再将附件解压到同一目录（形成 `gateway-telemetry/build/ocx.exe` 等文件），随后在管理页显式开启实时指标补丁。开关会校验版本和哈希，可能重启网关；忙碌或版本不符时拒绝切换。不要覆盖其他版本的 OpenCodex。更新侧栏本身不会自动应用此附件，官方 OpenCodex 更新后需重新适配


## 安装

1. 将整个目录解压到目标电脑的普通用户目录，例如 D:\Tools\codex-quota-badge。
2. 确认目标电脑已安装 Python 3.10 或更高版本，并且 python、pythonw.exe 可以在 PowerShell 7 中找到。
3. 用 PowerShell 7 进入解压目录，运行 .\install.ps1。

安装脚本只检查或安装 Pillow，不修改 Codex 配置、OpenCodex 配置或账号凭据。

## 运行

- 启动侧栏：.\start.ps1
- 打开管理页：.\manage.ps1
- 停止侧栏：python .\manage.py stop
- 查看状态：python .\manage.py status

## Windows 窗口兼容模式

默认使用透明子窗口，不会强制所有电脑使用 popup。仅当原始子窗口在该电脑不可见时，在安装目录用 PowerShell 7 执行：

```powershell
python .\manage.py stop
python .\window_mode.py --set owned-popup
.\start.ps1
```

切回默认方式时，将第二条命令改为 `python .\window_mode.py --set child`。模式选择保存到本机 `window-mode.json`，不修改 `sidebar-config.json`，也不涉及账号和网关重启。`python .\window_mode.py` 可查看保存的选择；`python .\manage.py status` 的 `mode` 可核对实际运行方式。

新版监督器按选择启动 `sidebar_compat.py` 或 `sidebar_plugin.py`。兼容模式仍使用 Codex 作为 owner，跟随宿主位置和显示状态，不使用全局置顶，不增加计时器或轮询。两种模式均支持管理页的水平偏移、动态模块间距和相同的图标样式。

从本地修改过的旧版本升级时，使用下方 `update.ps1`：它在覆盖程序前只读解析旧监督器的静态 `CHILD` 选择；确认指向 `sidebar_compat.py` 且本机尚无模式文件时，才迁移为 popup。已有显式选择优先，普通安装保持默认子窗口。不要直接解压覆盖旧目录，否则无法在覆盖后识别旧入口；对特殊自定义启动脚本，请升级前显式选择模式。

## 安全更新

1. 将新版压缩包解压到一个临时目录，不要覆盖当前安装目录。
2. 使用 PowerShell 7（`pwsh.exe`）进入新版解压目录。
3. 运行 `./update.ps1 -TargetPath "D:\Tools\codex-quota-badge"`，把参数改为原安装目录。

更新脚本会先在临时目录执行 `py_compile`，再停止当前侧栏、备份将被替换的程序文件、部署新版并再次执行编译检查，最后重启侧栏。更新中途失败时会恢复备份；若更新前侧栏正在运行，也会尝试恢复运行。备份保存在原安装目录的 `.update-backups` 文件夹中。

更新不会覆盖 `window-mode.json`、`sidebar-config.json`、`quota-history.json`、`quota-alert-state.json`、`connection-history.json`，也不会覆盖原安装目录中的状态文件和日志。更新清单不复制账号、Token、Cookie、OpenCodex/Codex 凭据或其他运行数据。

请勿把 `update.ps1` 放在原安装目录中直接对自身执行；新版解压目录和目标安装目录必须是两个不同目录。

## GitHub Release 自动检查

三台电脑需要同步同一套程序时，在每台目标电脑的插件目录创建 `update-config.json`，写入 GitHub 的 owner、repo、enabled 和 checkSeconds。插件按配置读取 GitHub Releases API；发现比当前版本新的 Release 后自动静默下载、校验并调用安全更新脚本。成功更新不弹窗；下载、校验或部署失败时显示错误提示，并由更新脚本回滚到旧版本。更新会保留该电脑自己的配置、额度历史、状态、日志和账号运行状态。

主电脑发布版本时，在插件目录执行 `publish-release.ps1`，传入 GitHub owner、repository、版本号和更新说明。脚本先运行专项测试和 Python 编译检查，再生成 ZIP、SHA-256 和 `release-manifest.json`；本机安装并登录 gh 时会自动创建 GitHub Release，否则只准备发布资产供手动上传。

自动更新不会复制 GitHub Token、OpenAI Token、Cookie、额度历史或设备状态。GitHub 仓库地址只保存在每台电脑自己的 `update-config.json` 中。

## macOS（Intel 与 Apple Silicon）

macOS 使用同一份数据层和动态供应商逻辑。Intel Mac 与 Apple Silicon Mac 只需安装各自架构的 Python 3.10+，然后在插件目录执行 chmod +x install-mac.sh start-mac.sh update-mac.sh、./install-mac.sh 和 ./start-mac.sh。

macOS 版本会从 OpenCodex 的 usage --surface all --json 结果读取实际供应商；侧栏会按当前供应商数量动态生成模块，不会写死 OCX、SKG 或 GPT。macOS 侧栏使用共享的额度/用量逻辑和 GitHub Release 检查；Windows 专用的原生子窗口、注册表自启动和 PowerShell 管理流程只在 Windows 使用。

## 数据和权限边界

- 额度读取使用 Codex app-server 的 account/rateLimits/read，只读。
- Token 读取使用本机 OpenCodex 的 usage 命令或记录，只读。
- 绿点读取当前 Codex 会话的只读状态；旧版 OpenCodex 活动接口可用时兼容读取，新版接口删除时使用会话状态回退。
- 包中不包含账号、Cookie、Token、额度历史、日志、截图或当前电脑的绝对路径。
- Pro 只有周窗口时显示周额度；Plus/Team 有 5 小时和周窗口时同时显示。
- 子代理会话不计入侧栏运行、未读或相关会话。

目标电脑的 Codex/OpenCodex 版本、DPI、窗口布局和 provider 配置不同，侧栏位置可能需要在管理页调整“底部留白”和“水平偏移”。水平偏移右移为正、左移为负，用于适配不同客户端左侧 Web 侧栏宽度。

## 卸载

停止侧栏后，直接删除本目录即可。若曾在管理页打开“随客户端自动显示”，请先关闭该开关，再删除目录。
