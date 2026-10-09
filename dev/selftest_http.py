"""Exercise the local HTTP API and inspect its actual archives."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import addon_config
import mczip_web


def main() -> None:
    previous_config = os.environ.get(addon_config.ENV_OVERRIDE)
    try:
        with tempfile.TemporaryDirectory(prefix="mczip-http-test-") as directory:
            base = Path(directory)
            os.environ[addon_config.ENV_OVERRIDE] = str(base / "config.json")
            addon = base / "Addon"
            for name, module_type in (("BP", "data"), ("RP", "resources"),
                                      ("Skin", "skin_pack"), ("docs/ExampleBP", "data")):
                pack = addon / name
                pack.mkdir(parents=True)
                if name == "BP":
                    (pack / "entities").mkdir()
                elif name == "RP":
                    (pack / "shader").mkdir()
                manifest = {"format_version": 2, "header": {
                    "name": name, "uuid": "11111111-1111-4111-8111-111111111111",
                    "version": [1, 0, 0]}, "modules": [{"type": module_type,
                    "uuid": "22222222-2222-4222-8222-222222222222", "version": [1, 0, 0]}]}
                (pack / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
                (pack / "asset.txt").write_text("resource", encoding="utf-8")
                (pack / "manifest.json.bak").write_bytes(b"old backup")
                (pack / "asset.bak").write_bytes(b"unrelated backup")
            (addon / "readme.txt").write_text("excluded", encoding="utf-8")
            skin_manifest = addon / "Skin" / "manifest.json"
            before_skin = skin_manifest.read_bytes()
            api = mczip_web.BridgeApi()
            server, port = mczip_web.make_server(api, ROOT / "frontend")

            def request(method, payload=None):
                body = json.dumps(payload).encode("utf-8") if payload is not None else None
                req = urllib.request.Request("http://127.0.0.1:%s/api/%s" % (port, method),
                    data=body, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=10) as response:
                    return json.load(response)

            try:
                imported = request("import_folder", {"folder": str(addon)})
                assert imported["ok"] and len(imported["packs"]) == 3, imported
                assert sum(row["packed"] for row in imported["packs"]) == 2
                viewer = request("get_manifest_text", {"index": 0})
                assert json.loads(viewer["text"])["header"]["name"] == "BP", viewer
                request("set_options", {"only_bp_rp": False})
                assert request("get_state")["options"]["only_bp_rp"] is True
                api.output_dir = base / "validation-output"
                for pack_name, required_name in (("BP", "entities"), ("RP", "shader")):
                    required_dir = addon / pack_name / required_name
                    required_dir.rmdir()
                    for shape in ("missing", "file", "nested"):
                        if shape == "file":
                            required_dir.write_text("not a directory", encoding="utf-8")
                        elif shape == "nested":
                            (required_dir.parent / "nested" / required_name).mkdir(parents=True)
                        for mode in ("auto", "mcaddon", "mcpack", "zip"):
                            for do_bump in (False, True):
                                before = {name: ((addon / name / "manifest.json").read_bytes(),
                                                (addon / name / "manifest.json.bak").read_bytes())
                                          for name in ("BP", "RP")}
                                invalid = request("do_package", {"pack_mode": mode,
                                    "pack_with_bump": do_bump, "backup": True})
                                assert not invalid["ok"] and any(pack_name in entry[0]
                                    and required_name in entry[0] for entry in invalid["logs"]), invalid
                                assert not api.output_dir.exists()
                                for name in ("BP", "RP"):
                                    assert (addon / name / "manifest.json").read_bytes() == before[name][0]
                                    assert (addon / name / "manifest.json.bak").read_bytes() == before[name][1]
                        if shape == "file":
                            required_dir.unlink()
                        elif shape == "nested":
                            (required_dir.parent / "nested" / required_name).rmdir()
                            (required_dir.parent / "nested").rmdir()
                    required_dir.mkdir()
                for mode in ("auto", "mcaddon", "mcpack", "zip"):
                    for do_bump in (False, True):
                        for backup in (False, True):
                            before = {}
                            for pack_name in ("BP", "RP"):
                                pack_dir = addon / pack_name
                                before[pack_name] = (pack_dir / "manifest.json").read_bytes()
                                (pack_dir / "manifest.json.bak").write_bytes(b"stale backup")
                            packaged = request("do_package", {"pack_mode": mode,
                                "only_bp_rp": False, "pack_with_bump": do_bump,
                                "backup": backup})
                            assert packaged["ok"], packaged
                            for pack_name in ("BP", "RP"):
                                pack_dir = addon / pack_name
                                assert not (pack_dir / "manifest.json.bak").exists()
                                assert (pack_dir / "asset.bak").read_bytes() == b"unrelated backup"
                                current = (pack_dir / "manifest.json").read_bytes()
                                if do_bump:
                                    old_version = json.loads(before[pack_name])["header"]["version"]
                                    assert json.loads(current)["header"]["version"] == [
                                        old_version[0], old_version[1], old_version[2] + 1]
                                else:
                                    assert current == before[pack_name]
                            for name in packaged["archives"]:
                                with zipfile.ZipFile(Path(packaged["outputDir"]) / name) as archive:
                                    assert archive.testzip() is None
                                    entries = archive.namelist()
                                    assert not any(entry.endswith(".bak") for entry in entries)
                                    if mode == "mcpack":
                                        assert "manifest.json" in entries
                                        required = ["entities/" if name.startswith("BP_") else "shader/"]
                                    else:
                                        assert {n.split("/")[0] for n in entries} == {"BP", "RP"}
                                        required = ["BP/entities/", "RP/shader/"]
                                    assert all(archive.getinfo(entry).is_dir() for entry in required)

                # A failed output write must leave the exact pre-change manifests recoverable.
                before_failure = {name: (addon / name / "manifest.json").read_bytes()
                                  for name in ("BP", "RP")}
                blocked_output = base / "blocked-output"
                blocked_output.write_bytes(b"not a directory")
                api.output_dir = blocked_output
                failed = request("do_package", {"pack_mode": "mcpack",
                    "pack_with_bump": True, "backup": True})
                assert not failed["ok"], failed
                for pack_name in ("BP", "RP"):
                    assert (addon / pack_name / "manifest.json.bak").read_bytes() == before_failure[pack_name]
                assert request("restore_backup")["ok"]
                for pack_name in ("BP", "RP"):
                    assert (addon / pack_name / "manifest.json").read_bytes() == before_failure[pack_name]
                api.output_dir = base / "recovered-output"
                assert request("do_package", {"pack_with_bump": False, "backup": False})["ok"]
                assert not any((addon / name / "manifest.json.bak").exists() for name in ("BP", "RP"))

                # A cleanup error warns without discarding an already saved archive or stopping other packs.
                blocked_backup = addon / "BP" / "manifest.json.bak"
                blocked_backup.mkdir()
                (addon / "RP" / "manifest.json.bak").write_bytes(b"stale backup")
                cleanup_warning = request("do_package", {"pack_with_bump": False, "backup": False})
                assert cleanup_warning["ok"], cleanup_warning
                assert any(len(entry) > 1 and entry[1] == "warn" and "备份清理失败" in entry[0]
                           for entry in cleanup_warning["logs"])
                assert blocked_backup.is_dir()
                assert not (addon / "RP" / "manifest.json.bak").exists()
                blocked_backup.rmdir()

                before_uuid = {name: (addon / name / "manifest.json").read_bytes()
                               for name in ("BP", "RP")}
                assert request("do_refresh_uuid", {"only_bp_rp": False})["ok"]
                for pack_name in ("BP", "RP"):
                    assert (addon / pack_name / "manifest.json.bak").read_bytes() == before_uuid[pack_name]
                assert request("restore_backup")["ok"]
                for pack_name in ("BP", "RP"):
                    assert (addon / pack_name / "manifest.json").read_bytes() == before_uuid[pack_name]
                for pack_name in ("Skin", "docs/ExampleBP"):
                    assert (addon / pack_name / "manifest.json.bak").read_bytes() == b"old backup"
                assert skin_manifest.read_bytes() == before_skin
                request("import_folder", {"folder": str(addon / "Skin")})
                rejected = request("do_package", {"only_bp_rp": False, "pack_with_bump": True})
                assert rejected["ok"] is False, rejected
                assert request("do_refresh_uuid", {"only_bp_rp": False})["ok"] is False
                assert skin_manifest.read_bytes() == before_skin
            finally:
                server.shutdown()
                server.server_close()
        print("HTTP API PASSED: 48 invalid directory checks before changes, empty entities/shader folders, 16 packaging options, backups, failures, UUID restore and BP/RP scope.")
    finally:
        if previous_config is None:
            os.environ.pop(addon_config.ENV_OVERRIDE, None)
        else:
            os.environ[addon_config.ENV_OVERRIDE] = previous_config


if __name__ == "__main__":
    main()
