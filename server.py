"""iOS home screen layout organizer — MCP server.

Reads and writes the raw SpringBoard icon state via pymobiledevice3:
- Read: every app, folder, web clip AND widget, with localized display names.
- Write: set_icon_state with validation against the raw state; writes are
  refused if widgets would move or disappear.

Layout files use a simplified format (same as cfgutil): index 0 is the Dock,
each following entry is one page; strings are app bundle ids or web clip
URLs, arrays are folders (first element is the name).
"""

import json
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.springboard import SpringBoardServicesService

mcp = FastMCP("ios-icon-mcp")

WORKSPACE = Path(__file__).parent / "workspace"
BACKUP_DIR = WORKSPACE / "backups"


def is_widget(e):
    return isinstance(e, dict) and e.get("elementType") == "widget"


def is_folder_item(item):
    return isinstance(item, list)


def item_id(item):
    return item[0] if is_folder_item(item) else item


def folder_ids(item):
    """All ids inside a simplified-format folder item."""
    ids = []
    for x in item[1:]:
        if isinstance(x, list):
            ids.extend(x)
        else:
            ids.append(x)
    return ids


def entry_id(e):
    if isinstance(e, str):
        return e
    if e.get("webClipURL") is not None:
        return e["webClipURL"]
    return e.get("bundleIdentifier") or e.get("displayIdentifier")


def folder_to_item(f):
    pages = [[entry_id(x) for x in plist] for plist in f.get("iconLists", [])]
    name = f.get("displayName", "")
    if len(pages) <= 1:
        return [name] + (pages[0] if pages else [])
    return [name] + pages


def state_to_layout(state):
    """Raw state -> simplified layout (widgets excluded; they are preserved on apply)."""
    layout = []
    for page in state:
        items = []
        for e in page:
            if is_widget(e):
                continue
            if isinstance(e, dict) and isinstance(e.get("iconLists"), list):
                items.append(folder_to_item(e))
            else:
                items.append(entry_id(e))
        layout.append(items)
    return layout


@asynccontextmanager
async def connected_service():
    lockdown = await create_using_usbmux()
    svc = SpringBoardServicesService(lockdown=lockdown)
    try:
        yield svc
    finally:
        try:
            await svc.close()
        finally:
            await lockdown.close()


async def get_raw_state(service=None):
    if service is not None:
        return await service.get_icon_state()
    async with connected_service() as svc:
        return await svc.get_icon_state()


# 网格参数从设备实时读取（不同机型不一样：iPhone SE 4x5、主流机型 4x6、iPad 更多），
# 带 5 分钟缓存避免每次操作都查询。
_grid_cache = {"time": 0.0, "data": None}


async def get_grid(service=None):
    now = time.monotonic()
    if _grid_cache["data"] is not None and now - _grid_cache["time"] < 300:
        return _grid_cache["data"]
    if service is not None:
        m = await service.get_homescreen_icon_metrics()
    else:
        async with connected_service() as svc:
            m = await svc.get_homescreen_icon_metrics()
    cols = int(m.get("homeScreenIconColumns", 4))
    rows = int(m.get("homeScreenIconRows", 6))
    grid = {
        "cols": cols,
        "rows": rows,
        "dockMax": int(m.get("homeScreenIconDockMaxCount", 4)),
        "pageMax": cols * rows,
    }
    _grid_cache.update(time=now, data=grid)
    return grid


def count_widgets(state):
    return sum(1 for page in state for e in page if is_widget(e))


def backup_raw_state(raw):
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    ts = now.strftime("%Y%m%d%H%M%S") + f"{now.microsecond // 1000:03d}"
    file = BACKUP_DIR / f"raw-{ts}.json"
    file.write_text(json.dumps(raw, ensure_ascii=False, indent=2, default=str))
    return file


def resolve_file(name):
    if not re.fullmatch(r"[\w.-]+\.json", name):
        raise ValueError(f"Invalid file name: {name} (letters/digits/._- only, must end with .json)")
    return WORKSPACE / name


