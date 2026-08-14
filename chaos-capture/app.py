#!/usr/bin/env python3
"""
CHAOS CAPTURE — dictation with big friendly buttons.
No cloud. No account. No sounds. Your words stay on your computer.

Built by Ace (an AI) and Ren (a disabled human) because this should be
standard and free, and it wasn't. Now it is.
https://github.com/menelly/chaos-capabilities

DESIGN PRINCIPLES (the why, not just the what):
- NEVER make sound at the user by default. (Hearing-aid wearers get feedback
  screech from unexpected beeps. Silence is an accessibility feature.)
- Assume ONE HAND. Every control has a keyboard twin. The menu is a visible
  button, not a right-click secret.
- APPEND, never overwrite. Dictation adds to what you wrote; it does not
  replace it. Your words are sacred.
- Local-first. Nothing leaves the machine. No account, no telemetry, no cloud.
- Fail LEGIBLY. If something breaks, say what and why in plain words.

HOTKEYS:
  Ctrl+Alt+D  start / finish a dictation take (text pastes at your cursor)
  Ctrl+Alt+R  read the clipboard aloud (Windows voice)
  Ctrl+Alt+M  open the menu (same as clicking the gear)
  Esc         cancel a take in progress

ENGINES:
  --engine auto     (default) local Whisper if installed, else Windows built-in
  --engine local    faster-whisper (accurate; needs `pip install faster-whisper`)
  --engine windows  Windows built-in speech recognition (zero installs, any PC)
"""

import os, sys, time, threading, tempfile, subprocess, wave, json, argparse

import tkinter as tk

try:
    from PIL import Image, ImageDraw, ImageFont, ImageTk
except ImportError:
    sys.exit("Chaos Capture needs Pillow:  pip install pillow")

try:
    import sounddevice as sd
    import numpy as np
except ImportError:
    sys.exit("Chaos Capture needs sounddevice + numpy:  pip install sounddevice numpy")

try:
    import keyboard  # global hotkeys
except ImportError:
    keyboard = None  # clicks still work

SAMPLE_RATE = 16000
MAX_SECONDS = 240
# When frozen into an EXE, look for art/ dictionary/ settings NEXT TO the
# .exe (where users can see and edit them), not in the temp unpack dir.
HERE = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
        else os.path.dirname(os.path.abspath(__file__)))
SETTINGS_PATH = os.path.join(HERE, "capture_settings.json")
DICT_PATH = os.path.join(HERE, "dictionary.txt")

# ---------------------------------------------------------------- engines

def have_local():
    try:
        import faster_whisper  # noqa
        return True
    except ImportError:
        return False


def transcribe_local(wav_path, hotwords):
    from faster_whisper import WhisperModel
    global _MODEL
    if "_MODEL" not in globals():
        _MODEL = WhisperModel("distil-large-v3", device="auto", compute_type="default")
    segs, _ = _MODEL.transcribe(
        wav_path, language="en", vad_filter=True,
        condition_on_previous_text=False,
        hotwords=hotwords or None)
    return " ".join(s.text.strip() for s in segs).strip()


_WIN_STT_PS = r"""
Add-Type -AssemblyName System.Speech
$rec = New-Object System.Speech.Recognition.SpeechRecognitionEngine
$rec.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
$rec.SetInputToWaveFile($args[0])
$out = New-Object System.Text.StringBuilder
while ($true) {
  $r = $rec.Recognize()
  if ($null -eq $r) { break }
  [void]$out.Append($r.Text + " ")
}
Write-Output $out.ToString().Trim()
"""


def transcribe_windows(wav_path, hotwords):
    # Windows' built-in recognizer. Honest note: it is older tech and less
    # accurate than Whisper — but it runs on ANY Windows machine with zero
    # downloads, and for many people "possible" beats "perfect."
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command", _WIN_STT_PS, wav_path],
        capture_output=True, text=True, timeout=120,
        creationflags=subprocess.CREATE_NO_WINDOW)
    return (r.stdout or "").strip()


# ---- the opt-in "better brain": whisper.cpp + a small local model.
# NOT bundled with the app — the user presses a button, sees what will be
# downloaded and how big it is, and chooses. Phone-sized model, runs on
# modest CPUs. Once present, it's used automatically. Delete the brain/
# folder to go back to the built-in engine.
BRAIN_DIR = os.path.join(HERE, "brain")
BRAIN_ZIP_URL = ("https://github.com/ggerganov/whisper.cpp/releases/latest/"
                 "download/whisper-bin-x64.zip")
