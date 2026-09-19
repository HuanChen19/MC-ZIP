# -*- coding: utf-8 -*-
"""
addon_core.py —— Minecraft 基岩版 Addon 核心处理逻辑

职责：
  1. 扫描 / 读取 Addon 文件夹中的 manifest.json
  2. 版本号自增（默认补丁位，0,0,1 -> 0,0,2）
  3. 随机刷新 UUID（header.uuid / modules[].uuid）
  4. 打包为 .mcaddon / .mcpack / .zip

设计约束（对应用户需求「不影响其他配置内容」）：
  * 使用 OrderedDict 解析，写回时严格保持原有键顺序
  * 自动探测原文件的缩进风格并沿用
  * 原子写入（先写 .tmp 再替换），避免写坏源文件
  * 修改前自动备份为 manifest.json.bak
  * 仅改动目标字段，其余字段（description / dependencies / min_engine_version ...）原样保留

本模块不依赖任何第三方库。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid as _uuid
import zipfile
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------- #
# 常量
# --------------------------------------------------------------------------- #

MANIFEST_NAME = "manifest.json"
BACKUP_NAME = "manifest.json.bak"
MAX_SCAN_DEPTH = 4

# 打包时忽略的目录名
SKIP_DIR_NAMES = {
    ".git", ".svn", ".hg", ".idea", ".vscode", "__macosx", "__pycache__",
    "node_modules", ".cache", ".venv", "venv", ".mypy_cache", ".pytest_cache",
    ".vs", ".gradle",
}
# 打包时忽略的文件名（小写比较）
SKIP_FILE_NAMES = {".ds_store", "thumbs.db", "desktop.ini", "manifest.json.bak"}
# 打包时忽略的后缀（小写比较）
SKIP_FILE_SUFFIXES = (".bak", ".pyc", ".pyo", ".swp", ".tmp", ".orig", ".log")

# module.type -> 中文说明
MODULE_TYPE_LABELS = {
    "data": "行为包",
    "resources": "资源包",
    "script": "脚本包",
    "javascript": "脚本包",
    "client_data": "客户端数据",
    "interface": "UI 界面",
    "skin_pack": "皮肤包",
    "world_template": "世界模板",
    "persona_piece": "个性化部件",
}

# 判定一个包属于 BP 还是 RP：看它 modules 里出现的 type
BP_MODULE_TYPES = {"data", "script", "javascript"}
RP_MODULE_TYPES = {"resources", "client_data", "interface"}

PACK_CATEGORY_LABELS = {
    "bp": "行为包",
    "rp": "资源包",
    "bp_rp": "行为包+资源包",
    "other": "其他",
}

VERSION_PART_LABELS = {
    "patch": "补丁位（第 3 位）",
    "minor": "次版本（第 2 位）",
    "major": "主版本（第 1 位）",
}

PACK_MODES = OrderedDict([
    ("auto", "自动识别（推荐）"),
    ("mcaddon", ".mcaddon 整合包"),
    ("mcpack", ".mcpack 单包"),
    ("zip", ".zip 目录快照"),
])


class ManifestError(Exception):
    """manifest.json 解析或结构异常。"""


# --------------------------------------------------------------------------- #
# 基础读写工具
# --------------------------------------------------------------------------- #

def read_text(path: Path) -> Tuple[str, str, bool]:
    """读取文本，返回 (文本, 编码, 是否带 BOM)。"""
    raw = Path(path).read_bytes()
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    if has_bom:
        raw = raw[3:]
    for enc in ("utf-8", "gb18030", "big5", "latin-1"):
        try:
            return raw.decode(enc), enc, has_bom
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace"), "utf-8", has_bom


def detect_indent(text: str) -> str:
    """从原文探测缩进风格（空格 / Tab），默认两空格。"""
    match = re.search(r"\n([ \t]+)\S", text)
    if not match:
        return "  "
    indent = match.group(1)
    # 排除误判成行首对齐的长串空白
    return indent if len(indent) <= 8 else "  "


def dump_manifest(data: Any, indent: str = "  ", trailing_newline: bool = True) -> str:
    """序列化 manifest，保持中文可读（不转义）。"""
    text = json.dumps(data, indent=indent, ensure_ascii=False)
    return text + "\n" if trailing_newline else text


def safe_filename(name: str, fallback: str = "addon") -> str:
    """去除 Windows 非法文件名字符。"""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name or "")).strip(" .")
    return cleaned or fallback


def fmt_version(version: Sequence[int]) -> str:
    return ".".join(str(int(x)) for x in version)


# --------------------------------------------------------------------------- #
# 版本号处理
# --------------------------------------------------------------------------- #

def normalize_version(value: Any, default: Sequence[int] = (0, 0, 1)) -> List[int]:
    """把任意形态的版本号规整为整数列表。"""
    if isinstance(value, (list, tuple)):
        out: List[int] = []
        for item in value:
            try:
                out.append(int(item))
            except (TypeError, ValueError):
                out.append(0)
        return out or list(default)
    if isinstance(value, str):
        nums = [int(p) for p in re.split(r"[.,\s\-_]+", value.strip()) if p.isdigit()]
        if nums:
            return nums
    return list(default)


def bump_version(version: Sequence[int], part: str = "patch") -> List[int]:
    """
    递增版本号。默认只动最后一位（补丁位）：
        [0, 0, 1] -> [0, 0, 2]
    升级主/次版本时，其右侧位归零：
        [1, 4, 7] minor -> [1, 5, 0]
    """
    result = [int(x) for x in version] or [0, 0, 1]
    while len(result) < 3:
        result.append(0)
    index = {"major": 0, "minor": 1, "patch": 2}.get(part, 2)
    if index >= len(result):
        index = len(result) - 1
    result[index] += 1
    if index < 2:
        for i in range(index + 1, len(result)):
            result[i] = 0
    return result


def generate_uuid(style: str = "hyphen") -> str:
    """生成随机 UUID v4。style: hyphen（带横线）/ hex（无横线）。"""
    value = _uuid.uuid4()
    return str(value) if style != "hex" else value.hex


# --------------------------------------------------------------------------- #
# 包信息
# --------------------------------------------------------------------------- #

def header_display_name(value: Any) -> str:
    """header.name 可能是字符串，也可能是本地化字典。"""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("zh_CN", "zh_cn", "zh-Hans", "en_US", "en_GB"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item
        for item in value.values():
            if isinstance(item, str) and item.strip():
                return item
    return ""


@dataclass
class PackInfo:
    """单个 Addon 包（一个 manifest.json 所在目录）。"""

    root: Path
    manifest_path: Path
    data: Any
    indent: str = "  "
    encoding: str = "utf-8"
    has_bom: bool = False
    trailing_newline: bool = True

    # 派生字段（由 sync() 维护）
    name: str = ""
    kind: str = ""
    category: str = "other"
    raw_types: List[str] = field(default_factory=list)
    format_version: Any = None
    version: List[int] = field(default_factory=list)
    pack_uuid: str = ""
    module_uuids: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    # ---------- 视图属性 ----------

    @property
    def rel_name(self) -> str:
        return self.root.name

    @property
    def display_name(self) -> str:
        return self.name or self.rel_name

    @property
    def display_version(self) -> str:
        return fmt_version(self.version) if self.version else "—"

    @property
    def display_uuid(self) -> str:
        return self.pack_uuid or "—"

    @property
    def is_bp_rp(self) -> bool:
        """是否属于 BP（行为包）/ RP（资源包）。"""
        return self.category in ("bp", "rp", "bp_rp")

    @property
    def is_single_pack(self) -> bool:
        return True

    # ---------- 结构访问 ----------

    def modules_list(self) -> List[Dict[str, Any]]:
        """返回 data['modules'] 中真正的字典列表（可直接改写）。"""
        modules = self.data.get("modules")
        if not isinstance(modules, list):
            return []
        return [m for m in modules if isinstance(m, dict)]

    def sync(self) -> None:
        """从 data 重新派生展示字段。"""
        self.warnings = []
        header = self.data.get("header")
        if not isinstance(header, dict):
            self.warnings.append("缺少 header 节点")
            header = {}
        self.name = header_display_name(header.get("name"))
        if "version" in header:
            self.version = normalize_version(header.get("version"))
        else:
            self.version = []
            self.warnings.append("header.version 缺失")
        self.pack_uuid = str(header.get("uuid") or "")
        if not self.pack_uuid:
            self.warnings.append("header.uuid 缺失")

        modules = self.modules_list()
        self.module_uuids = [str(m.get("uuid") or "") for m in modules]

        types = [str(m.get("type") or "").strip().lower() for m in modules]
        self.raw_types = types
        has_bp = any(t in BP_MODULE_TYPES for t in types)
        has_rp = any(t in RP_MODULE_TYPES for t in types)
        if has_bp and has_rp:
            self.category = "bp_rp"
        elif has_bp:
            self.category = "bp"
        elif has_rp:
            self.category = "rp"
        else:
            self.category = "other"

        if not modules:
            self.kind = "未知"
            self.warnings.append("modules 为空")
        elif self.category == "other":
            first = types[0] if types else ""
            self.kind = MODULE_TYPE_LABELS.get(first, first or "未知")
        else:
            self.kind = PACK_CATEGORY_LABELS[self.category]

        self.format_version = self.data.get("format_version")

    # ---------- 写盘 ----------

    def save(self, backup: bool = True) -> Optional[Path]:
        """写回 manifest.json，可选先备份。返回备份文件路径。"""
        backup_path: Optional[Path] = None
        if backup:
            try:
                backup_path = self.manifest_path.with_name(BACKUP_NAME)
                shutil.copy2(self.manifest_path, backup_path)
            except OSError:
                backup_path = None

        text = dump_manifest(self.data, self.indent, self.trailing_newline)
        payload = text.encode("utf-8")
        if self.has_bom:
            payload = b"\xef\xbb\xbf" + payload

        tmp_path = self.manifest_path.with_name(MANIFEST_NAME + ".tmp")
        tmp_path.write_bytes(payload)
        os.replace(tmp_path, self.manifest_path)
        return backup_path


# --------------------------------------------------------------------------- #
# 扫描 / 加载
# --------------------------------------------------------------------------- #

def find_pack_roots(root: Path) -> List[Path]:
    """
    返回根目录下所有包目录（含 manifest.json 的目录）。
      * 若 root 自身就有 manifest.json -> 视为单包，直接返回 [root]
      * 否则向下扫描（最多 MAX_SCAN_DEPTH 层），命中即停止下钻
    """
    root = Path(root)
    if not root.is_dir():
        raise ManifestError(f"路径不存在或不是文件夹：{root}")
    if (root / MANIFEST_NAME).is_file():
        return [root]

    found: List[Path] = []

    def walk(current: Path, depth: int) -> None:
        if depth > MAX_SCAN_DEPTH:
            return
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return
        for entry in entries:
            if not entry.is_dir():
                continue
            lowered = entry.name.lower()
            if lowered in SKIP_DIR_NAMES or entry.name.startswith("."):
                continue
            if (entry / MANIFEST_NAME).is_file():
                found.append(entry)
                continue
            walk(entry, depth + 1)

    walk(root, 1)
    return found


def load_pack(pack_root: Path) -> PackInfo:
    """读取并解析一个包目录的 manifest.json。"""
    root = Path(pack_root)
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise ManifestError(f"未找到 {MANIFEST_NAME}：{manifest_path}")

    text, encoding, has_bom = read_text(manifest_path)
    try:
        data = json.loads(text, object_pairs_hook=OrderedDict)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{manifest_path} 不是合法 JSON：第 {exc.lineno} 行 {exc.msg}") from exc
    if not isinstance(data, dict):
        raise ManifestError(f"{manifest_path} 顶层结构必须是 JSON 对象")

    info = PackInfo(
        root=root,
        manifest_path=manifest_path,
        data=data,
        indent=detect_indent(text),
        encoding=encoding,
        has_bom=has_bom or encoding == "utf-8-sig",
        trailing_newline=text.endswith("\n"),
    )
    info.sync()
    return info


def load_addon(root: Path) -> List[PackInfo]:
    """加载一个 Addon 文件夹中的全部包，并按目录名排序。"""
    roots = find_pack_roots(root)
    if not roots:
        raise ManifestError(
            "没有在该文件夹中找到任何 manifest.json。\n"
            "请确认选择的是 Addon 根目录（其下含 BP/RP 子包）或单个包目录。"
        )
    packs = [load_pack(p) for p in roots]
    packs.sort(key=lambda p: (p.root.name.lower(), str(p.root).lower()))
    return packs


# --------------------------------------------------------------------------- #
# 修改操作
# --------------------------------------------------------------------------- #

def _require_header(pack: PackInfo) -> Dict[str, Any]:
    header = pack.data.get("header")
    if not isinstance(header, dict):
        raise ManifestError(f"{pack.manifest_path} 缺少 header 节点，无法修改")
    return header


@dataclass
class ChangeReport:
    """一次修改操作的结果，供界面展示日志。"""

    pack: PackInfo
    action: str
    messages: List[str] = field(default_factory=list)
    backup: Optional[Path] = None
    changed: bool = False


def bump_versions(
    pack: PackInfo,
    part: str = "patch",
    include_modules: bool = False,
    backup: bool = True,
) -> ChangeReport:
    """递增 header.version（可选同时递增 modules[].version）。"""
    report = ChangeReport(pack=pack, action="version")
    header = _require_header(pack)

    if "version" not in header:
        report.messages.append(f"[{pack.rel_name}] header.version 缺失，按 0.0.1 起算")

    old_version = normalize_version(header.get("version"))
    new_version = bump_version(old_version, part)
    header["version"] = new_version
    report.messages.append(
        f"[{pack.rel_name}] header.version  {fmt_version(old_version)} -> {fmt_version(new_version)}"
    )

    if include_modules:
        for index, module in enumerate(pack.modules_list()):
            if "version" not in module:
                continue
            module_old = normalize_version(module.get("version"))
            module_new = bump_version(module_old, part)
            module["version"] = module_new
            report.messages.append(
                f"[{pack.rel_name}] modules[{index}].version  "
                f"{fmt_version(module_old)} -> {fmt_version(module_new)}"
            )

    pack.sync()
    report.backup = pack.save(backup=backup)
    report.changed = True
    return report


def refresh_uuids(
    pack: PackInfo,
    include_header: bool = True,
    include_modules: bool = True,
    style: str = "hyphen",
    backup: bool = True,
) -> ChangeReport:
    """随机刷新 header.uuid 与 modules[].uuid。dependencies 中的 uuid 一律不动。"""
    report = ChangeReport(pack=pack, action="uuid")
    header = _require_header(pack)

    if include_header:
        old_uuid = str(header.get("uuid") or "(缺失)")
        new_uuid = generate_uuid(style)
        header["uuid"] = new_uuid
        report.messages.append(f"[{pack.rel_name}] header.uuid  {old_uuid} -> {new_uuid}")

    if include_modules:
        for index, module in enumerate(pack.modules_list()):
            if "uuid" not in module:
                report.messages.append(f"[{pack.rel_name}] modules[{index}].uuid 缺失，已跳过")
                continue
            old_uuid = str(module.get("uuid"))
            new_uuid = generate_uuid(style)
            module["uuid"] = new_uuid
            report.messages.append(
                f"[{pack.rel_name}] modules[{index}].uuid  {old_uuid} -> {new_uuid}"
            )

    dependencies = pack.data.get("dependencies")
    if isinstance(dependencies, list) and dependencies:
        report.messages.append(
            f"[{pack.rel_name}] 已保留 dependencies 中的 uuid（指向外部包，不可改动）"
        )

    pack.sync()
    report.backup = pack.save(backup=backup)
    report.changed = True
    return report


# --------------------------------------------------------------------------- #
# 打包
# --------------------------------------------------------------------------- #

def collect_entries(
    pack_roots: Sequence[Path],
    base: Path,
    skip_paths: Iterable[Path] = (),
) -> List[Tuple[Path, str]]:
    """收集待压缩文件，返回 [(绝对路径, 压缩包内相对路径)]。"""
    base = Path(base)
    skip_files = set()
    skip_dirs = set()
    for item in skip_paths:
        try:
            resolved = Path(item).resolve()
        except OSError:
            continue
        skip_files.add(resolved)
        if resolved.is_dir():
            skip_dirs.add(resolved)

    entries: List[Tuple[Path, str]] = []
    for pack_root in pack_roots:
        pack_root = Path(pack_root)
        for dirpath, dirnames, filenames in os.walk(pack_root):
            # 原地过滤目录，避免走入无关目录
            kept: List[str] = []
            for name in sorted(dirnames):
                if name.lower() in SKIP_DIR_NAMES or name.startswith("."):
                    continue
                child = Path(dirpath) / name
                try:
                    if child.resolve() in skip_dirs:
                        continue
                except OSError:
                    pass
                kept.append(name)
            dirnames[:] = kept

            for name in sorted(filenames):
                lowered = name.lower()
                if lowered in SKIP_FILE_NAMES:
                    continue
                if lowered.endswith(SKIP_FILE_SUFFIXES):
                    continue
                source = Path(dirpath) / name
                try:
                    if source.resolve() in skip_files:
                        continue
                except OSError:
                    pass
                try:
                    arcname = source.relative_to(base).as_posix()
                except ValueError:
                    arcname = source.name
                entries.append((source, arcname))
    entries.sort(key=lambda item: item[1])
    return entries


def _write_zip(entries: Sequence[Tuple[Path, str]], out_path: Path) -> int:
    """按条目写压缩包，保留文件修改时间。返回写入文件数。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source, arcname in entries:
            try:
                stat = source.stat()
                stamp = time.localtime(stat.st_mtime)[:6]
            except OSError:
                stat = None
                stamp = time.localtime()[:6]
            # 早于 1980 的时间戳 zip 不支持
            if stamp[0] < 1980:
                stamp = (1980, 1, 1, 0, 0, 0)
            info = zipfile.ZipInfo(arcname, date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            if stat is not None:
                info.external_attr = (stat.st_mode & 0xFFFF) << 16
            archive.writestr(info, source.read_bytes())
            count += 1
    return count


def version_tag(packs: Sequence[PackInfo]) -> str:
    """多包取最高版本号作为文件名后缀。"""
    versions = [tuple(p.version) for p in packs if p.version]
    if not versions:
        return "0.0.1"
    return fmt_version(max(versions))


@dataclass
class PackageResult:
    archives: List[Path] = field(default_factory=list)
    file_count: int = 0
    skipped_note: str = ""
    included: List[str] = field(default_factory=list)
    excluded: List[str] = field(default_factory=list)


def _is_root_itself(root: Path, packs: Sequence[PackInfo]) -> bool:
    if len(packs) != 1:
        return False
    try:
        return packs[0].root.resolve() == Path(root).resolve()
    except OSError:
        return False


def select_packs(
    packs: Sequence[PackInfo],
    only_bp_rp: bool = True,
    root: Optional[Path] = None,
) -> Tuple[List[PackInfo], List[PackInfo]]:
    """
    按类型筛选要打包的包，返回 (保留, 排除)。

    * 只保留行为包（BP）/ 资源包（RP），皮肤包 / 世界模板等其他类型会被排除
    * 例外：导入的就是单个包目录本身时不做筛选（用户已明确指定了那个目录）
    * only_bp_rp=False 时全部保留
    """
    items = list(packs)
    if not only_bp_rp:
        return items, []
    if root is not None and _is_root_itself(root, items):
        return items, []
    included = [p for p in items if p.is_bp_rp]
    excluded = [p for p in items if not p.is_bp_rp]
    return included, excluded


def resolve_mode(mode: str, root: Path, packs: Sequence[PackInfo]) -> str:
    """自动模式判定：单包（根目录自身即包）-> mcpack，否则 mcaddon。"""
    if mode != "auto":
        return mode
    if _is_root_itself(root, packs):
        return "mcpack"
    return "mcaddon"


def build_package(
    root: Path,
    packs: Sequence[PackInfo],
    out_dir: Path,
    mode: str = "auto",
    base_name: Optional[str] = None,
    only_bp_rp: bool = True,
) -> PackageResult:
    """
    执行打包。

    压缩包结构说明：
      * .mcpack  —— base 取包目录本身，manifest.json 位于压缩包根
      * .mcaddon —— base 取 Addon 根目录，各子包以文件夹形式并列存放，
                    避免 BP / RP 的 manifest.json、pack_icon.png 互相覆盖
      * .zip     —— base 取根目录的父级，保留 Addon 文件夹名

    only_bp_rp=True（默认）时，只打包行为包 / 资源包：
      Addon 根目录下的其他内容（皮肤包、世界模板、文档、脚本工具等）一律不进压缩包。
    """
    root = Path(root)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    included, excluded = select_packs(packs, only_bp_rp=only_bp_rp, root=root)
    if not included:
        if excluded:
            names = "、".join(p.root.name for p in excluded)
            raise ManifestError(
                "过滤后没有可打包的包。\n"
                f"检测到的包均不是行为包 / 资源包：{names}\n"
                "如需打包它们，请取消勾选「只打包 BP / RP」。"
            )
        raise ManifestError("没有可打包的包。")

    pack_name = safe_filename(base_name or root.name, "addon")
    resolved_mode = resolve_mode(mode, root, included)
    selected_roots = [p.root for p in included]

    # (压缩包基准目录, 要打包的包目录, 输出路径)
    jobs: List[Tuple[Path, List[Path], Path]] = []

    if resolved_mode == "mcpack":
        if _is_root_itself(root, included):
            jobs.append((root, [root], out_dir / f"{pack_name}_v{version_tag(included)}.mcpack"))
        else:
            for pack in included:
                name = safe_filename(pack.root.name, "pack")
                jobs.append((pack.root, [pack.root],
                             out_dir / f"{name}_v{version_tag([pack])}.mcpack"))
    elif resolved_mode == "zip":
        jobs.append((root.parent, selected_roots,
                     out_dir / f"{pack_name}_v{version_tag(included)}.zip"))
    else:  # mcaddon
        jobs.append((root, selected_roots,
                     out_dir / f"{pack_name}_v{version_tag(included)}.mcaddon"))

    skip_paths = [out_dir] + [job[2] for job in jobs]
    result = PackageResult(
        included=[p.root.name for p in included],
        excluded=[p.root.name for p in excluded],
    )

    for base, pack_roots, out_path in jobs:
        entries = collect_entries(pack_roots, base, skip_paths)
        if not entries:
            raise ManifestError(f"没有可打包的文件：{out_path.name}")
        count = _write_zip(entries, out_path)
        result.archives.append(out_path)
        result.file_count += count

    notes = ["已忽略 备份文件 / 缓存目录 / 隐藏目录"]
    if excluded:
        notes.append("已排除非 BP/RP 类型：" + "、".join(p.root.name for p in excluded))
    result.skipped_note = "；".join(notes)
    return result


def default_output_dir(root: Path) -> Path:
    return Path(root).parent / f"{Path(root).name}_dist"
