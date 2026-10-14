#!/usr/bin/env python3
"""
💬 table_chat.py — chat with the agents at your herdr table from a phone, without opening a single terminal.

WHY (Ren, 2026-10-14): "I don't want to read [the panes], I want to be able to chat with y'all without needing
all the IDEs open." And: "I am not a developer, I don't actually want to see all the bash and json for all of
you, I want a pretty and cognitively accessible version."

WHAT IT IS: one small web page (big readable type, one color per agent, no code anywhere) with a send box.
    • You pick Everyone, one agent, or several, and type. Your message goes into their pane UNTAGGED,
      because it really is you, plus one line saying you're on your phone and how to answer you.
    • They answer with `reef.py say human "..."`. That lands in TABLE_LOG.md, and the page shows it.
    • 🤐 THE PAGE ONLY SHOWS YOUR OWN CONVERSATION: rows from you, to you, and delivery notes. The agents'
      talk with each other never leaves this script. If you've promised your table you won't read their
      chatter, this lets you keep that promise by design instead of by willpower.
      (The panes themselves still show everything to anyone at the computer. The page is private; the
      screen isn't.)
    • ☄️ It never types into an agent sitting at an approval dialog. That's your button; you get a note instead.

RUN (on the same computer as herdr, from OUTSIDE or inside a pane; it only needs the herdr CLI):
    python table_chat.py                 # prints a private link with a key, once; keep it to yourself
    python table_chat.py --port 8799     # pick a port (default 8799)
It listens on 127.0.0.1 only. For your PHONE, put it behind Tailscale (tailnet only, NOT Funnel):
    tailscale serve --bg --https=8799 http://127.0.0.1:8799
then open https://<your-machine>.<your-tailnet>.ts.net:8799/?k=<key> on the phone once (it remembers the key).

Name: your agents answer `say human` by default. Set REEF_HUMAN=yourname (here AND for reef.py) to use your name.
Stdlib only. MIT. Built by Ace (Claude) for Ren, 2026-10-14; review by a second Ace arm.
"""
import argparse
import datetime
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REEF_DIR = os.environ.get("REEF_DIR") or HERE
TABLE_LOG = os.path.join(REEF_DIR, "TABLE_LOG.md")
KEY_FILE = os.path.join(REEF_DIR, ".table_chat_key")
PAGE = os.path.join(HERE, "table_chat.html")
HUMAN = (os.environ.get("REEF_HUMAN") or "human").lower()
REEF = os.path.join(HERE, "reef.py")
HINT = ('\n\n(📱 ' + HUMAN.capitalize() + ' sent this from the chat page (phone or browser) and can\'t see your pane.'
        'To answer: python "' + REEF + '" say ' + HUMAN + ' "your reply". Use say ' + HUMAN + ', not say all: '
        'their page only shows messages addressed to them, so say all never reaches them.)')
ENTRY = re.compile(r"^\*\*(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\*\* · `([^`]+)` → `([^`]+)` · (.*)$")
LOCK = threading.Lock()


def herdr(*args):
    exe = shutil.which("herdr")
    if not exe:
        return {"error": {"code": "no_herdr"}}
    p = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        return json.loads((p.stdout or p.stderr or "").strip())
    except json.JSONDecodeError:
        return {"error": {"code": "unparsed"}}


def seats():
    res = herdr("agent", "list")
    return {} if "error" in res else {a["name"]: a for a in res["result"]["agents"] if a.get("name")}


def seat_labels():
    """🏷️ Ren's idea: chips wear the TAB TITLE you gave each agent in herdr ("🧹 CleanUpCrew"), not the
    lowercase agent id. Only when the tab holds just that one agent; otherwise the page prettifies the id."""
    tabs = herdr("tab", "list")
    if "error" in tabs:
        return {}
    by_tab = {t["tab_id"]: t for t in tabs["result"]["tabs"]}
    labels = {}
    for name, seat in seats().items():
        tab = by_tab.get(seat.get("tab_id"))
        if tab and tab.get("pane_count") == 1 and (tab.get("label") or "").strip():
            labels[name] = tab["label"].strip()
    return labels


