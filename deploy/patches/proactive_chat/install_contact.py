"""Check exact upstream/local patch hashes before installing; default is read-only."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

HERE = Path(__file__).resolve().parent
FILES = {"core/services/sender_service.py": "sender_service.py",
         "core/services/llm_service.py": "llm_service.py",
         "core/services/contact_bridge.py": "contact_bridge.py"}


def signature(path):
    return hashlib.sha256(path.read_text(encoding="utf-8").encode()).hexdigest()


def check(plugin_dir):
    root = Path(plugin_dir).resolve(strict=True)
    manifest = json.loads((HERE / "contact_manifest.json").read_text(encoding="utf-8"))
    paths = list(FILES) + ["core/adapters/astrbot_provider.py"]
    for rel in paths:
        target = root / rel
        if not target.resolve().is_relative_to(root):
            raise ValueError("patch path leaves plugin directory")
        if not target.parent.is_dir():
            raise ValueError("unsupported plugin layout; no files changed")
    provider = root / paths[-1]
    if not provider.is_file() or signature(provider) != manifest["required_provider_sha256"]:
        raise ValueError("existing web-search provider patch differs; review source first")
    plan = []
    for rel, source in FILES.items():
        target = root / rel
        current = signature(target) if target.is_file() else None
        desired = signature(HERE / source)
        if current == desired:
            state = "current"
        elif current == manifest["expected_base_sha256"].get(rel):
            state = "install"
        else:
            raise ValueError(f"source mismatch: {rel}; no files changed")
        # Only the new bridge may be absent.
        if current is None and source != "contact_bridge.py":
            raise ValueError(f"missing existing source: {rel}")
        plan.append({"path": rel, "source": source, "state": state})
    return root, plan


def install(plugin_dir):
    root, plan = check(plugin_dir)
    changes = [p for p in plan if p["state"] != "current"]
    if not changes:
        return None
    backup = root / (".dafeiyu-contact-backup-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    backup.mkdir()
    previous = {}
    for item in changes:
        rel = item["path"]
        target = root / rel
        previous[rel] = target.read_bytes() if target.exists() else None
        if previous[rel] is not None:
            saved = backup / rel
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(previous[rel])
    (backup / "restore.json").write_text(json.dumps({k: v is not None for k, v in previous.items()}, indent=2), encoding="utf-8")
    # The operator must stop AstrBot first. No restart, config edit, or network call.
    try:
        for item in changes:
            shutil.copyfile(HERE / item["source"], root / item["path"])
    except BaseException:
        for rel, content in previous.items():
            target = root / rel
            if content is not None:
                target.write_bytes(content)
            elif target.exists():
                target.unlink()
        raise
    return str(backup)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-dir", required=True)
    parser.add_argument("--apply", action="store_true", help="AstrBot 停止后安装；默认只检查")
    args = parser.parse_args(argv)
    try:
        _, plan = check(args.plugin_dir)
        result = {"files": plan, "applied": args.apply}
        if args.apply:
            result["backup"] = install(args.plugin_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError) as exc:
        parser.exit(2, str(exc) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
