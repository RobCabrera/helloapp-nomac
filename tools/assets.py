#!/usr/bin/env python3
"""Phase 5 macOS runner check, macOS half (docs/qa/phase5-acceptance.md P5-150 to P5-154).

  assets.py snapshot <root> <catalog>         records every file of a catalog (before Xcode runs)
  assets.py unchanged <root> <results>        Xcode opening the catalog rewrote none of our files
  assets.py rewrite <root> <results>          P5-150: Xcode's duplicate of each set equals our Contents.json
  assets.py new <root> <results>              Xcode's new items equal ours (new files)
  assets.py template <root>                   P5-150: the Xcode template's catalog files equal ours
  assets.py plists <root> <results>           P5-153: plutil on our binary plists; XML plists to binary1
  assets.py actool <root> <results>           P5-151: actool --compile, warnings listed
  assets.py archive <root> <archive> <results>  P5-152: archived app icon, accent color, plist resource

<root> holds expected.json (written by tools/xcode-check src/bin/p5.rs) and the catalogs. One PASS, FAIL, or INFO
line per case; exits 1 on any FAIL. Files Xcode wrote go only to <results> (a 1-day artifact), never into the repo.
Template files from the Xcode app are compared here and never copied out: only the first differing offset and our
line are printed. Standard library only.
"""
import base64
import datetime
import difflib
import json
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

FAILS = []


def report(ok, what):
    tag = "PASS" if ok is True else "FAIL" if ok is False else "INFO"
    print(f"{tag} {what}", flush=True)
    if ok is False:
        FAILS.append(what)


def expected(root):
    return json.loads((root / "expected.json").read_text())


def first_difference(ours, theirs):
    """Offset of the first differing byte and our line there."""
    n = next((i for i, (a, b) in enumerate(zip(ours, theirs)) if a != b), min(len(ours), len(theirs)))
    line_start = ours.rfind(b"\n", 0, n) + 1
    line_end = ours.find(b"\n", n)
    line = ours[line_start:line_end if line_end >= 0 else len(ours)]
    return n, line.decode("utf-8", "replace")


def facts(data):
    text = data.decode("utf-8", "replace")
    return {
        "final newline": text.endswith("\n"),
        "escaped slash": "\\/" in text,
        "empty array": re.findall(r"\[\s*\]", text)[:1],
        "empty object": re.findall(r"\{\s*\}", text)[:1],
    }


def save_diff(results, name, ours, theirs):
    out = results / "diff"
    out.mkdir(parents=True, exist_ok=True)
    diff = difflib.unified_diff(ours.decode().splitlines(True), theirs.decode().splitlines(True),
                                "ours/" + name, "xcode/" + name)
    (out / (name.replace("/", "__") + ".diff")).write_text("".join(diff))


def snap_path(root, name):
    return root.parent / f"snap-{name}.json"


def snapshot(root, name):
    """Every file of the catalog: Contents.json text in full, other files by size (Xcode never edits images)."""
    cat = root / name
    files = {}
    for p in sorted(cat.rglob("*")):
        if p.is_file():
            rel = str(p.relative_to(cat))
            files[rel] = p.read_text() if p.name == "Contents.json" else p.stat().st_size
    snap_path(root, name).write_text(json.dumps(files))
    print(f"snapshot {name}: {len(files)} files", flush=True)


def compare_snapshot(root, name):
    """(Contents.json files changed in place, Contents.json files added) since the snapshot."""
    cat = root / name
    before = json.loads(snap_path(root, name).read_text())
    changed_files, added = [], []
    for p in sorted(cat.rglob("Contents.json")):
        rel = str(p.relative_to(cat))
        if rel not in before:
            added.append(rel)
        elif p.read_text() != before[rel]:
            changed_files.append(rel)
    return before, changed_files, added


def save(results, folder, rel, data):
    out = results / folder / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)


COPY = re.compile(r"^(?P<base>.*?)(?: copy(?: \d+)?| \d+)(?P<ext>\.[a-z]+)?$")


