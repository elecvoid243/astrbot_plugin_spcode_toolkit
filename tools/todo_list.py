from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime
from pathlib import Path

"""
todo_list — LLM Agent 自我管理的 todo list 工具。

按 umo (unified_msg_origin) 隔离,每个会话通道(私聊/群聊)1 个 active list,持久化到 .md 文件。
v2.11 起:隔离粒度从 sender_key (platform:sender_id) 切到 umo,以切断跨私聊/群聊的"会话接力"。
详见 docs/superpowers/specs/2026-06-20-todo-per-umo-design.md
"""


# ── 常量 ────────────────────────────────────────────

MAX_ITEMS = 100
MAX_FILE_SIZE = 1024 * 1024  # 1MB
MAX_IMPORT_BYTES = MAX_FILE_SIZE  # 1MB 别名,语义清晰。
# 有意与 MAX_FILE_SIZE 共享上限值——任一变化须同步评估。
MAX_FILENAME_LEN = 200
ILLEGAL_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

VALID_STATUSES = {"pending", "in_progress", "done", "cancelled"}
STATUS_MARK = {
    "done": "[x]",
    "in_progress": "[~]",
    "pending": "[ ]",
    "cancelled": "[-]",
}
MARK_STATUS = {v: k for k, v in STATUS_MARK.items()}


# ── 缺失值标记 ──────────────────────────────────────
# 用于 todo_modify 工具区分"未传 notes"与"传了空字符串"。
# 使用 None(JSON 安全)作为缺失值标记。
# - None = 未传 → 保留旧值
# - ""   = 传了空串 → 清空 notes
# - "x"  = 传了内容 → 覆盖 notes
UNSET_NOTES: None = None


# ── umo & filename ──────────────────────────────────


def extract_umo(event) -> str:
    """从 AstrMessageEvent 提取 umo (unified_msg_origin)。

    v2.11 起:todo_list 的隔离粒度由 sender_key (platform:sender_id) 切到 umo。
    umo 是 AstrBot 给每个会话通道(私聊/群聊/不同群)的全局唯一 ID,
    典型格式:
      - 私聊: ``webchat:FriendMessage:astrbot``
      - 群聊: ``telegram:GroupMessage:123456:group_xxxx``
      - QQ 私聊: ``aiocqhttp:PrivateMessage:987654``

    同一 sender 在私聊/群聊/不同群有不同 umo → 天然切断"会话接力"。

    兜底:event 为 None / 没有 unified_msg_origin / umo 是空字符串时,
    返回占位符 ``unknown:unknown:unknown:unknown``,避免下游路径生成失败。
    """
    if event is None:
        return "unknown:unknown:unknown:unknown"
    umo = getattr(event, "unified_msg_origin", None)
    if not umo:
        return "unknown:unknown:unknown:unknown"
    return str(umo).strip() or "unknown:unknown:unknown:unknown"


def import_from_path(path: str) -> tuple[list[dict], str, str]:
    """读 .md todo 文件 → 返回 (items, parsed_title, error).

    - 成功: error == ""，items 长度 ≥ 1
    - 失败: items == [], error 非空(供工具层转 {"ok": False, "error": ...})

    校验链(按顺序短路返回,见 spec §"设计 1"):
    1. 类型      → 必须是 str
    2. 绝对路径  → Path.is_absolute()
    3. 存在性    → Path.exists()
    4. 是文件    → Path.is_file()
    5. stat      → p.stat() 不抛
    6. 大小      → size <= MAX_IMPORT_BYTES
    7. 读取      → Path.read_text(encoding="utf-8")
    8. 解析      → parse_md(text)
    9. 非空      → data["items"] 至少 1 个

    Note: 不做 umo 重写 / ID 重排 / title 覆盖 — 全部透传给 TodoStore.create()。
    """
    # 1. 类型
    if not isinstance(path, str):
        return [], "", "from_path 必须是字符串路径"

    # 2. 绝对路径
    p = Path(path)
    if not p.is_absolute():
        return [], "", f"路径必须是绝对路径: {path}"

    # 3. 存在性
    if not p.exists():
        return [], "", f"文件不存在: {path}"

    # 4. 是文件
    if not p.is_file():
        return [], "", f"不是文件: {path}"

    # 5. stat
    try:
        size = p.stat().st_size
    except OSError as e:
        return [], "", f"stat 失败: {e}"

    # 6. 大小上限
    if size > MAX_IMPORT_BYTES:
        return (
            [],
            "",
            (f"文件过大: {size} bytes (上限 {MAX_IMPORT_BYTES} bytes). 请精简后重试"),
        )

    # 7. 读取
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return [], "", f"读取失败: {e}"

    # 8. 解析
    try:
        data = parse_md(text)
    except Exception as e:
        return [], "", f"parse 失败: {e}"

    # 9. 非空
    items = data.get("items", [])
    if not items:
        return (
            [],
            "",
            "解析成功但未找到任何 item (文件可能不含 [ ] / [x] / [~] / [-] checkbox)",
        )

    return items, data.get("title", ""), ""