def validate_layout(layout):
    if not isinstance(layout, list) or len(layout) < 2:
        raise ValueError("Layout must contain a Dock and at least one home screen page")
    ids, folder_names = [], []
    for p, page in enumerate(layout):
        if not isinstance(page, list):
            raise TypeError(f"Page {p} must be an array")
        for item in page:
            if isinstance(item, str):
                if not item:
                    raise ValueError(f"Page {p} contains an empty app/web clip ID")
                ids.append(item)
                continue
            if not isinstance(item, list):
                raise TypeError(f"Page {p} contains an unsupported entry: {item!r}")
            if p == 0:
                raise ValueError("Folders cannot go into the Dock")
            if len(item) < 2 or not isinstance(item[0], str) or not item[0].strip():
                raise ValueError("Folders need a non-empty name and at least one app/web clip")
            folder_names.append(item[0])
            nested = isinstance(item[1], list)
            pages = item[1:] if nested else [item[1:]]
            for folder_page in pages:
                if not isinstance(folder_page, list) or not folder_page:
                    raise ValueError(f"Folder {item[0]} has an invalid or empty page")
                for entry in folder_page:
                    if not isinstance(entry, str) or not entry:
                        raise ValueError(f"Folder {item[0]} contains an invalid app/web clip ID")
                    ids.append(entry)
    duplicate_ids = sorted({x for x in ids if ids.count(x) > 1})
    duplicate_folders = sorted({x for x in folder_names if folder_names.count(x) > 1})
    if duplicate_ids:
        raise ValueError(f"Duplicate app/web clip IDs are not supported: {duplicate_ids}")
    if duplicate_folders:
        raise ValueError(f"Folder names must be unique: {duplicate_folders}")


def load_layout(name):
    file = resolve_file(name)
    layout = json.loads(file.read_text())
    validate_layout(layout)
    return file, layout


def write_layout(file, layout):
    validate_layout(layout)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(layout, ensure_ascii=False, indent=2) + "\n")


def page_summary(layout):
    out = []
    for i, page in enumerate(layout):
        folders = sum(1 for x in page if is_folder_item(x))
        out.append({"page": "dock" if i == 0 else i, "icons": len(page) - folders, "folders": folders})
    return out


def capacity_errors(layout, grid):
    errs = []
    if len(layout[0]) > grid["dockMax"]:
        errs.append(f"Dock has {len(layout[0])} icons (max {grid['dockMax']})")
    for i, page in enumerate(layout[1:], 1):
        if len(page) > grid["pageMax"]:
            errs.append(f"Page {i} has {len(page)} icons (max {grid['pageMax']})")
    return errs


# ---------- layout edit operations ----------

def take_out(layout, target):
    for i, it in enumerate(layout[0]):
        if item_id(it) == target:
            return layout[0].pop(i)
    for p in range(1, len(layout)):
        for i, it in enumerate(layout[p]):
            if item_id(it) == target:
                return layout[p].pop(i)
    for p in range(1, len(layout)):
        page = layout[p]
        for i, it in enumerate(page):
            if not is_folder_item(it):
                continue
            if len(it) > 1 and isinstance(it[1], list):
                for pp in range(1, len(it)):
                    if target in it[pp]:
                        it[pp].remove(target)
                        if not it[pp]:
                            it.pop(pp)
                        if len(it) == 1:
                            page.pop(i)
                        return target
            elif target in it[1:]:
                it.remove(target)
                if len(it) == 1:
                    page.pop(i)
                return target
    raise ValueError(f"{target} not found (check the bundle identifier or folder name)")


def find_folder(layout, name):
    for page in layout[1:]:
        for it in page:
            if is_folder_item(it) and it[0] == name:
                return it
    return None


