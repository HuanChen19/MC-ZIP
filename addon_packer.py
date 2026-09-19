# -*- coding: utf-8 -*-
"""
addon_packer.py —— MC-ZIP（Windows 桌面版）

功能：
  1. 导入 Minecraft 基岩版 Addon 文件夹（支持拖拽，需可选依赖 tkinterdnd2）
  2. 打包：一键打包为 .mcaddon / .mcpack / .zip，并自动递增 manifest.json 版本号
  3. UUID：一键随机刷新 manifest.json 中的 uuid 字段

依赖：仅 Python 标准库（tkinter）。Python 3.8+ 均可运行。
运行：python addon_packer.py
"""

from __future__ import annotations

import os
import queue
import shutil
import sys
import threading
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parent))
import addon_config  # noqa: E402
import addon_core as core  # noqa: E402

APP_TITLE = "MC-ZIP"
APP_SUBTITLE = "Minecraft 基岩版 Addon 打包工具 · 一键打包 / 版本号自增 / UUID 随机刷新"
APP_AUTHOR = "幻尘"
APP_VERSION = "1.0.0"

# --------------------------------------------------------------------------- #
# 配色（浅色主题）
# --------------------------------------------------------------------------- #
BG = "#eef1f6"
CARD = "#ffffff"
BORDER = "#d5dae2"
TEXT = "#1f2328"
MUTED = "#5d6672"
ACCENT = "#2f6feb"
ACCENT_DARK = "#1f57c9"
OK = "#1a7f37"
WARN = "#b45309"
ERR = "#c0392b"
TABLE_HEAD = "#f2f4f8"

# 可选：拖拽支持
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore
    HAS_DND = True
except Exception:  # pragma: no cover
    DND_FILES = None
    TkinterDnD = None
    HAS_DND = False


def pick_font(root: tk.Misc, candidates, size=10, weight="normal"):
    """在系统已安装字体中挑选第一个可用的。"""
    try:
        from tkinter import font as tkfont
        families = set(tkfont.families(root))
    except Exception:
        families = set()
    for name in candidates:
        if name in families:
            return (name, size, weight)
    return ("TkDefaultFont", size, weight)


def resource_path(relative: str) -> Path:
    """兼容源码运行与 PyInstaller 打包后的资源定位。"""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        candidate = base / relative
        if candidate.exists():
            return candidate
        return Path(sys.executable).parent / relative
    return Path(__file__).resolve().parent / relative