def original_of(rel):
    """`Star 1.imageset/Contents.json` -> `Star.imageset/Contents.json` (the first component that is a copy)."""
    parts = rel.split("/")
    for i, p in enumerate(parts[:-1]):
        m = COPY.match(p)
        if m:
            parts[i] = m["base"] + (m["ext"] or "")
            return "/".join(parts)
    return None


def unchanged(root, results):
    cat = root / "Corpus.xcassets"
    ours = set(expected(root)["corpus"]["ours"])
    _, inplace, added = compare_snapshot(root, "Corpus.xcassets")
    for rel in inplace:
        save(results, "inplace-open", rel, (cat / rel).read_bytes())
        # Controls with incomplete grids are rewritten on open by design (Xcode fills the missing slots).
        report(False if rel in ours else None, f"Xcode rewrote {rel} on open")
    report(not added and not (set(inplace) & ours), f"Xcode open and quit rewrote none of our files (added {added or 'none'})")


def rewrite(root, results):
    exp = expected(root)["corpus"]
    cat = root / "Corpus.xcassets"
    ours = set(exp["ours"])
    before, inplace, added = compare_snapshot(root, "Corpus.xcassets")
    print("Xcode changed", len(inplace), "and added", len(added), "Contents.json files", flush=True)
    for rel in inplace:
        save(results, "inplace", rel, (cat / rel).read_bytes())
        report(False if rel in ours else None, f"Xcode rewrote {rel} in place")
    copies = {}
    for rel in added:
        orig = original_of(rel)
        if orig:
            copies.setdefault(orig, rel)
    verified = 0
    for rel in sorted(ours):
        copy = copies.get(rel)
        if copy is None:
            report(None, f"not verified {rel}: no Xcode copy")
            continue
        a, b = before[rel].encode(), (cat / copy).read_bytes()
        verified += 1
        if a == b:
            report(True, f"P5-150 {rel}: Xcode's copy is byte-identical")
        else:
            n, line = first_difference(a, b)
            save_diff(results, rel, a, b)
            save(results, "xcode", rel, b)
            report(False, f"P5-150 {rel}: differs at byte {n}, our line {line.strip()!r}")
    report(verified > 0 or None, f"P5-150 sets verified: {verified} of {len(ours)}")
    for c in exp["controls"]:
        copy = copies.get(f"{c}/Contents.json")
        if copy is None:
            report(None, f"control {c}: no Xcode copy")
            continue
        theirs = (cat / copy).read_bytes()
        save(results, "xcode", f"{c}/Contents.json", theirs)
        same = before.get(f"{c}/Contents.json", "").encode() == theirs
        report(None, f"control {c}: Xcode re-serialized: {'no' if same else 'yes'}; {facts(theirs)}")


def new(root, results):
    cat = root / "XcodeNew.xcassets"
    corpus = root / "Corpus.xcassets"
    _, inplace, made = compare_snapshot(root, "XcodeNew.xcassets")
    print("Xcode added:", made, "changed:", inplace, flush=True)
    report(not inplace or None, f"XcodeNew.xcassets root unchanged ({inplace or 'yes'})")
    ours = {".imageset": "Image.imageset", ".colorset": "Color.colorset", ".dataset": "Data.dataset",
            ".appiconset": "AppIcon.appiconset"}
    for rel in made:
        folder = str(Path(rel).parent)
        theirs = (cat / rel).read_bytes()
        save(results, "xcode-new", rel, theirs)
        mine = ours.get(Path(folder).suffix)
        if mine is None:
            namespace = b"provides-namespace" in theirs
            mine = "Icons" if namespace else "Folder"
        a = (corpus / mine / "Contents.json").read_bytes()
        if mine == "Color.colorset" and a != theirs:
            # Known difference: Xcode's new color set has Any and Dark white colors; ours starts with one Any entry
            # and no color (spec 2.6, owned by UX). Our writer matches Xcode for the same content (AccentColor).
            save_diff(results, f"new-{folder}", a, theirs)
            report(None, f"new {folder}: known difference from our {mine} (spec 2.6: one Any entry, no color)")
            continue
        if a == theirs:
            report(True, f"new {folder}: equals our {mine}")
        else:
            n, line = first_difference(a, theirs)
            save_diff(results, f"new-{folder}", a, theirs)
            report(False, f"new {folder}: differs from our {mine} at byte {n}, our line {line.strip()!r}")


