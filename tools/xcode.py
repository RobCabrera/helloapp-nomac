#!/usr/bin/env python3
"""Drives Xcode on the macOS runner (docs/qa/phase3-acceptance.md P3-16 and the signing switch check).

  xcode.py open <project.xcodeproj>
      Opens the project in Xcode, waits for its window, lets it settle, reports whether File > Save is enabled, and
      quits with Cmd-Q; fails if Xcode shows a prompt instead of quitting. git diff afterwards shows any rewrite.

  xcode.py signing <project.xcodeproj> <log dir>
      Best effort, UI scripting: selects the project, the Signing & Capabilities tab, and turns on "Automatically
      manage signing", accepting Xcode's confirmation if one appears, then closes and quits. Saves UI dumps and
      screenshots in <log dir>. Exits 2 when a control cannot be found.

Standard library only. Uses System Events (Accessibility, granted on GitHub macOS images); Apple Events to Xcode
itself are not granted there.
"""
import subprocess
import sys
import time
from pathlib import Path

SHOTS = Path("../ui-logs")


def osa(script, check=True, timeout=60):
    """Runs one AppleScript statement; a hung Apple Event (permission prompt, modal dialog) fails after `timeout` s."""
    try:
        r = subprocess.run(["osascript", "-s", "s", "-e", script], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        if check:
            raise RuntimeError(f"osascript timed out after {timeout} s: {script}")
        return ""
    if check and r.returncode:
        raise RuntimeError(f"osascript failed: {r.stderr.strip()}\n{script}")
    return r.stdout.strip()


def screenshot(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["screencapture", "-x", str(path)], check=False, timeout=30)


def xcode_app():
    dev = subprocess.run(["xcode-select", "-p"], capture_output=True, text=True, check=True).stdout.strip()
    return str(Path(dev).parent.parent)


def system_events(statement, check=True):
    return osa(f'tell application "System Events" to tell process "Xcode" to {statement}', check=check)


def refs_of_window():
    text = system_events("get entire contents of window 1", check=False)
    return [r.strip() for r in text.strip("{}").split(", ") if r.strip()]


def first_launch():
    """Dismisses Xcode's first-launch component sheet (fresh runner user): unchecks the optional downloads, then
    presses its button; Esc as a fallback. Logs what it saw."""
    subprocess.run(["xcodebuild", "-checkFirstLaunchStatus"], check=False)
    subprocess.run(["open", "-a", xcode_app()], check=True)
    deadline = time.time() + 120
    seen = False
    while time.time() < deadline:
        time.sleep(5)
        refs = refs_of_window()
        buttons = [r for r in refs if r.startswith("button ")]
        texts = [r for r in refs if r.startswith("static text ")]
        if any("Select the components" in t for t in texts):
            seen = True
            print("first launch sheet: found", flush=True)
            screenshot(SHOTS / "first-launch-1.png")
            for box in (r for r in refs if r.startswith("checkbox ")):
                state = system_events(f"get {{enabled, value}} of ({box})", check=False)
                if state.replace(" ", "") == "{true,1}":
                    system_events(f"click ({box})", check=False)
            refs = refs_of_window()
            buttons = [r for r in refs if r.startswith("button ")]
            print("first launch buttons:", buttons, flush=True)
            screenshot(SHOTS / "first-launch-2.png")
            # With every download unchecked the default button reads "Continue"; its buttons carry no names, so
            # press Return (default button) first, then the last button if the sheet is still up.
            system_events("key code 36", check=False)
            time.sleep(3)
            if any("Select the components" in t for t in refs_of_window() if t.startswith("static text ")) and buttons:
                system_events(f"click ({buttons[-1]})", check=False)
            continue
        if seen or texts:
            print("first launch: done; window texts:", texts[:5], flush=True)
            screenshot(SHOTS / "first-launch-3.png")
            return
    screenshot(SHOTS / "first-launch-timeout.png")
    print("first launch: no window seen in 120 s (accessibility denied?)", flush=True)


def open_project(project):
    """Opens the project and waits for its window (System Events only: Apple Events to Xcode itself need an
    Automation grant the runner does not have). Closes the What's New window if it shows."""
    subprocess.run(["open", "-a", xcode_app(), str(project)], check=True)
    deadline = time.time() + 240
    while time.time() < deadline:
        names = system_events("get name of every window", check=False)
        if "New in Xcode" in names:
            system_events('click (first button of window "What’s New in Xcode" whose subrole is "AXCloseButton")',
                          check=False)
        if project.stem in names:
            system_events("set frontmost to true", check=False)
            print("loaded: window", names, flush=True)
            return
        print("waiting, windows:", names or "(none)", flush=True)
        time.sleep(5)
    screenshot(SHOTS / f"not-loaded-{project.stem}.png")
    raise RuntimeError("no project window in 240 s")


def close_and_quit(tag):
    """Records whether Xcode offers File > Save (a modified document), then quits with Cmd-Q. Any window or sheet
    left after that is a prompt (P3-16 fails)."""
    system_events("set frontmost to true", check=False)
    save = system_events('get enabled of menu item "Save" of menu 1 of menu bar item "File" of menu bar 1', check=False)
    print("File > Save enabled:", save or "(unknown)", flush=True)
    system_events('keystroke "q" using command down')
    for _ in range(30):
        time.sleep(1)
        if subprocess.run(["pgrep", "-x", "Xcode"], capture_output=True).returncode:
            print("quit: no prompt", flush=True)
            return
    screenshot(SHOTS / f"prompt-on-quit-{tag}.png")
    print("PROMPT on quit:", [r for r in refs_of_window() if r.startswith(("button ", "static text "))], flush=True)
    raise RuntimeError("Xcode did not quit within 30 s of Cmd-Q (prompt shown)")


class Ui:
    """System Events references of the front Xcode window, found by text."""

    def __init__(self, logs):
        self.logs, self.n = Path(logs), 0
        self.logs.mkdir(parents=True, exist_ok=True)

    def snapshot(self, tag):
        self.n += 1
        name = f"{self.n:02d}-{tag}"
        screenshot(self.logs / f"{name}.png")
        refs = refs_of_window()
        (self.logs / f"{name}.txt").write_text("\n".join(refs) + "\n")
        return refs

    def find(self, tag, predicate):
        for _ in range(10):
            refs = self.snapshot(tag)
            hit = next((r for r in refs if predicate(r)), None)
            if hit:
                print(f"{tag}: {hit}")
                return hit
            time.sleep(3)
        print(f"{tag}: not found (see {self.logs})")
        sys.exit(2)

    def act(self, statement):
        return system_events(statement)

    def click(self, ref):
        """A real click at the element's centre (rows and tabs ignore a scripted AXPress or AXSelected)."""
        pos = self.act(f"get position of ({ref})").strip("{}").split(",")
        size = self.act(f"get size of ({ref})").strip("{}").split(",")
        x, y = (int(float(pos[i])) + int(float(size[i])) // 2 for i in (0, 1))
        self.act(f"click at {{{x}, {y}}}")


def signing(project, logs):
    first_launch()
    open_project(project)
    time.sleep(15)
    ui = Ui(logs)
    nav = 'of outline 1 of scroll area 1 of group 1 of splitter group "HelloApp.xcodeproj" of window'
    ui.find("navigator-row", lambda r: r.startswith(f"row 2 {nav}"))
    # Keyboard selection opens the item in the editor; a scripted selection or click does not.
    ui.act(f'set value of attribute "AXSelected" of (row 2 {nav} "HelloApp — HelloApp.xcodeproj") to true')
    ui.act(f'set focused of (outline 1 of scroll area 1 of group 1 of splitter group "HelloApp.xcodeproj" of window '
           f'"HelloApp — HelloApp.xcodeproj") to true')
    ui.act("key code 126")
    time.sleep(6)
    # Xcode 26: the target is preselected and the sections are a pop-up button showing "General".
    refs = ui.snapshot("project-editor")
    pops = [r for r in refs if r.startswith("pop up button ")]
    section = next((p for p in pops if system_events(f"get value of ({p})", check=False) == '"General"'), None)
    print("section pop-up:", section)
    if not section:
        sys.exit(2)
    ui.act(f"click ({section})")
    time.sleep(1)
    ui.act(f'click menu item "Signing & Capabilities" of menu 1 of ({section})')
    time.sleep(5)
    box = ui.find("auto-checkbox", lambda r: r.startswith('checkbox "Automatically manage signing"'))
    print("checkbox before:", ui.act(f"get value of ({box})"))
    ui.click(box)
    time.sleep(5)
    refs = ui.snapshot("after-click")
    sheet_buttons = [r for r in refs if r.startswith("button ") and " of sheet " in r]
    print("sheet buttons:", sheet_buttons or "none")
    confirm = next((b for b in sheet_buttons if "Enable" in b or "Automatic" in b), None)
    if confirm:
        ui.click(confirm)
        time.sleep(5)
    print("checkbox after:", ui.act(f"get value of ({box})"))
    ui.snapshot("final")
    time.sleep(10)
    close_and_quit("signing")


if __name__ == "__main__":
    cmd, project = sys.argv[1], Path(sys.argv[2]).resolve()
    if cmd == "open":
        if not subprocess.run(["pgrep", "-x", "Xcode"], capture_output=True).returncode == 0:
            first_launch()
        open_project(project)
        time.sleep(30)
        screenshot(SHOTS / f"open-{project.stem}.png")
        close_and_quit(project.stem)
    else:
        signing(project, sys.argv[3])
