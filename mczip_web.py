# -*- coding: utf-8 -*-
"""
mczip_web.py —— MC-ZIP（Ore UI 网页界面版）

用 pywebview 承载 frontend/ 下的 Ore UI 前端，界面逻辑在 JS，
文件操作与核心逻辑仍由 addon_core / addon_config 完成（与桌面版同一套实现）。

运行：
  源码：  python mczip_web.py [Addon文件夹]
  自测：  python mczip_web.py --selftest   （无窗口跑通 API 全流程）
  联调：  python mczip_web.py --e2e        （起窗口，验证 JS<->Python 桥后自动退出）
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import addon_config  # noqa: E402
import addon_core as core  # noqa: E402

APP_TITLE = "MC-ZIP"
APP_VERSION = "1.0.0"
APP_AUTHOR = "幻尘"


def resource_path(relative: str) -> Path:
    """兼容源码运行与 PyInstaller 打包后的资源定位。"""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidate = base / relative
        if candidate.exists():
            return candidate
        return Path(sys.executable).parent / relative
    return Path(__file__).resolve().parent / relative


def dbg(msg: str) -> None:
    """启动期诊断日志（写入临时目录，便于排查打包后的问题）。"""
    try:
        if os.environ.get("MCZIP_DEBUG") != "1":
            return
        path = Path(os.environ.get("TEMP", ".")) / "mczip-boot.log"
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")
    except Exception:
        pass


def prepare_webview_env() -> None:
    """WebView2 运行参数：Edge 内核 + 本地 HTTP 服务。

    注意：不要加 --disable-gpu。实测在部分环境（WebView2 运行时 + 独显驱动）
    禁用 GPU 会让渲染面整片纯黑，用户看到的就是「打开后一片黑」。
    """
    os.environ.setdefault("PYWEBVIEW_GUI", "edgechromium")
    args = os.environ.get("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "")
    keep = [a for a in args.split() if a != "--disable-gpu"]
    if keep:
        os.environ["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = " ".join(keep)
    else:
        os.environ.pop("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", None)


# --------------------------------------------------------------------------- #
# 桥接 API（暴露给前端 JS：window.pywebview.api.*）
# --------------------------------------------------------------------------- #
class BridgeApi:
    """JS 桥：所有方法返回可 JSON 序列化的 dict。

    不依赖任何 GUI 库；窗口可通过 pywebview 或 Edge --app 模式承载。
    """

    def __init__(self) -> None:
        self.window = None            # 可选：需要原生文件对话框时由窗口层注入
        self.config = addon_config.Config()
        self.addon_root: Path | None = None
        self.output_dir: Path | None = None
        self.packs: list = []
        self.preselect: str | None = None
        self.http_server = None
        self.pending_dialog = None    # 无 window 时的对话框回退标记

    # ------------------------------------------------------------------ #
    # 原生文件对话框（有窗口层时用；否则由前端走浏览器目录选择）
    # ------------------------------------------------------------------ #
    def _ask_directory(self, initial: str) -> str | None:
        """弹出系统「选择文件夹」对话框。优先 pywebview，其次 tkinter。"""
        if self.window is not None:
            try:
                import webview
                result = self.window.create_file_dialog(
                    webview.FOLDER_DIALOG, directory=initial, allow_multiple=False)
                if result:
                    return result[0]
                return None
            except Exception as exc:
                self._dialog_error = str(exc)
        # tkinter 回退（Windows 上不依赖第三方库）
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            chosen = filedialog.askdirectory(title="选择文件夹", initialdir=initial)
            root.destroy()
            return chosen or None
        except Exception as exc:
            self._dialog_error = str(exc)
            return None

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    def _rows_and_summary(self, only_bp_rp: bool):
        included, excluded = core.select_packs(self.packs, only_bp_rp=only_bp_rp,
                                               root=self.addon_root)
        included_ids = {id(p) for p in included}
        try:
            root_resolved = self.addon_root.resolve()
        except OSError:
            root_resolved = self.addon_root
        rows = []
        for pack in self.packs:
            try:
                rel = pack.root.resolve().relative_to(root_resolved)
                rel_text = "." if str(rel) == "." else str(rel)
            except (ValueError, OSError):
                rel_text = pack.root.name
            rows.append({
                "name": pack.root.name,
                "packed": id(pack) in included_ids,
                "kind": pack.kind,
                "version": pack.display_version,
                "uuid": pack.display_uuid,
                "modules": len(pack.module_uuids),
                "rel": rel_text,
            })
        total_modules = sum(len(p.module_uuids) for p in included)
        total_uuid = sum(1 for p in included if p.pack_uuid)
        summary = (f"共 {len(self.packs)} 个包 · 本次打包 {len(included)} 个"
                   f"（{total_modules} 个模块 · {total_uuid} 个 header.uuid）")
        if excluded:
            summary += f" · 已忽略 {len(excluded)} 个非 BP/RP 包"
        summary += " · 双击某行查看 manifest.json"
        return rows, summary

    def _scan_payload(self, announce: bool = True) -> dict:
        if not self.addon_root:
            return {"ok": False, "error": "尚未导入", "logs": [], "packs": [],
                    "status": "就绪 · 请先导入 Addon 文件夹"}
        try:
            self.packs = core.load_addon(self.addon_root)
        except core.ManifestError as exc:
            self.packs = []
            logs = [[f"扫描失败：{exc}", "err"]] if announce else []
            return {"ok": False, "error": str(exc), "logs": logs, "packs": [],
                    "root": str(self.addon_root),
                    "outputDir": str(self.output_dir or ""),
                    "status": "未找到可处理的 manifest.json"}

        logs = []
        if announce:
            logs.append(["", "info"])
            logs.append([f"已导入：{self.addon_root}", "head"])
            logs.append([f"检测到 {len(self.packs)} 个包："])
            for pack in self.packs:
                logs.append([f"  · {pack.root.name}  [{pack.kind}]  v{pack.display_version}"
                             f"  uuid={pack.display_uuid}"])
                for warning in pack.warnings:
                    logs.append([f"      警告：{warning}", "warn"])
            logs.append(["输出目录：" + str(self.output_dir), "muted"])

        only = bool(self.config.options.get("only_bp_rp", True))
        rows, summary = self._rows_and_summary(only)
        return {
            "ok": True, "error": None,
            "root": str(self.addon_root),
            "outputDir": str(self.output_dir or ""),
            "packs": rows, "summary": summary, "logs": logs,
            "status": f"已加载 {len(self.packs)} 个包 · 可执行打包或随机刷新 UUID",
        }

    # ------------------------------------------------------------------ #
    # 启动状态
    # ------------------------------------------------------------------ #
    def get_state(self) -> dict:
        recents = []
        for path in self.config.recent_folders:
            recents.append({"path": path, "exists": Path(path).is_dir()})
        return {
            "options": dict(self.config.options),
            "recents": recents,
            "lastFolder": self.config.last_folder,
            "preselect": self.preselect,
            "appVersion": APP_VERSION,
            "author": APP_AUTHOR,
        }

    def set_options(self, opts: dict) -> dict:
        allowed = {"version_part", "pack_mode", "uuid_style", "pack_with_bump",
                   "bump_modules", "uuid_modules", "backup", "only_bp_rp", "auto_load_last"}
        cleaned = {k: v for k, v in (opts or {}).items() if k in allowed}
        self.config.set_options(cleaned)
        self.config.save()
        return {"ok": True}

    # ------------------------------------------------------------------ #
    # 导入 / 扫描
    # ------------------------------------------------------------------ #
    def browse_folder(self) -> dict:
        initial = str(self.addon_root.parent) if self.addon_root else str(Path.home())
        chosen = self._ask_directory(initial)
        if not chosen:
            return {"cancelled": True}
        return self.import_folder(chosen)

    def import_folder(self, folder: str) -> dict:
        folder = (folder or "").strip().strip('"')
        if not folder:
            return {"ok": False, "error": "请先选择 Addon 文件夹。", "logs": []}
        path = Path(folder)
        if not path.is_dir():
            return {"ok": False, "error": f"文件夹不存在：\n{path}",
                    "logs": [[f"文件夹不存在：{path}", "err"]]}
        self.addon_root = path

        remembered = self.config.output_dir_for(path)
        if remembered:
            self.output_dir = Path(remembered)
        elif not self.output_dir or self.output_dir.parent != path.parent:
            self.output_dir = core.default_output_dir(path)

        self.config.remember_folder(path)
        self.config.save()
        return self._scan_payload(announce=True)

    def rescan(self) -> dict:
        if not self.addon_root:
            return {"ok": False, "error": "请先导入 Addon 文件夹。", "logs": []}
        return self._scan_payload(announce=True)

    def mark_rows(self, only_bp_rp: bool) -> dict:
        rows, summary = self._rows_and_summary(bool(only_bp_rp))
        return {"packs": rows, "summary": summary}

    # ------------------------------------------------------------------ #
    # 输出目录 / 打开目录 / 最近记录
    # ------------------------------------------------------------------ #
    def choose_output_dir(self) -> dict:
        initial = str(self.output_dir) if self.output_dir else str(Path.home())
        chosen = self._ask_directory(initial)
        if not chosen:
            return {"cancelled": True}
        self.output_dir = Path(chosen)
        if self.addon_root:
            self.config.set_output_dir(self.addon_root, self.output_dir)
            self.config.save()
        return {"ok": True, "outputDir": str(self.output_dir),
                "logs": [[f"输出目录已更改为：{self.output_dir}"
                          + ("（将记住，下次打开该项目自动还原）" if self.addon_root else ""), "muted"]]}

    def open_output_dir(self) -> dict:
        if not self.output_dir or not Path(self.output_dir).is_dir():
            return {"ok": False, "msg": "输出目录还不存在，请先执行一次打包。"}
        os.startfile(str(self.output_dir))  # noqa: S606
        return {"ok": True}

    def open_addon_dir(self) -> dict:
        if not self.addon_root or not self.addon_root.is_dir():
            return {"ok": False, "msg": "还没有导入任何项目。"}
        os.startfile(str(self.addon_root))  # noqa: S606
        return {"ok": True}

    def clear_recents(self) -> dict:
        self.config.clear_recents()
        self.config.save()
        return {"ok": True}

    def get_manifest_text(self, index: int) -> dict:
        try:
            pack = self.packs[int(index)]
        except (ValueError, IndexError):
            return {"ok": False, "name": "", "text": "（无效行）"}
        text, _enc, _bom = core.read_text(pack.manifest_path)
        return {"ok": True, "name": f"{pack.root.name} / manifest.json", "text": text}

    # ------------------------------------------------------------------ #
    # 功能 1：打包并升级版本
    # ------------------------------------------------------------------ #
    def do_package(self, opts: dict) -> dict:
        if not self.addon_root:
            return {"ok": False, "logs": [["请先导入 Addon 文件夹。", "err"]]}
        self.set_options(opts)
        root = self.addon_root
        out_dir = Path(self.output_dir) if self.output_dir else core.default_output_dir(root)
        self.output_dir = out_dir
        self.config.set_output_dir(root, out_dir)
        self.config.save()

        mode = str(opts.get("pack_mode", "auto"))
        part = str(opts.get("version_part", "patch"))
        do_bump = bool(opts.get("pack_with_bump", True))
        bump_modules = bool(opts.get("bump_modules", False))
        backup = bool(opts.get("backup", True))
        only_bp_rp = bool(opts.get("only_bp_rp", True))
        title = "打包并升级版本号" if do_bump else "打包（不修改版本号）"

        logs = [[f"—— {title} ——", "head"]]
        try:
            packs = core.load_addon(root)
            included, excluded = core.select_packs(packs, only_bp_rp=only_bp_rp, root=root)
            old_versions = {str(p.root): p.display_version for p in included}

            logs.append([f"打包范围：{'仅 BP / RP' if only_bp_rp else '全部检测到的包'}（{len(included)} 个包）"])
            if excluded:
                logs.append(["  已排除：" + "、".join(f"{p.root.name}（{p.kind}）" for p in excluded), "warn"])

            if do_bump:
                logs.append([f"版本递增位：{core.VERSION_PART_LABELS.get(part, part)}"
                             f"{'（含 modules）' if bump_modules else '（仅 header）'}"])
                for pack in included:
                    report = core.bump_versions(pack, part=part,
                                                include_modules=bump_modules, backup=backup)
                    for line in report.messages:
                        logs.append(["  " + line, "ok"])
                    if report.backup:
                        logs.append([f"  [{pack.rel_name}] 已备份 -> {report.backup.name}", "muted"])
            else:
                logs.append(["  已跳过版本号修改（本次打包不写入 manifest.json）", "muted"])

            result = core.build_package(root, packs, out_dir, mode=mode, only_bp_rp=only_bp_rp)

            archives = []
            for archive in result.archives:
                size_kb = archive.stat().st_size / 1024
                archives.append(archive.name)
                logs.append([f"  已生成：{archive.name}  ({size_kb:.1f} KB)  ->  {archive.parent}", "ok"])
            logs.append([f"  共压缩 {result.file_count} 个文件", "muted"])
            if result.skipped_note:
                logs.append(["  " + result.skipped_note, "muted"])

            self.packs = core.load_addon(root)
            for pack in self.packs:
                old = old_versions.get(str(pack.root))
                if old and old != pack.display_version:
                    logs.append([f"  {pack.root.name} 版本：{old} -> {pack.display_version}", "ok"])
            logs.append(["打包完成。", "ok"])

            rows, summary = self._rows_and_summary(only_bp_rp)
            return {"ok": True, "logs": logs, "packs": rows, "summary": summary,
                    "archives": archives, "outputDir": str(out_dir),
                    "status": f"打包完成 · 输出 {len(result.archives)} 个文件到 {out_dir}"}
        except Exception as exc:  # noqa: BLE001
            logs.append([f"操作失败：{exc}", "err"])
            return {"ok": False, "logs": logs, "status": "操作失败"}

    # ------------------------------------------------------------------ #
    # 功能 2：随机刷新 UUID
    # ------------------------------------------------------------------ #
    def do_refresh_uuid(self, opts: dict) -> dict:
        if not self.addon_root:
            return {"ok": False, "logs": [["请先导入 Addon 文件夹。", "err"]]}
        self.set_options(opts)
        root = self.addon_root
        include_modules = bool(opts.get("uuid_modules", True))
        backup = bool(opts.get("backup", True))
        style = "hex" if str(opts.get("uuid_style")) == "hex" else "hyphen"
        only_bp_rp = bool(opts.get("only_bp_rp", True))

        logs = [["—— 随机刷新 UUID ——", "head"]]
        try:
            packs = core.load_addon(root)
            included, excluded = core.select_packs(packs, only_bp_rp=only_bp_rp, root=root)
            logs.append([f"处理范围：{'仅 BP / RP' if only_bp_rp else '全部检测到的包'}（{len(included)} 个包）"])
            if excluded:
                logs.append(["  已跳过：" + "、".join(f"{p.root.name}（{p.kind}）" for p in excluded), "warn"])

            for pack in included:
                report = core.refresh_uuids(pack, include_header=True,
                                            include_modules=include_modules,
                                            style=style, backup=backup)
                for line in report.messages:
                    tag = "muted" if ("已保留" in line or "跳过" in line) else "ok"
                    logs.append(["  " + line, tag])
                if report.backup:
                    logs.append([f"  [{pack.rel_name}] 已备份 -> {report.backup.name}", "muted"])

            self.packs = core.load_addon(root)
            total = sum(1 + len(p.module_uuids) for p in included)
            logs.append([f"UUID 刷新完成，共更新 {total} 处；重新导入游戏后生效。", "ok"])
            rows, summary = self._rows_and_summary(only_bp_rp)
            return {"ok": True, "logs": logs, "packs": rows, "summary": summary,
                    "status": f"UUID 刷新完成 · {len(included)} 个包"}
        except Exception as exc:  # noqa: BLE001
            logs.append([f"操作失败：{exc}", "err"])
            return {"ok": False, "logs": logs, "status": "操作失败"}

    # ------------------------------------------------------------------ #
    # 功能 3：还原备份
    # ------------------------------------------------------------------ #
    def restore_backup(self) -> dict:
        if not self.addon_root:
            return {"ok": False, "logs": []}
        candidates = []
        for pack in self.packs:
            backup = pack.manifest_path.with_name(core.BACKUP_NAME)
            if backup.is_file():
                candidates.append((pack, backup))
        if not candidates:
            return {"ok": False, "none": True,
                    "logs": [["没有找到 manifest.json.bak 备份文件。", "warn"]]}
        logs = []
        for pack, backup in candidates:
            try:
                shutil.copy2(backup, pack.manifest_path)
                logs.append([f"[还原] {pack.manifest_path.name} <- {backup.name}  ({pack.root.name})", "warn"])
            except OSError as exc:
                logs.append([f"[还原失败] {pack.manifest_path}：{exc}", "err"])
        payload = self._scan_payload(announce=False)
        payload["logs"] = logs + payload.get("logs", [])
        payload["status"] = "已从备份还原 manifest.json"
        payload["ok"] = True
        return payload

    def list_backups(self) -> dict:
        items = []
        for pack in self.packs:
            if pack.manifest_path.with_name(core.BACKUP_NAME).is_file():
                items.append(f"{pack.root.name}/manifest.json")
        return {"items": items}


# --------------------------------------------------------------------------- #
# 本地 HTTP 服务：托管前端静态文件 + 暴露 /api/<method> 调用桥方法
# （同源，零第三方依赖；任何浏览器/WebView 都能用）
# --------------------------------------------------------------------------- #
def make_server(api: "BridgeApi", frontend_dir: Path) -> Tuple["ThreadingHTTPServer", int]:
    import functools
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args):  # 静音访问日志
            pass

        def _json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            if self.path.startswith("/api/"):
                return self._api({})
            return super().do_GET()

        def do_POST(self):  # noqa: N802
            if self.path.startswith("/api/"):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    payload = json.loads(raw.decode("utf-8") or "{}")
                except json.JSONDecodeError:
                    payload = {}
                return self._api(payload)
            self.send_error(405)

        def _api(self, payload: dict) -> None:
            import inspect

            name = self.path[len("/api/"):].split("?")[0].strip("/")
            method = getattr(api, name, None)
            if method is None or name.startswith("_"):
                return self._json({"ok": False, "error": f"未知接口：{name}"}, 404)
            try:
                # 按签名决定传参方式：
                #   单个参数且名字是 opts/options/payload -> 整体作为该参数传
                #   其余情况且 payload 键名与形参名一致 -> 展开传关键字
                named = [p for p in inspect.signature(method).parameters.values()
                         if p.name != "self"]
                if not payload:
                    result = method()
                elif len(named) == 1:
                    pname = named[0].name
                    if pname in payload and isinstance(payload[pname], (dict, list)):
                        result = method(**{pname: payload[pname]})
                    elif pname in ("folder", "name", "path", "text") and len(payload) == 1:
                        result = method(**payload)
                    else:
                        result = method(payload)
                else:
                    result = method(**payload)
                if result is None:
                    result = {"ok": True}
            except TypeError as exc:
                result = {"ok": False, "error": f"参数错误：{exc}"}
            except Exception as exc:  # noqa: BLE001
                result = {"ok": False, "error": str(exc)}
            return self._json(result)

    handler = functools.partial(Handler, directory=str(frontend_dir))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, port


def find_edge() -> str | None:
    """定位本机 Edge（用于 --app 模式承载界面）。"""
    candidates = [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Edge/Application/msedge.exe",
    ]
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def run_edge_app(api: "BridgeApi", url: str, title: str) -> int:
    """用 Edge 应用模式打开界面（无地址栏，像独立窗口）。

    注意：不要用 Edge 子进程是否存活来判断程序是否该退出 ——
    若本机已有 Edge 在运行，`--app=<url>` 会被交给既有进程处理，
    这里 Popen 出来的进程会立刻退出，导致程序"打开即关闭"。因此：
      * 启动后只做一次存活观察，之后一直保持服务运行；
      * 用 --user-data-dir 指定的独立 profile，避免污染用户日常浏览器会话。
    """
    edge = find_edge()
    if not edge:
        print("[提示] 未找到 Edge 浏览器。请把上面的界面地址复制到任意浏览器打开。")
        print("       服务运行中，Ctrl+C 退出。")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return 0

    profile = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "MC-ZIP" / "app-profile"
    profile.mkdir(parents=True, exist_ok=True)

    args = [
        edge,
        f"--app={url}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=msEdgeIdentity,msEdgeSyncPromo",
        "--window-size=1180,940",
    ]
    print(f"[MC-ZIP] 界面已打开：{url}")
    print("[MC-ZIP] 关闭程序的两种方式：在本窗口按 Ctrl+C，或直接关掉任务栏里的 Python 进程。")

    try:
        subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        print(f"[提示] Edge 启动失败（{exc}），请手动打开上面的地址。")

    print("[MC-ZIP] 服务运行中，请勿关闭此窗口（最小化即可）。")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[MC-ZIP] 已退出。")
    return 0


# --------------------------------------------------------------------------- #
# 自测（无窗口）：fixture -> 导入 -> 打包 -> 校验产物
# --------------------------------------------------------------------------- #
def selftest() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent / "dev"))
    import selftest as st  # type: ignore  # dev/selftest.py
    fixture_root = Path(st.build_fixture())

    api = BridgeApi()
    # 自测不走原生对话框：直接指向 fixture
    api._ask_directory = lambda initial: str(fixture_root)  # type: ignore[method-assign]

    payload = api.browse_folder()
    assert payload.get("ok"), payload
    assert len(payload["packs"]) >= 2, payload["packs"]
    print("[selftest] 导入成功:", [p["name"] for p in payload["packs"]])

    opts = dict(api.config.options)
    opts.update({"pack_mode": "auto", "version_part": "patch",
                 "pack_with_bump": True, "backup": True, "only_bp_rp": True})
    result = api.do_package(opts)
    assert result.get("ok"), result["logs"]
    out_dir = Path(result["outputDir"])
    assert result["archives"], "没有产物"
    for name in result["archives"]:
        target = out_dir / name
        assert target.is_file(), f"产物不存在: {target}"
        print(f"[selftest] 产物: {target.name} ({target.stat().st_size} bytes)")

    result2 = api.do_refresh_uuid(opts)
    assert result2.get("ok"), result2["logs"]
    print("[selftest] UUID 刷新成功")

    manifest = fixture_root / "MyAddon_BP" / "manifest.json"
    assert (fixture_root / "MyAddon_BP" / core.BACKUP_NAME).is_file(), "缺少 .bak 备份"
    result3 = api.restore_backup()
    assert result3.get("ok"), result3
    print("[selftest] 还原备份成功")
    assert manifest.is_file()
    print("SELFTEST PASSED")
    return 0


# --------------------------------------------------------------------------- #
# 窗口 / E2E
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    dbg("main() 启动，frozen=%s argv=%r" % (getattr(sys, "frozen", False), sys.argv))
    if "--selftest" in args:
        return selftest()

    api = BridgeApi()
    dbg("BridgeApi 已创建")
    for arg in args:
        if arg.startswith("--"):
            continue
        candidate = Path(arg.strip('"'))
        if candidate.exists():
            api.preselect = str(candidate if candidate.is_dir() else candidate.parent)
            break

    index = resource_path("frontend/index.html")
    dbg("前端入口解析为 %s（存在=%s）" % (index, index.is_file()))
    if not index.is_file():
        dbg("前端入口不存在，退出")
        print(f"[错误] 找不到前端入口：{index}")
        return 1

    # 本地 HTTP 服务同时托管前端静态文件与 /api 接口（同源，规避 file:// 限制）
    http_server, port = make_server(api, index.parent)
    api.http_server = http_server
    url = f"http://127.0.0.1:{port}/index.html"
    dbg("HTTP 服务已就绪：%s" % url)
    print(f"[MC-ZIP] 界面地址：{url}")
    print("[MC-ZIP] 界面使用 Ore UI 前端；文件操作由 Python 核心完成，manifest 修改前自动备份。")

    # --no-window：只起服务（自检 / 调试用），打印地址后阻塞
    if "--no-window" in args:
        print("[MC-ZIP] --no-window 模式：服务已就绪，Ctrl+C 退出。")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return 0

    # 窗口承载策略：
    #   默认：Edge 应用模式（零第三方依赖；渲染、JS、fetch 全部正常）
    #   --pywebview：改用 pywebview 承载（需该环境已装好 pywebview）
    if "--pywebview" in args:
        prepare_webview_env()
        import webview  # noqa: PLC0415

        window = webview.create_window(
            f"{APP_TITLE} v{APP_VERSION}", url, js_api=api,
            width=1120, height=920, min_size=(960, 680))
        api.window = window
        webview.start(debug=False)
        return 0

    if "--e2e" in args:
        print("[e2e] 服务已启动，前端与 API 就绪。")
        return 0

    rc = run_edge_app(api, url, f"{APP_TITLE} v{APP_VERSION}")
    dbg("run_edge_app 返回 %s，准备关停服务" % rc)
    http_server.shutdown()
    return 0 if rc == -1 else rc


if __name__ == "__main__":
    raise SystemExit(main())