def template(root):
    corpus = root / "Corpus.xcassets"
    dev = subprocess.run(["xcode-select", "-p"], capture_output=True, text=True, check=True).stdout.strip()
    templates = Path(dev).parent.parent
    pairs = {"Contents.json": "Contents.json", "AppIcon.appiconset/Contents.json": "AppIcon.appiconset/Contents.json",
             "AccentColor.colorset/Contents.json": "Color.colorset/Contents.json"}
    found = 0
    for cat in sorted(templates.rglob("*.xctemplate/**/Assets.xcassets")):
        rel_cat = cat.relative_to(templates)
        if "/iOS/" not in f"/{rel_cat}" and "MultiPlatform" not in str(rel_cat):
            continue
        for theirs_rel, ours_rel in pairs.items():
            t = cat / theirs_rel
            if not t.exists():
                continue
            found += 1
            theirs, ours = t.read_bytes(), (corpus / ours_rel).read_bytes()
            what = f"template {rel_cat}/{theirs_rel}"
            if theirs == ours:
                report(True, f"P5-150 {what}: equals our writer's output")
                continue
            same_content = json.loads(theirs) == json.loads(ours)
            n, line = first_difference(ours, theirs)
            ok = False if "/iOS/" in f"/{rel_cat}" else None
            newline = theirs.endswith(b"\n")
            report(ok, f"P5-150 {what}: differs at byte {n} (content equal: {same_content}; Xcode final newline: "
                       f"{newline}), our line {line.strip()!r}")
    report(found > 0 or None, f"template files compared: {found}")


def plist_value(v):
    if isinstance(v, datetime.datetime):
        return {"$date": v.strftime("%Y-%m-%dT%H:%M:%SZ")}
    if isinstance(v, bytes):
        return {"$data": base64.b64encode(v).decode()}
    if isinstance(v, dict):
        return {k: plist_value(x) for k, x in v.items()}
    if isinstance(v, list):
        return [plist_value(x) for x in v]
    return v


def plutil_values(path):
    """Values through plutil: JSON when the file has only JSON types, else XML read by plistlib."""
    j = subprocess.run(["plutil", "-convert", "json", "-o", "-", str(path)], capture_output=True)
    if j.returncode == 0:
        return "json", json.loads(j.stdout)
    x = subprocess.run(["plutil", "-convert", "xml1", "-o", "-", str(path)], capture_output=True, check=True)
    return "xml1", plist_value(plistlib.loads(x.stdout))


def plists(root, results):
    exp = expected(root)
    base = root / "plists"
    for rel, want in exp["plists"].items():
        p = base / rel
        lint = subprocess.run(["plutil", "-lint", str(p)], capture_output=True, text=True)
        report(lint.returncode == 0, f"P5-153 plutil -lint {rel}: {lint.stdout.strip() or lint.stderr.strip()}")
        how, got = plutil_values(p)
        report(got == want, f"P5-153 {rel} values through plutil -convert {how}"
                            + ("" if got == want else f": expected {want}, actual {got}"))
        if rel.startswith("xml/"):
            out = results / "plutil-binary" / Path(rel).name
            out.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["plutil", "-convert", "binary1", "-o", str(out), str(p)], check=True)
    settings = root / "HelloApp/HelloApp/Settings.plist"
    lint = subprocess.run(["plutil", "-lint", str(settings)], capture_output=True, text=True)
    report(lint.returncode == 0, f"P5-153 plutil -lint HelloApp Settings.plist (binary: "
                                 f"{settings.read_bytes().startswith(b'bplist00')})")
    how, got = plutil_values(settings)
    want = exp["hello"]["settings"]
    report(got == want, f"P5-153 HelloApp Settings.plist values" + ("" if got == want else f": {got} != {want}"))


