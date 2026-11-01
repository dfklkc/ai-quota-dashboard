<p align="center"><img src="assets/AppIcon.png" width="128" alt="AI 桌面卡片图标"></p>
<h1 align="center">AI 桌面卡片</h1>
<p align="center">在 macOS 桌面查看 AI 账号额度与中转站费用。</p>

## 下载与安装

在本仓库 [Releases](https://github.com/dfklkc/ai-quota-dashboard/releases/latest) 下载 `AIQuotaDashboard-1.0.0-macOS-universal.pkg`，或下载 DMG 后把应用拖入 Applications。

- macOS 14 或更高版本；支持 Apple Silicon 与 Intel。
- 内置独立 Python 运行时，使用者无需安装 Python、Xcode 或命令行工具。
- 本版本使用 ad-hoc 本地签名；尚无 Apple Developer ID 签名或公证。下载后 macOS 可能要求用户在系统设置中确认来源。
- `SHA256SUMS.txt` 可用于校验安装包。

## 能查看什么

| 来源 | 数据范围 | 条件 |
| --- | --- | --- |
| App | 当前本机登录账号的 **Codex 使用额度** | 读取当前用户的 Codex 登录信息 |
| Chrome | 当前 ChatGPT 会话对应的 **Codex 使用额度** | 已登录页面、现有自动化与 Apple 事件 JavaScript 权限 |
| Antigravity | 服务实际提供的 Gemini / Claude 额度窗口 | 本机应用及额度服务运行中 |
| LinksAPI | 账户钱包金额，或单个 API Key 的费用用量 | 在设置中提供匹配查询范围的凭证 |

GPT 卡片不是 ChatGPT 聊天次数计数器。不存在的窗口显示“未提供”。当前会话读取失败时，不沿用上一个账号的额度。

LinksAPI 的“未设限 Key”不等于“账户余额无限”：它可以提供 Key 用量，但没有返回的钱包余额会明确标为待配置。钱包余额和 Key 额度分别显示，不从小时额度或其他账号推算。

## 日常使用

- 自动每分钟检查；连接失败按 60 / 120 / 240 / 300 秒退避重试。
- 手动刷新可立即重试；各来源完成后独立显示。
- 可切换紧凑模式、置顶、低额度阈值。系统通知默认关闭。
- 关闭卡片仅隐藏窗口，通过菜单栏重新显示或退出。再次打开应用也会恢复窗口。
- 首次运行生成默认配置。升级保留已有用户配置、登录凭证与显示偏好。

## 数据与隐私

发布包不包含任何真实账号、浏览器会话、API Key、密码或额度缓存。

用户数据保存在 `~/.swiftbar/`，与应用包分离。手动保存的中转站查询凭证保存在专用 `tokens/linksapi.json`，权限为 `0600`；跟随 Claude 模式直接读取本机现有配置，不复制 Key。卸载应用不会删除这些数据。

接口均使用 HTTPS 读取查询数据；不会为了显示余额发送模型生成请求。LinksAPI 凭证仅发往固定官方主机，拒绝自动重定向及跨站发送。

## 从源码构建

开发机需要 macOS、Swift / Clang / Xcode Command Line Tools、Python 3 和 GitHub CLI。构建时从 `astral-sh/python-build-standalone` 的固定版本下载运行时，并核对仓库记录的 SHA-256。

```sh
python3 -m unittest discover -s tests -v
python3 scripts/build.py
python3 scripts/verify_release.py
```

产物位于 `dist/`：PKG、DMG、发布清单及 SHA-256 文件。构建脚本只复制明确列出的源码和默认配置，不从开发者的用户目录取数据。

可用干净数据目录进行离线自检：

```sh
AI_QUOTA_DATA_DIR="$(mktemp -d)" ".build.noindex/AI 桌面卡片.app/Contents/MacOS/AIDesktopCard" --self-check
```

`--self-check` 验证默认配置及内置运行时，不读取登录账号或发起额度请求。Chrome 延迟响应的人工回归检查在 `tests/check_chrome_bridge_live.py`，需要运行中的 Chrome 与 ChatGPT 页面。

## 项目结构

- `src/`：SwiftUI 主程序、Chrome 原生桥接和 Python 查询模块。
- `assets/`：图标 PNG、ICNS 与生成提示词。
- `config/`：不含凭证的默认配置。
- `runtime/manifest.json`：运行时来源、固定版本及校验摘要。
- `scripts/`：可复现构建与发行包验证。
- `tests/`：额度、账号切换、隐私和接口异常回归检查。

第三方运行时许可说明见 `docs/THIRD_PARTY.txt`，完整许可随运行时保留在应用包中。
