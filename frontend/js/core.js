/* ==========================================================================
   core.js —— addon_core.py 的浏览器移植版
   职责与 Python 版一致：
     1. 扫描 / 读取 Addon 文件夹中的 manifest.json（File System Access API）
     2. 版本号自增（默认补丁位）
     3. 随机刷新 UUID（header.uuid / modules[].uuid）
     4. 打包为 .mcaddon / .mcpack / .zip（CompressionStream deflate-raw）
   设计约束同样保留：键顺序、缩进风格、BOM、末尾换行、先备份再写入。
   仅依赖浏览器原生 API，无第三方库；同时可被 Node 引入做逻辑自测。
   ========================================================================== */
"use strict";

const MCZIP_CORE = (() => {

  /* ------------------------------------------------------------------ *
   * 常量（与 addon_core.py 保持一致）
   * ------------------------------------------------------------------ */
  const MANIFEST_NAME = "manifest.json";
  const BACKUP_NAME = "manifest.json.bak";
  const MAX_SCAN_DEPTH = 4;

  const SKIP_DIR_NAMES = new Set([
    ".git", ".svn", ".hg", ".idea", ".vscode", "__macosx", "__pycache__",
    "node_modules", ".cache", ".venv", "venv", ".mypy_cache", ".pytest_cache",
    ".vs", ".gradle",
  ]);
  const SKIP_FILE_NAMES = new Set([".ds_store", "thumbs.db", "desktop.ini", "manifest.json.bak"]);
  const SKIP_FILE_SUFFIXES = [".bak", ".pyc", ".pyo", ".swp", ".tmp", ".orig", ".log"];

  const MODULE_TYPE_LABELS = {
    data: "行为包", resources: "资源包", script: "脚本包", javascript: "脚本包",
    client_data: "客户端数据", interface: "UI 界面", skin_pack: "皮肤包",
    world_template: "世界模板", persona_piece: "个性化部件",
  };
  const BP_MODULE_TYPES = new Set(["data", "script", "javascript"]);
  const RP_MODULE_TYPES = new Set(["resources", "client_data", "interface"]);
  const PACK_CATEGORY_LABELS = { bp: "行为包", rp: "资源包", bp_rp: "行为包+资源包", other: "其他" };

  const VERSION_PART_LABELS = {
    patch: "补丁位（第 3 位）",
    minor: "次版本（第 2 位）",
    major: "主版本（第 1 位）",
  };
  const PACK_MODES = {
    auto: "自动识别（推荐）",
    mcaddon: ".mcaddon 整合包",
    mcpack: ".mcpack 单包",
    zip: ".zip 直出（不套外层目录）",
  };

  class ManifestError extends Error {}

  /* ------------------------------------------------------------------ *
   * 基础读写工具
   * ------------------------------------------------------------------ */

  /** 读字节 → { text, encoding, hasBom }（utf-8 → gb18030 → latin-1 逐级回退） */
  function decodeText(bytes) {
    let hasBom = false;
    if (bytes.length >= 3 && bytes[0] === 0xEF && bytes[1] === 0xBB && bytes[2] === 0xBF) {
      hasBom = true;
      bytes = bytes.subarray(3);
    }
    for (const enc of ["utf-8", "gb18030", "windows-1252"]) {
      try {
        return { text: new TextDecoder(enc, { fatal: true }).decode(bytes), encoding: enc, hasBom };
      } catch (e) { /* 尝试下一种编码 */ }
    }
    return { text: new TextDecoder("utf-8").decode(bytes), encoding: "utf-8", hasBom };
  }

  /** 从原文探测缩进风格（空格 / Tab），默认两空格。 */
  function detectIndent(text) {
    const m = /\n([ \t]+)\S/.exec(text);
    if (!m) return "  ";
    return m[1].length <= 8 ? m[1] : "  ";
  }

  /** 序列化 manifest：JS 对象天然保持字符串键插入顺序；不转义中文。 */
  function dumpManifest(data, indent = "  ", trailingNewline = true) {
    const text = JSON.stringify(data, null, indent);
    return trailingNewline ? text + "\n" : text;
  }

  /** 去除 Windows 非法文件名字符。 */
  function safeFilename(name, fallback = "addon") {
    const cleaned = String(name || "").replace(/[<>:"/\\|?*\x00-\x1f]/g, "_").replace(/^[ .]+|[ .]+$/g, "");
    return cleaned || fallback;
  }

  function fmtVersion(version) { return version.map(x => String(x | 0)).join("."); }

  /* ------------------------------------------------------------------ *
   * 版本号 / UUID
   * ------------------------------------------------------------------ */
  function normalizeVersion(value, dflt = [0, 0, 1]) {
    if (Array.isArray(value)) {
      const out = value.map(item => {
        const n = parseInt(item, 10);
        return Number.isFinite(n) ? n : 0;
      });
      return out.length ? out : [...dflt];
    }
    if (typeof value === "string") {
      const nums = value.trim().split(/[.,\s\-_]+/).filter(p => /^\d+$/.test(p)).map(Number);
      if (nums.length) return nums;
    }
    return [...dflt];
  }

  /** 递增版本号：主/次位升级时其右归零（[1,4,7] minor -> [1,5,0]）。 */
  function bumpVersion(version, part = "patch") {
    const result = version.map(x => x | 0);
    if (!result.length) result.push(0, 0, 1);
    while (result.length < 3) result.push(0);
    let index = { major: 0, minor: 1, patch: 2 }[part] ?? 2;
    if (index >= result.length) index = result.length - 1;
    result[index] += 1;
    if (index < 2) for (let i = index + 1; i < result.length; i++) result[i] = 0;
    return result;
  }

  /** 随机 UUID v4。style: hyphen（带横线）/ hex（无横线）。 */
  function generateUuid(style = "hyphen") {
    const buf = new Uint8Array(16);
    crypto.getRandomValues(buf);
    buf[6] = (buf[6] & 0x0f) | 0x40;
    buf[8] = (buf[8] & 0x3f) | 0x80;
    const hex = [...buf].map(b => b.toString(16).padStart(2, "0")).join("");
    const hyphen = `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    return style === "hex" ? hex : hyphen;
  }

  /* ------------------------------------------------------------------ *
   * 包信息
   * ------------------------------------------------------------------ */
  function headerDisplayName(value) {
    if (typeof value === "string") return value;
    if (value && typeof value === "object" && !Array.isArray(value)) {
      for (const key of ["zh_CN", "zh_cn", "zh-Hans", "en_US", "en_GB"]) {
        const item = value[key];
        if (typeof item === "string" && item.trim()) return item;
      }
      for (const item of Object.values(value)) {
        if (typeof item === "string" && item.trim()) return item;
      }
    }
    return "";
  }

  class PackInfo {
    /** dirHandle: 包目录句柄；manifestHandle: manifest.json 文件句柄 */
    constructor(dirHandle, manifestHandle, data, meta) {
      this.dirHandle = dirHandle;
      this.manifestHandle = manifestHandle;
      this.data = data;
      this.indent = meta.indent;
      this.encoding = meta.encoding;
      this.hasBom = meta.hasBom;
      this.trailingNewline = meta.trailingNewline;
      this.relPath = meta.relPath || dirHandle.name;   // 相对 Addon 根的路径
      this.sync();
    }
    get relName() { return this.dirHandle.name; }
    get displayName() { return this.name || this.relName; }
    get displayVersion() { return this.version.length ? fmtVersion(this.version) : "—"; }
    get displayUuid() { return this.packUuid || "—"; }
    get isBpRp() { return ["bp", "rp", "bp_rp"].includes(this.category); }

    modulesList() {
      const modules = this.data.modules;
      return Array.isArray(modules) ? modules.filter(m => m && typeof m === "object" && !Array.isArray(m)) : [];
    }

    sync() {
      this.warnings = [];
      let header = this.data.header;
      if (!header || typeof header !== "object" || Array.isArray(header)) {
        this.warnings.push("缺少 header 节点");
        header = {};
      }
      this.name = headerDisplayName(header.name);
      if ("version" in header) this.version = normalizeVersion(header.version);
      else { this.version = []; this.warnings.push("header.version 缺失"); }
      this.packUuid = String(header.uuid || "");
      if (!this.packUuid) this.warnings.push("header.uuid 缺失");

      const modules = this.modulesList();
      this.moduleUuids = modules.map(m => String(m.uuid || ""));
      const types = modules.map(m => String(m.type || "").trim().toLowerCase());
      this.rawTypes = types;
      const hasBp = types.some(t => BP_MODULE_TYPES.has(t));
      const hasRp = types.some(t => RP_MODULE_TYPES.has(t));
      this.category = hasBp && hasRp ? "bp_rp" : hasBp ? "bp" : hasRp ? "rp" : "other";

      if (!modules.length) { this.kind = "未知"; this.warnings.push("modules 为空"); }
      else if (this.category === "other") {
        const first = types[0] || "";
        this.kind = MODULE_TYPE_LABELS[first] || first || "未知";
      } else this.kind = PACK_CATEGORY_LABELS[this.category];

      this.formatVersion = this.data.format_version;
    }

    /** 写回 manifest.json：先备份（可选），再写入；支持 move 时走 .tmp 原子替换。 */
    async save(backup = true) {
      let backupName = null;
      const dir = this.dirHandle;
      if (backup) {
        try {
          const raw = await (await this.manifestHandle.getFile()).arrayBuffer();
          const bak = await dir.getFileHandle(BACKUP_NAME, { create: true });
          const w = await bak.createWritable();
          await w.write(raw);
          await w.close();
          backupName = BACKUP_NAME;
        } catch (e) { backupName = null; }
      }
      const text = dumpManifest(this.data, this.indent, this.trailingNewline);
      let payload = new TextEncoder().encode(text);
      if (this.hasBom) {
        const withBom = new Uint8Array(3 + payload.length);
        withBom.set([0xEF, 0xBB, 0xBF], 0);
        withBom.set(payload, 3);
        payload = withBom;
      }
      // 优先 .tmp + move 原子替换；不支持 move 则直接覆盖（备份已先行）
      let atomic = false;
      if (typeof FileSystemFileHandle !== "undefined" &&
          FileSystemFileHandle.prototype.move) {
        try {
          const tmp = await dir.getFileHandle(MANIFEST_NAME + ".tmp", { create: true });
          const w = await tmp.createWritable();
          await w.write(payload);
          await w.close();
          await tmp.move(MANIFEST_NAME);
          atomic = true;
        } catch (e) { atomic = false; }
      }
      if (!atomic) {
        const w = await this.manifestHandle.createWritable();
        await w.write(payload);
        await w.close();
      }
      return { backupName, atomic };
    }
  }

  /* ------------------------------------------------------------------ *
   * 扫描 / 加载（dirHandle 仅需 entries() / getFileHandle()）
   * ------------------------------------------------------------------ */
  async function hasManifest(dirHandle) {
    try { await dirHandle.getFileHandle(MANIFEST_NAME); return true; }
    catch (e) { return false; }
  }

  async function findPackRoots(rootHandle) {
    if (await hasManifest(rootHandle)) return [{ handle: rootHandle, rel: "." }];
    const found = [];
    async function walk(dir, depth, prefix) {
      if (depth > MAX_SCAN_DEPTH) return;
      const entries = [];
      for await (const [name, handle] of dir.entries()) entries.push([name, handle]);
      entries.sort((a, b) => a[0].toLowerCase().localeCompare(b[0].toLowerCase()));
      for (const [name, handle] of entries) {
        if (handle.kind !== "directory") continue;
        const lowered = name.toLowerCase();
        if (SKIP_DIR_NAMES.has(lowered) || name.startsWith(".")) continue;
        if (await hasManifest(handle)) { found.push({ handle, rel: prefix + name }); continue; }
        await walk(handle, depth + 1, prefix + name + "/");
      }
    }
    await walk(rootHandle, 1, "");
    return found;
  }

  async function loadPack(entry) {
    const manifestHandle = await entry.handle.getFileHandle(MANIFEST_NAME);
    const file = await manifestHandle.getFile();
    const { text, encoding, hasBom } = decodeText(new Uint8Array(await file.arrayBuffer()));
    let data;
    try { data = JSON.parse(text); }
    catch (e) {
      const line = (e.message.match(/position (\d+)/) || [])[1];
      throw new ManifestError(`${entry.rel}/${MANIFEST_NAME} 不是合法 JSON${line ? "（约第 " + line + " 字符）" : ""}`);
    }
    if (!data || typeof data !== "object" || Array.isArray(data)) {
      throw new ManifestError(`${entry.rel}/${MANIFEST_NAME} 顶层结构必须是 JSON 对象`);
    }
    return new PackInfo(entry.handle, manifestHandle, data, {
      indent: detectIndent(text), encoding, hasBom,
      trailingNewline: text.endsWith("\n"), relPath: entry.rel,
    });
  }

  /** 加载 Addon 文件夹中的全部包（按目录名排序）。 */
  async function loadAddon(rootHandle) {
    const roots = await findPackRoots(rootHandle);
    if (!roots.length) {
      throw new ManifestError(
        "没有在该文件夹中找到任何 manifest.json。\n" +
        "请确认选择的是 Addon 根目录（其下含 BP/RP 子包）或单个包目录。");
    }
    const packs = [];
    for (const r of roots) packs.push(await loadPack(r));
    packs.sort((a, b) => a.relName.toLowerCase().localeCompare(b.relName.toLowerCase()));
    return packs;
  }

  /* ------------------------------------------------------------------ *
   * 修改操作
   * ------------------------------------------------------------------ */
  function requireHeader(pack) {
    const header = pack.data.header;
    if (!header || typeof header !== "object" || Array.isArray(header)) {
      throw new ManifestError(`${pack.relPath}/manifest.json 缺少 header 节点，无法修改`);
    }
    return header;
  }

  /** 递增 header.version（可选同时递增 modules[].version）。 */
  async function bumpVersions(pack, { part = "patch", includeModules = false, backup = true } = {}) {
    const header = requireHeader(pack);
    const messages = [];
    if (!("version" in header)) messages.push(`[${pack.relName}] header.version 缺失，按 0.0.1 起算`);
    const oldV = normalizeVersion(header.version);
    const newV = bumpVersion(oldV, part);
    header.version = newV;
    messages.push(`[${pack.relName}] header.version  ${fmtVersion(oldV)} -> ${fmtVersion(newV)}`);
    if (includeModules) {
      pack.modulesList().forEach((module, index) => {
        if (!("version" in module)) return;
        const mOld = normalizeVersion(module.version);
        const mNew = bumpVersion(mOld, part);
        module.version = mNew;
        messages.push(`[${pack.relName}] modules[${index}].version  ${fmtVersion(mOld)} -> ${fmtVersion(mNew)}`);
      });
    }
    pack.sync();
    const { backupName } = await pack.save(backup);
    return { messages, backupName };
  }

  /** 随机刷新 header.uuid 与 modules[].uuid；dependencies 中的 uuid 一律不动。 */
  async function refreshUuids(pack, { includeHeader = true, includeModules = true, style = "hyphen", backup = true } = {}) {
    const header = requireHeader(pack);
    const messages = [];
    if (includeHeader) {
      const oldUuid = String(header.uuid || "(缺失)");
      const newUuid = generateUuid(style);
      header.uuid = newUuid;
      messages.push(`[${pack.relName}] header.uuid  ${oldUuid} -> ${newUuid}`);
    }
    if (includeModules) {
      pack.modulesList().forEach((module, index) => {
        if (!("uuid" in module)) { messages.push(`[${pack.relName}] modules[${index}].uuid 缺失，已跳过`); return; }
        const oldUuid = String(module.uuid);
        const newUuid = generateUuid(style);
        module.uuid = newUuid;
        messages.push(`[${pack.relName}] modules[${index}].uuid  ${oldUuid} -> ${newUuid}`);
      });
    }
    if (Array.isArray(pack.data.dependencies) && pack.data.dependencies.length) {
      messages.push(`[${pack.relName}] 已保留 dependencies 中的 uuid（指向外部包，不可改动）`);
    }
    pack.sync();
    const { backupName } = await pack.save(backup);
    return { messages, backupName };
  }

  /* ------------------------------------------------------------------ *
   * 筛选 / 模式
   * ------------------------------------------------------------------ */
  function isRootItself(rootEntry, packs) {
    return packs.length === 1 && packs[0].relPath === ".";
  }

  /** 只保留 BP / RP；导入的就是单个包目录本身时不做筛选。 */
  function selectPacks(packs, onlyBpRp = true) {
    if (!onlyBpRp || isRootItself(null, packs)) return { included: [...packs], excluded: [] };
    return {
      included: packs.filter(p => p.isBpRp),
      excluded: packs.filter(p => !p.isBpRp),
    };
  }

  /** 自动模式判定：单包（根目录自身即包）-> mcpack，否则 mcaddon。 */
  function resolveMode(mode, packs) {
    if (mode !== "auto") return mode;
    return isRootItself(null, packs) ? "mcpack" : "mcaddon";
  }

  /** 多包取最高版本号作为文件名后缀。 */
  function versionTag(packs) {
    const versions = packs.filter(p => p.version.length).map(p => p.version);
    if (!versions.length) return "0.0.1";
    let best = versions[0];
    for (const v of versions) {
      const len = Math.max(v.length, best.length);
      for (let i = 0; i < len; i++) {
        const a = v[i] || 0, b = best[i] || 0;
        if (a !== b) { if (a > b) best = v; break; }
      }
    }
    return fmtVersion(best);
  }

  /* ------------------------------------------------------------------ *
   * 收集待压缩文件
   * ------------------------------------------------------------------ */
  async function collectEntries(packEntries, basePrefix, { outDirHandle = null, skipNames = new Set() } = {}) {
    const entries = [];
    async function walk(dir, prefix) {
      const items = [];
      for await (const [name, handle] of dir.entries()) items.push([name, handle]);
      items.sort((a, b) => a[0].localeCompare(b[0]));
      for (const [name, handle] of items) {
        const lowered = name.toLowerCase();
        if (handle.kind === "directory") {
          if (SKIP_DIR_NAMES.has(lowered) || name.startsWith(".")) continue;
          if (outDirHandle && handle.isSameEntry) {
            try { if (await handle.isSameEntry(outDirHandle)) continue; } catch (e) {}
          }
          await walk(handle, prefix + name + "/");
        } else {
          if (SKIP_FILE_NAMES.has(lowered)) continue;
          if (SKIP_FILE_SUFFIXES.some(s => lowered.endsWith(s))) continue;
          if (skipNames.has(name)) continue;   // 跳过已生成的同名压缩包
          entries.push({ fileHandle: handle, arcname: prefix + name });
        }
      }
    }
    for (const p of packEntries) {
      // basePrefix：arcname 的前缀（mcaddon: "包目录/"，mcpack/zip 视结构而定）
      await walk(p.handle, basePrefix(p));
    }
    entries.sort((a, b) => a.arcname.localeCompare(b.arcname));
    return entries;
  }

  /* ------------------------------------------------------------------ *
   * ZIP 写出（deflate-raw 压缩 + CRC32）
   * ------------------------------------------------------------------ */
  const CRC_TABLE = (() => {
    const table = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
      table[n] = c >>> 0;
    }
    return table;
  })();

  function crc32(bytes) {
    let crc = 0xFFFFFFFF;
    for (let i = 0; i < bytes.length; i++) {
      crc = CRC_TABLE[(crc ^ bytes[i]) & 0xFF] ^ (crc >>> 8);
    }
    return (crc ^ 0xFFFFFFFF) >>> 0;
  }

  async function deflateRaw(bytes) {
    if (typeof CompressionStream === "undefined") return null; // 回退 store
    const cs = new CompressionStream("deflate-raw");
    const stream = new Blob([bytes]).stream().pipeThrough(cs);
    return new Uint8Array(await new Response(stream).arrayBuffer());
  }

  function dosDateTime(ms) {
    const d = new Date(ms);
    let year = d.getFullYear();
    if (year < 1980) year = 1980;
    return {
      time: ((d.getHours() << 11) | (d.getMinutes() << 5) | (d.getSeconds() >> 1)) & 0xFFFF,
      date: (((year - 1980) << 9) | ((d.getMonth() + 1) << 5) | d.getDate()) & 0xFFFF,
    };
  }

  /**
   * 生成 ZIP 字节。entries: [{ name(utf8 路径), bytes(Uint8Array), mtime(ms) }]
   * 返回 Uint8Array（local headers + central directory + EOCD）。
   */
  async function buildZip(entries) {
    const chunks = [];
    const central = [];
    let offset = 0;
    const u16 = v => new Uint8Array([v & 0xFF, (v >>> 8) & 0xFF]);
    const u32 = v => new Uint8Array([v & 0xFF, (v >>> 8) & 0xFF, (v >>> 16) & 0xFF, (v >>> 24) & 0xFF]);

    for (const entry of entries) {
      const nameBytes = new TextEncoder().encode(entry.name);
      const compressed = await deflateRaw(entry.bytes);
      const method = compressed ? 8 : 0;
      const data = compressed || entry.bytes;
      const crc = crc32(entry.bytes);
      const { time, date } = dosDateTime(entry.mtime ?? Date.now());
      const flags = 0x0800; // UTF-8 文件名

      const local = [
        u32(0x04034b50), u16(20), u16(flags), u16(method), u16(time), u16(date),
        u32(crc), u32(data.length), u32(entry.bytes.length),
        u16(nameBytes.length), u16(0), nameBytes,
      ];
      for (const c of local) chunks.push(c);
      chunks.push(data);

      central.push({
        nameBytes, crc, csize: data.length, usize: entry.bytes.length,
        method, time, date, flags, localOffset: offset,
      });
      offset += local.reduce((n, c) => n + c.length, 0) + data.length;
    }

    const cdStart = offset;
    for (const c of central) {
      const rec = [
        u32(0x02014b50), u16(20), u16(20), u16(c.flags), u16(c.method), u16(c.time), u16(c.date),
        u32(c.crc), u32(c.csize), u32(c.usize),
        u16(c.nameBytes.length), u16(0), u16(0), u16(0), u16(0), u32(0), u32(c.localOffset),
        c.nameBytes,
      ];
      for (const r of rec) chunks.push(r);
      offset += rec.reduce((n, r) => n + r.length, 0);
    }
    const cdSize = offset - cdStart;
    const eocd = [
      u32(0x06054b50), u16(0), u16(0), u16(central.length), u16(central.length),
      u32(cdSize), u32(cdStart), u16(0),
    ];
    for (const r of eocd) chunks.push(r);

    const total = chunks.reduce((n, c) => n + c.length, 0);
    const out = new Uint8Array(total);
    let pos = 0;
    for (const c of chunks) { out.set(c, pos); pos += c.length; }
    return out;
  }

  /* ------------------------------------------------------------------ *
   * 打包总流程（与 build_package 对应；产出内存 ZIP，由调用方落地）
   * ------------------------------------------------------------------ */
  async function buildPackage(rootHandle, rootName, packs, { mode = "auto", onlyBpRp = true, baseName = null, outDirHandle = null } = {}) {
    const { included, excluded } = selectPacks(packs, onlyBpRp);
    if (!included.length) {
      if (excluded.length) {
        throw new ManifestError(
          "过滤后没有可打包的包。\n检测到的包均不是行为包 / 资源包：" +
          excluded.map(p => p.relName).join("、") +
          "\n如需打包它们，请取消勾选「只处理 BP / RP」。");
      }
      throw new ManifestError("没有可打包的包。");
    }

    const packName = safeFilename(baseName || rootName, "addon");
    const resolvedMode = resolveMode(mode, included);
    const single = isRootItself(null, included);

    // 子包在压缩包内的路径前缀（与 Python 版 base/relative_to 对应）
    const relPrefix = p => (p.relPath === "." ? "" : p.relPath + "/");

    // jobs: [{ outName, packs, prefix(pack) }]
    const jobs = [];
    if (resolvedMode === "mcpack") {
      if (single) {
        jobs.push({ outName: `${packName}_v${versionTag(included)}.mcpack`, packs: included, prefix: () => "" });
      } else {
        for (const pack of included) {
          const name = safeFilename(pack.relName, "pack");
          jobs.push({ outName: `${name}_v${versionTag([pack])}.mcpack`, packs: [pack], prefix: () => "" });
        }
      }
    } else if (resolvedMode === "zip") {
      // ZIP 面向"解压即用"：不保留 Addon 根目录名。
      //   * 只有一个包时 -> 该包内容直接在压缩包根（manifest.json 等不再套一层）
      //   * 多个包时     -> 各包以文件夹并列（否则多个 manifest.json 会互相覆盖）
      if (included.length === 1) {
        jobs.push({ outName: `${packName}_v${versionTag(included)}.zip`, packs: included, prefix: () => "" });
      } else {
        jobs.push({ outName: `${packName}_v${versionTag(included)}.zip`, packs: included, prefix: relPrefix });
      }
    } else {
      // mcaddon：base 取 Addon 根目录，各子包以文件夹并列存放
      jobs.push({ outName: `${packName}_v${versionTag(included)}.mcaddon`, packs: included, prefix: relPrefix });
    }

    const archives = [];
    let fileCount = 0;
    const outNames = new Set(jobs.map(j => j.outName));
    for (const job of jobs) {
      const entries = await collectEntries(
        job.packs.map(p => ({ handle: p.dirHandle, relPath: p.relPath })),
        job.prefix,
        { outDirHandle, skipNames: outNames },
      );
      if (!entries.length) throw new ManifestError(`没有可打包的文件：${job.outName}`);
      const zipEntries = [];
      for (const e of entries) {
        const file = await e.fileHandle.getFile();
        zipEntries.push({
          name: e.arcname,
          bytes: new Uint8Array(await file.arrayBuffer()),
          mtime: file.lastModified,
        });
      }
      const zipBytes = await buildZip(zipEntries);
      archives.push({ name: job.outName, bytes: zipBytes });
      fileCount += zipEntries.length;
    }

    const notes = ["已忽略 备份文件 / 缓存目录 / 隐藏目录"];
    if (excluded.length) notes.push("已排除非 BP/RP 类型：" + excluded.map(p => p.relName).join("、"));
    return {
      archives, fileCount,
      skippedNote: notes.join("；"),
      included: included.map(p => p.relName),
      excluded: excluded.map(p => p.relName),
      resolvedMode,
    };
  }

  /* ------------------------------------------------------------------ */
  return {
    MANIFEST_NAME, BACKUP_NAME, MAX_SCAN_DEPTH,
    SKIP_DIR_NAMES, SKIP_FILE_NAMES, SKIP_FILE_SUFFIXES,
    MODULE_TYPE_LABELS, BP_MODULE_TYPES, RP_MODULE_TYPES, PACK_CATEGORY_LABELS,
    VERSION_PART_LABELS, PACK_MODES, ManifestError,
    decodeText, detectIndent, dumpManifest, safeFilename, fmtVersion,
    normalizeVersion, bumpVersion, generateUuid,
    PackInfo, loadPack, loadAddon, findPackRoots,
    requireHeader, bumpVersions, refreshUuids,
    selectPacks, resolveMode, versionTag,
    collectEntries, crc32, buildZip, buildPackage,
  };
})();

if (typeof module !== "undefined" && module.exports) module.exports = MCZIP_CORE;