def build_filename(umo: str, when: datetime | None = None) -> str:
    """Build a .md filename for the given umo at the given timestamp.

    v2.11: 输入从 sender_key (platform:sender_id) 切到 umo (unified_msg_origin)。
    umo 形如 ``webchat:FriendMessage:astrbot`` 含多段 `:`,
    Windows 文件名不能含 `:` → 把所有 `:` 替换为 `_` 后拼上时间戳。

    Format: {umo_清洗后}_{YYYYMMDDhhmm}.md (minute precision)
    Fallback: sha256(umo)[:16]_{YYYYMMDDhhmm}.md
    """
    when = when or datetime.now()
    ts = when.strftime("%Y%m%d%H%M")
    # umo 含多个 `:`(如 webchat:FriendMessage:astrbot),把所有分隔符替换为 `_`
    safe = umo.replace(":", "_")
    candidate = f"{safe}_{ts}.md"

    if len(candidate) <= MAX_FILENAME_LEN and not ILLEGAL_FILENAME_CHARS.search(
        candidate
    ):
        return candidate

    h = hashlib.sha256(umo.encode("utf-8")).hexdigest()[:16]
    return f"{h}_{ts}.md"


# ── MD 序列化 ────────────────────────────────────────


def render_item_line(item: dict) -> str:
    """把单个 item 渲染为 .md 的一行。

    设计要点:
    - 占位符 `**(N)**` 永远在 title 之前,与 status mark 一起构成固定头部
    - **空 title 不写尾随空格**(关键:旧实现写 `- [ ] **(3)** ` 带尾随空格,
      会让 parse_md 的旧 regex 退化为 fallback,把整行 `**(N)**` 误识别为 title,
      下次 update 时再 render 就变成 `**(N)** **(N)` 重复累积 — Bug #1 根因)
    - title 与 notes 之间用 `  *(...)*` 隔开,notes 内部允许任意字符
      (包括 `*` `(` `)`,因为我们用 rfind/endswith 定位 notes,不做 regex 贪婪)
    """
    mark = STATUS_MARK.get(item.get("status", "pending"), "[ ]")
    title = (item.get("title") or "").strip()
    notes = (item.get("notes") or "").strip()
    parts = [f"- {mark} **({item['id']})**"]
    if title:
        parts.append(title)
    if notes:
        parts.append(f"*({notes})*")
    return " ".join(parts) if notes else (parts[0] if not title else " ".join(parts))


def render_md(data: dict) -> str:
    """把 list dict 渲染为 .md 文本。"""
    lines: list[str] = []
    # YAML frontmatter
    # v2.11: 由 sender_key/platform/sender_id 三字段简化为单字段 umo。
    # umo 已包含 platform/msg_type/sender_id/session_id 信息,无冗余。
    lines.append("---")
    lines.append(f"umo: {data['umo']}")
    lines.append(f"title: {data['title']}")
    lines.append(f"created_at: {data['created_at']}")
    lines.append(f"updated_at: {data['updated_at']}")
    lines.append("---")
    lines.append("")
    # 标题
    lines.append(f"# {data['title']}")
    lines.append("")
    # items
    for item in data["items"]:
        lines.append(render_item_line(item))
    lines.append("")
    # 进度统计
    stats = compute_stats(data["items"])
    lines.append("---")
    lines.append("")
    summary = (
        f"**进度**: {stats['done']}/{stats['effective_total']} 完成 "
        f"({stats['progress_pct']}%)"
    )
    parts = []
    if stats["in_progress"]:
        parts.append(f"{stats['in_progress']} in_progress")
    if stats["blocked_count"]:
        parts.append(f"{stats['blocked_count']} blocked")
    if stats["cancelled"]:
        parts.append(f"{stats['cancelled']} cancelled")
    if parts:
        summary += " · " + " · ".join(parts)
    lines.append(summary)
    lines.append("")
    return "\n".join(lines)


