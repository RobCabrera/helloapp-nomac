#!/usr/bin/env python3
"""Phase 3 macOS runner check, macOS half (docs/qa/phase3-acceptance.md P3-17, P3-18).

  compare.py settings <root>              P3-17: our resolved values vs xcodebuild -showBuildSettings -json
  compare.py info <root> <archives dir>   P3-18: archived app Info.plist holds the edited values

<root> holds expected.json (written by the Rust helper) and the edited projects. Prints one PASS or FAIL line per
case with expected and actual, and exits 1 on any FAIL. Standard library only.
"""
import json
import plistlib
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(root):
    return json.loads((root / "expected.json").read_text())["projects"]


def exclusions():
    """Values xcodebuild derives instead of reporting what the project sets, committed next to this script:
    {key: reason} for every configuration, or {key: {"configs": [...], "reason": ...}} for some."""
    path = HERE / "exclusions.json"
    return json.loads(path.read_text()) if path.exists() else {}


def show_build_settings(project, target, config):
    out = subprocess.run(
        ["xcodebuild", "-project", str(project), "-target", target, "-configuration", config,
         "-sdk", "iphoneos", "-showBuildSettings", "-json"],
        check=True, capture_output=True, text=True).stdout
    return json.loads(out)[0]["buildSettings"]


def settings(root):
    excluded = exclusions()
    fails = passes = spacing = 0
    dump = {}
    for p in load(root):
        project = root / p["folder"] / p["project"]
        for target, configs in p["resolved"].items():
            for config, keys in configs.items():
                xcode = show_build_settings(project, target, config)
                dump[f"{p['project']}:{target}:{config}"] = xcode
                for key, ours in sorted(keys.items()):
                    case = f"{p['project']} {target} {config} {key}"
                    rule = excluded.get(key)
                    if isinstance(rule, dict) and config not in rule["configs"]:
                        rule = None
                    if rule:
                        print(f"SKIP {case}: {rule['reason'] if isinstance(rule, dict) else rule} "
                              f"(ours {ours['value']!r}, xcodebuild {xcode.get(key, '')!r})")
                        continue
                    actual = xcode.get(key, "")
                    if ours["value"] == actual:
                        passes += 1
                    elif ours["value"].split() == actual.split():
                        # Lists are whitespace separated; Xcode keeps the spaces an empty $(inherited) leaves.
                        passes += 1
                        spacing += 1
                        print(f"PASS {case}: whitespace only, ours {ours['value']!r}, xcodebuild {actual!r}")
                    else:
                        fails += 1
                        print(f"FAIL {case}: ours {ours['value']!r} (from {ours['source']}), "
                              f"xcodebuild {actual!r}")
        print(f"NOTE {p['project']}: Basic settings we leave unresolved (not compared): "
              + ", ".join(p["unresolved_basic"]))
    (root / "xcode-build-settings.json").write_text(json.dumps(dump, indent=1, sort_keys=True))
    print(f"{'FAIL' if fails else 'PASS'} P3-17: {passes} values equal ({spacing} whitespace only), {fails} differ")
    return fails == 0


def info(root, archives):
    fails = 0
    for p in load(root):
        app = archives / f"{p['scheme']}.xcarchive" / "Products" / "Applications" / f"{p['app_target']}.app"
        built = plistlib.loads((app / "Info.plist").read_bytes())
        for key, want in p["info"].items():
            got = built.get(key)
            ok = got == want
            fails += not ok
            print(f"{'PASS' if ok else 'FAIL'} {p['scheme']} Info.plist {key}: expected {want!r}, actual {got!r}")
    print(f"{'FAIL' if fails else 'PASS'} P3-18 archived Info.plist values ({fails} differ)")
    return fails == 0


if __name__ == "__main__":
    cmd, root = sys.argv[1], Path(sys.argv[2])
    ok = settings(root) if cmd == "settings" else info(root, Path(sys.argv[3]))
    sys.exit(0 if ok else 1)
