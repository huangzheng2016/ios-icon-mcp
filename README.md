# ios-icon-mcp

[![CI](https://github.com/huangzheng2016/ios-icon-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/huangzheng2016/ios-icon-mcp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/ios-icon-mcp.svg)](https://pypi.org/project/ios-icon-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/ios-icon-mcp.svg)](https://pypi.org/project/ios-icon-mcp/)

Organize an iPhone home screen from any MCP-compatible AI client. Move apps and web clips, create folders, rearrange pages, and inspect widget-aware text layouts with natural language.

**No jailbreak, device supervision, or Apple Configurator is required.**

[中文版 README](README.zh-CN.md)

> **Warning:** This project uses private iOS APIs through pymobiledevice3. Compatibility can change with iOS updates. Use the preview and backup features before writing.

## Features

- Read apps, folders, web clips, and widgets, including localized names and bundle IDs.
- Render pages as text grids using the device's reported rows and columns.
- Edit a saved layout or make a direct change on the device.
- Create folders, including folders with multiple pages.
- Preview changes by default before applying a layout file.
- Refuse unknown IDs, accidental app removal, over-capacity pages, and widget displacement.
- Back up automatically before every write and verify the resulting layout and widgets.
- Restore complete backups, with a preview and a fresh safety backup.

## Requirements

- macOS with its built-in `usbmuxd`.
- One iPhone connected over USB, unlocked, and trusted by the Mac.
- [uv](https://docs.astral.sh/uv/), which provides the `uvx` command.

The MCP server needs Python 3.10 or newer; `uvx` handles the Python environment and dependencies.

## Install

Add this to your MCP client's configuration:

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

Restart the MCP client after changing its configuration. The first run downloads the package into an isolated environment.

To run directly from GitHub instead:

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

To upgrade the cached PyPI package, run `uvx --refresh ios-icon-mcp` or restart the client with that command once.

## Quick start

Connect and unlock the iPhone, then ask your AI client:

- “Show my home screen as a text grid.”
- “Export every app with its name and bundle ID.”
- “Save the current layout, put my music apps in a folder, and preview the change.”
- “Move this app to page 2 without changing anything else.”

For a batch edit, the intended flow is:

1. `save_layout` creates a workspace snapshot.
2. `move_icon`, `swap_icons`, or `create_folder` edits that file.
3. `apply_icon_layout` previews the complete diff by default.
4. After reviewing the result, call it with `dry_run=false` to write.
5. Check `verified` in the response. Backups are created automatically.

Direct `*_on_device` tools are intended for small changes and write immediately unless `dry_run=true` is supplied.

## Layout format

A layout is a JSON array. Index 0 is the Dock; subsequent arrays are home screen pages. Strings are app bundle IDs or web clip URLs. A folder is an array whose first item is its name.

```json
[
  ["com.apple.mobilephone", "com.apple.MobileSMS"],
  [
    "com.apple.mobilesafari",
    ["Social", "com.example.chat", "com.example.social"],
    ["Multi-page folder", ["com.example.one"], ["com.example.two"]]
  ]
]
```

The IDs above only illustrate the syntax. Use IDs from `get_icon_layout` or `export_icon_detail` for a real edit.

- Flat folder form: `["Folder", "app.one", "app.two"]`.
- Multi-page folder form: `["Folder", ["app.one"], ["app.two"]]`.
- Widgets are deliberately omitted from this editable representation and merged from the current device state during writes.

## Tools

| Tool | Purpose |
|---|---|
| `device_info` | Read the connected device name, model, and iOS version |
| `render_home_screen` | Render every page as a widget-aware text grid |
| `get_icon_layout` | Read the editable Dock/page/folder representation |
| `export_icon_detail` | Export names, IDs, versions, types, and estimated positions |
| `get_widgets` | List widget sizes, owners, and estimated positions |
| `save_layout` / `read_layout` / `list_layouts` | Save, inspect, and list workspace layouts |
| `move_icon` / `swap_icons` / `create_folder` | Edit a workspace layout file |
| `apply_icon_layout` | Preview or apply a complete layout; supports `allow_remove` |
| `move_icon_on_device` / `swap_icons_on_device` / `create_folder_on_device` | Make a direct edit, with optional dry-run |
| `list_backups` / `restore_backup` | List backups and preview or restore a complete state |

`apply_icon_layout` defaults to `dry_run=true`. `restore_backup` also defaults to `dry_run=true`; a real restore requires `dry_run=false`.

## Safety and limitations

- **No silent app loss.** If a layout omits existing apps or web clips, writing is refused. For intentional removal from the home screen, set `allow_remove=true`. This never uninstalls the app.
- **No unknown IDs.** Only apps and web clips already present in the current home screen state can be rearranged. Adding arbitrary items from App Library is not supported.
- **Widgets are protected, not editable.** The server cannot create or configure widgets. A layout that would move a widget to another page or change its estimated grid position is refused. Keep the widget's page and the number of icons before it unchanged.
- **Capacity includes widgets.** Limits come from the device rather than a hard-coded 4×6 grid. Validation also simulates widget occupancy.
- **Positions are estimates.** Row and column values are inferred from icon order and widget sizes; they are not coordinates supplied by iOS.
- **Backups contain personal layout data.** They stay in the local, Git-ignored `workspace/` directory. Review files before sharing them.
- **One device at a time.** Connect only the iPhone you intend to modify.

## Development

```bash
git clone https://github.com/huangzheng2016/ios-icon-mcp.git
cd ios-icon-mcp
uv sync --locked
uv run python -m compileall -q server.py
uvx --from ruff==0.17.1 ruff check server.py
uv build
uvx twine check dist/*
```

CI runs offline checks across supported Python versions and builds the wheel and source distribution. GitHub releases are published to PyPI with Trusted Publishing (OIDC), without a stored PyPI password or long-lived API token.

## License

MIT