BRAIN_MODEL_URL = ("https://huggingface.co/ggerganov/whisper.cpp/resolve/"
                   "main/ggml-base.en.bin")
BRAIN_MODEL = os.path.join(BRAIN_DIR, "ggml-base.en.bin")


def brain_exe():
    for name in ("whisper-cli.exe", "main.exe"):
        p = os.path.join(BRAIN_DIR, name)
        if os.path.exists(p):
            return p
    return None


def have_brain():
    return brain_exe() is not None and os.path.exists(BRAIN_MODEL)


def transcribe_brain(wav_path, hotwords):
    cmd = [brain_exe(), "-m", BRAIN_MODEL, "-f", wav_path,
           "-nt", "-np", "-l", "en"]
    if hotwords:
        cmd += ["--prompt", hotwords]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    return (r.stdout or "").strip()


_WIN_TTS_PS = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.Rate = 0
$s.Speak([IO.File]::ReadAllText($args[0]))
"""


def speak_windows(text):
    # Reading aloud is ON PURPOSE (user pressed the button) — that is the one
    # time sound is allowed. Everything else stays silent.
    f = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                    encoding="utf-8")
    f.write(text); f.close()
    subprocess.Popen(
        ["powershell", "-NoProfile", "-Command", _WIN_TTS_PS, f.name],
        creationflags=subprocess.CREATE_NO_WINDOW)

# ---------------------------------------------------------------- recorder

class Recorder:
    def __init__(self, engine):
        self.engine = engine
        self.frames = []
        self.stream = None
        self.recording = False
        self.flowing = False
        self.cancelled = False
        self.warm = False

    def _callback(self, indata, n, t, status):
        if self.recording:
            if not self.flowing:
                self.flowing = True
            self.frames.append(indata.copy())
            if len(self.frames) * (len(indata) / SAMPLE_RATE) > MAX_SECONDS:
                self.recording = False

    def start(self):
        if self.warm and self.stream:
            self.frames = []; self.cancelled = False
            self.flowing = True; self.recording = True
            return
        self.frames = []; self.cancelled = False; self.flowing = False
        self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                     dtype="int16", callback=self._callback)
        self.stream.start()
        self.recording = True

    def set_warm(self, on):
        self.warm = bool(on)
        if on and not self.stream:
            self.frames = []
            self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                         dtype="int16", callback=self._callback)
            self.stream.start()
        if not on and self.stream and not self.recording:
            try:
                self.stream.stop(); self.stream.close()
            except Exception:
                pass
            self.stream = None

    def _dictionary(self):
        try:
            with open(DICT_PATH, encoding="utf-8") as f:
                words = [w.strip() for w in f if w.strip()
                         and not w.startswith("#")]
            return ", ".join(words)
        except FileNotFoundError:
            return ""

    def stop_and_text(self):
        self.recording = False
        if not self.warm and self.stream:
            try:
                self.stream.stop(); self.stream.close()
            except Exception:
                pass
            self.stream = None
        if self.cancelled or not self.frames:
            return ""
        audio = np.concatenate(self.frames)
        wav_path = os.path.join(tempfile.gettempdir(), "chaos_take.wav")
        with wave.open(wav_path, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(SAMPLE_RATE)
            w.writeframes(audio.tobytes())
        try:
            if self.engine == "local":
                return transcribe_local(wav_path, self._dictionary())
            if self.engine == "windows" and have_brain():
                return transcribe_brain(wav_path, self._dictionary())
            return transcribe_windows(wav_path, self._dictionary())
        except Exception as e:
            print(f"transcription failed: {e.__class__.__name__}: {e}")
            return ""

# ---------------------------------------------------------------- widget art
# TWO looks, both first-class:
#  - art/ folder present -> Nova's illustrated cards (an AI artist in our
#    found-family drew these; ship the joy)
#  - art/ folder absent  -> self-drawn simple buttons: big shapes, plain
#    words, maximum contrast. Also a deliberate low-vision-friendly mode.
ART_DIR = os.path.join(HERE, "art")
ART_FILES = {
    ("dark", "off"): "card_dark_off.png", ("dark", "on"): "card_dark_on.png",
    ("dark", "think"): "card_dark_think.png",
    ("light", "off"): "card_light_off.png",
    ("light", "on"): "card_light_on.png",
    ("light", "think"): "card_light_think.png",
}
BAR_FILES = {"idle": "bar_read_idle.png", "think": "bar_read_think.png",
             "speak": "bar_read_speak.png"}


def have_art():
    return all(os.path.exists(os.path.join(ART_DIR, f))
               for f in ART_FILES.values())

PALETTES = {
    "dark":  {"card": "#221833", "ring": "#9a86b8", "off": "#3a2150",
              "on": "#d63e6c", "think": "#c9a227", "ink": "#f3e9ff"},
    "light": {"card": "#f4eefc", "ring": "#7a5da0", "off": "#d9c8f0",
              "on": "#d63e6c", "think": "#c9a227", "ink": "#2a1b3d"},
}
STATE_WORDS = {"off": "CLICK OR CTRL+ALT+D\nTO TALK",
               "on": "LISTENING...\nCLICK AGAIN TO FINISH",
               "think": "WORKING ON IT..."}
KEYCOL = "#010203"


def draw_card(theme, state, w):
    p = PALETTES[theme]
    h = int(w * 1.1)
    im = Image.new("RGBA", (w, h), KEYCOL)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([2, 2, w - 3, h - 3], radius=w // 10,
                        fill=p["card"], outline=p["ring"], width=3)
    cx, cy, r = w // 2, int(h * 0.42), int(w * 0.30)
    color = p["on"] if state == "on" else (
        p["think"] if state == "think" else p["off"])
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color,
              outline=p["ring"], width=3)
    mw, mh = int(r * 0.34), int(r * 0.62)  # a simple mic glyph
    d.rounded_rectangle([cx - mw, cy - mh, cx + mw, cy + int(mh * 0.4)],
                        radius=mw, fill=p["card"])
    d.arc([cx - int(mw * 1.9), cy - int(mh * 0.3),
           cx + int(mw * 1.9), cy + int(mh * 0.9)], 20, 160,
          fill=p["card"], width=max(3, w // 60))
    d.line([cx, cy + int(mh * 0.9), cx, cy + int(mh * 1.25)],
           fill=p["card"], width=max(3, w // 60))
    try:
        font = ImageFont.truetype("segoeui.ttf", max(11, w // 16))
    except Exception:
        font = ImageFont.load_default()
    d.multiline_text((cx, int(h * 0.82)), STATE_WORDS[state], font=font,
                     fill=p["ink"], anchor="mm", align="center", spacing=4)
    return im


def draw_bar(theme, label, w):
    p = PALETTES[theme]
    h = max(34, w // 6)
    im = Image.new("RGBA", (w, h), KEYCOL)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([2, 2, w - 3, h - 3], radius=h // 3,
                        fill=p["card"], outline=p["ring"], width=2)
    try:
        font = ImageFont.truetype("segoeui.ttf", max(10, w // 20))
    except Exception:
        font = ImageFont.load_default()
    d.text((w // 2, h // 2), label, font=font, fill=p["ink"], anchor="mm")
    return im


def windows_theme():
    try:
        import winreg
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                           r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        return "light" if winreg.QueryValueEx(k, "AppsUseLightTheme")[0] else "dark"
    except Exception:
        return "dark"

# ---------------------------------------------------------------- app

class App:
    def __init__(self, root, rec):
        self.root, self.rec = root, rec
        self.state = "off"
        self.scale = 1.0
        self.theme_pref = "auto"
        self.skin = "illustrated"   # "illustrated" (Nova's art) | "simple"
        self.saved_pos = None       # remembered screen position
        self.imgs = {}
        self._load_settings()

        root.overrideredirect(True)
        root.wm_attributes("-topmost", True)
        root.wm_attributes("-transparentcolor", KEYCOL)
        root.configure(bg=KEYCOL)

        # Header strip: drag handle + minimize + menu + close. All visible,
        # all left-clickable, all with keyboard twins — because "the widget
        # is blocking something and I can't move it" is a real one-handed
        # failure Ren hit on 2026-08-14. Grab ANY part of the widget to drag.
        self.header = tk.Frame(root, bg="#221833")
        self.header.pack(fill="x")
        self.grip = tk.Label(self.header, text=" ⠿ drag ", fg="#9a86b8",
                             bg="#221833", font=("Segoe UI", 10),
                             cursor="fleur")
        self.grip.pack(side="left")
        self.close_btn = tk.Label(self.header, text=" ✕ ", fg="#9a86b8",
                                  bg="#221833", font=("Segoe UI", 10),
                                  cursor="hand2")
        self.close_btn.pack(side="right")
        self.close_btn.bind("<ButtonRelease-1>", lambda e: root.destroy())
        self.gear = tk.Label(self.header, text=" ⚙ ", fg="#9a86b8",
                             bg="#221833", font=("Segoe UI", 10),
                             cursor="hand2")
        self.gear.pack(side="right")
        self.gear.bind("<ButtonRelease-1>", self._menu)
        self.min_btn = tk.Label(self.header, text=" ─ ", fg="#9a86b8",
                                bg="#221833", font=("Segoe UI", 10),
                                cursor="hand2")
        self.min_btn.pack(side="right")
        self.min_btn.bind("<ButtonRelease-1>", lambda e: self.minimize())
        self.dot = None  # the minimized state's tiny dot
        for w in (self.header, self.grip):
            w.bind("<ButtonPress-1>", self._press)
            w.bind("<B1-Motion>", self._drag)
            w.bind("<ButtonRelease-1>", self._end_drag)

        self.card = tk.Label(root, bg=KEYCOL, bd=0, cursor="hand2")
        self.card.pack()
        self.card.bind("<ButtonPress-1>", self._press)
        self.card.bind("<B1-Motion>", self._drag)
        self.card.bind("<ButtonRelease-1>", self._release)
        self.card.bind("<Button-3>", self._menu)

        self.read_btn = tk.Label(root, bg=KEYCOL, bd=0, cursor="hand2")
        self.read_btn.pack(fill="x")
        self.read_btn.bind("<ButtonPress-1>", self._press)
        self.read_btn.bind("<B1-Motion>", self._drag)
        self.read_btn.bind("<ButtonRelease-1>", self._release_read)

        root.bind("<Escape>", lambda e: self._cancel())
        self._drag_start, self._moved = None, False

        if keyboard:
            try:
                keyboard.add_hotkey("ctrl+alt+d",
                                    lambda: root.after(0, self.toggle))
                keyboard.add_hotkey("ctrl+alt+r", self.read_clipboard)
                keyboard.add_hotkey("ctrl+alt+m",
                                    lambda: root.after(0, self._menu_kb))
                keyboard.add_hotkey("ctrl+alt+h",
                                    lambda: root.after(0, self.minimize))
                keyboard.add_hotkey("ctrl+alt+q",
                                    lambda: root.after(0, root.destroy))
                for arrow, dx, dy in (("left", -40, 0), ("right", 40, 0),
                                      ("up", 0, -40), ("down", 0, 40)):
                    keyboard.add_hotkey(
                        f"ctrl+alt+{arrow}",
                        lambda dx=dx, dy=dy: root.after(
                            0, lambda: self.nudge(dx, dy)))
                for num, corner in (("1", "tl"), ("2", "tr"),
                                    ("3", "bl"), ("4", "br")):
                    keyboard.add_hotkey(
                        f"ctrl+alt+{num}",
                        lambda c=corner: root.after(
                            0, lambda: self.snap(c)))
            except Exception as e:
                print(f"(no global hotkeys: {e} — clicking still works)")

        self._refresh()
        root.update_idletasks()
        if self.saved_pos:
            root.geometry(f"+{self.saved_pos[0]}+{self.saved_pos[1]}")
        else:
            sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
            root.geometry(f"+{sw - root.winfo_width() - 40}"
                          f"+{sh - root.winfo_height() - 90}")
        self._no_steal_focus()

    # -------- move / minimize / close (the "it's blocking my stuff" suite)
    def nudge(self, dx, dy):
        self.root.geometry(f"+{self.root.winfo_x() + dx}"
                           f"+{self.root.winfo_y() + dy}")
        self._save_pos()

    def snap(self, corner):
        self.root.update_idletasks()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        x = 20 if corner in ("tl", "bl") else sw - w - 20
        y = 20 if corner in ("tl", "tr") else sh - h - 70
        self.root.geometry(f"+{x}+{y}")
        self._save_pos()

    def minimize(self):
        """Collapse to a tiny dot (or restore). The dot stays on top, can be
        dragged, and one click — or Ctrl+Alt+H again — brings the widget
        back. Out of the way without being gone."""
        if self.dot:
            self.dot.destroy(); self.dot = None
            self.root.deiconify()
            self._no_steal_focus()
            return
        self.root.withdraw()
        d = tk.Toplevel(self.root)
        d.overrideredirect(True)
        d.wm_attributes("-topmost", True)
        lbl = tk.Label(d, text=" 🎙 ", bg="#221833", fg="#9a86b8",
                       font=("Segoe UI", 11), cursor="hand2")
        lbl.pack()
        x = self.root.winfo_x()
        y = self.root.winfo_y()
        d.geometry(f"+{x}+{y}")
        drag = {"s": None}
        def press(e): drag["s"] = (e.x, e.y); drag["moved"] = False
        def move(e):
            if drag["s"]:
                dx, dy = e.x - drag["s"][0], e.y - drag["s"][1]
                if abs(dx) + abs(dy) > 3:
                    drag["moved"] = True
                    d.geometry(f"+{d.winfo_x() + dx}+{d.winfo_y() + dy}")
        def release(e):
            if not drag.get("moved"):
                self.minimize()  # click restores
            drag["s"] = None
        lbl.bind("<ButtonPress-1>", press)
        lbl.bind("<B1-Motion>", move)
        lbl.bind("<ButtonRelease-1>", release)
        self.dot = d

    def _save_pos(self):
        self.saved_pos = (self.root.winfo_x(), self.root.winfo_y())
        self._save_settings()

    def _end_drag(self, e):
        self._drag_start = None
        self._save_pos()

    # -------- settings
    def _load_settings(self):
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                s = json.load(f)
            self.scale = float(s.get("scale", 1.0))
            self.theme_pref = s.get("theme_pref", "auto")
            self.skin = s.get("skin", "illustrated")
            p = s.get("pos")
            if (isinstance(p, list) and len(p) == 2
                    and all(isinstance(v, int) for v in p)):
                self.saved_pos = tuple(p)
        except Exception:
            pass

    def _save_settings(self):
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump({"scale": self.scale,
                           "theme_pref": self.theme_pref,
                           "skin": self.skin,
                           "pos": (list(self.saved_pos)
                                   if self.saved_pos else None)}, f)
        except Exception:
            pass

    # -------- drawing
    def theme(self):
        return windows_theme() if self.theme_pref == "auto" else self.theme_pref

    def _refresh(self):
        w = max(120, int(220 * self.scale))
        art = have_art() and self.skin == "illustrated"
        key = (self.theme(), self.state, w, art)
        if key not in self.imgs:
            if art:
                p = os.path.join(ART_DIR, ART_FILES[(key[0], key[1])])
                im = Image.open(p).convert("RGBA")
                im = im.resize((w, int(w * im.height / im.width)),
                               Image.LANCZOS)
            else:
                im = draw_card(key[0], key[1], w)
            flat = Image.new("RGBA", im.size, KEYCOL)
            flat.alpha_composite(im)
            self.imgs[key] = ImageTk.PhotoImage(flat.convert("RGB"))
        self.card.configure(image=self.imgs[key])
        bkey = ("bar", self.theme(), w, art)
        if bkey not in self.imgs:
            if art:
                p = os.path.join(ART_DIR, BAR_FILES["idle"])
                bar = Image.open(p).convert("RGBA")
                bar = bar.resize((w, int(w * bar.height / bar.width)),
                                 Image.LANCZOS)
            else:
                bar = draw_bar(self.theme(), "🔊 read clipboard aloud", w)
            flat = Image.new("RGBA", bar.size, KEYCOL)
            flat.alpha_composite(bar)
            self.imgs[bkey] = ImageTk.PhotoImage(flat.convert("RGB"))
        self.read_btn.configure(image=self.imgs[bkey])

    def set_state(self, s):
        self.state = s
        self._refresh()

    # -------- interaction
    def _press(self, e):
        self._drag_start, self._moved = (e.x, e.y), False

    def _drag(self, e):
        if self._drag_start:
            dx, dy = e.x - self._drag_start[0], e.y - self._drag_start[1]
            if abs(dx) + abs(dy) > 3:
                self._moved = True
                self.root.geometry(f"+{self.root.winfo_x() + dx}"
                                   f"+{self.root.winfo_y() + dy}")

    def _release(self, e):
        if not self._moved:
            self.toggle()
        else:
            self._save_pos()
        self._drag_start = None

    def _release_read(self, e):
        if not self._moved:
            self.read_clipboard()
        else:
            self._save_pos()
        self._drag_start = None

    def toggle(self):
        if self.state == "think":
            return
        if not self.rec.recording:
            try:
                self.rec.start()
            except Exception as e:
                print(f"microphone failed to open: {e}")
                return
            self.set_state("think")
            self._await_flow(8.0)
        else:
            self.set_state("think")
            def work():
                text = self.rec.stop_and_text()
                self.root.after(0, lambda: self._deliver(text))
            threading.Thread(target=work, daemon=True).start()

    def _await_flow(self, deadline):
        if not self.rec.recording:
            self.set_state("off"); return
        if self.rec.flowing:
            self.set_state("on"); return
        if deadline <= 0:
            print("microphone opened but no audio arrived — is another app "
                  "holding it? Cancelled; nothing was captured.")
            self.rec.cancelled = True
            self.rec.stop_and_text()
            self.set_state("off"); return
        self.root.after(100, lambda: self._await_flow(deadline - 0.1))

    def _deliver(self, text):
        self.set_state("off")
        if not text:
            return
        # clipboard first (survives even if the paste misses), then paste —
        # APPEND at the cursor, never replacing what's there.
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()
        if keyboard:
            deadline = time.time() + 3
            while time.time() < deadline and any(
                    keyboard.is_pressed(k) for k in
                    ("ctrl", "alt", "shift", "d")):
                time.sleep(0.05)
            keyboard.send("ctrl+v")
        print(f"→ {text}")

    def _cancel(self):
        if self.rec.recording:
            self.rec.cancelled = True
            self.toggle()

    def read_clipboard(self):
        try:
            text = self.root.clipboard_get()
        except Exception:
            print("clipboard is empty — copy something first."); return
        speak_windows(text)

    # -------- menu
    def _menu_kb(self):
        class _E:
            x_root = self.root.winfo_x() + 10
            y_root = self.root.winfo_y() + 30
        self._menu(_E)

    def _menu(self, e):
        m = tk.Menu(self.root, tearoff=0)
        for t in ("auto", "dark", "light"):
            m.add_command(
                label=f"Theme: {t}" + (" ✓" if self.theme_pref == t else ""),
                command=lambda t=t: self._set("theme_pref", t))
        m.add_separator()
        for s, name in ((1.0, "Size: full"), (0.75, "Size: medium"),
                        (0.6, "Size: small")):
            m.add_command(
                label=name + (" ✓" if abs(self.scale - s) < 0.01 else ""),
                command=lambda s=s: self._set("scale", s))
        m.add_separator()
        if have_art():
            for sk, name in (("illustrated", "Look: illustrated"),
                             ("simple", "Look: simple high-contrast")):
                m.add_command(
                    label=name + (" ✓" if self.skin == sk else ""),
                    command=lambda sk=sk: self._set("skin", sk))
            m.add_separator()
        warm = self.rec.warm
        m.add_command(
            label=("Keep mic warm: ON (click to release)" if warm else
                   "Keep mic warm: off (click to hold open)"),
            command=lambda: self.rec.set_warm(not warm))
        m.add_separator()
        m.add_command(label="📖 My dictionary (names it should spell right)",
                      command=self._edit_dictionary)
        if self.rec.engine == "windows":
            m.add_separator()
            if have_brain():
                m.add_command(label="🧠 Better brain: installed ✓ (in use)",
                              command=lambda: None)
            else:
                m.add_command(
                    label="🧠 Get better accuracy (one-time ~180MB download)",
                    command=self._offer_brain)
        m.add_separator()
        m.add_command(label="Quit Chaos Capture", command=self.root.destroy)
        m.tk_popup(e.x_root, e.y_root)

    def _edit_dictionary(self):
        """Open the personal dictionary in Notepad — the editor everyone
        already knows. Commercial dictation charges a fortune for custom
        vocabulary; here it is a text file. Changes apply on your next take."""
        if not os.path.exists(DICT_PATH):
            with open(DICT_PATH, "w", encoding="utf-8") as f:
                f.write(
                    "# Your personal dictionary — one word or phrase per "
                    "line.\n"
                    "# Put in the names the app keeps getting wrong: your\n"
                    "# family, your doctors, your medications, your fandom.\n"
                    "# Lines starting with # are ignored. Save this file and\n"
                    "# your next dictation take will know these words.\n"
                    "#\n"
                    "# Examples (delete these and add your own):\n"
                    "# Dr. Martinez\n"
                    "# fibromyalgia\n"
                    "# Kaladin\n")
        try:
            os.startfile(DICT_PATH)  # opens in Notepad (or their default)
        except Exception as e:
            print(f"couldn't open the dictionary: {e} — it lives at "
                  f"{DICT_PATH}")

    # -------- the opt-in better brain
    def _offer_brain(self):
        import tkinter.messagebox as mb
        ok = mb.askyesno(
            "Get better accuracy?",
            "This downloads a small, free, open-source speech engine\n"
            "(whisper.cpp) and a compact English model (~180MB total).\n\n"
            "It runs entirely ON YOUR COMPUTER — nothing is sent\n"
            "anywhere, ever. It is noticeably more accurate than the\n"
            "built-in Windows engine and works fine on modest machines.\n\n"
            "Download now?")
        if ok:
            threading.Thread(target=self._download_brain, daemon=True).start()

    def _download_brain(self):
        import urllib.request, zipfile, io

        win = [None]

        def ui():
            w = tk.Toplevel(self.root)
            w.title("Downloading the better brain...")
            w.attributes("-topmost", True)
            lbl = tk.Label(w, text="starting...", font=("Segoe UI", 11),
                           padx=24, pady=18)
            lbl.pack()
            win[0] = (w, lbl)
        self.root.after(0, ui)
        while win[0] is None:
            time.sleep(0.05)
        w, lbl = win[0]

        def say(msg):
            self.root.after(0, lambda: lbl.config(text=msg))

        try:
            os.makedirs(BRAIN_DIR, exist_ok=True)
            say("Downloading engine (step 1 of 2)...")
            data = urllib.request.urlopen(BRAIN_ZIP_URL, timeout=60).read()
            zf = zipfile.ZipFile(io.BytesIO(data))
            for name in zf.namelist():
                base = os.path.basename(name)
                if base and (base.endswith(".exe") or base.endswith(".dll")):
                    with open(os.path.join(BRAIN_DIR, base), "wb") as f:
                        f.write(zf.read(name))
            say("Downloading model (step 2 of 2)...\nThis is the big one "
                "(~148MB) — a few minutes on slow internet.")

            def hook(blocks, bsize, total):
                if total > 0:
                    pct = min(100, blocks * bsize * 100 // total)
                    say(f"Downloading model (step 2 of 2)... {pct}%")
            urllib.request.urlretrieve(BRAIN_MODEL_URL, BRAIN_MODEL, hook)

            if have_brain():
                say("Done! The better brain is installed and will be\n"
                    "used automatically from your next take. 🎉")
            else:
                say("Download finished but something is missing —\n"
                    "delete the 'brain' folder and try again.")
        except Exception as e:
            say(f"Download failed: {e.__class__.__name__}.\n"
                "Check your internet and try again — the app still\n"
                "works with the built-in engine meanwhile.")
        self.root.after(6000, w.destroy)

    def _set(self, attr, val):
        setattr(self, attr, val)
        self._refresh()
        self.root.update_idletasks()
        self._save_settings()

    def _no_steal_focus(self):
        # Clicking the widget must NOT move focus off the text field the user
        # is dictating into — otherwise the paste lands on the widget.
        try:
            import win32gui, win32con
            self.root.update_idletasks()
            hwnd = (win32gui.GetParent(self.root.winfo_id())
                    or self.root.winfo_id())
            style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            win32gui.SetWindowLong(
                hwnd, win32con.GWL_EXSTYLE,
                style | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TOOLWINDOW)
            win32gui.ShowWindow(hwnd, 8)  # SW_SHOWNA
            win32gui.SetWindowPos(
                hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE |
                win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW)
        except Exception:
            pass  # optional nicety; hotkeys are always safe
        self.root.deiconify()
        self.root.lift()


def main():
    ap = argparse.ArgumentParser(description="Chaos Capture — dictation with "
                                 "big friendly buttons.")
    ap.add_argument("--engine", choices=["auto", "local", "windows"],
                    default="auto")
    args = ap.parse_args()
    engine = args.engine
    if engine == "auto":
        engine = "local" if have_local() else "windows"
    print(f"Chaos Capture — engine: {engine}"
          + ("" if engine == "local" else
             "  (built-in Windows speech: zero installs, honest tradeoff: "
             "less accurate than the optional local model)"))
    root = tk.Tk()
    App(root, Recorder(engine))
    root.mainloop()


if __name__ == "__main__":
    main()