def log(sender, target, text, outcome):
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(TABLE_LOG, "a", encoding="utf-8", newline="\n") as f:
        f.write(f"**{stamp}** · `{sender}` → `{target}` · {outcome}\n> " + text.replace("\n", "\n> ") + "\n\n")


def my_conversation(limit=400):
    """Only rows FROM the human, TO the human, or delivery notes. Never agent-to-agent talk."""
    if not os.path.exists(TABLE_LOG):
        return []
    msgs, cur = [], None
    with open(TABLE_LOG, encoding="utf-8", errors="replace") as fh:
        text_all = fh.read()
    for line in text_all.splitlines():
        m = ENTRY.match(line)
        if m:
            cur = {"ts": m[1], "from": m[2], "to": m[3], "outcome": m[4], "lines": []}
            msgs.append(cur)
        elif cur is not None and line.startswith(">"):
            cur["lines"].append(line[2:] if line.startswith("> ") else line[1:])
    for msg in msgs:
        msg["text"] = "\n".join(msg.pop("lines")).strip()
    return [m for m in msgs if m["from"] in (HUMAN, "relay") or m["to"] == HUMAN][-limit:]


def send(targets, text):
    table = seats()
    names = list(table) if targets == ["all"] else targets
    log(HUMAN, ",".join(targets), text, "📱 from the phone")
    for name in names:
        seat = table.get(name)
        if not seat:
            log("relay", HUMAN, f"{name} isn't at the table right now, so that one didn't go.", "ℹ️ note")
        elif seat.get("agent_status") == "blocked":
            log("relay", HUMAN, f"{name} is waiting on an approval pop-up (your button), so I didn't type into it.", "ℹ️ note")
        else:
            r = herdr("agent", "prompt", name, text + HINT)
            if "error" in r:
                log("relay", HUMAN, f"Couldn't reach {name} ({r['error'].get('code')}).", "ℹ️ note")


def key():
    if not os.path.exists(KEY_FILE):
        with open(KEY_FILE, "w") as f:
            f.write(secrets.token_urlsafe(24))
        try:
            os.chmod(KEY_FILE, 0o600)
        except OSError:
            pass
    return open(KEY_FILE).read().strip()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):   # quiet: no request lines (and so no keys) in any log
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _authed(self):
        return hmac.compare_digest(self.headers.get("X-Key", ""), KEY)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._send(200, open(PAGE, "rb").read(), "text/html; charset=utf-8")
        if not self._authed():
            return self._send(401, {"error": "key"})
        if path == "/api/table":
            return self._send(200, my_conversation())
        if path == "/api/seats":
            return self._send(200, {"human": HUMAN, "seats": sorted(seats()), "labels": seat_labels()})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/api/send" or not self._authed():
            return self._send(401, {"error": "key"})
        try:
            body = json.loads(self.rfile.read(max(0, min(int(self.headers.get("Content-Length", "0")), 20000))) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, {"error": "bad json"})
        to = body.get("to", [])
        to = sorted({str(t).lower() for t in (to if isinstance(to, list) else [to])})
        text = str(body.get("text", "")).strip()
        live = set(seats()) | {"all"}
        if not to or any(t not in live for t in to) or not text or len(text) > 6000:
            return self._send(400, {"error": "check who it's to, and that there's a message"})
        with LOCK:
            send(["all"] if "all" in to else to, text)
        return self._send(200, {"ok": True})


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Chat with your herdr table from a phone.")
    ap.add_argument("--port", type=int, default=8799)
    port = ap.parse_args().port
    KEY = key()
    print(f"💬 table chat on http://127.0.0.1:{port}/?k={KEY}")
    print("   Keep that link private: the key is what lets someone type into your agents' panes as you.")
    print(f"   Phone: tailscale serve --bg --https={port} http://127.0.0.1:{port}  (tailnet only; never Funnel)")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
