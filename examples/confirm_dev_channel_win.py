#!/usr/bin/env python3
r"""
confirm_dev_channel_win.py — auto-confirm Claude Code's DevChannelsDialog on Windows.

Only needed if you go the "Claude Code + channel/ plugin" route. When CC is
started with
    claude --dangerously-load-development-channels server:companion
it pops this every time:

    WARNING: Loading development channels
      1. I am using this for local development   <- highlighted by default, press Enter to pass
      2. Exit

For a self-hosted local server the channel can't get into the channel allowlist
(that needs Team/Enterprise), this dialog ignores --dangerously-skip-permissions,
and there's no env/setting that silences it. Unattended (autostart / auto-restart)
it just hangs here forever.

Workaround: after launching CC, inject one Enter into its child console to confirm
for it. Mechanism: AttachConsole(pid) + CreateFileW("CONIN$") + WriteConsoleInputW.

> Don't use this on Linux / macOS — tmux is cleaner:
>     tmux new-session -d -s cc 'claude --dangerously-load-development-channels server:companion'
>     sleep 3 && tmux send-keys -t cc Enter

Usage (Windows):
    # A. As a launcher: open a separate console running CC and auto-confirm (good for unattended)
    python confirm_dev_channel_win.py -- claude --dangerously-load-development-channels server:companion

    # B. Confirm an already-running CC process (good for calling from a console-less service/scheduler)
    python confirm_dev_channel_win.py --pid 12345
"""

import sys
import time
import ctypes
from ctypes import wintypes

if sys.platform != "win32":
    sys.exit("This script is Windows-only; on Linux/macOS use tmux send-keys (see the module docstring).")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

GENERIC_WRITE = 0x40000000
FILE_SHARE_RW = 0x00000003
OPEN_EXISTING = 0x00000003
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
KEY_EVENT = 0x0001
VK_RETURN = 0x0D

kernel32.AttachConsole.argtypes = [wintypes.DWORD]
kernel32.AttachConsole.restype = wintypes.BOOL
kernel32.FreeConsole.restype = wintypes.BOOL
kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
kernel32.CreateFileW.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


class KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [
        ("bKeyDown", wintypes.BOOL),
        ("wRepeatCount", wintypes.WORD),
        ("wVirtualKeyCode", wintypes.WORD),
        ("wVirtualScanCode", wintypes.WORD),
        ("uChar", wintypes.WCHAR),
        ("dwControlKeyState", wintypes.DWORD),
    ]


class INPUT_RECORD(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("KeyEvent", KEY_EVENT_RECORD)]
    _anonymous_ = ("u",)
    _fields_ = [("EventType", wintypes.WORD), ("u", _U)]


def send_enter_to_console(pid: int) -> bool:
    """Send one Enter (down+up) to pid's console input buffer. Returns True on success."""
    kernel32.FreeConsole()                       # detach from our own console first
    if not kernel32.AttachConsole(pid):          # attach to the target process's console
        return False
    handle = INVALID_HANDLE_VALUE
    try:
        handle = kernel32.CreateFileW("CONIN$", GENERIC_WRITE, FILE_SHARE_RW,
                                      None, OPEN_EXISTING, 0, None)
        if handle == INVALID_HANDLE_VALUE:
            return False
        recs = (INPUT_RECORD * 2)()
        for i, down in enumerate((1, 0)):        # one full keystroke = press + release
            recs[i].EventType = KEY_EVENT
            ke = recs[i].KeyEvent
            ke.bKeyDown = down
            ke.wRepeatCount = 1
            ke.wVirtualKeyCode = VK_RETURN
            ke.wVirtualScanCode = 0x1C
            ke.uChar = "\r"
            ke.dwControlKeyState = 0
        written = wintypes.DWORD(0)
        ok = kernel32.WriteConsoleInputW(handle, recs, 2, ctypes.byref(written))
        return bool(ok)
    finally:
        if handle and handle != INVALID_HANDLE_VALUE:
            kernel32.CloseHandle(handle)
        kernel32.FreeConsole()                   # give our own console back


def auto_confirm(pid: int, window: int = 20, interval: int = 2) -> None:
    """Send Enter once every `interval` seconds for `window` seconds; the moment the
    dialog renders, the queued Enter is consumed and confirms it. Extra blank
    Enters landing in the input box are harmless. Never raises (worst case, a
    failed keystroke just falls back to pressing Enter by hand once)."""
    deadline = time.time() + window
    sent = 0
    while time.time() < deadline:
        try:
            if send_enter_to_console(pid):
                sent += 1
        except Exception:
            pass
        time.sleep(interval)
    print(f"[devchan] auto-Enter done (sent={sent})", file=sys.stderr, flush=True)


def main(argv: list) -> int:
    if "--pid" in argv:
        pid = int(argv[argv.index("--pid") + 1])
        auto_confirm(pid)
        return 0

    # launcher mode: everything after -- is the command to start
    if "--" in argv:
        cmd = argv[argv.index("--") + 1:]
    else:
        cmd = argv[1:]
    if not cmd:
        print(__doc__)
        return 2

    import subprocess
    import threading
    CREATE_NEW_CONSOLE = 0x00000010              # give CC its own console so we can Attach cleanly
    proc = subprocess.Popen(cmd, creationflags=CREATE_NEW_CONSOLE)
    print(f"[devchan] launched pid={proc.pid}: {' '.join(cmd)}", file=sys.stderr, flush=True)
    threading.Thread(target=auto_confirm, args=(proc.pid,), daemon=True).start()
    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
