/* ==========================================================================
   app.js —— MC-ZIP 前端主逻辑（双后端）
   桥模式：运行于 pywebview 内（window.pywebview.api）→ 文件操作走 Python
           addon_core / addon_config（与桌面版同一实现，含原生文件对话框）。
   网页模式：普通浏览器 → File System Access API 直读直写；?demo=1 内置演示。
   界面交互：① 导入 → ② 包列表 → ③ 打包 / UUID / 还原 → ④ 日志 + 状态栏。
   ========================================================================== */
"use strict";

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const core = MCZIP_CORE;

const APP_TITLE = "MC-ZIP";
const APP_VERSION = "1.0.0";
const APP_AUTHOR = "幻尘";

/* ------------------------------------------------------------------ *
 * 后端探测
 *   bridge  —— pywebview 注入的 window.pywebview.api（js_api 直调）
 *   http    —— 本地 HTTP 服务（/api/<method>，Edge app 模式 / 浏览器托管）
 *   web     —— 无本地后端，退化为浏览器 File System Access API
 * ------------------------------------------------------------------ */
function waitBridge(timeout = 1500) {
  return new Promise(res => {
    if (window.pywebview && window.pywebview.api) return res(true);
    let done = false;
    const finish = v => { if (!done) { done = true; clearTimeout(t); res(v); } };
    const t = setTimeout(() => finish(!!(window.pywebview && window.pywebview.api)), timeout);
    window.addEventListener("pywebviewready", () => finish(true));
  });
}

