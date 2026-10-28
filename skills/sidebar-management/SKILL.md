---
name: sidebar-management
description: 管理用户的 Codex 原生侧栏伴随插件，查看今日 ChatGPT/SKG Token 和周额度，开关、排序、刷新或扩展展示项。用户提到侧栏额度插件、侧栏 Token 框、隐藏展示项或调整展示顺序时使用。
---

# 本地侧栏插件管理

运行目录：用户解压本安装包的目录（包含 manage.py）
这里只管理用户的独立 Windows 伴随程序，不提供也不声称拥有官方 Codex 侧栏 UI 插槽。

- 查看配置：在运行目录执行 `python -X utf8 manage.py list`
- 查看状态：`python -X utf8 manage.py status`，并读取 `sidebar-status.json`
- 开关：`python manage.py enable <id>` / `python manage.py disable <id>`
- 排序：`python manage.py move <id> <从1开始的位置>`
- 启动：PowerShell 执行 `./start.ps1`
- 退出：`python manage.py stop`
- 默认 ID：`chatgpt_today`、`skg_today`、`weekly_quota`
- `sidebar-config.json` 支持热重载，至少保留一项；无效配置不替代当前有效配置
- 新增已有 provider 的今日 Token：添加唯一 id、type=opencodex_today、label、provider、enabled；刷新沿用同一次查询
- 新增数据源类型：扩展 sidebar_data.py 与 sidebar_plugin.py 的类型分发并补充测试

Token 数据源为本机 `ocx usage --range today --surface all --json`，使用 providers 的 totalTokens。
openai 对应 ChatGPT 通道；按 provider 汇总不是账号级跨设备统计。缓存已包含在输入中，不再次相加。
未上报请求、截断和过期必须保留提示，不能把无法查询伪装成零。不得输出凭据或读取 OpenCodex 的密钥文件。
不要重启用户的 Codex，不修改厂商安装文件，不擅自设置开机自启。
若插件尚未安装，源码包的存在不等于 Codex 插件管理页中已经启用；如实区分。