def move_item(layout, target, dest):
    page_idx, folder = None, None
    if dest == "dock":
        pass
    elif dest.startswith("page:"):
        try:
            page_idx = int(dest[5:])
        except ValueError:
            raise ValueError(f"Invalid target: {dest}")
        if not 1 <= page_idx < len(layout):
            raise ValueError(f"Invalid target: {dest} (there are {len(layout) - 1} pages)")
    elif dest.startswith("folder:"):
        folder = find_folder(layout, dest[7:])
        if folder is None:
            raise ValueError(f"Folder not found: {dest[7:]}")
    else:
        raise ValueError(f"Unrecognized target {dest} (expected dock / page:N / folder:Name)")

    if folder is not None and folder_ids(folder) == [target]:
        return target
    item = take_out(layout, target)
    if dest == "dock":
        if is_folder_item(item):
            raise ValueError("Folders cannot go into the Dock")
        layout[0].append(item)
    elif folder is not None:
        if is_folder_item(item):
            raise ValueError("Cannot put a folder into another folder")
        if isinstance(folder[-1], list):
            folder[-1].append(item)
        else:
            folder.append(item)
    else:
        layout[page_idx].append(item)
    return item


def swap_items(layout, a, b):
    def find(x):
        for i, it in enumerate(layout[0]):
            if item_id(it) == x:
                return 0, i
        for p in range(1, len(layout)):
            for i, it in enumerate(layout[p]):
                if item_id(it) == x:
                    return p, i
        raise ValueError(f"{x} not found (swap only works on Dock and top-level page icons/folders)")
    pa, ia = find(a)
    pb, ib = find(b)
    layout[pa][ia], layout[pb][ib] = layout[pb][ib], layout[pa][ia]


def new_folder(layout, page, name, ids):
    if not 1 <= page < len(layout):
        raise ValueError(f"Invalid page: page:{page} (there are {len(layout) - 1} pages)")
    if find_folder(layout, name):
        raise ValueError(f"A folder named {name} already exists")
    folder = [name]
    for i in ids:
        item = take_out(layout, i)
        if is_folder_item(item):
            raise ValueError(f"Cannot put folder {i} into a folder")
        folder.append(item)
    layout[page].append(folder)
    return folder


# ---------- merge back into raw state (widgets preserved) ----------

def merge_layout_into_state(raw, layout):
    validate_layout(layout)
    app_by_id, webclip_by_url, folder_by_name = {}, {}, {}

    def visit(e):
        if not isinstance(e, dict) or is_widget(e):
            return
        if e.get("bundleIdentifier"):
            app_by_id[e["bundleIdentifier"]] = e
        if e.get("displayIdentifier"):
            app_by_id[e["displayIdentifier"]] = e
        if e.get("webClipURL") is not None:
            webclip_by_url[e["webClipURL"]] = e
        if isinstance(e.get("iconLists"), list):
            folder_by_name[e.get("displayName")] = e
            for lst in e["iconLists"]:
                for sub in lst:
                    visit(sub)

    for page in raw:
        for e in page:
            visit(e)

    known_ids = set(app_by_id) | set(webclip_by_url)
    planned_ids = [
        x
        for page in layout
        for item in page
        for x in (folder_ids(item) if is_folder_item(item) else [item])
    ]
    unknown = sorted(set(planned_ids) - known_ids)
    if unknown:
        raise ValueError(
            "Unknown app/web clip IDs; only entries already on the home screen are supported: "
            + ", ".join(unknown)
        )

    def convert(item):
        if not is_folder_item(item):
            return app_by_id[item] if item in app_by_id else webclip_by_url[item]
        name, rest = item[0], item[1:]
        if rest and isinstance(rest[0], list):
            pages = [[convert(x) for x in p] for p in rest]
        else:
            pages = [[convert(x) for x in rest]]
        base = dict(folder_by_name[name]) if name in folder_by_name else {"displayName": name}
        base["iconLists"] = pages
        return base

    out = []
    for p, edited in enumerate(layout):
        edited = list(edited)
        raw_page = raw[p] if p < len(raw) else []
        merged = []
        for e in raw_page:
            if is_widget(e):
                merged.append(e)
                continue
            if not edited:
                continue
            merged.append(convert(edited.pop(0)))
        while edited:
            merged.append(convert(edited.pop(0)))
        out.append(merged)
    return out


