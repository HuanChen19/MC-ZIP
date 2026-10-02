"use strict";
// Real ZIP contents and CRCs are checked, independently of the UI rendering.
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { inflateRawSync } = require("node:zlib");
const core = require("../frontend/js/core.js");

class FileHandle {
  constructor(location) { this.location = location; this.kind = "file"; this.name = path.basename(location); }
  async getFile() {
    const bytes = await fs.readFile(this.location), stat = await fs.stat(this.location);
    return { arrayBuffer: () => new Blob([bytes]).arrayBuffer(), lastModified: stat.mtimeMs };
  }
}
class DirectoryHandle {
  constructor(location) { this.location = location; this.kind = "directory"; this.name = path.basename(location); }
  async getFileHandle(name) {
    const location = path.join(this.location, name);
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
    console.log("WEB CORE PASSED: 8 mixed-addon modes, 4 rejected skin modes, single BP, ZIP CRCs and UTF-8.");
  } finally {
    await fs.rm(location, { recursive: true, force: true });
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
