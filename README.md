# Codex Quota Badge

[![Latest Release](https://img.shields.io/github/v/release/yydeclx/codex-quota-badge?display_name=tag&sort=semver)](https://github.com/yydeclx/codex-quota-badge/releases/latest) [![Release Downloads](https://img.shields.io/github/downloads/yydeclx/codex-quota-badge/total)](https://github.com/yydeclx/codex-quota-badge/releases)

一个轻量的 Codex / OpenCodex 额度侧栏伴随工具。它把本机 provider 用量、周额度、运行状态和额度重置监测显示在 Codex 最左侧的原生侧栏区域。

支持 Windows、Intel Mac 和 Apple Silicon Mac。程序以本机只读数据为主，不修改 Codex 安装、不读取模型密钥、不发起模型请求。

> 当前最新版本：[v1.3.9](https://github.com/yydeclx/codex-quota-badge/releases/tag/v1.3.9)

## 预览

### 原生侧栏

![侧栏额度与运行状态](docs/images/current-sidebar.png)

Token 模块显示各 provider 的本机 Token 用量；主会话或原生子代理运行时显示绿色运行指示，完成未读状态单独显示。

### 管理窗口

![侧栏管理窗口](docs/images/manager-modern-0.png)

管理窗口支持模块开关、排序、间距、底部留白、水平偏移、Token 样式和周额度样式预览。

### 周额度与状态

![周额度样式](docs/images/quota-style-01-current.png)

![运行状态](docs/images/live-sidebar-green-diagnostic.png)

![额度重置监测](docs/images/reset-forecast-centered-check.png)

重置监测优先显示明确的公开信号；没有明确信号时显示 24 小时和 48 小时预测，并显示数据新鲜度。账号额度变化会用于确认是否真的完成重置。

## 功能

- **Provider 用量**：显示 ChatGPT、OpenCodex、SKG 以及其他已配置 provider 的本机 Token 用量
- **周额度**：显示剩余周额度、重置时间和 Pro / Plus 兼容数据
- **运行指示**：按实际模型路由归属 provider，支持主会话和原生子代理并行运行
- **未读状态**：保留主会话完成未读语义，不把子代理重复加入主任务列表
- **额度重置监测**：明确 Tibo 信号优先，无明确信号时显示 24h / 48h 概率
- **本机归因**：按额度采样、活动区间和本机 OpenAI 用量估算本机与其他设备的消耗
- **跨设备更新**：从 GitHub Releases 检查并静默更新，失败时保留旧版本
- **显示适配**：支持 DPI 缩放、水平偏移、MiSans → Microsoft YaHei 字体回退
- **低资源模式**：后台休眠、缓存快照、自适应任务扫描和分批管理页预览

## 快速安装

1. 打开[最新 Release](https://github.com/yydeclx/codex-quota-badge/releases/latest)
2. 下载对应平台的 ZIP 包并解压到固定目录
3. Windows 运行 ~install.ps1~ 或 ~start.ps1~
4. 打开 Codex，侧栏会自动附着到主窗口
5. 右键侧栏进入“侧栏管理”调整模块和位置

Windows 脚本使用 PowerShell 7。首次安装和跨电脑配置请参考 [PORTABLE-INSTALL.md](PORTABLE-INSTALL.md)。

## 配置示例

~sidebar-config.json~ 控制模块和布局：

~~~json
{
  "refreshSeconds": 60,
  "items": [
    {"id": "provider_ocx", "type": "opencodex_today", "provider": "OCX", "label": "OCX", "enabled": true, "gapAfter": 3},
    {"id": "weekly_quota", "type": "weekly_quota", "label": "周额度", "enabled": true, "gapAfter": 0}
  ],
  "quotaStyle": "01",
  "bottomOffset": 82,
  "horizontalOffset": 0,
  "activityEnabled": true
}
~~~

| 字段 | 作用 |
| --- | --- |
| ~items~ | 模块顺序、显示名称和启用状态 |
| ~refreshSeconds~ | 本机用量刷新间隔，范围 30–3600 秒 |
| ~quotaStyle~ | 周额度显示样式 |
| ~bottomOffset~ | 侧栏底部留白 |
| ~horizontalOffset~ | 适配不同侧栏宽度的左右偏移 |
| ~activityEnabled~ | 开关运行绿点和活动状态读取 |

## 数据口径与隐私

- Token 用量来自本机 OpenCodex usage 数据，不代表账号在其他电脑上的完整账单
- 周额度来自本机可用的 Codex / OpenCodex 额度接口
- 多台设备同时使用同一账号时，本机归因只能是采样估算，无法从官方周额度接口精确拆分
- 额度重置监测使用公开的只读预测和信号数据，并缓存最后一次成功结果
- 插件不保存模型正文、密钥或管理员令牌
- 状态中心只读取脱敏快照，诊断复制使用字段白名单
- 插件不会启动、停止或修改 OpenCodex 网关，也不会发送模型请求

## 自动更新

从 v1.3.8 开始，安装包包含 GitHub Release 更新器。它会定期检查 GitHub 最新 Release，下载完整 ZIP，校验 SHA256，再替换程序文件。更新时保留每台电脑自己的配置、额度历史、连接历史和账号状态；更新失败会保留旧版本。

本项目同时使用：

- Git 提交：保存源码历史和差异
- Git tag：标记源码版本，例如 ~v1.3.9~
- GitHub Release：提供可安装 ZIP 和校验值

## 开发与验证

~~~powershell
python -m unittest test_update_client test_reset_signals test_quota_windows test_sidebar_activity test_modules test_provider_indicators test_sidebar_data test_power_mode test_stability
python -m py_compile *.py
~~~

测试覆盖额度解析、OpenCodex 兼容、子代理运行指示、DPI 布局、缓存、后台省电、管理页和更新器。

## 故障排查

### 侧栏没有显示

运行 ~start.ps1~，确认 Codex 主窗口已经打开；也可以运行 ~manage.ps1~，在管理窗口中点击“启动 / 恢复侧栏”。

### 额度显示为破折号

破折号表示读取失败、数据过期或当前没有可用结果。插件会优先保留上一次成功数据，避免休眠后短暂闪烁为破折号。

### 其他电脑没有自动更新

确认插件版本至少为 v1.3.8，~update-config.json~ 中 ~enabled~ 为 ~true~，并且电脑可以访问 GitHub API 和 Release 下载地址。默认检查周期为 6 小时。

### 高 DPI 下位置不齐

在“侧栏管理 → 外观与布局”中调整“水平偏移”和“底部留白”。正数向右移动，负数向左移动。

## 相关文档

- [便携版安装与跨电脑更新](PORTABLE-INSTALL.md)
- [功能验收记录](FEATURES-QA.md)
- [活动状态实现说明](ACTIVITY-IMPLEMENTATION.md)
- [侧栏管理技能说明](skills/sidebar-management/SKILL.md)
- [所有 Releases](https://github.com/yydeclx/codex-quota-badge/releases)

## 免责声明

这是一个社区维护的本地伴随工具，不是 OpenAI、Codex 或 OpenCodex 官方组件。客户端更新可能改变窗口结构、额度接口或本地数据格式；遇到接口变化时，相关模块可能暂时显示缓存、过期或不可用状态。

## License

当前仓库尚未提供独立 LICENSE 文件。若要长期对外维护，建议补充明确的开源许可证。