def actool_compile(catalog, out, app_icon=None):
    out.mkdir(parents=True, exist_ok=True)
    cmd = ["xcrun", "actool", str(catalog), "--compile", str(out), "--platform", "iphoneos",
           "--minimum-deployment-target", "17.0", "--target-device", "iphone", "--target-device", "ipad",
           "--output-partial-info-plist", str(out / "partial.plist"), "--output-format", "xml1",
           "--warnings", "--notices", "--errors"]
    if app_icon:
        cmd += ["--app-icon", app_icon]
    r = subprocess.run(cmd, capture_output=True)
    try:
        doc = plistlib.loads(r.stdout)
    except Exception:
        doc = {}
    msgs = {k.rsplit(".", 1)[-1]: v for k, v in doc.items() if k.startswith("com.apple.actool")}
    return r.returncode, msgs, r.stderr.decode("utf-8", "replace")


def actool(root, results):
    for name, icon, strict in [("Icons.xcassets", "AppIcon", True), ("Corpus.xcassets", None, False)]:
        code, msgs, err = actool_compile(root / name, results / "actool" / name, icon)
        problems = {k: v for k, v in msgs.items() if k in ("warnings", "errors") and v}
        notices = msgs.get("notices") or []
        text = json.dumps(problems, default=str)[:3000]
        ok = code == 0 and (not problems if strict else not msgs.get("errors"))
        report(ok, f"P5-151 actool {name}: exit {code}; warnings/errors {text}; notices {len(notices)}"
                   + (f"; stderr {err.strip()[:300]}" if code else ""))
        (results / "actool" / f"{name}.json").write_text(json.dumps(msgs, default=str, indent=1))


def archive(root, archive_path, results):
    exp = expected(root)["hello"]
    app = Path(archive_path) / "Products/Applications/HelloApp.app"
    info = plistlib.loads((app / "Info.plist").read_bytes())
    icons = info.get("CFBundleIcons", {}).get("CFBundlePrimaryIcon", {})
    report(icons.get("CFBundleIconName") == exp["icon"], f"P5-152 CFBundleIcons names {icons.get('CFBundleIconName')}")
    report(info.get("NSAccentColorName") == exp["accent"], f"P5-152 NSAccentColorName {info.get('NSAccentColorName')}")
    for k, v in exp["info"].items():
        report(info.get(k) == v, f"P5-152 Info.plist {k}: {info.get(k)!r}")
    r = subprocess.run(["xcrun", "assetutil", "--info", str(app / "Assets.car")], capture_output=True, text=True)
    report(r.returncode == 0, f"P5-152 assetutil --info exit {r.returncode}")
    entries = json.loads(r.stdout) if r.returncode == 0 else []
    (results / "assetutil.json").write_text(json.dumps(entries, indent=1))
    icon = [e for e in entries if e.get("Name") == exp["icon"]]
    looks = sorted({str(e.get("Appearance", "any")) for e in icon})
    report(len(looks) >= 3, f"P5-152 {exp['icon']} renditions: {len(icon)}, appearances {looks}")
    color = [e for e in entries if e.get("Name") == exp["accent"] and e.get("AssetType") == "Color"]
    report(bool(color), f"P5-152 color {exp['accent']} in Assets.car: {len(color)} rendition(s)")
    settings = app / "Settings.plist"
    how, got = plutil_values(settings)
    report(got == exp["settings"], f"P5-152 Settings.plist resource values (format in app: "
                                   f"{'binary' if settings.read_bytes().startswith(b'bplist00') else 'other'})")


if __name__ == "__main__":
    cmd, root = sys.argv[1], Path(sys.argv[2]).resolve()
    if cmd == "snapshot":
        snapshot(root, sys.argv[3])
    elif cmd == "unchanged":
        unchanged(root, Path(sys.argv[3]).resolve())
    elif cmd == "rewrite":
        rewrite(root, Path(sys.argv[3]).resolve())
    elif cmd == "new":
        new(root, Path(sys.argv[3]).resolve())
    elif cmd == "template":
        template(root)
    elif cmd == "plists":
        plists(root, Path(sys.argv[3]).resolve())
    elif cmd == "actool":
        actool(root, Path(sys.argv[3]).resolve())
    elif cmd == "archive":
        archive(root, sys.argv[3], Path(sys.argv[4]).resolve())
    print(f"{'FAIL' if FAILS else 'PASS'} {cmd}: {len(FAILS)} failure(s)")
    sys.exit(1 if FAILS else 0)
