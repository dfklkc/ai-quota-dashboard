AI 桌面卡片首个独立安装版本。

- 新增统一 macOS 图标，应用程序入口与安装包保持一致。
- 提供通用 PKG 和 DMG，支持 macOS 14+、Apple Silicon 与 Intel。
- 内置两种架构的 Python 运行时、Chrome 桥接和全部查询脚本，无需额外安装开发工具。
- 首次启动生成无凭证的默认配置，升级保留原有本机设置。
- GPT 跟随本机与 Chrome 登录账号，展示 Codex 使用额度；包含 Antigravity 与 LinksAPI 数据源。
- LinksAPI 区分 Key 用量与钱包余额；未取得的余额明确显示待配置。
- 安装包不含作者账号、API Key、浏览器会话或额度缓存。

验证：51 项 Python 回归检查；Apple Silicon 原生与 Intel/Rosetta 离线自检；现有配置保留；签名、PKG 解包、DMG 挂载和 SHA-256 校验。

此版本采用 ad-hoc 本地签名，尚未进行 Developer ID 签名和 Apple 公证。下载后 macOS 可能要求用户在系统设置中确认来源。

下载 `.pkg` 直接安装，或打开 `.dmg` 后将应用拖入 Applications。`SHA256SUMS.txt` 用于校验下载文件。
