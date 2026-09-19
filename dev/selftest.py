# -*- coding: utf-8 -*-
"""
selftest.py —— addon_core 自测脚本（不依赖 GUI）

构造一个含 行为包 + 资源包 的假 Addon，验证：
  1. 包扫描
  2. 版本号递增 0.0.1 -> 0.0.2（含 modules 选项）
  3. UUID 刷新（header + modules），dependencies 保持不变
  4. 键顺序、description、min_engine_version 等其他配置不被影响
  5. 打包产物结构与内容正确，备份/缓存文件被排除
"""
from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import addon_config  # noqa: E402
import addon_core as core  # noqa: E402

BASE = Path(__file__).resolve().parent.parent / ".build" / "selftest"
PASSED: list = []
FAILED: list = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(label)
        print(f"  [OK]   {label}")
    else:
        FAILED.append(f"{label} {detail}")
        print(f"  [FAIL] {label} {detail}")


BP_MANIFEST = """{
  "format_version": 2,
  "header": {
    "name": "测试行为包",
    "description": "中文描述，需要保持原样 \\n 与转义",
    "uuid": "11111111-2222-3333-4444-555555555555",
    "version": [0, 0, 1],
    "min_engine_version": [1, 20, 30]
  },
  "modules": [
    {
      "type": "data",
      "uuid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
      "version": [0, 0, 1]
    },
    {
      "type": "script",
      "language": "javascript",
      "uuid": "99999999-8888-7777-6666-555555555555",
      "entry": "scripts/main.js",
      "version": [0, 0, 1]
    }
  ],
  "dependencies": [
    {
      "uuid": "deadbeef-dead-beef-dead-beefdeadbeef",
      "version": [1, 0, 0]
    },
    {
      "module_name": "@minecraft/server",
      "version": "1.11.0"
    }
  ],
  "metadata": {
    "authors": ["Tester"],
    "license": "MIT"
  }
}
"""

RP_MANIFEST = """{
  "format_version": 2,
  "header": {
    "name": "Test RP",
    "description": "resource pack",
    "uuid": "12121212-3434-5656-7878-909090909090",
    "version": [1, 2, 3]
  },
  "modules": [
    {
      "type": "resources",
      "uuid": "0f0f0f0f-1e1e-2d2d-3c3c-4b4b4b4b4b4b",
      "version": [1, 2, 3]
    }
  ]
}
"""

SKIN_MANIFEST = """{
  "format_version": 2,
  "header": {
    "name": "Test Skin",
    "description": "skin pack",
    "uuid": "abababab-cdcd-efef-0101-232323232323",
    "version": [1, 0, 0]
  },
  "modules": [
    {
      "type": "skin_pack",
      "uuid": "cdcdcdcd-efef-0101-2323-454545454545",
      "version": [1, 0, 0]
    }
  ]
}
"""


