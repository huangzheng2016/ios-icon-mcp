# ios-icon-mcp

[![CI](https://github.com/huangzheng2016/ios-icon-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/huangzheng2016/ios-icon-mcp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/ios-icon-mcp.svg)](https://pypi.org/project/ios-icon-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/ios-icon-mcp.svg)](https://pypi.org/project/ios-icon-mcp/)

在任意支持 MCP 的 AI 客户端里，用自然语言整理 iPhone 主屏幕：移动 App 和网页书签、创建文件夹、调整页面，并查看包含小组件占位的文本布局。

**无需越狱、无需监督设备，也不需要 Apple Configurator。**

[English README](README.md)

> **注意：** 本项目通过 pymobiledevice3 使用 iOS 私有接口，系统更新可能改变兼容性。写入前请使用预览和备份功能。

## 功能

- 读取 App、文件夹、网页书签和小组件，包括本地化名称与包名。
- 按设备返回的行列数，将每页渲染为文本网格。
- 编辑保存的布局文件，或直接对设备进行单项修改。
- 创建文件夹，支持多页文件夹。
- 应用布局文件前默认预览完整差异。
- 拒绝未知 ID、意外移除 App、页面容量溢出和小组件位移。
- 每次写入前自动备份，写入后验证实际布局与小组件。
- 完整恢复备份；恢复前可预览，并会再保存一份当前状态。

## 环境要求

- macOS，使用系统自带的 `usbmuxd`。
- 一台通过 USB 连接的 iPhone，已解锁并信任这台 Mac。
- [uv](https://docs.astral.sh/uv/)，用于提供 `uvx` 命令。

MCP 服务器需要 Python 3.10 或更新版本；Python 环境和依赖由 `uvx` 隔离管理。

## 安装

在 MCP 客户端配置中添加：

```json
{
  "mcpServers": {
    "ios-icon": {
      "command": "uvx",
      "args": ["ios-icon-mcp"]
    }
  }
}
```

修改配置后重启 MCP 客户端。首次运行会将软件包下载到隔离环境。

如果希望直接从 GitHub 运行：

```json
{
  "mcpServers": {
    "ios-icon": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/huangzheng2016/ios-icon-mcp",
        "ios-icon-mcp"
      ]
    }
  }
}
```

需要刷新 PyPI 包缓存时，可执行一次 `uvx --refresh ios-icon-mcp`，或用该命令重启客户端。

## 快速使用

连接并解锁 iPhone，然后对 AI 客户端说：

- “把我的主屏幕渲染成文本网格。”
- “导出每个 App 的名称和包名。”
- “保存当前布局，把音乐 App 放进文件夹，然后先预览。”
- “只把这个 App 移到第 2 页，不改其他内容。”

批量整理的推荐流程：

1. 用 `save_layout` 保存当前布局快照。
2. 用 `move_icon`、`swap_icons` 或 `create_folder` 修改该文件。
3. 用 `apply_icon_layout` 预览完整差异，默认不会写入。
4. 检查差异后，以 `dry_run=false` 调用，真正写入。
5. 检查返回结果中的 `verified`。每次写入都会自动备份。

`*_on_device` 系列用于单项直接修改，除非显式设置 `dry_run=true`，否则会立即写入。

## 布局文件格式

布局是一个 JSON 数组。下标 0 是 Dock，后续数组依次为主屏幕页面。字符串是 App 包名或网页书签 URL。文件夹也是数组，第一项为文件夹名称。

```json
[
  ["com.apple.mobilephone", "com.apple.MobileSMS"],
  [
    "com.apple.mobilesafari",
    ["社交", "com.example.chat", "com.example.social"],
    ["多页文件夹", ["com.example.one"], ["com.example.two"]]
  ]
]
```

以上 ID 仅用于演示语法。实际编辑时，请使用 `get_icon_layout` 或 `export_icon_detail` 返回的 ID。

- 单页文件夹：`["文件夹", "app.one", "app.two"]`。
- 多页文件夹：`["文件夹", ["app.one"], ["app.two"]]`。
- 可编辑格式会有意省略小组件；写入时会从设备当前状态合并，并进行安全校验。

## 工具一览

| 工具 | 作用 |
|---|---|
| `device_info` | 读取已连接设备的名称、机型和系统版本 |
| `render_home_screen` | 将每页渲染为包含小组件占位的文本网格 |
| `get_icon_layout` | 读取可编辑的 Dock、页面和文件夹结构 |
| `export_icon_detail` | 导出名称、ID、版本、类型和推算位置 |
| `get_widgets` | 列出小组件尺寸、所属 App 和推算位置 |
| `save_layout` / `read_layout` / `list_layouts` | 保存、读取和列出工作区布局 |
| `move_icon` / `swap_icons` / `create_folder` | 编辑工作区布局文件 |
| `apply_icon_layout` | 预览或应用完整布局；支持 `allow_remove` |
| `move_icon_on_device` / `swap_icons_on_device` / `create_folder_on_device` | 直接修改设备，可选择 dry-run |
| `list_backups` / `restore_backup` | 列出备份，预览或恢复完整状态 |

`apply_icon_layout` 默认 `dry_run=true`。`restore_backup` 同样默认只预览；真正恢复需要 `dry_run=false`。

## 安全机制与限制

- **不会静默丢失 App。** 布局缺少现有 App 或网页书签时会拒绝写入。确实要从主屏幕移除时，必须显式设置 `allow_remove=true`；这不会卸载 App。
- **拒绝未知 ID。** 只能整理当前主屏幕状态中已有的 App 和网页书签，不支持从 App 资源库任意添加项目。
- **保护小组件，但不编辑小组件。** 服务器不能创建或配置小组件。如果布局会把小组件移到其他页面或改变其推算网格位置，写入会被拒绝。请保持小组件所在页面及其前方的图标数量不变。
- **容量计算包含小组件。** 行列上限从设备实时读取，不写死为 4×6；校验还会模拟小组件占位。
- **位置是推算值。** 行列信息来自图标顺序和小组件尺寸，不是 iOS 提供的系统坐标。
- **备份包含个人布局数据。** 备份仅保存在本地、已被 Git 忽略的 `workspace/` 目录；分享文件前请自行检查。
- **一次只操作一台设备。** 请只连接要修改的 iPhone。

## 开发与检查

```bash
git clone https://github.com/huangzheng2016/ios-icon-mcp.git
cd ios-icon-mcp
uv sync --locked
uv run python -m compileall -q server.py
uvx --from ruff==0.17.1 ruff check server.py
uv build
uvx twine check dist/*
```

CI 会在受支持的 Python 版本上运行离线检查，并构建 wheel 和源码包。GitHub Release 通过 Trusted Publishing（OIDC）发布到 PyPI，不保存 PyPI 密码或长期 API token。

## 许可证

MIT
