# -*- coding: utf-8 -*-
"""
addon_config.py —— 配置持久化

记录内容：
  * 最近打开的 Addon 文件夹（去重、按最近使用排序）
  * 上次打开的项目（供「启动时自动打开」使用）
  * 各项目的输出目录（换回项目时自动还原）
  * 界面选项偏好（版本递增位、输出格式、各类开关）

存放位置（按优先级）：
  1. 环境变量 MC_ZIP_CONFIG 指定的路径（便于测试 / 多套配置）
  2. 程序（exe）同目录下的 config.json —— 便携模式，换机器拷走即可
  3. %APPDATA%\\MC-ZIP\\config.json —— 程序目录不可写时的兜底

只依赖标准库；写入采用「先写 .tmp 再替换」的原子方式。
"""

from __future__ import annotations

import json
import os
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

APP_DIR_NAME = "MC-ZIP"
CONFIG_NAME = "config.json"
ENV_OVERRIDE = "MC_ZIP_CONFIG"

CONFIG_VERSION = 1
MAX_RECENT = 10
MAX_OUTPUTS = 30

# 界面选项默认值（键名与 config.json 一致）
DEFAULT_OPTIONS: "OrderedDict[str, Any]" = OrderedDict([
    ("version_part", "patch"),
    ("pack_mode", "auto"),
    ("uuid_style", "hyphen"),
    ("pack_with_bump", True),
    ("bump_modules", False),
    ("uuid_modules", True),
    ("backup", True),
    ("only_bp_rp", True),
    ("auto_load_last", True),
])