def compute_stats(items: list[dict]) -> dict:
    """根据 items 计算统计信息。"""
    by_status: dict[str, int] = {
        "pending": 0,
        "in_progress": 0,
        "done": 0,
        "cancelled": 0,
    }
    blocked = 0
    for it in items:
        s = it.get("status", "pending")
        by_status[s] = by_status.get(s, 0) + 1
        if s == "in_progress" and it.get("notes"):
            blocked += 1
    effective_total = sum(v for k, v in by_status.items() if k != "cancelled")
    pct = round(by_status["done"] / effective_total * 100) if effective_total else 0
    return {
        "total": len(items),
        "done": by_status["done"],
        "in_progress": by_status["in_progress"],
        "pending": by_status["pending"],
        "cancelled": by_status["cancelled"],
        "blocked_count": blocked,
        "effective_total": effective_total,
        "progress_pct": pct,
    }


# ── 反序列化 ────────────────────────────────────────

_FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)
# 解析 item 的固定头部: `- [mark] **(id)** ` (允许尾部空白)
# 关键:不再用单条大 regex 一次性 match title+notes,因为:
#   1. 旧 regex 要求 title 至少 1 字符,空 title 走 fallback 把 `**(N)**` 误识别为 title
#   2. 旧 regex 内部用 `[^(\n]+?` 匹配 title,在 title 含 `*` `(` 等字符时易错位
# 现在改为两阶段: 先定位头部,再从剩余中按"右锚定"剥离末尾 notes
_ITEM_HEADER = re.compile(r"^-\s+\[(?P<mark>[x~\-\s])\]\s+\*\*\((?P<id>\d+)\)\*\*\s*")
# 兼容旧 fallback 格式:`- [mark] title` (没有 `**(N)**` 占位符)
# 这种情况通常出现在外部手工编辑或更早版本写入的 md 文件。
# 注意 fallback **无法**提取 id,只能返回 None 让上层兜底。
_ITEM_FALLBACK = re.compile(r"^-\s+\[(?P<mark>[x~\-\s])\]\s+(?P<title>.+?)\s*$")


def _parse_item_line(line: str) -> dict | None:
    """解析单行 item,返回 {id, title, status, notes} 或 None(不识别)。

    两阶段策略:
      1. 匹配占位符头部,得到 mark / id 和剩余 rest
      2. 从 rest 末尾用 `rfind('*(')` + `endswith(')*')` 定位 notes
         (从右锚定,不依赖 regex 贪婪,能正确处理 notes 内部含 `*` `(` 等字符)
      3. title = rest 去掉 notes 段后 strip

    边界处理:
      - 空 title:render_md 现在写 `- [ ] **(N)**` 无尾随空格,rest 为空,title=""
      - 只有 notes:render_md 写 `- [ ] **(N)**  *(notes)*`,rest=`  *(notes)*`,
        rfind 定位到 notes,title=""
      - title 含 `*` `(`:只要不构成末尾 `*(...)`*` 模式,title 完整保留
    """
    m = _ITEM_HEADER.match(line)
    if not m:
        # 兼容旧 fallback(无占位符的 item 行)
        m2 = _ITEM_FALLBACK.match(line)
        if m2:
            return {
                "id": None,  # 旧格式无法可靠推断 id
                "title": m2.group("title").strip(),
                "status": MARK_STATUS.get(f"[{m2.group('mark')}]", "pending"),
                "notes": "",
            }
        return None
    rest = line[m.end() :]
    notes = ""
    idx = rest.rfind("*(")
    if idx >= 0 and rest.rstrip().endswith(")*"):
        # 找到的 *( 必须在 )* 之前,否则不算 notes
        # 例如 rest = "  *(abc)*  "  → idx=2, rstrip 后以 )* 结尾 ✓
        # 例如 rest = "*(" → 长度不足,跳过
        if len(rest) >= idx + 2 + 1:  # 至少有 * + ( + 一个字符
            candidate_notes = rest[idx + 2 : -2]  # 去掉 *( 和 )*
            notes = candidate_notes.strip()
            rest = rest[:idx]
    title = rest.strip()
    return {
        "id": int(m.group("id")),
        "title": title,
        "status": MARK_STATUS.get(f"[{m.group('mark')}]", "pending"),
        "notes": notes,
    }


