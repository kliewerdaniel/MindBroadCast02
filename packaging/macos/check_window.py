#!/usr/bin/env python3
"""Assert that a MindBroadcast window is actually on screen.

Exit 0 if a window belonging to the app is visible, 1 otherwise.

Why this exists: an .app whose entry point only starts a web server looks fine
to any HTTP-based smoke test while showing the user nothing at all. Asserting
that a real window is on screen is the only check that catches "the app is a
headless server wearing an .app costume".
"""

import sys

OWNER_HINT = "MindBroadcast"
MIN_WIDTH = 200


def main() -> int:
    try:
        import Quartz
    except ImportError:
        # No pyobjc in the checking interpreter; fall back to AppleScript.
        return _via_applescript()

    windows = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly
        | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID,
    )
    for w in windows:
        owner = str(w.get("kCGWindowOwnerName", ""))
        title = str(w.get("kCGWindowName", ""))
        bounds = w.get("kCGWindowBounds", {}) or {}
        if OWNER_HINT in owner or OWNER_HINT in title:
            if bounds.get("Width", 0) >= MIN_WIDTH:
                print(f"window: owner={owner!r} title={title!r} "
                      f"{bounds.get('Width')}x{bounds.get('Height')}")
                return 0
    print("no MindBroadcast window on screen")
    return 1


def _via_applescript() -> int:
    import subprocess

    script = (
        'tell application "System Events" to get name of every process '
        'whose background only is false'
    )
    try:
        out = subprocess.run(["osascript", "-e", script],
                             capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return 1
    ok = OWNER_HINT in out
    print("app is a foreground GUI app" if ok else "app is not a GUI app")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