def app_dir() -> Path:
    """程序所在目录（源码运行 / PyInstaller 打包后均适用）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _candidate_paths() -> List[Path]:
    candidates: List[Path] = []
    override = os.environ.get(ENV_OVERRIDE)
    if override:
        candidates.append(Path(override))
        return candidates
    candidates.append(app_dir() / CONFIG_NAME)
    base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
    if base:
        candidates.append(Path(base) / APP_DIR_NAME / CONFIG_NAME)
    else:
        candidates.append(Path.home() / ("." + APP_DIR_NAME) / CONFIG_NAME)
    return candidates


def normalize_key(folder: Any) -> str:
    """把路径规整成用于去重 / 查表的键（Windows 下忽略大小写与分隔符差异）。"""
    try:
        return os.path.normcase(os.path.normpath(str(folder)))
    except (TypeError, ValueError):
        return str(folder)


class Config:
    """配置对象。构造时自动选择可写路径并读入现有配置。"""

    def __init__(self, path: Optional[Path] = None, autoload: bool = True) -> None:
        self.path: Path = Path(path) if path else self._resolve_path()
        self.data: Dict[str, Any] = self._empty()
        self.last_error: str = ""
        if autoload:
            self.load()

    # ------------------------------------------------------------------ #
    # 路径与读写
    # ------------------------------------------------------------------ #
    @staticmethod
    def _resolve_path() -> Path:
        candidates = _candidate_paths()
        for candidate in candidates:
            if candidate.exists():
                return candidate
        # 都不存在：挑第一个能成功写入的
        for candidate in candidates:
            try:
                candidate.parent.mkdir(parents=True, exist_ok=True)
                Config._write_json(candidate, Config._empty())
                return candidate
            except OSError:
                continue
        return candidates[-1]

    @staticmethod
    def _empty() -> Dict[str, Any]:
        return {
            "version": CONFIG_VERSION,
            "recent_folders": [],
            "last_folder": "",
            "output_dirs": {},
            "options": OrderedDict(DEFAULT_OPTIONS),
        }

    @staticmethod
    def _write_json(path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    def load(self) -> None:
        """读取配置；文件损坏时回退到默认值并记下原因。"""
        self.data = self._empty()
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return
        except OSError as exc:
            self.last_error = f"读取配置失败：{exc}"
            return
        try:
            if raw.startswith(b"\xef\xbb\xbf"):
                raw = raw[3:]
            loaded = json.loads(raw.decode("utf-8"), object_pairs_hook=OrderedDict)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.last_error = f"配置文件损坏，已重置：{exc}"
            return
        if not isinstance(loaded, dict):
            self.last_error = "配置文件格式异常，已重置"
            return

        recents = loaded.get("recent_folders")
        if isinstance(recents, list):
            self.data["recent_folders"] = [str(x) for x in recents if str(x).strip()][:MAX_RECENT]

        outputs = loaded.get("output_dirs")
        if isinstance(outputs, dict):
            self.data["output_dirs"] = {str(k): str(v) for k, v in outputs.items()}

        last = loaded.get("last_folder")
        if isinstance(last, str):
            self.data["last_folder"] = last

        options = loaded.get("options")
        merged = OrderedDict(DEFAULT_OPTIONS)
        if isinstance(options, dict):
            for key in DEFAULT_OPTIONS:
                if key in options:
                    merged[key] = options[key]
        self.data["options"] = merged

    def save(self) -> bool:
        """写盘。成功返回 True。"""
        try:
            self._write_json(self.path, self.data)
            self.last_error = ""
            return True
        except OSError as exc:
            self.last_error = f"保存配置失败：{exc}"
            return False

    # ------------------------------------------------------------------ #
    # 最近打开的项目
    # ------------------------------------------------------------------ #
    @property
    def recent_folders(self) -> List[str]:
        return list(self.data.get("recent_folders") or [])

    @property
    def last_folder(self) -> str:
        return str(self.data.get("last_folder") or "")

    def remember_folder(self, folder: Any) -> None:
        """把文件夹置顶到最近列表，并记为「上次打开」。"""
        text = str(folder).strip()
        if not text:
            return
        key = normalize_key(text)
        recents = [p for p in self.recent_folders if normalize_key(p) != key]
        recents.insert(0, text)
        self.data["recent_folders"] = recents[:MAX_RECENT]
        self.data["last_folder"] = text

    def forget_folder(self, folder: Any) -> None:
        key = normalize_key(folder)
        self.data["recent_folders"] = [
            p for p in self.recent_folders if normalize_key(p) != key
        ]
        if normalize_key(self.data.get("last_folder") or "") == key:
            recents = self.recent_folders
            self.data["last_folder"] = recents[0] if recents else ""

    def clear_recents(self) -> None:
        self.data["recent_folders"] = []
        self.data["last_folder"] = ""

    def existing_recents(self) -> List[str]:
        """按最近顺序返回仍然存在的文件夹。"""
        return [p for p in self.recent_folders if Path(p).is_dir()]

    # ------------------------------------------------------------------ #
    # 每个项目的输出目录
    # ------------------------------------------------------------------ #
    def output_dir_for(self, folder: Any) -> Optional[str]:
        return (self.data.get("output_dirs") or {}).get(normalize_key(folder))

    def set_output_dir(self, folder: Any, out_dir: Any) -> None:
        key = normalize_key(folder)
        outputs = self.data.setdefault("output_dirs", {})
        outputs[key] = str(out_dir)

        # 只保留最近使用的若干个，避免无限增长
        if len(outputs) > MAX_OUTPUTS:
            keep = OrderedDict()
            for candidate in self.recent_folders:
                ck = normalize_key(candidate)
                if ck in outputs:
                    keep[ck] = outputs[ck]
            if key in outputs:
                keep[key] = outputs[key]
            self.data["output_dirs"] = keep

    # ------------------------------------------------------------------ #
    # 选项偏好
    # ------------------------------------------------------------------ #
    @property
    def options(self) -> Dict[str, Any]:
        return dict(self.data.get("options") or DEFAULT_OPTIONS)

    def get_option(self, key: str, default: Any = None) -> Any:
        options = self.data.get("options") or {}
        return options.get(key, DEFAULT_OPTIONS.get(key, default))

    def set_options(self, mapping: Dict[str, Any]) -> None:
        options = self.data.setdefault("options", OrderedDict(DEFAULT_OPTIONS))
        for key in DEFAULT_OPTIONS:
            if key in mapping:
                options[key] = mapping[key]