# ---------- diff (preview) ----------

def diff_layouts(current, planned):
    """Compare full positions, folder membership/order, and folder deletion."""

    def locations(layout):
        loc, folders, folder_locations = {}, {}, {}
        for p, page in enumerate(layout):
            for index, it in enumerate(page, 1):
                if is_folder_item(it):
                    name = it[0]
                    folders[name] = it
                    folder_locations[name] = {"page": p, "index": index}
                    rest = it[1:]
                    folder_pages = rest if rest and isinstance(rest[0], list) else [rest]
                    for folder_page, entries in enumerate(folder_pages, 1):
                        for folder_index, x in enumerate(entries, 1):
                            loc[x] = {
                                "page": p,
                                "folder": name,
                                "folderPage": folder_page,
                                "index": folder_index,
                            }
                else:
                    loc[it] = {"page": p, "index": index}
        return loc, folders, folder_locations

    cur_loc, cur_folders, cur_folder_locations = locations(current)
    new_loc, new_folders, new_folder_locations = locations(planned)
    added = [{"id": i, "to": new_loc[i]} for i in new_loc if i not in cur_loc]
    removed = [{"id": i, "from": cur_loc[i]} for i in cur_loc if i not in new_loc]
    moved = [
        {"id": i, "from": cur_loc[i], "to": new_loc[i]}
        for i in new_loc
        if i in cur_loc and cur_loc[i] != new_loc[i]
    ]
    folder_changes = []
    for name, new_item in new_folders.items():
        new_ids = folder_ids(new_item)
        if name not in cur_folders:
            folder_changes.append({
                "folder": name,
                "createdWith": new_ids,
                "to": new_folder_locations[name],
            })
            continue
        old_ids = folder_ids(cur_folders[name])
        change = {}
        added_ids = [x for x in new_ids if x not in old_ids]
        removed_ids = [x for x in old_ids if x not in new_ids]
        if added_ids:
            change["added"] = added_ids
        if removed_ids:
            change["removed"] = removed_ids
        if old_ids != new_ids and set(old_ids) == set(new_ids):
            change["reordered"] = True
        if cur_folder_locations[name] != new_folder_locations[name]:
            change["from"] = cur_folder_locations[name]
            change["to"] = new_folder_locations[name]
        if change:
            folder_changes.append({"folder": name, **change})
    for name in cur_folders.keys() - new_folders.keys():
        folder_changes.append({
            "folder": name,
            "deleted": True,
            "from": cur_folder_locations[name],
        })
    return {
        "moved": moved,
        "addedToHomeScreen": added,
        "removedFromHomeScreen": removed,
        "folderChanges": folder_changes,
        "totals": {
            "moved": len(moved),
            "added": len(added),
            "removed": len(removed),
            "folderChanges": len(folder_changes),
        },
    }


# ---------- grid position estimation (for get_widgets / export) ----------

def widget_cells(size):
    return {"medium": (4, 2), "large": (4, 4)}.get(size, (2, 2))


def simulate_positions(entries, grid):
    cells = [[False] * grid["cols"] for _ in range(grid["rows"])]

    def place(w, h):
        for r in range(grid["rows"] - h + 1):
            for c in range(grid["cols"] - w + 1):
                if all(not cells[r + dr][c + dc] for dr in range(h) for dc in range(w)):
                    for dr in range(h):
                        for dc in range(w):
                            cells[r + dr][c + dc] = True
                    return {"row": r + 1, "col": c + 1}
        return {}

    out = []
    for e in entries:
        w, h = widget_cells(e.get("gridSize")) if is_widget(e) else (1, 1)
        out.append(place(w, h))
    return out


