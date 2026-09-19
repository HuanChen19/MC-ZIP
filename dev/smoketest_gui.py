# -*- coding: utf-8 -*-
"""
smoketest_gui.py —— GUI 冒烟测试

真实创建 tkinter 窗口，pump 事件循环，走完整的：
    导入文件夹 -> 打包并升级版本 -> 随机刷新 UUID -> 还原备份
并断言产物存在、manifest 内容正确。

运行：python smoketest_gui.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import zipfile
from pathlib import Path

DEV_DIR = Path(__file__).resolve().parent
ROOT_DIR = DEV_DIR.parent
sys.path.insert(0, str(DEV_DIR))
sys.path.insert(0, str(ROOT_DIR))
from selftest import BASE, build_fixture  # noqa: E402
import addon_config  # noqa: E402
import addon_core as core  # noqa: E402
import addon_packer as gui  # noqa: E402


def pump(app: gui.AddonPackerApp, seconds: float = 30.0) -> None:
    """驱动事件循环直到后台任务结束。"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.root.update()
        if not app.busy and app.task_queue.empty():
            # 多泵几帧，确保 finish 回调执行完
            for _ in range(3):
                app.root.update()
                time.sleep(0.02)
            if not app.busy:
                return
        time.sleep(0.02)
    raise TimeoutError("GUI 操作超时")


def pump_fixed(app: gui.AddonPackerApp, seconds: float) -> None:
    """固定时长地驱动事件循环（用于等待 after() 定时回调）。"""
    end = time.time() + seconds
    while time.time() < end:
        app.root.update()
        time.sleep(0.02)


