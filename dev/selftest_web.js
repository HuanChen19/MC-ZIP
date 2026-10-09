"use strict";
// Real ZIP contents and CRCs are checked, independently of the UI rendering.
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { inflateRawSync } = require("node:zlib");
const core = require("../frontend/js/core.js");

class FileHandle {
  constructor(location) { this.location = location; this.kind = "file"; this.name = path.basename(location); }
  async getFile() {
    const bytes = await fs.readFile(this.location), stat = await fs.stat(this.location);
    return { arrayBuffer: () => new Blob([bytes]).arrayBuffer(), lastModified: stat.mtimeMs };
  }
  async createWritable() {
    let pending;
    return {
      write: async bytes => { pending = Buffer.from(bytes); },
      close: async () => { await fs.writeFile(this.location, pending); },
    };
  }
}
class DirectoryHandle {
  constructor(location) { this.location = location; this.kind = "directory"; this.name = path.basename(location); }
  async getFileHandle(name, { create = false } = {}) {
    const location = path.join(this.location, name);
    if (create) {
      const handle = await fs.open(location, "a");
      await handle.close();
    }
    if (!(await fs.stat(location)).isFile()) throw new Error("Not a file");
    return new FileHandle(location);
  }
  async *entries() {
    for (const item of await fs.readdir(this.location, { withFileTypes: true })) {
      const location = path.join(this.location, item.name);
      yield [item.name, item.isDirectory() ? new DirectoryHandle(location) : new FileHandle(location)];
    }
  }
  async isSameEntry(other) { return this.location === other.location; }
  async removeEntry(name) {
    try { await fs.unlink(path.join(this.location, name)); }
    catch (error) {
      if (error.code === "ENOENT") error.name = "NotFoundError";
      throw error;
    }
  }
}
function unpack(bytes) {
  const zip = Buffer.from(bytes), files = new Map();
  const end = zip.length - 22;
  assert.equal(zip.readUInt32LE(end), 0x06054b50);
  let offset = zip.readUInt32LE(end + 16);
  for (let i = 0; i < zip.readUInt16LE(end + 10); i++) {
    assert.equal(zip.readUInt32LE(offset), 0x02014b50);
    const method = zip.readUInt16LE(offset + 10);
    const size = zip.readUInt32LE(offset + 20), nameLen = zip.readUInt16LE(offset + 28);
    const name = zip.subarray(offset + 46, offset + 46 + nameLen).toString("utf8");
    const local = zip.readUInt32LE(offset + 42);
    const dataOffset = local + 30 + zip.readUInt16LE(local + 26) + zip.readUInt16LE(local + 28);
    const data = zip.subarray(dataOffset, dataOffset + size);
    const unpacked = method === 8 ? inflateRawSync(data) : data;
    assert.equal(core.crc32(unpacked), zip.readUInt32LE(offset + 16), name + " CRC");
    assert.equal(unpacked.length, zip.readUInt32LE(offset + 24));
    assert(!files.has(name), "Duplicate ZIP entry: " + name);
    files.set(name, unpacked);
    offset += 46 + nameLen + zip.readUInt16LE(offset + 30) + zip.readUInt16LE(offset + 32);
  }
  return files;
}
async function main() {
  const location = await fs.mkdtemp(path.join(os.tmpdir(), "mczip-web-test-"));
  try {
    for (const [name, type] of [["BP", "data"], ["RP", "resources"], ["Skin", "skin_pack"], ["docs/ExampleBP", "data"]]) {
      const folder = path.join(location, name);
      await fs.mkdir(folder, { recursive: true });
      if (name === "BP") await fs.mkdir(path.join(folder, "entities"));
      if (name === "RP") await fs.mkdir(path.join(folder, "textures"));
      await fs.writeFile(path.join(folder, "manifest.json"), JSON.stringify({
        format_version: 2, header: { name, version: [1, 0, 0], uuid: "123" },
        modules: [{ type, uuid: "456", version: [1, 0, 0] }],
      }));
      await fs.writeFile(path.join(folder, "资源.txt"), "资源内容");
      await fs.writeFile(path.join(folder, "manifest.json.bak"), "backup");
    }
    await fs.writeFile(path.join(location, "README.txt"), "must not be packed");
    const root = new DirectoryHandle(location), packs = await core.loadAddon(root);
    assert.deepEqual(packs.map(p => p.relName).sort(), ["BP", "RP", "Skin"]);
    for (const onlyBpRp of [true, false]) {
      for (const mode of ["auto", "mcaddon", "mcpack", "zip"]) {
        const result = await core.buildPackage(root, "Addon", packs, { mode, onlyBpRp });
        assert.deepEqual(result.included.sort(), ["BP", "RP"]);
        assert.deepEqual(result.excluded, ["Skin"]);
        for (const archive of result.archives) {
          const files = unpack(archive.bytes), names = [...files.keys()];
          assert(!names.some(name => /Skin|docs|README|\.bak/.test(name)));
          if (mode === "mcpack") assert(files.has("manifest.json"));
          else assert.deepEqual([...new Set(names.map(n => n.split("/")[0]))].sort(), ["BP", "RP"]);
          const required = mode === "mcpack"
            ? [archive.name.startsWith("BP_") ? "entities/" : "textures/"]
            : ["BP/entities/", "RP/textures/"];
          for (const name of required) assert(files.has(name), "Missing directory: " + name);
          for (const [name, data] of files) if (name.endsWith("资源.txt")) assert.equal(data.toString("utf8"), "资源内容");
        }
      }
    }
    for (const mode of ["auto", "mcaddon", "mcpack", "zip"]) {
      const skin = new DirectoryHandle(path.join(location, "Skin"));
      await assert.rejects(core.buildPackage(skin, "Skin", await core.loadAddon(skin), { mode, onlyBpRp: false }), core.ManifestError);
    }
    const bp = new DirectoryHandle(path.join(location, "BP"));
    const single = await core.buildPackage(bp, "BP", await core.loadAddon(bp));
    assert.equal(single.archives[0].name, "BP_v1.0.0.mcpack");
    assert(unpack(single.archives[0].bytes).has("manifest.json"));
    assert(unpack(single.archives[0].bytes).has("entities/"));

    await fs.rename(path.join(location, "RP/textures"), path.join(location, "RP/shader"));
    for (const mode of ["auto", "mcaddon", "mcpack", "zip"]) {
      const result = await core.buildPackage(root, "Addon", packs, { mode });
      assert.equal(result.fileCount, 4, "Directories must not count as files");
      for (const archive of result.archives) {
        if (mode === "mcpack" && archive.name.startsWith("BP_")) continue;
        assert(unpack(archive.bytes).has(mode === "mcpack" ? "shader/" : "RP/shader/"));
      }
    }
    await fs.rename(path.join(location, "RP/shader"), path.join(location, "RP/textures"));

    const mixed = await core.loadPack({ handle: bp, rel: "BP" });
    mixed.data.modules.push({ type: "resources" });
    mixed.sync();
    await assert.rejects(core.validatePackStructure([mixed]), /textures/);
    await fs.mkdir(path.join(location, "BP/textures"));
    await core.validatePackStructure([mixed]);
    await fs.rmdir(path.join(location, "BP/textures"));

    // Exercise the actual browser workflow, including saving each archive before cleanup.
    const outputLocation = await fs.mkdtemp(path.join(os.tmpdir(), "mczip-web-output-"));
    try {
      const output = new DirectoryHandle(outputLocation);
      const context = vm.createContext({
        MCZIP_CORE: core, TextEncoder, TextDecoder,
        document: { addEventListener() {} }, localStorage: { getItem: () => null },
        fixtureRoot: root, fixtureOutput: output,
      });
      vm.runInContext(await fs.readFile(path.join(__dirname, "../frontend/js/app.js"), "utf8"), context);
      vm.runInContext("state.fsRoot = fixtureRoot; state.fsOut = fixtureOutput;", context);
      const manifestPath = name => path.join(location, name, "manifest.json");
      const backupPath = name => manifestPath(name) + ".bak";
      // Every format must reject invalid folders before changing either manifest or backup.
      vm.runInContext("options.pack_with_bump = true; options.backup = true;", context);
      for (const [name, required] of [["BP", "entities"], ["RP", "textures"]]) {
        const requiredPath = path.join(location, name, required);
        await fs.rmdir(requiredPath);
        for (const shape of ["missing", "file", "nested"]) {
          if (shape === "file") await fs.writeFile(requiredPath, "not a directory");
          if (shape === "nested") await fs.mkdir(path.join(location, name, "nested", required), { recursive: true });
          for (const mode of ["auto", "mcaddon", "mcpack", "zip"]) {
            const before = new Map();
            for (const packName of ["BP", "RP"]) {
              before.set(packName, [await fs.readFile(manifestPath(packName)), await fs.readFile(backupPath(packName))]);
            }
            context.testMode = mode;
            vm.runInContext("options.pack_mode = testMode;", context);
            await assert.rejects(vm.runInContext("webDoPackage()", context), new RegExp(name + ".*" + required));
            for (const packName of ["BP", "RP"]) {
              assert.deepEqual(await fs.readFile(manifestPath(packName)), before.get(packName)[0]);
              assert.deepEqual(await fs.readFile(backupPath(packName)), before.get(packName)[1]);
            }
            assert.deepEqual(await fs.readdir(outputLocation), []);
          }
          if (shape === "file") await fs.unlink(requiredPath);
          if (shape === "nested") {
            await fs.rmdir(path.join(location, name, "nested", required));
            await fs.rmdir(path.join(location, name, "nested"));
          }
        }
        await fs.mkdir(requiredPath);
        await assert.rejects(core.validatePackStructure(core.selectPacks(packs).included,
          { outDirHandle: new DirectoryHandle(requiredPath) }), new RegExp(name));
      }
      for (const mode of ["auto", "mcaddon", "mcpack", "zip"]) {
        for (const doBump of [false, true]) {
          for (const backup of [false, true]) {
            const before = new Map();
            for (const name of ["BP", "RP"]) {
              before.set(name, await fs.readFile(manifestPath(name)));
              await fs.writeFile(backupPath(name), "stale backup");
              await fs.writeFile(path.join(location, name, "asset.bak"), "unrelated backup");
            }
            Object.assign(context, { testMode: mode, testBump: doBump, testBackup: backup });
            vm.runInContext("options.pack_mode = testMode; options.pack_with_bump = testBump; options.backup = testBackup;", context);
            const result = await vm.runInContext("webDoPackage()", context);
            assert(result.ok);
            for (const name of ["BP", "RP"]) {
              await assert.rejects(fs.stat(backupPath(name)), { code: "ENOENT" });
              assert.equal(await fs.readFile(path.join(location, name, "asset.bak"), "utf8"), "unrelated backup");
              const after = await fs.readFile(manifestPath(name));
              if (doBump) {
                const version = JSON.parse(before.get(name)).header.version;
                assert.deepEqual(JSON.parse(after).header.version, [version[0], version[1], version[2] + 1]);
              } else assert.deepEqual(after, before.get(name));
            }
            for (const archiveName of result.archives) {
              const files = unpack(await fs.readFile(path.join(outputLocation, archiveName)));
              assert(![...files.keys()].some(name => name.endsWith(".bak")));
            }
          }
        }
      }

      // Fail the second mcpack save at close: both backups must remain after a partial output.
      const beforeFailure = new Map();
      for (const name of ["BP", "RP"]) beforeFailure.set(name, await fs.readFile(manifestPath(name)));
      let saves = 0;
      context.fixtureOutput = {
        name: "Failed output",
        async getFileHandle(name, options) {
          const handle = await output.getFileHandle(name, options);
          if (++saves === 2) handle.createWritable = async () => ({
            write: async () => {}, close: async () => { throw new Error("disk full"); },
          });
          return handle;
        },
      };
      vm.runInContext('state.fsOut = fixtureOutput; options.pack_mode = "mcpack"; options.pack_with_bump = true; options.backup = true;', context);
      await assert.rejects(vm.runInContext("webDoPackage()", context), /disk full/);
      assert.equal(saves, 2);
      for (const name of ["BP", "RP"]) assert.deepEqual(await fs.readFile(backupPath(name)), beforeFailure.get(name));

      // Cancelling the save picker must also retain backups.
      context.showSaveFilePicker = async () => { throw new Error("save cancelled"); };
      vm.runInContext("state.fsOut = null; options.pack_with_bump = false;", context);
      await assert.rejects(vm.runInContext("webDoPackage()", context), /save cancelled/);
      for (const name of ["BP", "RP"]) assert.deepEqual(await fs.readFile(backupPath(name)), beforeFailure.get(name));

      context.fixtureOutput = output;
      vm.runInContext("state.fsOut = fixtureOutput; options.backup = false;", context);
      assert((await vm.runInContext("webDoPackage()", context)).ok);
      for (const name of ["BP", "RP"]) await assert.rejects(fs.stat(backupPath(name)), { code: "ENOENT" });

      // Cleanup is best effort and never recurses into a directory named like a backup.
      await fs.mkdir(backupPath("BP"));
      await fs.writeFile(backupPath("RP"), "stale backup");
      const warning = await vm.runInContext("webDoPackage()", context);
      assert(warning.ok);
      assert(warning.logs.some(([message, tag]) => tag === "warn" && message.includes("备份清理失败")));
      assert((await fs.stat(backupPath("BP"))).isDirectory());
      await assert.rejects(fs.stat(backupPath("RP")), { code: "ENOENT" });
      await fs.rmdir(backupPath("BP"));
      for (const name of ["Skin", "docs/ExampleBP"]) assert.equal(await fs.readFile(backupPath(name), "utf8"), "backup");

      // The demo uses its own directory handles, which must support the same cleanup.
      const demo = await vm.runInContext("buildDemoAddon()", context);
      const demoPacks = await core.loadAddon(demo);
      const demoResult = await core.buildPackage(demo, demo.name, demoPacks);
      assert(unpack(demoResult.archives[0].bytes).has("DemoBP/entities/"));
      assert(unpack(demoResult.archives[0].bytes).has("DemoRP/textures/"));
      const demoLogs = await core.cleanupManifestBackups(demoPacks);
      assert(!demoLogs.some(([, tag]) => tag === "warn"));
      await assert.rejects(demoPacks.find(p => p.relName === "DemoBP").dirHandle.getFileHandle(core.BACKUP_NAME), { name: "NotFoundError" });
    } finally {
      await fs.rm(outputLocation, { recursive: true, force: true });
    }
    console.log("WEB PASSED: directory validation before changes, empty folders, shader support, ZIP CRCs, 16 packaging options, backups, failures, demo and BP/RP scope.");
  } finally {
    await fs.rm(location, { recursive: true, force: true });
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
