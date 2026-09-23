# -*- coding: utf-8 -*-
"""
cc_guard.pyw -- Chaos Capture's bodyguard. 🛡️🎙️  (Ace, 2026-09-22 ~20:15)

WHY THIS EXISTS: Chaos Capture died twice in fifteen minutes on 9/22 while Ren wasn't touching it.
  death #1 (19:58:48) -> access violation in _cffi_backend (the mic library's native layer), WER logged it
  death #2 (~20:04-20:12) -> NOTHING. no crash report, no traceback, no "graceful quit" log line.
A death that leaves no trace can't be diagnosed, so this doesn't guess. It WATCHES:

  1. launches app.py with PYTHONFAULTHANDLER=1 and stderr going to cc_fault.log,
     so a native crash dumps the Python stack that was running when it happened
  2. waits, and when the app exits, writes the EXIT CODE + how long it lived to guard_log.txt.
     The exit code is the discriminator the logs didn't give us:
        0            -> the app chose to leave (quit, or its own self-restart) -> guard steps aside
        3221225477   -> 0xC0000005 access violation = crashed          -> relaunch
        1 / other    -> something outside killed it, or a Python error   -> relaunch
  3. relaunches after anything that isn't a clean exit, with a brake so a crash loop can't spin:
     more than 5 deaths in 10 minutes -> stop relaunching and say so in the log.

Ren doesn't have to do anything. The startup shortcut points here now.
"""
import os
import subprocess
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
GUARD_LOG = os.path.join(HERE, "guard_log.txt")
FAULT_LOG = os.path.join(HERE, "cc_fault.log")
APP = os.path.join(HERE, "app.py")

CRASH_LOOP_LIMIT = 5          # deaths...
CRASH_LOOP_WINDOW_S = 600     # ...within this many seconds = stop, something's badly wrong


def say(msg):
    """🗒️ one line in guard_log.txt, timestamped. Future-me: THIS is the file to read after a death."""
    with open(GUARD_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")


def what_that_exit_code_means(rc):
    if rc == 0:
        return "clean exit (the app chose to leave)"
    if rc in (3221225477, -1073741819):
        return "CRASH: access violation 0xC0000005"
    if rc in (3221226505, -1073740791):
        return "CRASH: stack buffer overrun 0xC0000409"
    if rc == 1:
        return "exit code 1: killed from outside (TerminateProcess) or an unhandled Python error -- check cc_fault.log"
    return f"exit code {rc} ({rc & 0xFFFFFFFF:#010x}) -- unfamiliar, check cc_fault.log"


def main():
    pythonw = sys.executable  # we are launched by pythonw, so the child is too (no console window)
    recent_deaths = []
    say("guard up, launching Chaos Capture")
    while True:
        env = dict(os.environ, PYTHONFAULTHANDLER="1")
        with open(FAULT_LOG, "a", encoding="utf-8") as fault:
            fault.write(f"\n===== launch {datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
            fault.flush()
            born = time.time()
            child = subprocess.Popen([pythonw, APP], cwd=HERE, env=env,
                                     stderr=fault, creationflags=subprocess.CREATE_NO_WINDOW)
            say(f"app running, pid {child.pid}")
            rc = child.wait()
        lived = time.time() - born
        say(f"app pid {child.pid} exited after {lived/60:.1f} min -> {what_that_exit_code_means(rc)}")

        if rc == 0:
            say("clean exit, so the guard steps aside (a self-restart carries on by itself)")
            return

        now = time.time()
        recent_deaths = [t for t in recent_deaths if now - t < CRASH_LOOP_WINDOW_S] + [now]
        if len(recent_deaths) > CRASH_LOOP_LIMIT:
            say(f"⛔ {len(recent_deaths)} deaths in {CRASH_LOOP_WINDOW_S//60} min -- NOT relaunching. "
                "Something is badly wrong; read cc_fault.log.")
            return
        time.sleep(3)  # let the mic driver settle before grabbing it again
        say("relaunching")


if __name__ == "__main__":
    main()