def parse_md(text: str) -> dict:
    """从 .md 文本解析为 list dict。"""
    meta: dict[str, str] = {}
    body = text

    m = _FRONT_RE.match(text)
    if m:
        front_text = m.group(1)
        body = m.group(2)
        for line in front_text.splitlines():
            if ":" not in line:
                continue
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip().strip('"')

    # 提取标题
    title = meta.get("title", "Untitled")
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# "):
            title = s[2:].strip()
            break

    # 提取 items
    items: list[dict] = []
    next_id = 1
    for line in body.splitlines():
        parsed = _parse_item_line(line)
        if parsed is None:
            continue
        # fallback 格式(无 id)的 item 顺序追加 next_id
        if parsed["id"] is None:
            parsed["id"] = next_id
        else:
            next_id = parsed["id"] + 1
        items.append(parsed)

    # v2.11: 优先读 umo 字段;旧文件(无 umo 但有 sender_key/platform/sender_id)按
    # 旧值合成一个伪 umo 挂 ``legacy:`` 前缀,这样旧文件至少能 query 出来(不会因缺
    # 字段崩),后续写入/覆盖则一律走 umo 路径 —— 旧文件被"冻结"在原地。
    # 用户已决定:不做主动迁移(2026-06-20 决策),旧文件自然淘汰。
    # NOTE: ``meta.get("sender_key", ...)`` 的 key 是 YAML 字段字面量,固定不动。
    umo = meta.get("umo")
    if not umo:
        _old_umo = meta.get("sender_key", "unknown:unknown")
        umo = f"legacy:{_old_umo}"
    return {
        "umo": umo,
        "title": title,
        "created_at": meta.get("created_at", ""),
        "updated_at": meta.get("updated_at", ""),
        "items": items,
    }


# ── item_id 规范化(int | list[int] → list[int]) ──────


def _normalize_item_ids(
    value, *, allow_zero: bool = False, context: str = "item_id"
) -> list[int]:
    """把 update / delete 的 `item_id` 参数归一为 list[int]。

    接受:
    - int > 0  →  [int]
    - int == 0  →  [0]  (仅当 allow_zero=True;v2.2.0 后 delete/update 都用 False,
                          此分支目前无活跃调用方,保留为 helper 的可选能力)
    - list[int] →  list[int](去重,保留首次出现的顺序)

    拒绝(抛 ValueError,让上层转 ok=False 错误响应):
    - int == 0 且 allow_zero=False  (update 等场景下 0 不是合法 ID)
    - list 含 0  (避免和"清空整个 list" 语义冲突,用单项 0 触发)
    - 空 list  (不允许 LLM 传 [] 当 no-op,直接报错更明确)
    - 负数     (ID 从 1 开始)
    - 非 int 元素 / bool (Python 中 bool 是 int 的子类,显式拒绝)
    - 任何其他类型(str / None / dict ...)

    Parameters
    ----------
    value : int | list[int] | 任意
        待归一化的输入。LLM 可能传错类型,函数显式拒绝并报清晰错误。
    allow_zero : bool
        是否允许 0(delete 用 True,update 用 False)。
    context : str
        错误信息里的字段名,默认 "item_id"。
    """
    if isinstance(value, bool):
        # bool 是 int 的子类,显式拒绝避免误判
        raise ValueError(f"{context} must be int or list[int], got bool")
    if isinstance(value, int):
        if value == 0:
            if not allow_zero:
                raise ValueError(f"{context}=0 is not valid; IDs start at 1")
            return [0]
        if value < 0:
            raise ValueError(f"{context} must be positive, got {value}")
        return [value]
    if isinstance(value, list):
        if not value:
            raise ValueError(f"{context} is an empty list; provide at least one ID")
        out: list[int] = []
        for v in value:
            if isinstance(v, bool) or not isinstance(v, int):
                raise ValueError(
                    f"{context} entries must be int, got {type(v).__name__}"
                )
            if v == 0:
                raise ValueError(f"{context}=0 is not valid; IDs start at 1")
            if v < 0:
                raise ValueError(f"{context} entries must be positive, got {v}")
            if v not in out:
                out.append(v)
        return out
    raise ValueError(f"{context} must be int or list[int], got {type(value).__name__}")