def main() -> int:
    # 说明：全程只覆盖写入、不删除文件，重复运行不会触发沙箱的批量删除保护
    root = build_fixture()
    out_dir = root.parent / "gui_dist"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 配置指向测试专用文件（首次运行清掉，保证从默认状态开始）
    cfg_path = BASE / "gui_config.json"
    if cfg_path.exists():
        cfg_path.unlink()
    os.environ[addon_config.ENV_OVERRIDE] = str(cfg_path)
    app = gui.AddonPackerApp()
    app.root.update()
    print("窗口创建成功：", app.root.title())
    print("拖拽支持:", gui.HAS_DND)
    assert app.config.path == cfg_path, app.config.path

    # 1) 导入
    app.import_folder(str(root))
    pump(app, 5)
    print(f"packs={len(app.packs)}  status={app.var_status.get()}")
    assert len(app.packs) == 3, app.packs
    assert len(app.tree.get_children()) == 3
    # 表格里应体现「哪些会被打包」
    rows = {app.tree.item(i, "values")[0]: app.tree.item(i, "values")[1]
            for i in app.tree.get_children()}
    print("打包标记:", rows)
    assert rows["MyAddon_BP"] == "打包" and rows["MyAddon_RP"] == "打包"
    assert rows["MyAddon_Skin"] == "跳过", rows
    assert app.tree.tag_has("skipped", "3"), "皮肤包行应带 skipped 标记"

    # 1b) 导入后应记住这个项目
    assert cfg_path.is_file(), "应生成配置文件"
    assert app.config.last_folder == str(root), app.config.last_folder
    assert app.config.recent_folders[0] == str(root), app.config.recent_folders
    assert str(root) in list(app.cmb_path.cget("values")), app.cmb_path.cget("values")
    print("已记住最近项目:", app.config.recent_folders)

    # 指定输出目录，避免污染 fixture 默认位置
    app.output_dir = out_dir
    app.var_out.set(str(out_dir))

    # 2) 打包 + 升级版本
    before = {p.root.name: p.display_version for p in app.packs}
    assert set(before.values()) == {"0.0.1", "1.2.3", "1.0.0"}, before
    app.do_package()
    pump(app)
    after = {p.root.name: p.display_version for p in app.packs}
    print("版本:", before, "->", after)
    assert after["MyAddon_BP"] == "0.0.2", after
    assert after["MyAddon_RP"] == "1.2.4", after
    assert after["MyAddon_Skin"] == "1.0.0", "皮肤包不应被改动"

    archives = list(out_dir.glob("*.mcaddon"))
    assert len(archives) == 1, archives
    assert archives[0].name == "MyAddon_v1.2.4.mcaddon", archives[0].name
    print("产物:", archives[0].name, archives[0].stat().st_size, "字节")
    with zipfile.ZipFile(archives[0]) as zf:
        names = zf.namelist()
        assert "MyAddon_BP/manifest.json" in names
        data = json.loads(zf.read("MyAddon_BP/manifest.json").decode("utf-8"))
        assert data["header"]["version"] == [0, 0, 2]
        assert data["metadata"] == {"authors": ["Tester"], "license": "MIT"}
        # 只保留 BP / RP
        assert not any(n.startswith("MyAddon_Skin") for n in names), names
        assert not any(n.startswith("docs") for n in names), names
        assert not any(n.startswith("tools") for n in names), names
        assert {n.split("/")[0] for n in names} == {"MyAddon_BP", "MyAddon_RP"}, names

    # 3) 随机刷新 UUID（只作用于 BP / RP）
    old_uuid = {p.root.name: p.pack_uuid for p in app.packs}
    app.do_refresh_uuid()
    pump(app)
    new_uuid = {p.root.name: p.pack_uuid for p in app.packs}
    print("UUID:", old_uuid)
    print("   ->", new_uuid)
    assert new_uuid["MyAddon_BP"] != old_uuid["MyAddon_BP"]
    assert new_uuid["MyAddon_RP"] != old_uuid["MyAddon_RP"]
    assert new_uuid["MyAddon_Skin"] == old_uuid["MyAddon_Skin"], "皮肤包 UUID 不应被改动"

    on_disk = json.loads((root / "MyAddon_BP" / "manifest.json").read_text(encoding="utf-8"))
    assert on_disk["header"]["uuid"] == new_uuid["MyAddon_BP"]
    assert on_disk["header"]["description"] == "中文描述，需要保持原样 \n 与转义"
    assert on_disk["dependencies"][0]["uuid"] == "deadbeef-dead-beef-dead-beefdeadbeef"
    assert list(on_disk.keys()) == ["format_version", "header", "modules", "dependencies", "metadata"]

    # 4) 还原备份
    backup = root / "MyAddon_BP" / core.BACKUP_NAME
    assert backup.is_file(), backup

    # 4b) 切换筛选开关后表格应重绘
    app.var_only_bp_rp.set(False)
    app._on_filter_changed()
    app.root.update()
    rows2 = {app.tree.item(i, "values")[0]: app.tree.item(i, "values")[1]
             for i in app.tree.get_children()}
    print("关闭筛选后:", rows2)
    assert all(v == "打包" for v in rows2.values()), rows2
    assert not app.tree.tag_has("skipped", "3")
    app.var_only_bp_rp.set(True)
    app._on_filter_changed()
    app.root.update()

    # 5) 关闭后再单包模式
    app.root.update()
    app._on_close()

    single = root / "MyAddon_RP"
    app2 = gui.AddonPackerApp()
    app2.root.update()
    app2.import_folder(str(single))
    pump(app2, 5)
    assert len(app2.packs) == 1, [p.root for p in app2.packs]
    app2.output_dir = out_dir
    app2.do_package()
    pump(app2)
    packs = list(out_dir.glob("*.mcpack"))
    assert len(packs) == 1, list(out_dir.iterdir())
    assert packs[0].name == "MyAddon_RP_v1.2.5.mcpack", packs[0].name
    with zipfile.ZipFile(packs[0]) as zf:
        assert "manifest.json" in zf.namelist()
    print("单包产物:", packs[0].name)
    app2._on_close()

    # 6) 新开一个实例：应自动打开上次的项目，并沿用保存的选项
    saved = addon_config.Config(path=cfg_path)
    print("最近记录:", saved.recent_folders)
    assert saved.last_folder == str(single), saved.last_folder
    assert saved.recent_folders[0] == str(single), saved.recent_folders
    assert saved.output_dir_for(single) == str(out_dir), saved.output_dir_for(single)

    app3 = gui.AddonPackerApp()
    app3.root.update()
    pump_fixed(app3, 1.0)          # 等待 after(150) 的自动打开
    print("自动打开:", app3.addon_root)
    assert app3.addon_root == single, f"{app3.addon_root} != {single}"
    assert len(app3.packs) == 1, app3.packs
    assert app3.var_only_bp_rp.get() is True, "选项应沿用上次保存的值"

    # 6b) 改选项后重启应生效
    app3.var_only_bp_rp.set(False)
    app3.var_version_part.set(core.VERSION_PART_LABELS["minor"])
    pump_fixed(app3, 0.2)
    app3._on_close()

    app4 = gui.AddonPackerApp()
    app4.root.update()
    pump_fixed(app4, 1.0)
    print("重启后选项:", app4.var_only_bp_rp.get(), app4.var_version_part.get())
    assert app4.var_only_bp_rp.get() is False, "选项未持久化"
    assert app4.var_version_part.get() == core.VERSION_PART_LABELS["minor"]

    # 6c) 关闭自动打开后不应再自动载入
    app4.var_auto_load.set(False)
    pump_fixed(app4, 0.2)
    app4._on_close()

    app5 = gui.AddonPackerApp()
    app5.root.update()
    pump_fixed(app5, 1.0)
    assert app5.addon_root is None, f"关闭自动打开后不应载入 {app5.addon_root}"
    assert app5.packs == []
    # 下拉框里仍应有历史记录可供选择
    values = list(app5.cmb_path.cget("values"))
    print("最近下拉项:", values)
    assert str(single) in values, values
    assert str(root) in values, values
    app5._on_close()

    print("\nGUI 冒烟测试全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