class AddonPackerApp:
    def __init__(self, scale: float = 1.0) -> None:
        self.scale = scale if scale and scale > 0 else 1.0
        self.root = TkinterDnD.Tk() if HAS_DND else tk.Tk()  # type: ignore[attr-defined]
        self.root.title(f"{APP_TITLE} v{APP_VERSION}")

        # DPI 自适应：让字号随系统缩放一起变大
        if abs(self.scale - 1.0) > 0.01:
            try:
                self.root.tk.call("tk", "scaling", 96.0 / 72.0 * self.scale)
            except tk.TclError:
                pass

        width, height = int(1060 * self.scale), int(880 * self.scale)
        self.root.configure(bg=BG)
        screen_w, screen_h = width * 2, height * 2
        try:
            self.root.update_idletasks()
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            # 避免在小屏幕上超出可用区域
            width = min(width, int(screen_w * 0.94))
            height = min(height, int(screen_h * 0.90))
            pos_x = max(0, (screen_w - width) // 2)
            pos_y = max(0, (screen_h - height) // 3)
            self.root.geometry(f"{width}x{height}+{pos_x}+{pos_y}")
        except tk.TclError:
            self.root.geometry(f"{width}x{height}")
        self.root.minsize(min(int(900 * self.scale), int(screen_w * 0.6)),
                          min(int(620 * self.scale), int(screen_h * 0.6)))

        # 窗口图标（打包后从 exe 所在目录读取）
        icon_path = resource_path("app.ico")
        if icon_path.is_file():
            try:
                self.root.iconbitmap(default=str(icon_path))
            except tk.TclError:
                pass

        self.font_ui = pick_font(self.root, ["Microsoft YaHei UI", "Microsoft YaHei",
                                             "PingFang SC", "Segoe UI"], 10)
        self.font_bold = (self.font_ui[0], 10, "bold")
        self.font_title = (self.font_ui[0], 16, "bold")
        self.font_sub = (self.font_ui[0], 9)
        self.font_mono = pick_font(self.root, ["Consolas", "Cascadia Mono", "Courier New"], 9)

        # 运行状态
        self.addon_root: Path | None = None
        self.packs: list = []
        self.output_dir: Path | None = None
        self.busy = False
        self.task_queue: "queue.Queue" = queue.Queue()
        self.log_queue: "queue.Queue" = queue.Queue()

        # 配置：最近打开的项目 + 选项偏好
        self.config = addon_config.Config()
        opts = self.config.options
        self._loading_options = True

        # 绑定变量（初值取自上次保存的偏好）
        self.var_path = tk.StringVar()
        self.var_out = tk.StringVar()
        self.var_status = tk.StringVar(value="就绪 · 请先导入 Addon 文件夹")
        self.var_pack_mode = tk.StringVar(
            value=core.PACK_MODES.get(str(opts.get("pack_mode")), core.PACK_MODES["auto"]))
        self.var_version_part = tk.StringVar(
            value=core.VERSION_PART_LABELS.get(str(opts.get("version_part")),
                                               core.VERSION_PART_LABELS["patch"]))
        self.var_uuid_style = tk.StringVar(
            value="无横线（32 位）" if str(opts.get("uuid_style")) == "hex" else "带横线（标准格式）")
        self.var_pack_with_bump = tk.BooleanVar(value=bool(opts.get("pack_with_bump", True)))
        self.var_bump_modules = tk.BooleanVar(value=bool(opts.get("bump_modules", False)))
        self.var_uuid_modules = tk.BooleanVar(value=bool(opts.get("uuid_modules", True)))
        self.var_backup = tk.BooleanVar(value=bool(opts.get("backup", True)))
        self.var_only_bp_rp = tk.BooleanVar(value=bool(opts.get("only_bp_rp", True)))
        self.var_auto_load = tk.BooleanVar(value=bool(opts.get("auto_load_last", True)))

        self._build_style()
        self._build_ui()
        self._refresh_recent_values()
        self._apply_state()
        self._after_id = None
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._schedule_drain()

        # 选项变更后自动落盘（放在 _build_ui 之后，避免初始化触发写盘）
        self._loading_options = False
        for var in (self.var_pack_mode, self.var_version_part, self.var_uuid_style,
                    self.var_pack_with_bump, self.var_bump_modules, self.var_uuid_modules,
                    self.var_backup, self.var_only_bp_rp, self.var_auto_load):
            var.trace_add("write", self._on_option_changed)

        self._log_startup_info()
        self.root.after(150, self._maybe_auto_load_last)

    # ------------------------------------------------------------------ #
    # 样式
    # ------------------------------------------------------------------ #
    def s(self, value: float) -> int:
        """按系统 DPI 缩放像素值。"""
        return max(1, int(round(value * self.scale)))

    def _build_style(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        pad = self.s(5)
        style.configure(".", background=BG, foreground=TEXT, font=self.font_ui)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG, foreground=TEXT, font=self.font_ui)
        style.configure("Card.TLabel", background=CARD, foreground=TEXT)
        style.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=self.font_sub)
        style.configure("Title.TLabel", background=BG, foreground=TEXT, font=self.font_title)
        style.configure("Sub.TLabel", background=BG, foreground=MUTED, font=self.font_sub)
        style.configure("Field.TLabel", background=CARD, foreground=TEXT, font=self.font_ui)

        style.configure("TCheckbutton", background=CARD, foreground=TEXT, font=self.font_ui,
                        padding=self.s(2))
        style.map("TCheckbutton", background=[("active", CARD)])

        style.configure("TEntry", fieldbackground="#ffffff", bordercolor=BORDER,
                        lightcolor=BORDER, darkcolor=BORDER, padding=pad)
        style.configure("TCombobox", fieldbackground="#ffffff", background="#ffffff",
                        bordercolor=BORDER, padding=self.s(4), arrowsize=self.s(14))
        style.map("TCombobox", fieldbackground=[("readonly", "#ffffff")])

        style.configure("TButton", font=self.font_ui, padding=(self.s(12), self.s(7)),
                        borderwidth=1, background="#ffffff", foreground=TEXT,
                        bordercolor=BORDER, focuscolor=BG, relief="flat")
        style.map("TButton",
                  background=[("active", "#e8ecf3"), ("disabled", "#f1f3f6")],
                  foreground=[("disabled", "#a3aab5")])

        style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                        bordercolor=ACCENT, font=self.font_bold,
                        padding=(self.s(16), self.s(9)))
        style.map("Accent.TButton",
                  background=[("active", ACCENT_DARK), ("disabled", "#aebfe6")],
                  foreground=[("disabled", "#f2f5fb")])

        style.configure("Secondary.TButton", background="#e7ecf5", foreground=ACCENT,
                        bordercolor="#c4d2ef", font=self.font_bold,
                        padding=(self.s(16), self.s(9)))
        style.map("Secondary.TButton",
                  background=[("active", "#d7e1f4"), ("disabled", "#f1f3f6")],
                  foreground=[("disabled", "#a3aab5")])

        style.configure("Treeview", background="#ffffff", fieldbackground="#ffffff",
                        foreground=TEXT, rowheight=self.s(28), font=self.font_ui, borderwidth=0)
        style.configure("Treeview.Heading", background=TABLE_HEAD, foreground=MUTED,
                        font=self.font_bold, relief="flat", padding=(self.s(6), self.s(6)))
        style.map("Treeview.Heading", background=[("active", "#e7ebf2")])
        style.map("Treeview", background=[("selected", "#dbe6ff")], foreground=[("selected", TEXT)])

        style.configure("Vertical.TScrollbar", background="#dde2ea", troughcolor=CARD,
                        bordercolor=CARD, arrowcolor=MUTED)

    # ------------------------------------------------------------------ #
    # 界面
    # ------------------------------------------------------------------ #
    def _make_card(self, parent, title: str):
        """带 1px 边框的卡片，返回 (卡片, 内容区 body)。子控件一律放进 body。"""
        card = tk.Frame(parent, bg=CARD, highlightthickness=1,
                        highlightbackground=BORDER, highlightcolor=BORDER, bd=0)
        bar = tk.Frame(card, bg=CARD)
        bar.pack(fill="x", padx=self.s(14), pady=(self.s(10), self.s(8)))
        tk.Label(bar, text=title, bg=CARD, fg=TEXT, font=self.font_bold).pack(side="left")
        body = tk.Frame(card, bg=CARD)
        body.pack(fill="both", expand=True)
        return card, body

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=(self.s(16), self.s(14), self.s(16), self.s(12)))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)
        outer.rowconfigure(4, weight=1)

        # ---------- 标题 ----------
        head = ttk.Frame(outer)
        head.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        head.columnconfigure(0, weight=1)
        head_left = ttk.Frame(head)
        head_left.grid(row=0, column=0, sticky="w")
        ttk.Label(head_left, text=APP_TITLE, style="Title.TLabel").pack(anchor="w")
        ttk.Label(head_left, text=APP_SUBTITLE, style="Sub.TLabel").pack(anchor="w", pady=(2, 0))
        ttk.Label(head, text=f"作者：{APP_AUTHOR}    v{APP_VERSION}",
                  style="Sub.TLabel").grid(row=0, column=1, sticky="e")

        # ---------- ① 导入 ----------
        import_card, import_body = self._make_card(outer, "① 导入 Addon 文件夹")
        import_card.grid(row=1, column=0, sticky="ew", pady=(0, 10))

        row = tk.Frame(import_body, bg=CARD)
        row.pack(fill="x", padx=14)
        row.columnconfigure(1, weight=1)
        ttk.Label(row, text="文件夹路径", style="Field.TLabel").grid(row=0, column=0, padx=(0, 10))
        # 可编辑下拉框：直接输入路径，或从「最近打开」里挑
        self.cmb_path = ttk.Combobox(row, textvariable=self.var_path, values=[])
        self.cmb_path.grid(row=0, column=1, sticky="ew")
        self.cmb_path.bind("<Return>", lambda _e: self.import_folder(self.var_path.get()))
        self.cmb_path.bind("<<ComboboxSelected>>",
                           lambda _e: self.import_folder(self.var_path.get()))
        ttk.Button(row, text="浏览文件夹…", command=self.browse_folder).grid(row=0, column=2, padx=(8, 0))
        self.btn_recent = ttk.Menubutton(row, text="最近打开 ▾")
        self.btn_recent.grid(row=0, column=3, padx=(6, 0))
        self._build_recent_menu()
        ttk.Button(row, text="重新扫描", command=self.rescan).grid(row=0, column=4, padx=(6, 0))

        hint_row = tk.Frame(import_body, bg=CARD)
        hint_row.pack(fill="x", padx=14, pady=(6, 10))
        hint = "支持选择 Addon 根目录（其下含 BP / RP 子包），也支持选择单个包目录。"
        if HAS_DND:
            hint += " 也可以直接把文件夹拖进来。"
        ttk.Label(hint_row, text=hint, style="Muted.TLabel").pack(side="left")
        ttk.Checkbutton(hint_row, text="启动时自动打开上次项目",
                        variable=self.var_auto_load).pack(side="right")

        # ---------- ② 包列表 ----------
        list_card, list_body = self._make_card(outer, "② 检测到的 manifest.json")
        list_card.grid(row=2, column=0, sticky="nsew", pady=(0, 10))
        list_body.rowconfigure(1, weight=1)
        list_body.columnconfigure(0, weight=1)

        columns = ("name", "pack", "kind", "version", "uuid", "modules", "path")
        self.tree = ttk.Treeview(list_body, columns=columns, show="headings",
                                 selectmode="browse", height=6)
        headings = [
            ("name", "包目录", 158, "w", False),
            ("pack", "打包", 62, "center", False),
            ("kind", "类型", 96, "center", False),
            ("version", "header.version", 130, "center", False),
            ("uuid", "header.uuid", 240, "w", True),
            ("modules", "模块数", 68, "center", False),
            ("path", "相对路径", 152, "w", True),
        ]
        for key, text, width, anchor, stretch in headings:
            self.tree.heading(key, text=text)
            self.tree.column(key, width=self.s(width), minwidth=self.s(50),
                             anchor=anchor, stretch=stretch)
        self.tree.tag_configure("skipped", foreground="#a3aab5")
        self.tree.grid(row=1, column=0, sticky="nsew", padx=(14, 0), pady=(0, 10))
        self.tree.bind("<Double-1>", self._open_selected_pack)

        scrollbar = ttk.Scrollbar(list_body, orient="vertical", command=self.tree.yview)
        scrollbar.grid(row=1, column=1, sticky="ns", padx=(0, 12), pady=(0, 10))
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.lbl_summary = tk.Label(list_body, text="尚未导入", bg=CARD, fg=MUTED,
                                    font=self.font_sub, anchor="w")
        self.lbl_summary.grid(row=2, column=0, columnspan=2, sticky="ew", padx=14, pady=(0, 10))

        # ---------- ③ 操作 ----------
        action_card, action_body = self._make_card(outer, "③ 操作")
        action_card.grid(row=3, column=0, sticky="ew", pady=(0, 10))

        btn_row = tk.Frame(action_body, bg=CARD)
        btn_row.pack(fill="x", padx=14, pady=(0, 10))
        self.btn_pack = ttk.Button(btn_row, text="打包并升级版本", style="Accent.TButton",
                                   command=self.do_package)
        self.btn_pack.pack(side="left")
        self.btn_uuid = ttk.Button(btn_row, text="随机刷新 UUID", style="Secondary.TButton",
                                   command=self.do_refresh_uuid)
        self.btn_uuid.pack(side="left", padx=(10, 0))
        self.btn_restore = ttk.Button(btn_row, text="还原备份", command=self.restore_backup)
        self.btn_restore.pack(side="left", padx=(10, 0))
        self.btn_open = ttk.Button(btn_row, text="打开输出目录", command=self.open_output_dir)
        self.btn_open.pack(side="left", padx=(10, 0))

        opt = tk.Frame(action_body, bg=CARD)
        opt.pack(fill="x", padx=14, pady=(0, 8))
        ttk.Label(opt, text="版本递增位", style="Field.TLabel").grid(row=0, column=0, padx=(0, 6))
        self.cmb_part = ttk.Combobox(opt, textvariable=self.var_version_part, state="readonly",
                                     values=list(core.VERSION_PART_LABELS.values()), width=17)
        self.cmb_part.grid(row=0, column=1, padx=(0, 22))
        ttk.Label(opt, text="输出格式", style="Field.TLabel").grid(row=0, column=2, padx=(0, 6))
        self.cmb_mode = ttk.Combobox(opt, textvariable=self.var_pack_mode, state="readonly",
                                     values=list(core.PACK_MODES.values()), width=19)
        self.cmb_mode.grid(row=0, column=3, padx=(0, 22))
        ttk.Label(opt, text="UUID 格式", style="Field.TLabel").grid(row=0, column=4, padx=(0, 6))
        self.cmb_uuid = ttk.Combobox(opt, textvariable=self.var_uuid_style, state="readonly",
                                     values=["带横线（标准格式）", "无横线（32 位）"], width=17)
        self.cmb_uuid.grid(row=0, column=5, padx=(0, 22))
        self.chk_bp_rp = ttk.Checkbutton(opt, text="只处理 BP / RP（打包 · 版本 · UUID）",
                                         variable=self.var_only_bp_rp,
                                         command=self._on_filter_changed)
        self.chk_bp_rp.grid(row=0, column=6)

        opt2 = tk.Frame(action_body, bg=CARD)
        opt2.pack(fill="x", padx=14, pady=(0, 8))
        ttk.Checkbutton(opt2, text="打包时升级版本号", variable=self.var_pack_with_bump).pack(side="left")
        ttk.Checkbutton(opt2, text="同时升级 modules 内版本号",
                        variable=self.var_bump_modules).pack(side="left", padx=(16, 0))
        ttk.Checkbutton(opt2, text="刷新 modules 内 UUID",
                        variable=self.var_uuid_modules).pack(side="left", padx=(16, 0))
        ttk.Checkbutton(opt2, text="修改前备份 manifest.json",
                        variable=self.var_backup).pack(side="left", padx=(16, 0))

        out_row = tk.Frame(action_body, bg=CARD)
        out_row.pack(fill="x", padx=14, pady=(0, 10))
        out_row.columnconfigure(1, weight=1)
        ttk.Label(out_row, text="输出目录", style="Field.TLabel").grid(row=0, column=0, padx=(0, 10))
        ttk.Entry(out_row, textvariable=self.var_out, state="readonly").grid(row=0, column=1, sticky="ew")
        ttk.Button(out_row, text="更改…", command=self.choose_output_dir).grid(row=0, column=2, padx=(8, 0))

        # ---------- ④ 日志 ----------
        log_card, log_body = self._make_card(outer, "④ 操作日志")
        log_card.grid(row=4, column=0, sticky="nsew")
        log_body.rowconfigure(0, weight=1)
        log_body.columnconfigure(0, weight=1)

        self.log = scrolledtext.ScrolledText(log_body, height=8, wrap="word", relief="flat",
                                             bg="#fbfcfe", fg=TEXT, font=self.font_mono,
                                             insertbackground=TEXT, borderwidth=0,
                                             highlightthickness=0)
        self.log.grid(row=0, column=0, sticky="nsew", padx=14, pady=(0, 12))
        self.log.tag_config("info", foreground=TEXT)
        self.log.tag_config("ok", foreground=OK)
        self.log.tag_config("warn", foreground=WARN)
        self.log.tag_config("err", foreground=ERR)
        self.log.tag_config("muted", foreground=MUTED)
        self.log.tag_config("head", foreground=ACCENT, font=self.font_bold)
        self.log.configure(state="disabled")

        # ---------- 状态栏 ----------
        status = tk.Frame(self.root, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
        status.pack(fill="x", side="bottom")
        tk.Label(status, textvariable=self.var_status, bg=CARD, fg=MUTED,
                 font=self.font_sub, anchor="w", padx=14, pady=6).pack(fill="x")

        self._setup_dnd()
        self.log_line(f"{APP_TITLE} v{APP_VERSION}  ·  作者：{APP_AUTHOR}", "head")
        self.log_line("Minecraft 基岩版 Addon 打包工具：一键打包 / 版本号自增 / UUID 随机刷新", "muted")
        self.log_line("流程：① 导入文件夹  →  ② 检查包列表  →  ③ 打包 / 刷新 UUID。", "muted")

    # ------------------------------------------------------------------ #
    # 配置：最近打开的项目 / 选项偏好
    # ------------------------------------------------------------------ #
    def _log_startup_info(self) -> None:
        recents = self.config.existing_recents()
        if recents:
            self.log_line(f"已记住 {len(recents)} 个最近打开的项目，可在「最近打开」里选择；"
                          f"最近一个：{recents[0]}", "muted")
        self.log_line("", "info")
        if self.config.last_error:
            self.log_line(self.config.last_error, "warn")

    def _refresh_recent_values(self) -> None:
        """刷新下拉框里的候选项。"""
        values = self.config.existing_recents()
        try:
            self.cmb_path.configure(values=values)
        except tk.TclError:
            pass

    def _build_recent_menu(self) -> None:
        menu = tk.Menu(self.btn_recent, tearoff=0, font=self.font_ui,
                       bg=CARD, fg=TEXT, activebackground="#dbe6ff",
                       activeforeground=TEXT, borderwidth=1)
        self.btn_recent.configure(menu=menu)
        self._recent_menu = menu
        menu.configure(postcommand=self._populate_recent_menu)

    def _populate_recent_menu(self) -> None:
        menu = self._recent_menu
        menu.delete(0, "end")
        recents = self.config.recent_folders
        if not recents:
            menu.add_command(label="（还没有记录）", state="disabled")
            return
        for path in recents:
            text = path if len(path) <= 68 else "…" + path[-66:]
            exists = Path(path).is_dir()
            label = text if exists else f"{text}   [已不存在]"
            menu.add_command(
                label=label,
                state="normal" if exists else "disabled",
                command=(lambda p=path: self.import_folder(p)),
            )
        menu.add_separator()
        menu.add_command(label="打开当前项目目录", command=self.open_addon_dir,
                         state="normal" if self.addon_root else "disabled")
        menu.add_command(label="打开输出目录", command=self.open_output_dir,
                         state="normal" if (self.output_dir and Path(self.output_dir).is_dir())
                         else "disabled")
        menu.add_separator()
        menu.add_command(label="清除最近记录", command=self.clear_recents)

    def clear_recents(self) -> None:
        if not self.config.recent_folders:
            messagebox.showinfo(APP_TITLE, "还没有最近打开的记录。")
            return
        if not messagebox.askyesno(APP_TITLE, "确定要清除最近打开的项目记录吗？\n"
                                              "（不会删除任何文件夹，只是清掉这份列表）"):
            return
        self.config.clear_recents()
        self.config.save()
        self.var_path.set("")
        self._refresh_recent_values()
        self.log_line("已清除最近打开记录。", "warn")
        self.set_status("已清除最近打开记录")

    def _on_option_changed(self, *_args) -> None:
        """任一选项变化就写入配置。"""
        if self._loading_options:
            return
        self._persist_options()

    def _persist_options(self) -> None:
        self.config.set_options({
            "version_part": self._current_part(),
            "pack_mode": self._current_mode(),
            "uuid_style": "hex" if "无横线" in self.var_uuid_style.get() else "hyphen",
            "pack_with_bump": bool(self.var_pack_with_bump.get()),
            "bump_modules": bool(self.var_bump_modules.get()),
            "uuid_modules": bool(self.var_uuid_modules.get()),
            "backup": bool(self.var_backup.get()),
            "only_bp_rp": bool(self.var_only_bp_rp.get()),
            "auto_load_last": bool(self.var_auto_load.get()),
        })
        self.config.save()

    def _maybe_auto_load_last(self) -> None:
        """启动时自动打开上次的项目（可在界面上关闭）。"""
        if not bool(self.var_auto_load.get()):
            return
        last = self.config.last_folder
        if not last:
            return
        if not Path(last).is_dir():
            self.log_line(f"上次的项目已不存在，跳过自动打开：{last}", "warn")
            self.config.forget_folder(last)
            self.config.save()
            self._refresh_recent_values()
            return
        self.log_line(f"自动打开上次的项目：{last}", "head")
        self.import_folder(last)

    def _setup_dnd(self) -> None:
        if not HAS_DND:
            return
        for widget in (self.cmb_path, self.root):
            try:
                widget.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
                widget.dnd_bind("<<Drop>>", self._on_drop)  # type: ignore[attr-defined]
            except Exception:
                continue

    def _on_drop(self, event) -> None:
        try:
            raw = list(self.root.tk.splitlist(event.data))
        except Exception:
            raw = [str(event.data)]
        if not raw:
            return
        path = raw[0]
        if os.path.isfile(path):
            path = os.path.dirname(path)
        self.import_folder(path)

    def _open_selected_pack(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        try:
            index = int(selection[0]) - 1
            pack = self.packs[index]
        except (ValueError, IndexError):
            return
        try:
            os.startfile(str(pack.root))  # noqa: S606
        except Exception as exc:
            messagebox.showwarning(APP_TITLE, f"无法打开目录：{exc}")

    # ------------------------------------------------------------------ #
    # 日志 / 状态（线程安全：日志先进队列，由主线程消费）
    # ------------------------------------------------------------------ #
    def log_line(self, message: str = "", tag: str = "info") -> None:
        self.log_queue.put((message, tag))

    def _flush_logs(self) -> None:
        lines = []
        while True:
            try:
                lines.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        if not lines:
            return
        self.log.configure(state="normal")
        for message, tag in lines:
            self.log.insert("end", (message + "\n") if message else "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")

    def set_status(self, text: str) -> None:
        self.var_status.set(text)

    def _apply_state(self) -> None:
        enabled = bool(self.packs) and not self.busy
        for btn in (self.btn_pack, self.btn_uuid, self.btn_restore):
            btn.configure(state="normal" if enabled else "disabled")
        has_out = bool(self.output_dir) and Path(self.output_dir).is_dir()
        self.btn_open.configure(state="normal" if (has_out and not self.busy) else "disabled")

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self.busy = busy
        if busy and text:
            self.set_status(text)
        self._apply_state()
        self.root.update_idletasks()

    # ------------------------------------------------------------------ #
    # 导入 / 扫描
    # ------------------------------------------------------------------ #
    def browse_folder(self) -> None:
        initial = self.addon_root.parent if self.addon_root else Path.home()
        chosen = filedialog.askdirectory(title="选择 Minecraft 基岩版 Addon 文件夹",
                                         initialdir=str(initial), mustexist=True)
        if chosen:
            self.import_folder(chosen)

    def import_folder(self, folder: str) -> None:
        folder = (folder or "").strip().strip('"')
        if not folder:
            messagebox.showinfo(APP_TITLE, "请先选择 Addon 文件夹。")
            return
        path = Path(folder)
        if not path.is_dir():
            messagebox.showerror(APP_TITLE, f"文件夹不存在：\n{path}")
            return
        self.addon_root = path
        self.var_path.set(str(path))

        # 还原该项目上次用的输出目录，没有就用默认位置
        remembered = self.config.output_dir_for(path)
        if remembered:
            self.output_dir = Path(remembered)
        elif not self.output_dir or self.output_dir.parent != path.parent:
            self.output_dir = core.default_output_dir(path)
        self.var_out.set(str(self.output_dir))

        # 记入「最近打开」
        self.config.remember_folder(path)
        self.config.save()
        self._refresh_recent_values()

        self.scan(announce=True)

    def rescan(self) -> None:
        if not self.addon_root:
            messagebox.showinfo(APP_TITLE, "请先导入 Addon 文件夹。")
            return
        self.scan(announce=True)

    def scan(self, announce: bool = False) -> None:
        if not self.addon_root:
            return
        try:
            packs = core.load_addon(self.addon_root)
        except core.ManifestError as exc:
            self.packs = []
            self._render_table()
            if announce:
                self.log_line(f"扫描失败：{exc}", "err")
                messagebox.showerror(APP_TITLE, str(exc))
            self.set_status("未找到可处理的 manifest.json")
            self._apply_state()
            return

        self.packs = packs
        self._render_table()
        if announce:
            self.log_line("", "info")
            self.log_line(f"已导入：{self.addon_root}", "head")
            self.log_line(f"检测到 {len(packs)} 个包：")
            for pack in packs:
                self.log_line(f"  · {pack.root.name}  [{pack.kind}]  v{pack.display_version}"
                              f"  uuid={pack.display_uuid}")
                for warning in pack.warnings:
                    self.log_line(f"      警告：{warning}", "warn")
            self.log_line("输出目录：" + str(self.output_dir), "muted")
        self.set_status(f"已加载 {len(packs)} 个包 · 可执行打包或随机刷新 UUID")
        self._apply_state()

    def _on_filter_changed(self) -> None:
        """切换「只打包 BP / RP」后重绘列表。"""
        self._render_table()
        if self.packs:
            included, excluded = core.select_packs(
                self.packs, only_bp_rp=bool(self.var_only_bp_rp.get()), root=self.addon_root
            )
            if excluded:
                names = "、".join(p.root.name for p in excluded)
                self.log_line(f"已排除 {len(excluded)} 个非 BP/RP 包：{names}", "warn")
            else:
                self.log_line("当前所有包都是 BP / RP，无需排除。", "muted")

    def _render_table(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        if not self.packs or not self.addon_root:
            self.lbl_summary.configure(text="尚未导入")
            return

        only_bp_rp = bool(self.var_only_bp_rp.get())
        included, excluded = core.select_packs(self.packs, only_bp_rp=only_bp_rp,
                                               root=self.addon_root)
        included_ids = {id(p) for p in included}

        try:
            root_resolved = self.addon_root.resolve()
        except OSError:
            root_resolved = self.addon_root

        for index, pack in enumerate(self.packs, start=1):
            try:
                rel = pack.root.resolve().relative_to(root_resolved)
                rel_text = "." if str(rel) == "." else str(rel)
            except (ValueError, OSError):
                rel_text = pack.root.name
            packed = id(pack) in included_ids
            self.tree.insert("", "end", iid=str(index), tags=() if packed else ("skipped",), values=(
                pack.root.name,
                "打包" if packed else "跳过",
                pack.kind,
                pack.display_version,
                pack.display_uuid,
                len(pack.module_uuids),
                rel_text,
            ))

        total_modules = sum(len(p.module_uuids) for p in included)
        total_uuid = sum(1 for p in included if p.pack_uuid)
        text = (f"共 {len(self.packs)} 个包 · 本次打包 {len(included)} 个"
                f"（{total_modules} 个模块 · {total_uuid} 个 header.uuid）")
        if excluded:
            text += f" · 已忽略 {len(excluded)} 个非 BP/RP 包"
        text += " · 双击某行可在资源管理器中打开"
        self.lbl_summary.configure(text=text)

    def choose_output_dir(self) -> None:
        initial = self.output_dir if self.output_dir else Path.home()
        chosen = filedialog.askdirectory(title="选择打包输出目录", initialdir=str(initial))
        if chosen:
            self.output_dir = Path(chosen)
            self.var_out.set(str(self.output_dir))
            if self.addon_root:
                self.config.set_output_dir(self.addon_root, self.output_dir)
                self.config.save()
            self.log_line("输出目录已更改为：" + str(self.output_dir)
                          + ("（将记住，下次打开该项目自动还原）" if self.addon_root else ""), "muted")
            self._apply_state()

    def open_output_dir(self) -> None:
        if not self.output_dir or not Path(self.output_dir).is_dir():
            messagebox.showinfo(APP_TITLE, "输出目录还不存在，请先执行一次打包。")
            return
        try:
            os.startfile(str(self.output_dir))  # noqa: S606
        except Exception as exc:
            messagebox.showwarning(APP_TITLE, f"无法打开目录：{exc}")

    def open_addon_dir(self) -> None:
        if not self.addon_root or not self.addon_root.is_dir():
            messagebox.showinfo(APP_TITLE, "还没有导入任何项目。")
            return
        try:
            os.startfile(str(self.addon_root))  # noqa: S606
        except Exception as exc:
            messagebox.showwarning(APP_TITLE, f"无法打开目录：{exc}")

    def restore_backup(self) -> None:
        """把 manifest.json.bak 还原回 manifest.json。"""
        if not self.addon_root:
            return
        candidates = []
        for pack in self.packs:
            backup = pack.manifest_path.with_name(core.BACKUP_NAME)
            if backup.is_file():
                candidates.append((pack, backup))
        if not candidates:
            messagebox.showinfo(APP_TITLE, "没有找到 manifest.json.bak 备份文件。")
            return
        listing = "\n".join(f"  · {pack.manifest_path}" for pack, _ in candidates)
        if not messagebox.askyesno(
            APP_TITLE,
            f"将用备份覆盖以下 {len(candidates)} 个 manifest.json：\n\n{listing}\n\n"
            "覆盖后当前未备份的修改会丢失，是否继续？",
        ):
            return
        for pack, backup in candidates:
            try:
                shutil.copy2(backup, pack.manifest_path)
                self.log_line(f"[还原] {pack.manifest_path.name} <- {backup.name}  ({pack.root.name})", "warn")
            except OSError as exc:
                self.log_line(f"[还原失败] {pack.manifest_path}：{exc}", "err")
        self.scan()
        self.set_status("已从备份还原 manifest.json")

    # ------------------------------------------------------------------ #
    # 后台任务调度
    # ------------------------------------------------------------------ #
    def _run_task(self, work, title: str) -> None:
        self._set_busy(True, title + "…")
        self.log_line(f"—— {title} ——", "head")

        def runner():
            try:
                self.task_queue.put(("ok", work()))
            except Exception as exc:  # noqa: BLE001
                self.task_queue.put(("err", (exc, traceback.format_exc())))

        threading.Thread(target=runner, daemon=True).start()

    def _schedule_drain(self) -> None:
        self._after_id = self.root.after(80, self._drain_queue)

    def _on_close(self) -> None:
        try:
            self._persist_options()
            if self.addon_root and self.output_dir:
                self.config.set_output_dir(self.addon_root, self.output_dir)
                self.config.save()
        except Exception:
            pass
        try:
            if self._after_id is not None:
                self.root.after_cancel(self._after_id)
                self._after_id = None
        except tk.TclError:
            pass
        self.root.destroy()

    def _drain_queue(self) -> None:
        try:
            self._flush_logs()
            try:
                while True:
                    kind, payload = self.task_queue.get_nowait()
                    self._set_busy(False)
                    if kind == "ok":
                        payload()
                    else:
                        exc, detail = payload
                        self.log_line(f"操作失败：{exc}", "err")
                        if not isinstance(exc, core.ManifestError):
                            self.log_line(detail, "muted")
                        self.set_status("操作失败")
                        messagebox.showerror(APP_TITLE, f"操作失败：\n{exc}")
                    self._flush_logs()
                    self._apply_state()
            except queue.Empty:
                pass
            self._flush_logs()
        except tk.TclError:
            return  # 窗口已销毁
        self._schedule_drain()

    # ------------------------------------------------------------------ #
    # 选项读取
    # ------------------------------------------------------------------ #
    def _current_part(self) -> str:
        label = self.var_version_part.get()
        for key, text in core.VERSION_PART_LABELS.items():
            if text == label:
                return key
        return "patch"

    def _current_mode(self) -> str:
        label = self.var_pack_mode.get()
        for key, text in core.PACK_MODES.items():
            if text == label:
                return key
        return "auto"

    # ------------------------------------------------------------------ #
    # 功能 1：打包 + 自动升级版本号
    # ------------------------------------------------------------------ #
    def do_package(self) -> None:
        if not self.addon_root:
            messagebox.showinfo(APP_TITLE, "请先导入 Addon 文件夹。")
            return
        root = self.addon_root
        out_dir = Path(self.output_dir) if self.output_dir else core.default_output_dir(root)
        self.output_dir = out_dir
        self.var_out.set(str(out_dir))
        self.config.set_output_dir(root, out_dir)
        self.config.save()

        mode = self._current_mode()
        part = self._current_part()
        do_bump = bool(self.var_pack_with_bump.get())
        bump_modules = bool(self.var_bump_modules.get())
        backup = bool(self.var_backup.get())
        only_bp_rp = bool(self.var_only_bp_rp.get())
        title = "打包并升级版本号" if do_bump else "打包（不修改版本号）"

        def work():
            packs = core.load_addon(root)
            included, excluded = core.select_packs(packs, only_bp_rp=only_bp_rp, root=root)
            old_versions = {str(p.root): p.display_version for p in included}

            if only_bp_rp:
                self.log_line(f"打包范围：仅 BP / RP（{len(included)} 个包）")
            else:
                self.log_line(f"打包范围：全部检测到的包（{len(included)} 个）")
            if excluded:
                self.log_line("  已排除：" + "、".join(
                    f"{p.root.name}（{p.kind}）" for p in excluded), "warn")

            if do_bump:
                self.log_line(f"版本递增位：{core.VERSION_PART_LABELS.get(part, part)}"
                              f"{'（含 modules）' if bump_modules else '（仅 header）'}")
                for pack in included:
                    report = core.bump_versions(pack, part=part, include_modules=bump_modules,
                                                backup=backup)
                    for line in report.messages:
                        self.log_line("  " + line, "ok")
                    if report.backup:
                        self.log_line(f"  [{pack.rel_name}] 已备份 -> {report.backup.name}", "muted")
            else:
                self.log_line("  已跳过版本号修改（本次打包不写入 manifest.json）", "muted")

            result = core.build_package(root, packs, out_dir, mode=mode,
                                        only_bp_rp=only_bp_rp)

            def finish():
                self.packs = core.load_addon(root)
                self._render_table()
                for archive in result.archives:
                    size_kb = archive.stat().st_size / 1024
                    self.log_line(f"  已生成：{archive.name}  ({size_kb:.1f} KB)  ->  {archive.parent}", "ok")
                self.log_line(f"  共压缩 {result.file_count} 个文件", "muted")
                if result.skipped_note:
                    self.log_line("  " + result.skipped_note, "muted")
                for pack in self.packs:
                    old = old_versions.get(str(pack.root))
                    if old and old != pack.display_version:
                        self.log_line(f"  {pack.root.name} 版本：{old} -> {pack.display_version}", "ok")
                self.log_line("打包完成。", "ok")
                self.set_status(f"打包完成 · 输出 {len(result.archives)} 个文件到 {out_dir}")

            return finish

        self._run_task(work, title)

    # ------------------------------------------------------------------ #
    # 功能 2：随机刷新 UUID
    # ------------------------------------------------------------------ #
    def do_refresh_uuid(self) -> None:
        if not self.addon_root:
            messagebox.showinfo(APP_TITLE, "请先导入 Addon 文件夹。")
            return
        root = self.addon_root
        include_modules = bool(self.var_uuid_modules.get())
        backup = bool(self.var_backup.get())
        style = "hex" if "无横线" in self.var_uuid_style.get() else "hyphen"
        only_bp_rp = bool(self.var_only_bp_rp.get())

        def work():
            packs = core.load_addon(root)
            included, excluded = core.select_packs(packs, only_bp_rp=only_bp_rp, root=root)
            self.log_line(f"处理范围：{'仅 BP / RP' if only_bp_rp else '全部检测到的包'}"
                          f"（{len(included)} 个包）")
            if excluded:
                self.log_line("  已跳过：" + "、".join(
                    f"{p.root.name}（{p.kind}）" for p in excluded), "warn")

            for pack in included:
                report = core.refresh_uuids(pack, include_header=True,
                                            include_modules=include_modules,
                                            style=style, backup=backup)
                for line in report.messages:
                    tag = "muted" if ("已保留" in line or "跳过" in line) else "ok"
                    self.log_line("  " + line, tag)
                if report.backup:
                    self.log_line(f"  [{pack.rel_name}] 已备份 -> {report.backup.name}", "muted")

            def finish():
                self.packs = core.load_addon(root)
                self._render_table()
                total = sum(1 + len(p.module_uuids) for p in included)
                self.log_line(f"UUID 刷新完成，共更新 {total} 处；重新导入游戏后生效。", "ok")
                self.set_status(f"UUID 刷新完成 · {len(included)} 个包")

            return finish

        self._run_task(work, "随机刷新 UUID")


def detect_dpi_scale() -> float:
    """获取系统 DPI 缩放系数（1.0 = 100%）。"""
    try:
        from ctypes import windll  # type: ignore
        windll.shcore.SetProcessDpiAwareness(1)  # System DPI aware
        return max(1.0, windll.shcore.GetScaleFactorForDevice(0) / 100.0)
    except Exception:
        try:
            from ctypes import windll  # type: ignore
            return max(1.0, windll.user32.GetDpiForSystem() / 96.0)
        except Exception:
            return 1.0


def main(argv=None) -> int:
    scale = detect_dpi_scale()

    # 支持命令行 / 拖到 exe 图标上传入文件夹路径：直接预加载
    args = list(sys.argv[1:] if argv is None else argv)
    preselect = None
    for arg in args:
        candidate = Path(arg.strip('"'))
        if candidate.exists():
            preselect = candidate if candidate.is_dir() else candidate.parent
            break

    app = AddonPackerApp(scale=scale)
    if preselect is not None:
        app.root.after(120, lambda: app.import_folder(str(preselect)))
    app.root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