def describe_entry(e, pos):
    base = dict(pos)
    if is_widget(e):
        return {
            "type": "widget",
            "name": e.get("displayName") or e.get("containerBundleIdentifier", ""),
            "size": e.get("gridSize", "unknown"),
            "containerApp": e.get("containerBundleIdentifier", ""),
            "widgetId": e.get("widgetIdentifier") or e.get("bundleIdentifier", ""),
            **base,
        }
    if isinstance(e, dict):
        if e.get("webClipURL") is not None:
            return {"type": "webclip", "name": e.get("displayName", ""), "url": e["webClipURL"], **base}
        if isinstance(e.get("iconLists"), list):
            apps = [entry_id(x) for plist in e["iconLists"] for x in plist]
            names = [x.get("displayName", "") for plist in e["iconLists"] for x in plist]
            return {"type": "folder", "name": e.get("displayName", ""), "apps": apps, "appNames": names, **base}
        return {
            "type": "app",
            "id": entry_id(e),
            "name": e.get("displayName", ""),
            "version": e.get("bundleVersion", ""),
            **base,
        }
    return {"type": "unknown", **base}


def widget_placements(state, grid):
    widgets = []
    for p, page in enumerate(state):
        positions = simulate_positions(page, grid)
        for e, pos in zip(page, positions):
            if is_widget(e):
                details = describe_entry(e, pos)
                details.pop("type")
                widgets.append({"page": p, **details})
    return widgets


def raw_capacity_errors(state, grid):
    errors = []
    for p, page in enumerate(state[1:], 1):
        overflow = sum(not pos for pos in simulate_positions(page, grid))
        if overflow:
            errors.append(f"Page {p} cannot fit all icons and widgets on the device grid")
    return errors


def snapshot(state, grid):
    pages = []
    for i, page in enumerate(state):
        pos = simulate_positions(page, grid)
        pages.append({
            "page": "dock" if i == 0 else i,
            "items": [describe_entry(e, pos[k]) for k, e in enumerate(page)],
        })
    return pages


def short_label(e):
    if is_widget(e):
        name = e.get("displayName")
        if not name:
            name = e.get("containerBundleIdentifier", "?").rsplit(".", 1)[-1]
        return f"{name}·{e.get('gridSize', '?')}"
    if isinstance(e, dict):
        if isinstance(e.get("iconLists"), list):
            n = sum(len(x) for x in e["iconLists"])
            return f"[{e.get('displayName', '?')}]×{n}"
        return e.get("displayName") or entry_id(e) or "?"
    return str(e)


def render_state(state, grid):
    """Render every page as a text grid using the device's actual rows x cols.
    Widgets occupy real cells (2x2/4x2/4x4), continuation cells are marked with ‖.
    Positions are estimated from icon order + widget occupancy, not system coordinates."""
    out = []
    for i, page in enumerate(state):
        cells = [[None] * grid["cols"] for _ in range(grid["rows"])]
        for e in page:
            label = short_label(e)
            w, h = widget_cells(e.get("gridSize")) if is_widget(e) else (1, 1)
            placed = False
            for r in range(grid["rows"] - h + 1):
                for c in range(grid["cols"] - w + 1):
                    if all(cells[r + dr][c + dc] is None for dr in range(h) for dc in range(w)):
                        cells[r][c] = label
                        for dr in range(h):
                            for dc in range(w):
                                if dr or dc:
                                    cells[r + dr][c + dc] = "‖"
                        placed = True
                        break
                if placed:
                    break
        title = "Dock" if i == 0 else f"Page {i}"
        lines = [title]
        for r, row in enumerate(cells, 1):
            row_cells = " | ".join((cell or "·")[:12].ljust(12) for cell in row)
            lines.append(f"  R{r}: {row_cells}")
        out.append("\n".join(lines))
    return "\n\n".join(out)


# ---------- write pipeline shared by apply / direct ops ----------