# ── item 规范化(dict | list[dict] → list[dict]) ──────


def _normalize_items(value, *, context: str = "item") -> list[dict]:
    """把 add 工具的 `item` 参数归一为 list[dict]。

    接受:
    - dict         →  [dict]   (包裹成单元素列表)
    - list[dict]   →  list[dict] (保留顺序)

    拒绝(抛 ValueError,让上层转 ok=False 错误响应):
    - 空 list      (不允许 LLM 传 [] 当 no-op)
    - list 中含非 dict 元素 (报错时附带索引,告诉 LLM 是哪一项坏掉)
    - 任何其他类型(str / int / None / bool ...) 都直接拒绝

    Parameters
    ----------
    value : dict | list[dict] | 任意
        待归一化的输入。
    context : str
        错误信息里的字段名,默认 "item"。
    """
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        if not value:
            raise ValueError(f"{context} is an empty list; provide at least one item")
        for idx, v in enumerate(value):
            if not isinstance(v, dict):
                raise ValueError(
                    f"{context}[{idx}] must be a dict, got {type(v).__name__}"
                )
        return value
    raise ValueError(
        f"{context} must be dict or list[dict], got {type(value).__name__}"
    )


# ── TodoStore ───────────────────────────────────────


class TodoStore:
    """单个用户的 todo list 持久化存储。"""

    def __init__(self, base_dir: str | Path):
        self._dir = Path(base_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, umo: str, when: datetime | None = None) -> Path:
        return self._dir / build_filename(umo, when)

    def _atomic_write(self, path: Path, content: str) -> None:
        """先写 tmp 文件，再 os.replace 原子替换。

        写失败时清理可能残留的 .tmp 文件，再重新抛出 OSError。
        调用方负责把 OSError 转为 {"ok": False, "error": ...}。
        """
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp.write_text(content, encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            # 清理可能残留的半成品 .tmp，避免污染目录
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            raise

    def _existing_path(self, umo: str) -> Path | None:
        """查找该 umo 的现有文件(日期不固定)。

        v2.11: 输入从 sender_key 切到 umo(可能含多 `:`)。
        文件名里 `:` 已被替换为 `_`,前缀匹配也按 umo 清洗后的字符串来。
        """
        safe = umo.replace(":", "_")
        prefix = f"{safe}_"
        for p in sorted(self._dir.glob(f"{prefix}*.md"), reverse=True):
            return p
        # 哈希回退形式
        h = hashlib.sha256(umo.encode("utf-8")).hexdigest()[:16]
        for p in sorted(self._dir.glob(f"{h}_*.md"), reverse=True):
            return p
        return None

    def _load(self, umo: str) -> tuple[Path | None, dict]:
        path = self._existing_path(umo)
        if not path:
            return None, {}
        return path, parse_md(path.read_text(encoding="utf-8"))

    def create(
        self,
        umo: str,
        title: str = "",
        items: list[dict] | None = None,
    ) -> dict:
        """Create a new todo list.

        v2.2.0: only the **Fresh** mode remains. `items` must be a non-empty
        list of dicts. Passing `items=None` or `items=[]` returns an error.

        Fresh mode overwrites any file at the current minute for this sender.
        The previous file (if any) is unlinked and `previous_item_count`
        reflects how many items it held.
        """
        items = items or []
        if not items:
            return {
                "ok": False,
                "error": "items 不能为空,请提供至少一个 item",
            }
        if len(items) > MAX_ITEMS:
            return {
                "ok": False,
                "error": f"items count {len(items)} exceeds limit {MAX_ITEMS}",
            }

        # --- Compute previous_item_count from any existing same-minute file ---
        previous_count = 0
        old_path, _ = self._load(umo)
        if old_path:
            try:
                old_data = parse_md(old_path.read_text(encoding="utf-8"))
                previous_count = len(old_data.get("items", []))
            except Exception:
                pass
            old_path.unlink()

        # --- Title resolution ---
        if not title:
            title = umo

        # --- Build new data ---
        # v2.11: 删除 platform/sender_id 拆分,umo 已是完整标识(含 platform/msg_type/sid/session_id)。
        now_iso = datetime.now().isoformat(timespec="seconds")
        data = {
            "umo": umo,
            "title": title,
            "created_at": now_iso,
            "updated_at": now_iso,
            "items": [
                {
                    "id": i + 1,
                    "title": it.get("title", ""),
                    "status": it.get("status", "pending")
                    if it.get("status") in VALID_STATUSES
                    else "pending",
                    "notes": it.get("notes", ""),
                }
                for i, it in enumerate(items)
            ],
        }
        new_path = self._path_for(umo)
        try:
            self._atomic_write(new_path, render_md(data))
        except OSError as e:
            return {"ok": False, "error": f"Write failed: {e}"}
        result = {
            "ok": True,
            "list_title": data["title"],
            "item_count": len(data["items"]),
            "previous_item_count": previous_count,
            "file": str(new_path),
        }
        # Include full list state for downstream display
        result.update(self._build_list_state(data, new_path))
        return result

    def query(self, umo: str) -> dict:
        """读取 list，返回结构化数据。"""
        path, data = self._load(umo)
        if not path:
            return {
                "ok": False,
                "proposal": (
                    "当前无 todo list，请先调用 todo_create(items=[...]) 创建"
                ),
            }
        result = {"ok": True, "file": str(path)}
        result.update(self._build_list_state(data, path))
        return result

    @staticmethod
    def _build_list_state(data: dict, path: Path) -> dict:
        """从内存中的 list dict 生成 {list, stats, attention_items} 段。

        供 query / create / add / update / delete 复用，避免各方法重复写 stats 与
        attention 标记逻辑。**不会修改**传入的 data，而是为每个 item
        浅拷贝一份再注入 attention 字段。
        """
        items_with_attention: list[dict] = []
        attention_ids: list[int] = []
        for it in data["items"]:
            attn = bool(it["status"] == "in_progress" and it.get("notes"))
            item_copy = {**it, "attention": attn}
            items_with_attention.append(item_copy)
            if attn:
                attention_ids.append(item_copy["id"])
        list_snapshot = {**data, "items": items_with_attention}
        return {
            "list": list_snapshot,
            "stats": compute_stats(items_with_attention),
            "attention_items": attention_ids,
        }

    def add(self, umo: str, items: dict | list[dict]) -> dict:
        """追加一个或多个 item。

        `items` 接受:
        - dict         → 追加单条
        - list[dict]   → 批量追加多条,每条独立带 title/status/notes

        行为契约:
        - 追加后总数超过 MAX_ITEMS → 全量回滚(已有数据原封不动)
        - 任一 item 含非法 status → 全量回滚
        - 永远返回完整 list + stats + attention_items
        - 返回统一使用 list 形式 item_ids + items(v2.2.0 移除单条兼容字段 item_id/item)
        """
        try:
            new_items = _normalize_items(items, context="items")
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        path, data = self._load(umo)
        if not path:
            return {
                "ok": False,
                "proposal": "当前无 todo list，请先 todo_create(items=[...])",
            }

        current = data["items"]
        # 容量校验:追加后总数不能超 MAX_ITEMS
        if len(current) + len(new_items) > MAX_ITEMS:
            return {
                "ok": False,
                "error": (
                    f"items 数量将超上限: 现有 {len(current)} + "
                    f"待加 {len(new_items)} > {MAX_ITEMS}"
                ),
                "item_count": len(current),  # v2.2.0: 便于前端展示当前上限状态
            }

        # 校验所有 status(任何一条非法就全量回滚)
        for idx, raw in enumerate(new_items):
            st = raw.get("status", "pending")
            if st not in VALID_STATUSES:
                return {
                    "ok": False,
                    "error": f"item[{idx}] 非法 status '{st}'",
                    "proposal": f"可选: {sorted(VALID_STATUSES)}",
                }

        # 分配连续自增 ID,从 max+1 开始
        next_id = max((it["id"] for it in current), default=0) + 1
        added: list[dict] = []
        for raw in new_items:
            added.append(
                {
                    "id": next_id,
                    "title": str(raw.get("title", "")),
                    "status": raw.get("status", "pending"),
                    "notes": str(raw.get("notes", "")),
                }
            )
            next_id += 1
        current.extend(added)

        data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        try:
            self._atomic_write(path, render_md(data))
        except OSError as e:
            return {"ok": False, "error": f"写入失败: {e}"}

        result: dict = {
            "ok": True,
            "item_ids": [it["id"] for it in added],  # 永远返回 list
            "items": added,  # 与 item_ids 一一对应
            "item_count": len(current),
        }
        # v2.2.0: 移除单条兼容字段 item_id(int)/ item(dict);统一返回 list 形式
        # 附带完整 list 状态，便于前端在 add 后直接展示
        result.update(self._build_list_state(data, path))
        return result

    def update(
        self,
        umo: str,
        item_ids: int | list[int],
        status: str = "",
        notes: str = "",
        clear_notes: bool = False,
    ) -> dict:
        """更新一个或多个 item 的 status / notes。

        `item_ids` 可为单个 int(只改一条)或 list[int](批量改,共用同一组
        status/notes/clear_notes)。任意 ID 不存在 → 全量回滚,不会留下
        残缺状态。

        返回: 成功时包含 `item_ids`(list) + `items`(list),单条 / 批量
        统一返回 list 形式,前端无需按数量分支。
        """
        try:
            ids = _normalize_item_ids(item_ids, allow_zero=False, context="item_ids")
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        path, data = self._load(umo)
        if not path:
            return {
                "ok": False,
                "proposal": "当前无 todo list，请先 todo_create(items=[...])",
            }

        # 任何 status 校验必须在动数据之前
        if status and status not in VALID_STATUSES:
            return {
                "ok": False,
                "error": f"非法 status '{status}'",
                "proposal": f"可选: {sorted(VALID_STATUSES)}",
            }

        # 批量校验:任一 ID 缺失 → 全部回滚
        by_id = {it["id"]: it for it in data["items"]}
        missing = [i for i in ids if i not in by_id]
        if missing:
            valid = [it["id"] for it in data["items"]]
            return {
                "ok": False,
                "error": f"item(s) {missing} 不存在",
                "proposal": f"有效 ID: {valid}",
            }

        # 应用变更到所有目标(顺序与 ids 一致,便于 LLM 推断对应关系)
        updated_items: list[dict] = []
        for i in ids:
            target = by_id[i]
            if status:
                target["status"] = status
            if clear_notes:
                target["notes"] = ""
            elif notes:  # 空字符串视为"保留旧值"
                target["notes"] = notes
            updated_items.append(dict(target))  # 浅拷贝快照,避免污染 list 状态

        data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        try:
            self._atomic_write(path, render_md(data))
        except OSError as e:
            return {"ok": False, "error": f"写入失败: {e}"}

        result: dict = {
            "ok": True,
            "item_ids": ids,  # 永远返回 list, 统一下游消费
            "items": updated_items,  # 与 item_ids 一一对应
        }
        result.update(self._build_list_state(data, path))
        return result

    def delete(self, umo: str, item_ids: int | list[int]) -> dict:
        """删一个或多个 item。

        `item_ids` 接受:
        - int > 0        → 删单条(回传完整 list/stats)
        - list[int > 0]  → 批量删多条(回传完整 list/stats)

        v2.2.0: **不再接受 0** —— 清空整个 list 由独立的 todo_clear 工具负责。
        `item_ids=0` 或 `item_ids=[..., 0, ...]` 都会返回 ok=False 错误。
        这样 update/delete 字段语义彻底统一,0 永远不是合法 ID。

        批量场景下,任一 ID 不存在 → 全量回滚,数据原封不动。

        返回 (v2.2.0 统一契约):
        - ok / error / proposal
        - deleted (int, 删除条数)
        - item_ids (list[int], 与 ids 入参顺序一致)
        - item_count (剩余条数)
        - list / stats / attention_items (与 query 对齐,便于前端在删后直接渲染)
        - **不带** 旧兼容字段 item_id (int);统一返回 list 形式以对齐 add/update
        """
        try:
            ids = _normalize_item_ids(item_ids, allow_zero=False, context="item_ids")
        except ValueError as e:
            return {"ok": False, "error": str(e)}

        path, data = self._load(umo)
        if not path:
            return {
                "ok": False,
                "proposal": "当前无 todo list",
            }

        # 批量校验:任一 ID 缺失 → 全部回滚
        by_id = {it["id"]: it for it in data["items"]}
        missing = [i for i in ids if i not in by_id]
        if missing:
            valid = [it["id"] for it in data["items"]]
            return {
                "ok": False,
                "error": f"item(s) {missing} 不存在",
                "proposal": f"有效 ID: {valid}",
            }

        before = len(data["items"])
        target_set = set(ids)
        data["items"] = [it for it in data["items"] if it["id"] not in target_set]
        deleted_count = before - len(data["items"])

        data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        try:
            self._atomic_write(path, render_md(data))
        except OSError as e:
            return {"ok": False, "error": f"写入失败: {e}"}

        result: dict = {
            "ok": True,
            "deleted": deleted_count,
            "item_ids": ids,  # 永远返回 list (v2.2.0: 移除单条兼容字段 item_id)
            "item_count": len(data["items"]),
        }
        # 删单/批条后列表还在,附带完整 list 状态
        result.update(self._build_list_state(data, path))
        return result

    def modify(
        self,
        umo: str,
        mode: str,
        items: list[dict] | None = None,
        item_ids: int | list[int] | None = None,
        status: str = "",
        notes: str | None = UNSET_NOTES,
    ) -> dict:
        """统一 add / update / delete 的分发入口。

        - mode='add':    调用 self.add(items=items)
        - mode='update': 调用 self.update(item_ids=item_ids, status=status, notes=notes)
        - mode='delete': 调用 self.delete(item_ids=item_ids)
        - 其他 mode:     返回 {"ok": False, "error": "未知 mode: {mode}"}

        notes 三态 (None = 未传, 保留旧值):
        - None  → 保留旧 notes
        - ""    → 清空 notes
        - "xxx" → 覆盖 notes
        """
        if mode == "add":
            return self.add(umo, items)
        if mode == "update":
            # notes 三态桥接到 update() 的 (notes, clear_notes) 二元:
            #   None (未传)  →  保留旧值   → clear_notes=False, notes=""
            #   ""   (传空)  →  清空 notes → clear_notes=True,  notes=""
            #   "x"  (传值)  →  覆盖       → clear_notes=False, notes="x"
            # 注意: update() 内部 if clear_notes: target["notes"] = "" 无条件清空,
            # 所以"覆盖"分支必须 clear_notes=False,否则新值会被清空逻辑覆盖掉。
            if notes is None:
                actual_notes = ""
                actual_clear = False
            elif notes == "":
                actual_notes = ""
                actual_clear = True
            else:
                actual_notes = notes
                actual_clear = False
            return self.update(
                umo,
                item_ids,
                status=status,
                notes=actual_notes,
                clear_notes=actual_clear,
            )
        if mode == "delete":
            return self.delete(umo, item_ids)
        return {
            "ok": False,
            "error": f"未知 mode: {mode}",
            "proposal": "可选: add/update/delete",
        }

    def clear(self, umo: str) -> dict:
        """删整个 todo list(文件 unlink)。

        v2.2.0: 不再是 delete(0) 的别名,而是独立实现。delete() 已拒绝 ID=0,
        所以本方法直接 unlink 文件,语义保持不变以让 main.py 的 action=='clear'
        继续可用。未来 todo_clear 工具可能进一步封装此方法。
        """
        path, data = self._load(umo)
        if not path:
            return {
                "ok": False,
                "proposal": "当前无 todo list",
            }
        path.unlink()
        return {"ok": True, "deleted": "list", "file": str(path)}