const Bridge = {
  backend: "web",            // "bridge" | "http" | "web"
  available: false,          // 是否存在本地后端（bridge 或 http）
  /**
   * 调用后端方法。
   *  - bridge：按位置参数透传
   *  - http：args[0] 若为对象则作 POST body；否则包成 {folder: v} 之类的单参
   *          （后端方法签名统一用关键字参数）
   */
  call(name, ...args) {
    if (this.backend === "bridge") return window.pywebview.api[name](...args);
    const first = args[0];
    const isDict = first && typeof first === "object" && !Array.isArray(first);
    let payload = null;
    if (isDict) payload = first;
    else if (first !== undefined) {
      // 单值参数：按方法名推断关键字名
      const keyByMethod = { import_folder: "folder" };
      payload = { [keyByMethod[name] || "value"]: first };
    }
    if (payload) {
      return fetch(`/api/${name}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }).then(r => r.json()).catch(e => ({ ok: false, error: String(e) }));
    }
    return fetch(`/api/${name}`)
      .then(r => r.json())
      .catch(e => ({ ok: false, error: String(e) }));
  },
};

async function detectBackend() {
  if (await waitBridge()) {
    Bridge.backend = "bridge";
    Bridge.available = true;
    return;
  }
  // 探测本地 HTTP 服务
  try {
    const r = await fetch("/api/get_state", { cache: "no-store" });
    if (r.ok) {
      const data = await r.json();
      if (data && typeof data === "object" && "options" in data) {
        Bridge.backend = "http";
        Bridge.available = true;
        return;
      }
    }
  } catch (e) { /* 无本地服务 */ }
  Bridge.backend = "web";
  Bridge.available = false;
}

/* ------------------------------------------------------------------ *
 * 选项（键与桌面版 config.json 一致；桥模式由 Python 持久化，网页模式 localStorage）
 * ------------------------------------------------------------------ */
const OPTION_DEFAULTS = {
  version_part: "patch", pack_mode: "auto", uuid_style: "hyphen",
  pack_with_bump: true, bump_modules: false, uuid_modules: true,
  backup: true, only_bp_rp: true, auto_load_last: true,
};
let options = (() => {
  try { return { ...OPTION_DEFAULTS, ...JSON.parse(localStorage.getItem("mczip-options") || "{}") }; }
  catch (e) { return { ...OPTION_DEFAULTS }; }
})();
function persistOptions() {
  if (Bridge.available) Bridge.call("set_options", options);
  else localStorage.setItem("mczip-options", JSON.stringify(options));
}

/* ------------------------------------------------------------------ *
 * 运行状态
 * ------------------------------------------------------------------ */
const state = {
  packs: [],        // 统一行格式 [{name, packed, kind, version, uuid, modules, rel}]
  fsPacks: [],      // 网页模式的 PackInfo 对象（manifest 查看用）
  fsRoot: null, fsOut: null,
  busy: false,
};

/* ------------------------------------------------------------------ *
 * 日志 / 状态栏
 * ------------------------------------------------------------------ */
function log(message = "", tag = "info") {
  const box = $("#logContent");
  const line = document.createElement("div");
  line.className = "log-line " + tag;
  line.textContent = message || " ";
  box.appendChild(line);
  const sc = $("#logScroll");
  if (sc && sc._scrollToBottom) sc._scrollToBottom();
}
function printLogs(logs) { (logs || []).forEach(([m, t]) => log(m, t || "info")); }
function setStatus(text) { $("#statusText").textContent = text; }

/* ------------------------------------------------------------------ *
 * 包列表渲染（统一行格式）
 * ------------------------------------------------------------------ */
function renderRows(rows, summary) {
  state.packs = rows || [];
  const tbody = $("#packRows");
  tbody.innerHTML = "";
  if (!state.packs.length) {
    $("#listSummary").textContent = "尚未导入";
    applyState();
    return;
  }
  state.packs.forEach((row, i) => {
    const tr = document.createElement("div");
    tr.className = "pack-row" + (row.packed ? "" : " skipped") + (i === 0 ? " sel" : "");
    tr.dataset.index = i;
    [row.name, row.packed ? "打包" : "跳过", row.kind, row.version, row.uuid, String(row.modules), row.rel]
      .forEach((text, ci) => {
        const td = document.createElement("span");
        td.className = "pack-cell c" + ci;
        td.textContent = text;
        td.title = text;
        tr.appendChild(td);
      });
    tr.addEventListener("click", () => {
      $$(".pack-row").forEach(r => r.classList.remove("sel"));
      tr.classList.add("sel");
    });
    tr.addEventListener("dblclick", () => showManifest(i));
    tbody.appendChild(tr);
  });
  $("#listSummary").textContent = summary || "";
  ORE.layoutScrollers();
  applyState();
}

async function showManifest(index) {
  if (Bridge.available) {
    const r = await Bridge.call("get_manifest_text", index);
    ORE.viewerDialog(r.name || "manifest.json", r.text || "（读取失败）");
  } else {
    const pack = state.fsPacks[index];
    if (pack) ORE.viewerDialog(`${pack.relName} / manifest.json`, JSON.stringify(pack.data, null, pack.indent));
  }
}

/* ------------------------------------------------------------------ *
 * 统一应用一次扫描结果
 * ------------------------------------------------------------------ */
function applyScan(p) {
  if (!p) return;
  if (p.cancelled) return;
  printLogs(p.logs);
  if (p.root) $("#pathDisplay").value = p.root;
  if (p.outputDir !== undefined) $("#outDisplay").value = p.outputDir || outPlaceholder();
  if (p.ok) {
    renderRows(p.packs, p.summary);
  } else {
    renderRows([], "");
    if (p.error) log(p.error, "err");
  }
  if (p.status) setStatus(p.status);
  applyState();
}
function outPlaceholder() {
  return Bridge.available ? "" : "（打包时询问保存位置）";
}

function applyState() {
  const enabled = state.packs.length > 0 && !state.busy;
  $("#packBtn").disabled = !enabled;
  $("#uuidBtn").disabled = !enabled;
  $("#restoreBtn").disabled = !enabled;
  $("#openOutBtn").disabled = state.busy;
}
function setBusy(busy, text = "") {
  state.busy = busy;
  if (busy && text) setStatus(text);
  applyState();
}

/* ------------------------------------------------------------------ *
 * 动作：导入 / 输出目录 / 打包 / UUID / 还原
 * ------------------------------------------------------------------ */
async function onBrowse() {
  if (Bridge.available) { applyScan(await Bridge.call("browse_folder")); return; }
  return webBrowseFolder();
}

async function onRescan() {
  if (Bridge.available) {
    const p = await Bridge.call("rescan");
    if (p && p.error && !p.root) { ORE.toast(p.error); return; }
    applyScan(p);
    return;
  }
  if (!state.fsRoot) { ORE.toast("请先导入 Addon 文件夹"); return; }
  applyScan(await webScan(true));
}

async function onChooseOut() {
  if (Bridge.available) {
    const r = await Bridge.call("choose_output_dir");
    if (r.cancelled) return;
    printLogs(r.logs);
    $("#outDisplay").value = r.outputDir;
    applyState();
    return;
  }
  return webChooseOutputDir();
}

async function onOpenOut() {
  if (Bridge.available) {
    const r = await Bridge.call("open_output_dir");
    if (!r.ok) ORE.toast(r.msg || "无法打开");
    return;
  }
  if (state.fsOut) ORE.toast("浏览器无法直接打开文件夹，产物已写入你选择的目录");
  else ORE.toast("尚未选择输出目录，打包时会询问保存位置");
}

async function onOpenAddonDir() {
  if (Bridge.available) {
    const r = await Bridge.call("open_addon_dir");
    if (!r.ok) ORE.toast(r.msg || "无法打开");
    return;
  }
  ORE.toast("浏览器无法直接打开文件夹，请使用系统文件管理器");
}

async function onPackage() {
  if (!state.packs.length) { ORE.toast("请先导入 Addon 文件夹"); return; }
  const title = options.pack_with_bump ? "打包并升级版本号" : "打包（不修改版本号）";
  setBusy(true, title + "…");
  try {
    const p = Bridge.available
      ? await Bridge.call("do_package", options)
      : await webDoPackage();
    printLogs(p.logs);
    if (p.packs) renderRows(p.packs, p.summary);
    if (p.outputDir) $("#outDisplay").value = p.outputDir;
    if (p.status) setStatus(p.status);
    if (p.ok) ORE.toast("打包完成");
  } catch (e) {
    log("操作失败：" + e.message, "err");
    setStatus("操作失败");
  }
  setBusy(false);
}

async function onRefreshUuid() {
  if (!state.packs.length) { ORE.toast("请先导入 Addon 文件夹"); return; }
  setBusy(true, "随机刷新 UUID…");
  try {
    const p = Bridge.available
      ? await Bridge.call("do_refresh_uuid", options)
      : await webDoRefreshUuid();
    printLogs(p.logs);
    if (p.packs) renderRows(p.packs, p.summary);
    if (p.status) setStatus(p.status);
    if (p.ok) ORE.toast("UUID 已刷新");
  } catch (e) {
    log("操作失败：" + e.message, "err");
    setStatus("操作失败");
  }
  setBusy(false);
}

async function onRestoreBackup() {
  if (!state.packs.length) { ORE.toast("请先导入 Addon 文件夹"); return; }
  if (Bridge.available) {
    const bak = await Bridge.call("list_backups");
    if (!bak.items.length) { ORE.toast("没有找到 manifest.json.bak 备份文件"); return; }
    const listing = bak.items.map(x => "  · " + x).join("\n");
    const ok = await ORE.confirmDialog({
      title: "还原备份",
      body: `将用备份覆盖以下 ${bak.items.length} 个 manifest.json：\n\n${listing}\n\n覆盖后当前未备份的修改会丢失，是否继续？`,
      okLabel: "还原",
    });
    if (!ok) return;
    applyScan(await Bridge.call("restore_backup"));
    return;
  }
  return webRestoreBackup();
}

/* ------------------------------------------------------------------ *
 * 最近打开菜单
 * ------------------------------------------------------------------ */
async function onRecentMenu() {
  if (Bridge.available) {
    const st = await Bridge.call("get_state");
    const items = [];
    if (!st.recents.length) items.push({ label: "（还没有记录）", disabled: true });
    for (const r of st.recents) {
      const text = r.path.length <= 68 ? r.path : "…" + r.path.slice(-66);
      items.push({
        label: text + (r.exists ? "" : "   [已不存在]"),
        disabled: !r.exists,
        onPick: async () => applyScan(await Bridge.call("import_folder", r.path)),
      });
    }
    items.push("sep");
    items.push({ label: "打开当前项目目录", disabled: !state.packs.length, onPick: onOpenAddonDir });
    items.push({ label: "打开输出目录", onPick: onOpenOut });
    items.push("sep");
    items.push({
      label: "清除最近记录",
      onPick: async () => {
        const ok = await ORE.confirmDialog({
          title: "清除最近记录",
          body: "确定要清除最近打开的项目记录吗？\n（不会删除任何文件夹，只是清掉这份列表）",
          okLabel: "清除",
        });
        if (!ok) return;
        await Bridge.call("clear_recents");
        log("已清除最近打开记录。", "warn");
        setStatus("已清除最近打开记录");
      },
    });
    ORE.showMenu($("#recentBtn"), items);
    return;
  }
  return webRecentMenu();
}

/* ------------------------------------------------------------------ *
 * 选项绑定
 * ------------------------------------------------------------------ */
function bindOptions() {
  const PART_VALUES = Object.values(core.VERSION_PART_LABELS);
  const partKey = label => Object.keys(core.VERSION_PART_LABELS).find(k => core.VERSION_PART_LABELS[k] === label) || "patch";
  ORE.bindDropdown({
    el: $("#partDrop"), values: PART_VALUES,
    get: () => core.VERSION_PART_LABELS[options.version_part],
    set: v => { options.version_part = partKey(v); persistOptions(); },
  });

  const MODE_VALUES = Object.values(core.PACK_MODES);
  const modeKey = label => Object.keys(core.PACK_MODES).find(k => core.PACK_MODES[k] === label) || "auto";
  ORE.bindDropdown({
    el: $("#modeDrop"), values: MODE_VALUES,
    get: () => core.PACK_MODES[options.pack_mode],
    set: v => { options.pack_mode = modeKey(v); persistOptions(); },
  });

  const UUID_VALUES = ["带横线（标准格式）", "无横线（32 位）"];
  ORE.bindDropdown({
    el: $("#uuidDrop"), values: UUID_VALUES,
    get: () => (options.uuid_style === "hex" ? UUID_VALUES[1] : UUID_VALUES[0]),
    set: v => { options.uuid_style = v.includes("无横线") ? "hex" : "hyphen"; persistOptions(); },
  });

  const binds = [
    ["#onlyBpRpToggle", "only_bp_rp", async on => {
      if (Bridge.available) {
        const r = await Bridge.call("mark_rows", on);
        renderRows(r.packs, r.summary);
      } else if (state.fsPacks.length) {
        webRenderTable();
        const { excluded } = core.selectPacks(state.fsPacks, on);
        if (excluded.length) log(`已排除 ${excluded.length} 个非 BP/RP 包：` + excluded.map(p => p.relName).join("、"), "warn");
        else log("当前所有包都是 BP / RP，无需排除。", "muted");
      }
    }],
    ["#bumpToggle", "pack_with_bump"],
    ["#bumpModulesToggle", "bump_modules"],
    ["#uuidModulesToggle", "uuid_modules"],
    ["#backupToggle", "backup"],
    ["#autoLoadToggle", "auto_load_last"],
  ];
  for (const [sel, key, extra] of binds) {
    const el = $(sel);
    ORE.setToggle(el, !!options[key]);
    el.addEventListener("click", () => {
      requestAnimationFrame(() => {
        options[key] = ORE.getToggle(el);
        persistOptions();
        if (extra) extra(options[key]);
      });
    });
  }
}

/* 用最新 options 刷新下拉框与开关显示（桥启动时对齐 Python 配置） */
function bindOptionsRefresh() {
  const PART = core.VERSION_PART_LABELS[options.version_part];
  if (PART) $("#partDrop .dd-label").textContent = PART;
  const MODE = core.PACK_MODES[options.pack_mode];
  if (MODE) $("#modeDrop .dd-label").textContent = MODE;
  $("#uuidDrop .dd-label").textContent = options.uuid_style === "hex" ? "无横线（32 位）" : "带横线（标准格式）";
  const map = [["#onlyBpRpToggle", "only_bp_rp"], ["#bumpToggle", "pack_with_bump"],
               ["#bumpModulesToggle", "bump_modules"], ["#uuidModulesToggle", "uuid_modules"],
               ["#backupToggle", "backup"], ["#autoLoadToggle", "auto_load_last"]];
  for (const [sel, key] of map) ORE.setToggle($(sel), !!options[key]);
}

/* ==========================================================================
 * 网页后端（File System Access API；浏览器 / ?demo=1 演示）
 * ========================================================================== */
const idb = {
  db: null,
  open() {
    return new Promise((res, rej) => {
      const r = indexedDB.open("mczip-web", 1);
      r.onupgradeneeded = () => r.result.createObjectStore("kv");
      r.onsuccess = () => { this.db = r.result; res(); };
      r.onerror = () => rej(r.error);
    });
  },
  get(key) {
    return new Promise(res => {
      try {
        const t = this.db.transaction("kv").objectStore("kv").get(key);
        t.onsuccess = () => res(t.result ?? null);
        t.onerror = () => res(null);
      } catch (e) { res(null); }
    });
  },
  set(key, val) {
    return new Promise(res => {
      try {
        const t = this.db.transaction("kv", "readwrite").objectStore("kv").put(val, key);
        t.onsuccess = () => res(true);
        t.onerror = () => res(false);
      } catch (e) { res(false); }
    });
  },
};

async function ensurePermission(handle, request) {
  const opts = { mode: "readwrite" };
  try {
    if (await handle.queryPermission(opts) === "granted") return true;
    if (request) return (await handle.requestPermission(opts)) === "granted";
  } catch (e) { /* 不支持 queryPermission 的环境 */ }
  return false;
}

function webRows(packs, onlyBpRp) {
  const { included, excluded } = core.selectPacks(packs, onlyBpRp);
  const inc = new Set(included);
  const rows = packs.map(p => ({
    name: p.relName, packed: inc.has(p), kind: p.kind,
    version: p.displayVersion, uuid: p.displayUuid,
    modules: p.moduleUuids.length, rel: p.relPath,
  }));
  const totalModules = included.reduce((n, p) => n + p.moduleUuids.length, 0);
  const totalUuid = included.filter(p => p.packUuid).length;
  let summary = `共 ${packs.length} 个包 · 本次打包 ${included.length} 个（${totalModules} 个模块 · ${totalUuid} 个 header.uuid）`;
  if (excluded.length) summary += ` · 已忽略 ${excluded.length} 个非 BP/RP 包`;
  summary += " · 双击某行查看 manifest.json";
  return { rows, summary };
}

function webRenderTable() {
  const { rows, summary } = webRows(state.fsPacks, options.only_bp_rp);
  renderRows(rows, summary);
}

async function webScan(announce) {
  const logs = [];
  try {
    state.fsPacks = await core.loadAddon(state.fsRoot);
  } catch (e) {
    state.fsPacks = [];
    if (announce) logs.push([`扫描失败：${e.message}`, "err"]);
    return { ok: false, error: e instanceof core.ManifestError ? null : e.message, logs, packs: [], status: "未找到可处理的 manifest.json" };
  }
  if (announce) {
    logs.push(["", "info"], [`已导入：${state.fsRoot.name}`, "head"], [`检测到 ${state.fsPacks.length} 个包：`]);
    for (const p of state.fsPacks) {
      logs.push([`  · ${p.relName}  [${p.kind}]  v${p.displayVersion}  uuid=${p.displayUuid}`]);
      p.warnings.forEach(w => logs.push([`      警告：${w}`, "warn"]));
    }
    logs.push(["输出目录：" + (state.fsOut ? state.fsOut.name + "（已授权目录）" : outPlaceholder()), "muted"]);
  }
  const { rows, summary } = webRows(state.fsPacks, options.only_bp_rp);
  return { ok: true, logs, packs: rows, summary, status: `已加载 ${state.fsPacks.length} 个包 · 可执行打包或随机刷新 UUID` };
}

async function webImportFolder(handle, { persist = true } = {}) {
  if (!(await ensurePermission(handle, true))) {
    log("没有获得目录的读写权限，无法导入。", "err");
    setStatus("授权被拒绝");
    return;
  }
  state.fsRoot = handle;
  $("#pathDisplay").value = handle.name;
  state.fsOut = (await idb.get("out:" + handle.name)) || null;
  $("#outDisplay").value = state.fsOut ? `${state.fsOut.name}（已授权目录）` : outPlaceholder();
  if (persist) {
    let list = (await idb.get("recents")) || [];
    list = [handle.name, ...list.filter(n => n !== handle.name)].slice(0, 10);
    await idb.set("recents", list);
    await idb.set("handle:" + handle.name, handle);
    await idb.set("lastRoot", handle.name);
  }
  applyScan(await webScan(true));
}

async function webBrowseFolder() {
  if (typeof showDirectoryPicker !== "function") {
    ORE.toast("当前浏览器不支持目录选择，请用 Edge / Chrome，或体验 ?demo=1 演示模式");
    log("此浏览器缺少 File System Access API，无法直接操作本地文件夹。可改用 Edge / Chrome，或在地址栏加 ?demo=1 体验演示模式。", "warn");
    return;
  }
  let handle;
  try { handle = await showDirectoryPicker({ mode: "readwrite" }); }
  catch (e) { return; }
  webImportFolder(handle);
}

async function webChooseOutputDir() {
  if (typeof showDirectoryPicker !== "function") {
    ORE.toast("当前浏览器不支持目录选择，打包时将逐个询问保存位置");
    return;
  }
  let handle;
  try { handle = await showDirectoryPicker({ mode: "readwrite" }); }
  catch (e) { return; }
  state.fsOut = handle;
  if (state.fsRoot) await idb.set("out:" + state.fsRoot.name, handle);
  $("#outDisplay").value = `${handle.name}（已授权目录）`;
  log("输出目录已更改为：" + handle.name + (state.fsRoot ? "（将记住，下次打开该项目自动还原）" : ""), "muted");
  applyState();
}

async function webSinkArchive(archive) {
  if (state.fsOut) {
    const fh = await state.fsOut.getFileHandle(archive.name, { create: true });
    const w = await fh.createWritable();
    await w.write(archive.bytes);
    await w.close();
    return state.fsOut.name;
  }
  if (typeof showSaveFilePicker === "function") {
    const fh = await showSaveFilePicker({
      suggestedName: archive.name,
      types: [{ description: "Minecraft 包", accept: { "application/zip": [".mcaddon", ".mcpack", ".zip"] } }],
    });
    const w = await fh.createWritable();
    await w.write(archive.bytes);
    await w.close();
    return fh.name;
  }
  const url = URL.createObjectURL(new Blob([archive.bytes], { type: "application/zip" }));
  const a = document.createElement("a");
  a.href = url; a.download = archive.name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
  return "浏览器下载";
}

async function webDoPackage() {
  const part = options.version_part, doBump = options.pack_with_bump;
  const bumpModules = options.bump_modules, backup = options.backup, onlyBpRp = options.only_bp_rp;
  const title = doBump ? "打包并升级版本号" : "打包（不修改版本号）";
  const logs = [[`—— ${title} ——`, "head"]];

  const packs = await core.loadAddon(state.fsRoot);
  const { included, excluded } = core.selectPacks(packs, onlyBpRp);
  const oldVersions = new Map(included.map(p => [p.relPath, p.displayVersion]));
  logs.push([onlyBpRp ? `打包范围：仅 BP / RP（${included.length} 个包）` : `打包范围：全部检测到的包（${included.length} 个）`]);
  if (excluded.length) logs.push(["  已排除：" + excluded.map(p => `${p.relName}（${p.kind}）`).join("、"), "warn"]);

  if (doBump) {
    logs.push([`版本递增位：${core.VERSION_PART_LABELS[part]}${bumpModules ? "（含 modules）" : "（仅 header）"}`]);
    for (const pack of included) {
      const rep = await core.bumpVersions(pack, { part, includeModules: bumpModules, backup });
      rep.messages.forEach(m => logs.push(["  " + m, "ok"]));
      if (rep.backupName) logs.push([`  [${pack.relName}] 已备份 -> ${rep.backupName}`, "muted"]);
    }
  } else {
    logs.push(["  已跳过版本号修改（本次打包不写入 manifest.json）", "muted"]);
  }

  const result = await core.buildPackage(state.fsRoot, state.fsRoot.name, packs, {
    mode: options.pack_mode, onlyBpRp, outDirHandle: state.fsOut,
  });
  const archives = [];
  for (const archive of result.archives) {
    const where = await webSinkArchive(archive);
    archives.push(archive.name);
    logs.push([`  已生成：${archive.name}  (${(archive.bytes.length / 1024).toFixed(1)} KB)  ->  ${where}`, "ok"]);
  }
  logs.push([`  共压缩 ${result.fileCount} 个文件`, "muted"], ["  " + result.skippedNote, "muted"]);

  state.fsPacks = await core.loadAddon(state.fsRoot);
  for (const pack of state.fsPacks) {
    const old = oldVersions.get(pack.relPath);
    if (old && old !== pack.displayVersion) logs.push([`  ${pack.relName} 版本：${old} -> ${pack.displayVersion}`, "ok"]);
  }
  logs.push(["打包完成。", "ok"]);

  const { rows, summary } = webRows(state.fsPacks, onlyBpRp);
  return { ok: true, logs, packs: rows, summary, archives, status: `打包完成 · 输出 ${result.archives.length} 个文件` };
}

async function webDoRefreshUuid() {
  const includeModules = options.uuid_modules, backup = options.backup;
  const style = options.uuid_style === "hex" ? "hex" : "hyphen";
  const onlyBpRp = options.only_bp_rp;
  const logs = [["—— 随机刷新 UUID ——", "head"]];

  const packs = await core.loadAddon(state.fsRoot);
  const { included, excluded } = core.selectPacks(packs, onlyBpRp);
  logs.push([`处理范围：${onlyBpRp ? "仅 BP / RP" : "全部检测到的包"}（${included.length} 个包）`]);
  if (excluded.length) logs.push(["  已跳过：" + excluded.map(p => `${p.relName}（${p.kind}）`).join("、"), "warn"]);

  for (const pack of included) {
    const rep = await core.refreshUuids(pack, { includeHeader: true, includeModules, style, backup });
    rep.messages.forEach(m => logs.push(["  " + m, (m.includes("已保留") || m.includes("跳过")) ? "muted" : "ok"]));
    if (rep.backupName) logs.push([`  [${pack.relName}] 已备份 -> ${rep.backupName}`, "muted"]);
  }

  state.fsPacks = await core.loadAddon(state.fsRoot);
  const total = included.reduce((n, p) => n + 1 + p.moduleUuids.length, 0);
  logs.push([`UUID 刷新完成，共更新 ${total} 处；重新导入游戏后生效。`, "ok"]);
  const { rows, summary } = webRows(state.fsPacks, onlyBpRp);
  return { ok: true, logs, packs: rows, summary, status: `UUID 刷新完成 · ${included.length} 个包` };
}

async function webRestoreBackup() {
  const candidates = [];
  for (const pack of state.fsPacks) {
    try { candidates.push({ pack, bak: await pack.dirHandle.getFileHandle(core.BACKUP_NAME) }); }
    catch (e) { /* 无备份 */ }
  }
  if (!candidates.length) { ORE.toast("没有找到 manifest.json.bak 备份文件"); return; }
  const listing = candidates.map(c => `  · ${c.pack.relPath}/manifest.json`).join("\n");
  const ok = await ORE.confirmDialog({
    title: "还原备份",
    body: `将用备份覆盖以下 ${candidates.length} 个 manifest.json：\n\n${listing}\n\n覆盖后当前未备份的修改会丢失，是否继续？`,
    okLabel: "还原",
  });
  if (!ok) return;
  for (const { pack, bak } of candidates) {
    try {
      const raw = await (await bak.getFile()).arrayBuffer();
      const w = await pack.manifestHandle.createWritable();
      await w.write(raw);
      await w.close();
      log(`[还原] ${pack.relPath}/manifest.json <- ${core.BACKUP_NAME}`, "warn");
    } catch (e) {
      log(`[还原失败] ${pack.relPath}：${e.message}`, "err");
    }
  }
  applyScan(await webScan(false));
  setStatus("已从备份还原 manifest.json");
}

async function webRecentMenu() {
  const list = (await idb.get("recents")) || [];
  const items = [];
  if (!list.length) items.push({ label: "（还没有记录）", disabled: true });
  for (const name of list) {
    items.push({
      label: name,
      onPick: async () => {
        const handle = await idb.get("handle:" + name);
        if (!handle) { log(`找不到该项目的访问句柄：${name}`, "err"); return; }
        if (!(await ensurePermission(handle, true))) { log(`未获得目录授权：${name}`, "err"); setStatus("授权被拒绝"); return; }
        webImportFolder(handle);
      },
    });
  }
  items.push("sep");
  items.push({ label: "打开当前项目目录", disabled: !state.fsRoot, onPick: () => ORE.toast("浏览器无法直接打开文件夹，请使用系统文件管理器") });
  items.push({ label: "打开输出目录", disabled: !state.fsOut, onPick: () => ORE.toast("浏览器无法直接打开文件夹，产物已写入你选择的目录") });
  items.push("sep");
  items.push({
    label: "清除最近记录",
    onPick: async () => {
      const ok = await ORE.confirmDialog({
        title: "清除最近记录",
        body: "确定要清除最近打开的项目记录吗？\n（不会删除任何文件夹，只是清掉这份列表）",
        okLabel: "清除",
      });
      if (!ok) return;
      await idb.set("recents", []);
      await idb.set("lastRoot", null);
      log("已清除最近打开记录。", "warn");
      setStatus("已清除最近打开记录");
    },
  });
  ORE.showMenu($("#recentBtn"), items);
}

/* ------------------------------------------------------------------ *
 * 演示模式（?demo=1）：内存虚拟 Addon
 * ------------------------------------------------------------------ */
class MemFile {
  constructor(name, bytes) { this.kind = "file"; this.name = name; this.bytes = bytes; this.lastModified = Date.now(); }
  async getFile() {
    const b = this.bytes;
    return {
      arrayBuffer: async () => b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength),
      text: async () => new TextDecoder().decode(b),
      lastModified: this.lastModified,
    };
  }
  async createWritable() {
    const self = this;
    return { write: async d => { self.bytes = d instanceof Uint8Array ? d : new Uint8Array(d); }, close: async () => {} };
  }
}
class MemDir {
  constructor(name) { this.kind = "directory"; this.name = name; this.kids = new Map(); }
  async *entries() { for (const [n, h] of [...this.kids.entries()].sort((a, b) => a[0].localeCompare(b[0]))) yield [n, h]; }
  async getFileHandle(name, { create } = {}) {
    let h = this.kids.get(name);
    if (!h) { if (!create) { const e = new Error("not found"); e.name = "NotFoundError"; throw e; } h = new MemFile(name, new Uint8Array(0)); this.kids.set(name, h); }
    if (h.kind !== "file") throw new Error("type mismatch");
    return h;
  }
  async getDirectoryHandle(name) { const h = this.kids.get(name); if (!h) throw new Error("not found"); return h; }
  async queryPermission() { return "granted"; }
  async requestPermission() { return "granted"; }
  dir(name) { const d = new MemDir(name); this.kids.set(name, d); return d; }
  file(name, text) { this.kids.set(name, new MemFile(name, new TextEncoder().encode(text))); return this; }
}

function buildDemoAddon() {
  const root = new MemDir("DemoAddon");
  const bp = root.dir("DemoBP");
  bp.file("manifest.json", JSON.stringify({
    format_version: 2,
    header: { name: "演示行为包", description: "demo bp", uuid: "11111111-1111-4111-8111-111111111111", version: [1, 0, 0] },
    modules: [{ type: "data", uuid: "22222222-2222-4222-8222-222222222222", version: [1, 0, 0] }],
    dependencies: [{ uuid: "33333333-3333-4333-8333-333333333333", version: [1, 0, 0] }],
  }, null, 4) + "\n");
  bp.file("pack_icon.png", "PNG");
  bp.dir("texts").file("zh_CN.lang", "pack.name=演示");
  bp.file("debug.log", "x");
  bp.file("manifest.json.bak", "x");
  const rp = root.dir("DemoRP");
  rp.file("manifest.json", '{\n  "format_version": 2,\n  "header": { "name": "演示资源包", "uuid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "version": [1, 2, 3] },\n  "modules": [ { "type": "resources", "uuid": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "version": [1, 2, 3] } ]\n}');
  rp.file("pack_icon.png", "PNG");
  const skin = root.dir("SkinPack");
  skin.file("manifest.json", '{"format_version":2,"header":{"name":"皮肤包","uuid":"cccccccc-cccc-4ccc-8ccc-cccccccccccc","version":[0,0,1]},"modules":[{"type":"skin_pack","uuid":"dddddddd-dddd-4ddd-8ddd-dddddddddddd"}]}');
  root.file("readme.txt", "不会被打包");
  root.dir(".git").file("config", "x");
  return root;
}

/* ------------------------------------------------------------------ *
 * 拖拽导入（网页模式）
 * ------------------------------------------------------------------ */
function initDnD() {
  document.addEventListener("dragover", e => e.preventDefault());
  document.addEventListener("drop", async e => {
    e.preventDefault();
    if (Bridge.available) return;  // 桥模式用原生对话框
    const item = [...(e.dataTransfer?.items || [])].find(i => i.kind === "file");
    if (!item || !item.getAsFileSystemHandle) return;
    try {
      const handle = await item.getAsFileSystemHandle();
      if (handle.kind === "directory") webImportFolder(handle);
      else ORE.toast("请直接拖入 Addon 文件夹（不是文件）");
    } catch (err) {
      log("拖拽导入失败：" + err.message, "err");
    }
  });
}

/* ------------------------------------------------------------------ *
 * 启动
 * ------------------------------------------------------------------ */
async function boot() {
  ORE.Theme.init();
  ORE.initPopup();
  ORE.initTooltip();
  ORE.initScrollers();
  ORE.initToggles();

  await detectBackend();
  window.MCZIP_BOOT_MODE = Bridge.backend;
  window.MCZIP_BACKEND = Bridge.backend;

  initDnD();
  bindOptions();
  applyState();
  $("#outDisplay").value = outPlaceholder();
  $("#browseBtn").addEventListener("click", onBrowse);
  $("#recentBtn").addEventListener("click", onRecentMenu);
  $("#rescanBtn").addEventListener("click", onRescan);
  $("#packBtn").addEventListener("click", onPackage);
  $("#uuidBtn").addEventListener("click", onRefreshUuid);
  $("#restoreBtn").addEventListener("click", onRestoreBackup);
  $("#openOutBtn").addEventListener("click", onOpenOut);
  $("#outChangeBtn").addEventListener("click", onChooseOut);

  log(`${APP_TITLE} v${APP_VERSION}  ·  作者：${APP_AUTHOR}`, "head");
  log("Minecraft 基岩版 Addon 打包工具：一键打包 / 版本号自增 / UUID 随机刷新", "muted");
  log("流程：① 导入文件夹  →  ② 检查包列表  →  ③ 打包 / 刷新 UUID。", "muted");

  const params = new URLSearchParams(location.search);

  if (Bridge.available) {
    const st = await Bridge.call("get_state");
    options = { ...OPTION_DEFAULTS, ...(st.options || {}) };
    bindOptionsRefresh();
    if (st.recents.length) {
      log(`已记住 ${st.recents.length} 个最近打开的项目，可在「最近打开」里选择；最近一个：${st.recents[0].path}`, "muted");
      log("");
    }
    if (st.preselect) {
      applyScan(await Bridge.call("import_folder", st.preselect));
    } else if (options.auto_load_last && st.lastFolder) {
      const last = st.recents.find(r => r.path === st.lastFolder);
      if (last && last.exists) {
        log(`自动打开上次的项目：${st.lastFolder}`, "head");
        applyScan(await Bridge.call("import_folder", st.lastFolder));
      } else if (st.lastFolder) {
        log(`上次的项目已不存在，跳过自动打开：${st.lastFolder}`, "warn");
      }
    }
    // ?autopack=1：自动跑一遍打包 + 刷新 UUID（用于自检 / 演示）
    if (params.get("autopack") === "1" && state.packs.length) {
      await onPackage();
      await onRefreshUuid();
    }
    return;
  }

  // 网页模式
  log("网页版：通过「浏览文件夹…」授权目录即可打包；manifest 修改前自动备份。", "muted");
  if (params.get("demo") === "1") {
    log("演示模式：内置 DemoAddon 为内存虚拟目录，所有改动只发生在浏览器里。", "head");
    await webImportFolder(buildDemoAddon(), { persist: false });
    if (params.get("autopack") === "1") { await onPackage(); await onRefreshUuid(); }
    return;
  }
  try { await Promise.race([idb.open(), new Promise(r => setTimeout(r, 1500))]); } catch (e) { /* 无 IDB 环境 */ }
  const recents = (await idb.get("recents")) || [];
  if (recents.length) {
    log(`已记住 ${recents.length} 个最近打开的项目，可在「最近打开」里选择；最近一个：${recents[0]}`, "muted");
    log("");
    if (options.auto_load_last) {
      const handle = await idb.get("handle:" + recents[0]);
      if (handle && await ensurePermission(handle, false)) {
        log(`自动打开上次的项目：${recents[0]}`, "head");
        await webImportFolder(handle);
      } else if (handle) {
        log("上次的项目需要重新授权：在「最近打开」里点击即可恢复。", "muted");
      }
    }
  }
}

document.addEventListener("DOMContentLoaded", () => {
  boot().catch(e => log("初始化失败：" + e.message, "err"));
});