def build_fixture() -> Path:
    """
    构造（或重建）测试用 Addon。

    刻意做成幂等：只覆盖写入、不整目录删除，
    这样重复运行不会触发沙箱的大量删除保护。
    """
    root = BASE / "MyAddon"
    bp = root / "MyAddon_BP"
    rp = root / "MyAddon_RP"
    skin = root / "MyAddon_Skin"

    for folder in (bp / "functions", bp / "scripts", rp / "textures" / "items",
                   skin / "skin", bp / "__pycache__", bp / ".git",
                   root / "docs", root / "tools"):
        folder.mkdir(parents=True, exist_ok=True)

    (bp / "manifest.json").write_text(BP_MANIFEST, encoding="utf-8")
    (rp / "manifest.json").write_text(RP_MANIFEST, encoding="utf-8")
    (skin / "manifest.json").write_text(SKIN_MANIFEST, encoding="utf-8")
    (bp / "functions" / "main.mcfunction").write_text("say hello\n", encoding="utf-8")
    (bp / "scripts" / "main.js").write_text("console.log(1);\n", encoding="utf-8")
    (bp / "pack_icon.png").write_bytes(b"\x89PNG\r\n\x1a\nBP")
    (rp / "pack_icon.png").write_bytes(b"\x89PNG\r\n\x1a\nRP")
    (rp / "textures" / "items" / "剑.png").write_bytes(b"\x89PNG-JIAN")
    (skin / "skin" / "default.png").write_bytes(b"\x89PNG-SKIN")

    # 非包内容：不应进入 .mcaddon
    (root / "docs" / "说明.md").write_text("# 文档\n", encoding="utf-8")
    (root / "tools" / "build.py").write_text("print('build')\n", encoding="utf-8")
    (root / "readme.txt").write_text("readme\n", encoding="utf-8")

    # 应当被排除的内容
    (bp / "__pycache__" / "main.cpython-310.pyc").write_bytes(b"junk")
    (bp / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    (bp / "Thumbs.db").write_bytes(b"junk")
    (bp / "manifest.json.bak").write_text("old", encoding="utf-8")
    return root


def make_copy(src: Path, dst: Path) -> Path:
    """复制一份包目录（幂等，目录已存在则覆盖）。"""
    shutil.copytree(src, dst, dirs_exist_ok=True)
    return dst


def main() -> int:
    print("== 1. 构造测试 Addon ==")
    root = build_fixture()
    print(f"  root = {root}")

    print("== 2. 扫描包 ==")
    packs = core.load_addon(root)
    check("扫描到 3 个包", len(packs) == 3, f"实际 {len(packs)}")
    kinds = sorted(p.kind for p in packs)
    check("包类型识别正确", kinds == ["皮肤包", "行为包", "资源包"], str(kinds))
    bp = next(p for p in packs if p.root.name.endswith("_BP"))
    rp = next(p for p in packs if p.root.name.endswith("_RP"))
    skin = next(p for p in packs if p.root.name.endswith("_Skin"))
    check("BP 名称解析", bp.name == "测试行为包", bp.name)
    check("version 解析", bp.version == [0, 0, 1] and rp.version == [1, 2, 3])
    check("BP 归类为 bp", bp.category == "bp" and bp.is_bp_rp, bp.category)
    check("RP 归类为 rp", rp.category == "rp" and rp.is_bp_rp, rp.category)
    check("皮肤包归类为 other 且非 BP/RP", skin.category == "other" and not skin.is_bp_rp, skin.category)

    print("== 2b. BP / RP 筛选 ==")
    included, excluded = core.select_packs(packs, only_bp_rp=True, root=root)
    check("筛选后保留 2 个", len(included) == 2, str([p.root.name for p in included]))
    check("筛选后排除 1 个", len(excluded) == 1 and excluded[0] is skin,
          str([p.root.name for p in excluded]))
    check("排除的是皮肤包", {p.root.name for p in included} == {"MyAddon_BP", "MyAddon_RP"},
          str({p.root.name for p in included}))
    all_in, none_out = core.select_packs(packs, only_bp_rp=False, root=root)
    check("关闭筛选时全部保留", len(all_in) == 3 and not none_out)
    single_only, single_excluded = core.select_packs([skin], only_bp_rp=True, root=skin.root)
    check("单包模式不做筛选（用户已指定目录）",
          len(single_only) == 1 and not single_excluded)

    print("== 3. 版本号递增 ==")
    rep_bp = core.bump_versions(bp, part="patch", include_modules=False, backup=True)
    for line in rep_bp.messages:
        print("    " + line)
    check("BP header.version 0.0.1 -> 0.0.2", bp.version == [0, 0, 2], str(bp.version))
    check("备份文件已生成", rep_bp.backup is not None and rep_bp.backup.is_file())

    disk_bp = json.loads((bp.manifest_path).read_text(encoding="utf-8"))
    check("磁盘 header.version", disk_bp["header"]["version"] == [0, 0, 2], str(disk_bp["header"]["version"]))
    check("modules 未被升级", disk_bp["modules"][0]["version"] == [0, 0, 1], str(disk_bp["modules"][0]["version"]))
    check("description 未被改动",
          disk_bp["header"]["description"] == "中文描述，需要保持原样 \n 与转义",
          repr(disk_bp["header"]["description"]))
    check("min_engine_version 未被改动", disk_bp["header"]["min_engine_version"] == [1, 20, 30])
    check("metadata 未被改动", disk_bp["metadata"] == {"authors": ["Tester"], "license": "MIT"})
    check("键顺序保持", list(disk_bp.keys()) == ["format_version", "header", "modules", "dependencies", "metadata"],
          str(list(disk_bp.keys())))
    check("header 键顺序保持",
          list(disk_bp["header"].keys()) == ["name", "description", "uuid", "version", "min_engine_version"],
          str(list(disk_bp["header"].keys())))

    rep_rp = core.bump_versions(rp, part="minor", include_modules=True, backup=False)
    for line in rep_rp.messages:
        print("    " + line)
    check("RP minor 升级且补丁归零", rp.version == [1, 3, 0], str(rp.version))
    check("RP modules 同步升级", rp.data["modules"][0]["version"] == [1, 3, 0],
          str(rp.data["modules"][0]["version"]))
    check("皮肤包版本未被改动", skin.version == [1, 0, 0], str(skin.version))

    print("== 4. UUID 刷新 ==")
    old_bp_uuid = bp.pack_uuid
    old_mod_uuids = list(bp.module_uuids)
    old_dep = json.dumps(bp.data["dependencies"], ensure_ascii=False, sort_keys=True)
    rep_uuid = core.refresh_uuids(bp, backup=False)
    for line in rep_uuid.messages:
        print("    " + line)
    check("header.uuid 已改变", bp.pack_uuid != old_bp_uuid)
    check("header.uuid 格式合法", len(bp.pack_uuid) == 36 and bp.pack_uuid.count("-") == 4, bp.pack_uuid)
    check("modules uuid 全部改变", all(a != b for a, b in zip(old_mod_uuids, bp.module_uuids)))
    check("modules uuid 互不相同", len(set(bp.module_uuids)) == len(bp.module_uuids))
    check("BP/RP uuid 不冲突", bp.pack_uuid != rp.pack_uuid)
    check("dependencies 完全未被改动",
          json.dumps(bp.data["dependencies"], ensure_ascii=False, sort_keys=True) == old_dep)

    print("== 5. 打包（默认只打包 BP / RP）==")
    out_dir = BASE / "dist"
    result = core.build_package(root, packs, out_dir, mode="auto")
    check("生成 1 个 .mcaddon", len(result.archives) == 1 and result.archives[0].suffix == ".mcaddon",
          str(result.archives))
    archive = result.archives[0]
    print(f"    产物: {archive.name}  ({archive.stat().st_size} 字节, {result.file_count} 个文件)")
    check("产物文件名符合最高版本", archive.name == "MyAddon_v1.3.0.mcaddon", archive.name)
    check("结果记录了被排除的包", result.excluded == ["MyAddon_Skin"], str(result.excluded))
    check("结果记录了包含的包", sorted(result.included) == ["MyAddon_BP", "MyAddon_RP"],
          str(result.included))

    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        check("含 BP manifest", "MyAddon_BP/manifest.json" in names)
        check("含 RP manifest", "MyAddon_RP/manifest.json" in names)
        check("含中文文件名（UTF-8）", "MyAddon_RP/textures/items/剑.png" in names)
        check("含 pack_icon", "MyAddon_BP/pack_icon.png" in names and "MyAddon_RP/pack_icon.png" in names)

        # 只保留 BP / RP —— 其他内容一律不打包
        check("排除皮肤包 manifest", "MyAddon_Skin/manifest.json" not in names)
        check("排除皮肤包目录", not any(n.startswith("MyAddon_Skin") for n in names))
        check("排除 docs 目录", not any(n.startswith("docs") for n in names))
        check("排除 tools 目录", not any(n.startswith("tools") for n in names))
        check("排除根目录散落文件", "readme.txt" not in names)
        check("压缩包内只有 BP/RP 两个顶层目录",
              {n.split("/")[0] for n in names} == {"MyAddon_BP", "MyAddon_RP"},
              str({n.split("/")[0] for n in names}))

        # 包内噪音仍被排除
        check("排除 __pycache__", not any("__pycache__" in n for n in names))
        check("排除 .git", not any(n.startswith("MyAddon_BP/.git") for n in names))
        check("排除 Thumbs.db", not any(n.endswith("Thumbs.db") for n in names))
        check("排除 .bak", not any(n.endswith(".bak") for n in names))
        check("排除 .pyc", not any(n.endswith(".pyc") for n in names))

        packed_bp = json.loads(zf.read("MyAddon_BP/manifest.json").decode("utf-8"))
        check("包内 BP 版本为 0.0.2", packed_bp["header"]["version"] == [0, 0, 2])
        check("包内 BP uuid 为刷新后的值", packed_bp["header"]["uuid"] == bp.pack_uuid)
        check("包内 BP description 原样", packed_bp["header"]["description"] == "中文描述，需要保持原样 \n 与转义")

    print("== 5b. 关闭筛选时打包全部类型 ==")
    all_dir = BASE / "dist_all"
    result_all = core.build_package(root, packs, all_dir, mode="auto", only_bp_rp=False)
    check("关闭筛选后无排除项", not result_all.excluded, str(result_all.excluded))
    check("关闭筛选产物名正确", result_all.archives[0].name == "MyAddon_v1.3.0.mcaddon",
          result_all.archives[0].name)
    with zipfile.ZipFile(result_all.archives[0]) as zf:
        names_all = zf.namelist()
        check("关闭筛选后含皮肤包", "MyAddon_Skin/manifest.json" in names_all)
        check("关闭筛选仍含 BP/RP", "MyAddon_BP/manifest.json" in names_all)
        check("关闭筛选也不含 docs", not any(n.startswith("docs") for n in names_all))

    print("== 5c. 全被过滤时给出明确错误 ==")
    only_skin_dir = BASE / "SkinOnly"
    make_copy(root / "MyAddon_Skin", only_skin_dir / "SkinA")
    make_copy(root / "MyAddon_Skin", only_skin_dir / "SkinB")
    try:
        core.build_package(only_skin_dir, core.load_addon(only_skin_dir), BASE / "dist_skin",
                           mode="auto", only_bp_rp=True)
        check("全被过滤时抛出异常", False, "未抛出 ManifestError")
    except core.ManifestError as exc:
        check("全被过滤时抛出异常", "BP" in str(exc), str(exc).replace("\n", " "))

    print("== 6. 单包 .mcpack 模式 ==")
    single_dir = BASE / "StandalonePack"
    make_copy(root / "MyAddon_RP", single_dir)
    single_packs = core.load_addon(single_dir)
    single_result = core.build_package(single_dir, single_packs, out_dir, mode="auto",
                                       base_name="StandalonePack")
    check("生成 .mcpack", single_result.archives[0].suffix == ".mcpack", str(single_result.archives))
    check("单包产物名正确", single_result.archives[0].name == "StandalonePack_v1.3.0.mcpack",
          single_result.archives[0].name)
    with zipfile.ZipFile(single_result.archives[0]) as zf:
        check("mcpack 的 manifest 在根目录", "manifest.json" in zf.namelist(), str(zf.namelist()[:5]))

    print("== 6b. 单包模式即使是皮肤包也不过滤 ==")
    skin_dir = BASE / "StandaloneSkin"
    make_copy(root / "MyAddon_Skin", skin_dir)
    skin_packs = core.load_addon(skin_dir)
    skin_result = core.build_package(skin_dir, skin_packs, out_dir, mode="auto",
                                     base_name="StandaloneSkin")
    check("皮肤包单包也能打包", skin_result.archives[0].suffix == ".mcpack"
          and not skin_result.excluded, str(skin_result.archives))
    check("皮肤包产物名正确", skin_result.archives[0].name == "StandaloneSkin_v1.0.0.mcpack",
          skin_result.archives[0].name)

    print("== 7. 重复打包不污染 ==")
    again = core.build_package(root, packs, out_dir, mode="auto")
    check("二次打包输出目录未被压缩进去",
          not any("_dist" in n for n in zipfile.ZipFile(again.archives[0]).namelist()))

    print("== 8. 配置持久化（最近打开的项目 / 选项偏好）==")
    cfg_path = BASE / "config_test" / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("{}", encoding="utf-8")   # 重置为初始状态，保证可重复运行
    cfg = addon_config.Config(path=cfg_path)
    check("首次读取为空", cfg.recent_folders == [] and cfg.last_folder == "")
    check("选项有默认值", cfg.get_option("only_bp_rp") is True
          and cfg.get_option("version_part") == "patch", str(cfg.options))

    a = str(root)
    b = str(BASE / "AnotherProject")
    Path(b).mkdir(parents=True, exist_ok=True)
    cfg.remember_folder(a)
    cfg.remember_folder(b)
    check("最近列表置顶", cfg.recent_folders[0] == b, str(cfg.recent_folders))
    check("last_folder 跟随更新", cfg.last_folder == b, cfg.last_folder)

    cfg.remember_folder(a)
    check("重复打开不会产生重复项", len(cfg.recent_folders) == 2, str(cfg.recent_folders))
    check("重复打开后置顶", cfg.recent_folders[0] == a, str(cfg.recent_folders))

    # 大小写 / 斜杠差异应视为同一个项目
    cfg.remember_folder(a.upper())
    check("路径大小写不敏感去重", len(cfg.recent_folders) == 2, str(cfg.recent_folders))

    for i in range(addon_config.MAX_RECENT + 5):
        cfg.remember_folder(str(BASE / f"proj{i}"))
    check(f"最近列表不超过 {addon_config.MAX_RECENT} 条",
          len(cfg.recent_folders) == addon_config.MAX_RECENT, str(len(cfg.recent_folders)))

    cfg.set_output_dir(a, r"D:\out\demo")
    cfg.set_options({"version_part": "minor", "only_bp_rp": False, "backup": False})
    check("配置写入成功", cfg.save(), cfg.last_error)
    check("配置文件已生成", cfg_path.is_file())

    reloaded = addon_config.Config(path=cfg_path)
    check("重新读回最近列表", reloaded.recent_folders == cfg.recent_folders)
    check("重新读回输出目录", reloaded.output_dir_for(a) == r"D:\out\demo",
          str(reloaded.output_dir_for(a)))
    check("重新读回选项", reloaded.get_option("version_part") == "minor"
          and reloaded.get_option("only_bp_rp") is False
          and reloaded.get_option("backup") is False, str(reloaded.options))
    check("未知选项不会丢失默认值", reloaded.get_option("uuid_modules") is True)

    reloaded.forget_folder(a)
    check("移除单个项目", a not in reloaded.recent_folders, str(reloaded.recent_folders))
    reloaded.clear_recents()
    check("清空最近列表", reloaded.recent_folders == [] and reloaded.last_folder == "")

    check("existing_recents 只返回真实存在的目录",
          reloaded.existing_recents() == [], str(reloaded.existing_recents()))
    cfg.remember_folder(a)
    check("existing_recents 能识别存在的目录",
          cfg.existing_recents() == [a], str(cfg.existing_recents()))

    # 损坏的配置文件应能优雅回退
    broken = BASE / "config_test" / "broken.json"
    broken.write_text("{ this is not json", encoding="utf-8")
    broken_cfg = addon_config.Config(path=broken)
    check("损坏配置回退到默认值", broken_cfg.recent_folders == []
          and broken_cfg.get_option("only_bp_rp") is True, broken_cfg.last_error)
    check("损坏配置有错误提示", bool(broken_cfg.last_error), broken_cfg.last_error)

    print()
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        for item in FAILED:
            print("  FAILED: " + item)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