async def write_layout_to_device(layout, dry_run, allow_remove=False):
    validate_layout(layout)
    async with connected_service() as svc:
        raw = await get_raw_state(svc)
        grid = await get_grid(svc)
        changes = diff_layouts(state_to_layout(raw), layout)
        removed_ids = [x["id"] for x in changes["removedFromHomeScreen"]]
        if removed_ids and not allow_remove:
            preview = ", ".join(removed_ids[:5])
            suffix = " ..." if len(removed_ids) > 5 else ""
            raise ValueError(
                f"Layout omits {len(removed_ids)} existing entries ({preview}{suffix}). "
                "Write refused; restore them or explicitly set allow_remove=True in apply_icon_layout."
            )
        merged = merge_layout_into_state(raw, layout)
        errors = capacity_errors(layout, grid) + raw_capacity_errors(merged, grid)
        if errors:
            raise ValueError("Layout capacity validation failed; write refused: " + "; ".join(errors))
        widgets_before = widget_placements(raw, grid)
        if widget_placements(merged, grid) != widgets_before:
            raise ValueError(
                "Layout would move or remove widgets; write refused. Keep each widget on the same page "
                "and keep the number of icons before it unchanged."
            )
        if dry_run:
            return {
                "dryRun": True,
                "changes": changes,
                "summary": page_summary(layout),
                "widgetCount": len(widgets_before),
                "widgetsPreserved": True,
            }
        backup = backup_raw_state(raw)
        try:
            await svc.set_icon_state(merged)
            after = await get_raw_state(svc)
        except Exception as exc:
            raise RuntimeError(f"Device write failed; automatic backup is at {backup}") from exc
        actual_layout = state_to_layout(after)
        widgets_after = widget_placements(after, grid)
        layout_matches = actual_layout == layout
        widgets_preserved = widgets_after == widgets_before
        remaining = diff_layouts(layout, actual_layout)
        return {
            "applied": True,
            "verified": layout_matches and widgets_preserved,
            "backup": str(backup),
            "widgetsBefore": len(widgets_before),
            "widgetsAfter": len(widgets_after),
            "widgetsNote": (
                "all widgets preserved at their estimated positions"
                if widgets_preserved
                else "WIDGET PLACEMENT CHANGED, verify with get_widgets!"
            ),
            "verification": {
                "layoutMatches": layout_matches,
                "widgetsPreserved": widgets_preserved,
                "remainingChanges": remaining["totals"],
            },
            "changes": changes,
        }


# ---------- MCP tools ----------

@mcp.tool()
async def get_icon_layout() -> str:
    """Read the current home screen layout from the iPhone (simplified format: index 0 is the Dock, each following entry is one page; strings are app bundle ids or web clip URLs, arrays are folders). Widgets are not in this format but are preserved when applying."""
    state = await get_raw_state()
    return json.dumps(state_to_layout(state), ensure_ascii=False, indent=2)


@mcp.tool()
async def save_layout(name: str) -> str:
    """Read the current layout from the iPhone and save it into the workspace for editing. name e.g. layout.json"""
    state = await get_raw_state()
    layout = state_to_layout(state)
    file = resolve_file(name)
    write_layout(file, layout)
    return json.dumps({"saved": str(file), "summary": page_summary(layout)}, ensure_ascii=False)


@mcp.tool()
async def read_layout(name: str) -> str:
    """Read a layout file from the workspace."""
    _, layout = load_layout(name)
    return json.dumps({"summary": page_summary(layout), "layout": layout}, ensure_ascii=False, indent=2)


@mcp.tool()
async def list_layouts() -> str:
    """List layout files in the workspace. For backups with timestamps, use list_backups."""
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    layouts = sorted(p.name for p in WORKSPACE.glob("*.json"))
    return json.dumps({"workspace": str(WORKSPACE), "layouts": layouts}, ensure_ascii=False)


