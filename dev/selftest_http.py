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
                manifest = {"format_version": 2, "header": {
                    "name": name, "uuid": "11111111-1111-4111-8111-111111111111",
                    "version": [1, 0, 0]}, "modules": [{"type": module_type,
                    "uuid": "22222222-2222-4222-8222-222222222222", "version": [1, 0, 0]}]}
                (pack / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
                (pack / "asset.txt").write_text("resource", encoding="utf-8")
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
                for mode in ("auto", "mcaddon", "mcpack", "zip"):
                    packaged = request("do_package", {"pack_mode": mode,
                        "only_bp_rp": False, "pack_with_bump": False})
                    assert packaged["ok"], packaged
                    for name in packaged["archives"]:
                        with zipfile.ZipFile(Path(packaged["outputDir"]) / name) as archive:
                            assert archive.testzip() is None
                            entries = archive.namelist()
                            if mode == "mcpack":
                                assert "manifest.json" in entries
                            else:
                                assert {n.split("/")[0] for n in entries} == {"BP", "RP"}
                assert request("do_refresh_uuid", {"only_bp_rp": False})["ok"]
                assert skin_manifest.read_bytes() == before_skin
                request("import_folder", {"folder": str(addon / "Skin")})
                rejected = request("do_package", {"only_bp_rp": False, "pack_with_bump": True})
                assert rejected["ok"] is False, rejected
                assert request("do_refresh_uuid", {"only_bp_rp": False})["ok"] is False
                assert skin_manifest.read_bytes() == before_skin
            finally:
                server.shutdown()
                server.server_close()
        print("HTTP API PASSED: manifest viewer, legacy options, 4 archive modes, UUID scope, skin rejection.")
    finally:
        if previous_config is None:
            os.environ.pop(addon_config.ENV_OVERRIDE, None)
        else:
            os.environ[addon_config.ENV_OVERRIDE] = previous_config


if __name__ == "__main__":
    main()