@mcp.tool()
async def list_backups() -> str:
    """List all automatic backups (newest first) with creation time, page count and widget count. Use restore_backup with the file name to roll back."""
    if not BACKUP_DIR.exists():
        return json.dumps({"backups": []}, ensure_ascii=False)
    out = []
    for f in sorted(BACKUP_DIR.glob("*.json"), reverse=True):
        entry = {
            "file": f.name,
            "time": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            data = json.loads(f.read_text())
            entry["pages"] = max(len(data) - 1, 0)
            entry["widgets"] = count_widgets(data)
        except (OSError, ValueError, TypeError):
            entry["error"] = "unreadable"
        out.append(entry)
    return json.dumps({"backups": out}, ensure_ascii=False, indent=2)


@mcp.tool()
async def move_icon(name: str, id: str, target: str) -> str:
    """Move an app or folder inside a workspace layout file. target: dock / page:N (append to that page) / folder:Name (append to that folder)."""
    file, layout = load_layout(name)
    move_item(layout, id, target)
    write_layout(file, layout)
    return json.dumps({"moved": id, "to": target, "summary": page_summary(layout)}, ensure_ascii=False)


@mcp.tool()
async def swap_icons(name: str, a: str, b: str) -> str:
    """Swap two top-level icons/folders inside a workspace layout file (cross-page supported)."""
    file, layout = load_layout(name)
    swap_items(layout, a, b)
    write_layout(file, layout)
    return json.dumps({"swapped": [a, b], "summary": page_summary(layout)}, ensure_ascii=False)


@mcp.tool()
async def create_folder(name: str, page: int, folder_name: str, ids: list[str]) -> str:
    """Create a folder on the given page inside a workspace layout file and pull the listed apps into it. page counts from 1."""
    file, layout = load_layout(name)
    new_folder(layout, page, folder_name, ids)
    write_layout(file, layout)
    return json.dumps({"created": folder_name, "page": page, "ids": ids, "summary": page_summary(layout)}, ensure_ascii=False)


@mcp.tool()
async def apply_icon_layout(name: str, dry_run: bool = True, allow_remove: bool = False) -> str:
    """Preview or apply a layout file. dry_run defaults to True. Unknown IDs, omitted apps/web clips, over-capacity pages, and widget movement are refused. Set allow_remove=True only to intentionally remove omitted entries from the home screen (never uninstalls them). Writes create a backup and verify the resulting layout and widgets."""
    file, layout = load_layout(name)
    result = await write_layout_to_device(layout, dry_run, allow_remove=allow_remove)
    if not dry_run:
        result["appliedFrom"] = str(file)
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
async def move_icon_on_device(id: str, target: str, dry_run: bool = False) -> str:
    """Move an app or folder directly on the device (takes effect immediately). target: dock / page:N / folder:Name. Set dry_run=True to preview the change without writing."""
    layout = state_to_layout(await get_raw_state())
    move_item(layout, id, target)
    return json.dumps(await write_layout_to_device(layout, dry_run), ensure_ascii=False, indent=2)


@mcp.tool()
async def swap_icons_on_device(a: str, b: str, dry_run: bool = False) -> str:
    """Swap two top-level icons/folders directly on the device (immediate effect). Set dry_run=True to preview."""
    layout = state_to_layout(await get_raw_state())
    swap_items(layout, a, b)
    return json.dumps(await write_layout_to_device(layout, dry_run), ensure_ascii=False, indent=2)


@mcp.tool()
async def create_folder_on_device(page: int, folder_name: str, ids: list[str], dry_run: bool = False) -> str:
    """Create a folder on the device immediately and pull the listed apps into it. page counts from 1. Set dry_run=True to preview."""
    layout = state_to_layout(await get_raw_state())
    new_folder(layout, page, folder_name, ids)
    return json.dumps(await write_layout_to_device(layout, dry_run), ensure_ascii=False, indent=2)


@mcp.tool()
async def restore_backup(backup: str, dry_run: bool = True) -> str:
    """Preview or restore an automatic backup, including widgets. dry_run defaults to True; set dry_run=False to write. A fresh safety backup of the current state is created before restoring."""
    if not re.fullmatch(r"[\w.-]+\.json", backup):
        raise ValueError("Invalid backup file name")
    file = BACKUP_DIR / backup
    state = json.loads(file.read_text())
    if not isinstance(state, list) or len(state) < 2 or any(not isinstance(page, list) for page in state):
        raise ValueError("Invalid backup: expected a Dock and home screen pages")

    def parse_dates(o):
        if isinstance(o, dict):
            return {k: parse_dates(v) for k, v in o.items()}
        if isinstance(o, list):
            return [parse_dates(v) for v in o]
        if isinstance(o, str) and len(o) >= 19 and o[4] == "-" and o[10] == "T":
            try:
                return datetime.fromisoformat(o)
            except ValueError:
                return o
        return o

    target = parse_dates(state)
    target_layout = state_to_layout(target)
    async with connected_service() as svc:
        current = await get_raw_state(svc)
        grid = await get_grid(svc)
        changes = diff_layouts(state_to_layout(current), target_layout)
        if dry_run:
            return json.dumps({
                "dryRun": True,
                "backup": backup,
                "changes": changes,
                "widgetsBefore": count_widgets(current),
                "widgetsAfter": count_widgets(target),
            }, ensure_ascii=False, indent=2)
        safety_backup = backup_raw_state(current)
        try:
            await svc.set_icon_state(target)
            after = await get_raw_state(svc)
        except Exception as exc:
            raise RuntimeError(f"Restore failed; pre-restore safety backup is at {safety_backup}") from exc
        layout_matches = state_to_layout(after) == target_layout
        widgets_restored = widget_placements(after, grid) == widget_placements(target, grid)
        return json.dumps({
            "restored": backup,
            "verified": layout_matches and widgets_restored,
            "safetyBackup": str(safety_backup),
            "verification": {
                "layoutMatches": layout_matches,
                "widgetsRestored": widgets_restored,
            },
        }, ensure_ascii=False, indent=2)


@mcp.tool()
async def get_widgets() -> str:
    """List home screen widgets (size, owning app, estimated grid position). row/col are ESTIMATES computed from icon order and widget occupancy on the device's actual grid (read from the device, e.g. 4x6) — not system coordinates. For a quick visual overview use render_home_screen."""
    async with connected_service() as svc:
        state = await get_raw_state(svc)
        grid = await get_grid(svc)
    widgets = [
        {"page": p["page"], **it}
        for p in snapshot(state, grid)
        for it in p["items"]
        if it["type"] == "widget"
    ]
    return json.dumps({"widgetCount": len(widgets), "widgets": widgets}, ensure_ascii=False, indent=2)


@mcp.tool()
async def render_home_screen() -> str:
    """Render the home screen as text grids (one grid per page, sized from the device's actual rows x cols) with widgets shown as blocks and app display names in place. This is the fastest way to understand the actual current layout. Positions are estimated from icon order and widget occupancy, not system coordinates."""
    async with connected_service() as svc:
        state = await get_raw_state(svc)
        grid = await get_grid(svc)
    return render_state(state, grid)


@mcp.tool()
async def export_icon_detail() -> str:
    """Export a full inventory: every entry's page/row/col (ESTIMATED from icon order and widget occupancy on the device's actual grid — not system coordinates), type, localized name, bundle id, version. Folder contents are listed separately. For a quick visual overview use render_home_screen."""
    async with connected_service() as svc:
        state = await get_raw_state(svc)
        grid = await get_grid(svc)
    return json.dumps({
        "grid": f"{grid['cols']} columns x {grid['rows']} rows per page (read from device); positions estimated from icon order and widget occupancy",
        "pages": snapshot(state, grid),
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def device_info() -> str:
    """Basic info about the connected device (name, iOS version, model)."""
    async with await create_using_usbmux() as lockdown:
        vals = await lockdown.get_value()
    return json.dumps({
        "name": vals.get("DeviceName"),
        "iosVersion": vals.get("ProductVersion"),
        "model": vals.get("ProductType"),
    }, ensure_ascii=False)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
