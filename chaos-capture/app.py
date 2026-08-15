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
LOG_PATH = os.path.join(HERE, "capture_log.txt")


def log(msg):
    """print() + a real file beside the exe, because windowed EXEs eat
    stdout and a mute app cannot be debugged from a phone photo."""
    line = time.strftime("[%H:%M:%S] ") + str(msg)
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
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
    import voices
    r = voices.run_ps(_WIN_STT_PS, wav_path, timeout=120)
    return (r.stdout or "").strip()


# ---- the opt-in "better brain": whisper.cpp + a small local model.
# NOT bundled with the app — the user presses a button, sees what will be
# downloaded and how big it is, and chooses. Phone-sized model, runs on
# modest CPUs. Once present, it's used automatically. Delete the brain/
# folder to go back to the built-in engine.
# Search order: beside the exe (old installs), then the stable per-user
# home that survives version upgrades (Ren, 8/15: "the wizard needs to
# recognize that I've already downloaded it" — 180MB is user data, not
# app data; it must outlive any one version's folder).
BRAIN_HOME = os.path.join(os.environ.get("LOCALAPPDATA", HERE),
                          "ChaosCapture", "brain")


def brain_dir():
    for d in (os.path.join(HERE, "brain"), BRAIN_HOME):
        if any(os.path.exists(os.path.join(d, n))
               for n in ("whisper-cli.exe", "main.exe")):
            return d
    return BRAIN_HOME   # downloads land in the version-proof home
BRAIN_DIR = None  # legacy name; use brain_dir()
BRAIN_ZIP_URL = ("https://github.com/ggerganov/whisper.cpp/releases/latest/"
                 "download/whisper-bin-x64.zip")
BRAIN_MODEL_URL = ("https://huggingface.co/ggerganov/whisper.cpp/resolve/"
                   "main/ggml-base.en.bin")
def brain_model():
    return os.path.join(brain_dir(), "ggml-base.en.bin")


def brain_exe():
    for name in ("whisper-cli.exe", "main.exe"):
        p = os.path.join(brain_dir(), name)
        if os.path.exists(p):
            return p
    return None


def have_brain():
    return brain_exe() is not None and os.path.exists(brain_model())


def transcribe_brain(wav_path, hotwords):
    cmd = [brain_exe(), "-m", brain_model(), "-f", wav_path,
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
    import voices
    threading.Thread(target=voices.run_ps, args=(_WIN_TTS_PS, f.name),
                     daemon=True).start()

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
        self.last_frame_at = 0.0

    def _callback(self, indata, n, t, status):
        self.last_frame_at = time.time()   # liveness heartbeat (BT health ring)
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

# ---- v2 (Aug 2026): Nova's second commission. ONE big state button with
# swappable faces plus keep-warm / refresh / menu ON the card. Buttons were
# rendered separately from the background so we composite them ourselves.
# Fractions of card width/height, from the approved layout mockups.
# Theme folders (Ren's structure, 8/15): art/v2/<theme>/ — every theme
# folder holds the SAME filenames (background, micon, off, thinking, read,
# keepwarm, refresh, menu, lost_banner). Adding a theme = dropping in a
# folder. Zero code changes, ever again.
V2_ROOT = os.path.join(ART_DIR, "v2")
V2_ASPECT = 747 / 560


def v2_themes():
    try:
        return sorted(
            d for d in os.listdir(V2_ROOT)
            if os.path.exists(os.path.join(V2_ROOT, d, "background.png")))
    except Exception:
        return []


def v2_dir(theme):
    return os.path.join(V2_ROOT, theme)
V2_STATE_ART = {"off": "off.png", "on": "micon.png", "think": "thinking.png"}
# TWO state buttons side by side (Ren, 8/15: "otherwise grandma has to know
# the hotkeys and can't click"): LISTEN on the left, READ ALOUD on the right.
# Identity stays visible (mic vs speaker art); state shows as glow — the read
# button sleeps dimmed until it's speaking.
V2_MIC = (0.28, 0.45, 0.179)              # cx(of w), cy(of h), r(of w)
V2_READ = (0.72, 0.45, 0.179)
V2_AUX3 = {"warm": (0.245, 0.75, 0.098),  # with refresh available
           "refresh": (0.50, 0.75, 0.098),
           "menu": (0.755, 0.75, 0.098)}
V2_AUX2 = {"warm": (0.34, 0.75, 0.098),   # refresh helper not installed
           "menu": (0.66, 0.75, 0.098)}


def have_v2():
    return bool(v2_themes())


def have_bt_refresh_task():
    """The 🔄 button only exists if the elevated helper task is installed —
    a button that can't do anything would be a lie on the card."""
    try:
        r = subprocess.run(["schtasks", "/query", "/tn", "BluetoothRefresh"],
                           capture_output=True, timeout=10,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        return r.returncode == 0
    except Exception:
        return False



# ---- Bluetooth refresh helper (the stale-hearing-aid medicine).
# Bouncing the BT radio needs admin, so a one-time elevated setup registers
# a SYSTEM scheduled task; after that the 🔄 button triggers it promptlessly.
# Born on Ren's Phonaks; shipped for every hearing-aid wearer's "connected
# but silently delivering nothing" mornings.
_BT_BOUNCE_PS = r"""
$log = Join-Path (Split-Path $PSCommandPath) "bt_refresh.log"
function Log($m) { Add-Content $log ("[{0:yyyy-MM-dd HH:mm:ss}] {1}" -f (Get-Date), $m) }
$radio = Get-PnpDevice -Class Bluetooth | Where-Object { $_.InstanceId -like "USB*" } | Select-Object -First 1
if (-not $radio) { $radio = Get-PnpDevice -Class Bluetooth | Select-Object -First 1 }
if (-not $radio) { Log "NO radio found"; exit 1 }
Log ("bouncing radio: {0}" -f $radio.FriendlyName)
Disable-PnpDevice -InstanceId $radio.InstanceId -Confirm:$false
Start-Sleep -Seconds 3
Enable-PnpDevice -InstanceId $radio.InstanceId -Confirm:$false
Log "radio re-enabled - paired devices re-handshake in ~10-20s"
"""

_BT_INSTALL_PS = r"""
# Elevated one-shot: register the bounce as a SYSTEM task (session 0 = no
# console flash), so future runs need no prompt.
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument ("-WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File `"" + $args[0] + "`"")
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
Register-ScheduledTask -TaskName "BluetoothRefresh" -Action $action -Principal $principal -Settings $settings -Force | Out-Null
Write-Output "installed"
"""


def setup_bt_refresh():
    """Write the bounce script beside the app, then ask Windows (ONE UAC
    yes) to register it as the promptless helper task. Returns success."""
    bounce_path = os.path.join(HERE, "bt_refresh.ps1")
    with open(bounce_path, "w", encoding="utf-8-sig") as f:
        f.write(_BT_BOUNCE_PS)
    inst_path = os.path.join(HERE, "bt_install.ps1")
    with open(inst_path, "w", encoding="utf-8-sig") as f:
        f.write(_BT_INSTALL_PS)
    # -Verb RunAs = the UAC prompt; the inner script registers the task
    r = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         f"Start-Process powershell -Verb RunAs -Wait -WindowStyle Hidden "
         f"-ArgumentList '-NoProfile','-ExecutionPolicy','Bypass',"
         f"'-File','{inst_path}','{bounce_path}'"],
        capture_output=True, text=True, timeout=120,
        creationflags=subprocess.CREATE_NO_WINDOW)
    return have_bt_refresh_task()


def have_art():
    return all(os.path.exists(os.path.join(ART_DIR, f))
               for f in ART_FILES.values())


# ---- desktop icon / start-with-Windows (Ren, 8/15: "Grandma Jane needs to
# put it on her desktop, where she will never find it again"). Plain .lnk
# shortcuts, no admin, no registry. Only meaningful for the frozen EXE.
_SHORTCUT_PS = r"""
$W = New-Object -ComObject WScript.Shell
$dest = Join-Path ([Environment]::GetFolderPath($args[0])) "Chaos Capture.lnk"
if ($args[2] -eq "remove") {
  if (Test-Path $dest) { Remove-Item $dest }
  Write-Output "removed"
} else {
  $s = $W.CreateShortcut($dest)
  $s.TargetPath = $args[1]
  $s.WorkingDirectory = (Split-Path $args[1])
  $s.Description = "Chaos Capture - talk instead of type"
  $s.Save()
  Write-Output "made $dest"
}
"""


def shortcut_path(where):
    import ctypes.wintypes
    name = {"desktop": 0x10, "startup": 0x07}[where]  # CSIDL codes
    buf = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
    ctypes.windll.shell32.SHGetFolderPathW(None, name, None, 0, buf)
    return os.path.join(buf.value, "Chaos Capture.lnk")


def has_shortcut(where):
    try:
        return os.path.exists(shortcut_path(where))
    except Exception:
        return False


def set_shortcut(where, want):
    """Create or remove the desktop/startup shortcut. Returns success."""
    if not getattr(sys, "frozen", False):
        print("(shortcuts are for the installed EXE — running from source)")
        return False
    import voices
    folder = "Desktop" if where == "desktop" else "Startup"
    r = voices.run_ps(_SHORTCUT_PS, folder, sys.executable,
                      "make" if want else "remove", timeout=30)
    return r.returncode == 0

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


def system_ram_gb():
    try:
        import ctypes
        class MEM(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MEM(); m.dwLength = ctypes.sizeof(MEM)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        return m.ullTotalPhys / (1024 ** 3)
    except Exception:
        return 0


# ---------------------------------------------------------------- onboarding
# First-run wizard: one question per screen, big text, big buttons, no
# menus to find. "Grandma Jane doesn't have to figure anything out" (Ren,
# 2026-08-14). Runs once; re-run anytime from the gear menu -> Setup helper.

class Wizard:
    F_TITLE = ("Segoe UI", 16, "bold")
    F_BODY = ("Segoe UI", 12)
    F_BTN = ("Segoe UI", 12)

    def __init__(self, app):
        self.app = app
        self.answers = {"engine": "windows", "bt_helper": "no",
                        "theme_pref": "auto",
                        "skin": "illustrated", "features": "both",
                        "words": "", "tts_engine": app.tts_engine}
        self.win = tk.Toplevel(app.root)
        self.win.title("Welcome to Chaos Capture")
        self.win.attributes("-topmost", True)
        self.win.geometry("560x480")
        self.win.configure(bg="white")
        self.frame = None
        self.step = 0
        self.steps = [self.s_welcome, self.s_features, self.s_engine,
                      self.s_voice, self.s_bluetooth, self.s_theme,
                      self.s_skin, self.s_words, self.s_done]
        self.show()

    def _clear(self):
        if self.frame:
            self.frame.destroy()
        self.frame = tk.Frame(self.win, bg="white")
        self.frame.pack(fill="both", expand=True, padx=28, pady=20)

    def _nav(self, next_ok=True, last=False):
        bar = tk.Frame(self.frame, bg="white")
        bar.pack(side="bottom", fill="x", pady=(16, 0))
        if self.step > 0:
            tk.Button(bar, text="◀ Back", font=self.F_BTN, padx=14, pady=6,
                      command=self.back).pack(side="left")
        tk.Button(bar, text=("Finish ✔" if last else "Next ▶"),
                  font=self.F_BTN, padx=20, pady=6, bg="#d63e6c",
                  fg="white", activebackground="#b83259",
                  command=(self.finish if last else self.next)
                  ).pack(side="right")

    def _title(self, text):
        tk.Label(self.frame, text=text, font=self.F_TITLE, bg="white",
                 wraplength=490, justify="left").pack(anchor="w")

    def _body(self, text):
        tk.Label(self.frame, text=text, font=self.F_BODY, bg="white",
                 wraplength=490, justify="left").pack(anchor="w", pady=(8, 4))

    def _radios(self, key, options):
        var = tk.StringVar(value=self.answers[key])
        for value, label in options:
            tk.Radiobutton(self.frame, text=label, variable=var, value=value,
                           font=self.F_BODY, bg="white", anchor="w",
                           wraplength=460, justify="left",
                           pady=6).pack(anchor="w", fill="x")
        self.vars = getattr(self, "vars", {})
        self.vars[key] = var

    def show(self):
        self._clear()
        self.steps[self.step]()

    def next(self):
        for k, v in getattr(self, "vars", {}).items():
            self.answers[k] = v.get()
        self.vars = {}
        if hasattr(self, "words_box"):
            try:
                self.answers["words"] = self.words_box.get("1.0", "end").strip()
            except Exception:
                pass
            del self.words_box
        self.step += 1
        self.show()

    def back(self):
        self.vars = {}
        self.step -= 1
        self.show()

    # ---- screens
    def s_welcome(self):
        self._title("Hi! Let's set this up together. 🎙️")
        self._body("Chaos Capture lets you TALK instead of type, and have "
                   "your computer READ things out loud to you.\n\n"
                   "Eight quick questions, all with fine default answers — "
                   "you can just press Next the whole way if you like. "
                   "Nothing here is permanent; there's a Setup helper in "
                   "the ⚙ menu to change your mind later.")
        self._nav()

    def s_features(self):
        self._title("What would you like?")
        self._radios("features", [
            ("both", "🎙️🔊 Both — talking instead of typing, AND having "
                     "things read aloud to me"),
            ("stt", "🎙️ Just talking instead of typing"),
            ("tts", "🔊 Just having things read aloud to me")])
        self._nav()

    def s_engine(self):
        self._title("Which speech engine for understanding you?")
        self._body("Both are free and both stay on your computer.")
        brain_label = ("🧠 The better brain — ALREADY ON THIS COMPUTER ✓ "
                       "(no download needed, just picks it)"
                       if have_brain() else
                       "🧠 The better brain — one-time ~180MB download, "
                       "noticeably more accurate. Recommended if your "
                       "internet can manage it.")
        self._radios("engine", [
            ("windows", "Built-in Windows speech — works right now, "
                        "nothing to download. Makes more mistakes."),
            ("brain", brain_label)])
        ram = system_ram_gb()
        cores = os.cpu_count() or 2
        if ram:
            verdict = ("Your computer can handle the better brain "
                       "comfortably. 👍" if ram >= 7 and cores >= 4 else
                       "Your computer is on the modest side — the better "
                       "brain will still work, just a little slower. The "
                       "built-in engine is the zippy choice.")
            self._body(f"(I peeked: {ram:.0f}GB memory, {cores} cores. "
                       f"{verdict})")
        if self.answers["features"] == "tts":
            self._body("(You picked read-aloud only, so this barely "
                       "matters — feel free to just press Next.)")
        self._nav()

    def s_voice(self):
        self._title("Whose voice should read to you?")
        self._radios("tts_engine", [
            ("windows", "🔊 The built-in Windows voice — free, works right "
                        "now, nothing to set up. (Recommended to start.)"),
            ("inworld", "🎭 Inworld — hundreds of natural voices. Needs "
                        "your own Inworld account and key (their pricing, "
                        "billed to you)."),
            ("elevenlabs", "🎤 ElevenLabs — very natural voices. Needs your "
                           "own ElevenLabs account and key (their pricing, "
                           "billed to you).")])
        self._body("Pick one of the key options and I'll ask for the key "
                   "when we finish. Change your mind anytime: ⚙ menu → "
                   "🗣 Read-aloud voice.")
        if self.answers["features"] == "stt":
            self._body("(You picked talking-only, so this doesn't matter — "
                       "just press Next.)")
        self._nav()

    def s_bluetooth(self):
        self._title("Do you use Bluetooth hearing aids or headphones?")
        already = have_bt_refresh_task()
        if already:
            self._body("Your computer already has the Bluetooth fixer set "
                       "up. The 🔄 button will be on the card — tap it "
                       "whenever a device is connected but acting deaf. "
                       "Nothing to do here; press Next.")
            self._radios("bt_helper", [("no", "OK! (already set up)")])
        else:
            self._body("Bluetooth audio has a classic failure: connected, "
                       "but silently deaf. One tap of our 🔄 button fixes it "
                       "— but setting that up needs ONE Windows admin OK.")
            self._radios("bt_helper", [
                ("yes", "🎧 Yes — set up the one-tap Bluetooth fixer when "
                        "we finish (Windows will ask once, say Yes)"),
                ("no", "No Bluetooth here — skip it")])
        self._nav()

    def s_theme(self):
        self._title("Light mode or dark mode?")
        self._radios("theme_pref", [
            ("auto", "🌗 Match my Windows setting (recommended)"),
            ("light", "☀️ Always light"),
            ("dark", "🌙 Always dark")])
        self._nav()

    def s_skin(self):
        self._title("Which look?")
        self._radios("skin", [
            ("illustrated", "🎨 Illustrated — warm painted cards"),
            ("simple", "🔲 Simple — big plain shapes, maximum contrast "
                       "(easier for low vision)")])
        self._nav()

    def s_words(self):
        self._title("Any names it should spell correctly?")
        self._body("Type the names and words it might not know — your "
                   "family, your doctor, your medications, your town. One "
                   "per line. Totally fine to leave empty and add later "
                   "(⚙ menu → My dictionary).")
        self.words_box = tk.Text(self.frame, height=7, font=self.F_BODY,
                                 wrap="word")
        self.words_box.pack(fill="both", expand=True, pady=(4, 0))
        self.words_box.insert("1.0", self.answers["words"])
        self._nav()

    def s_done(self):
        if hasattr(self, "words_box"):
            pass
        self._title("That's everything! 🎉")
        if getattr(sys, "frozen", False):
            self.v_desktop = tk.BooleanVar(value=True)
            self.v_startup = tk.BooleanVar(value=True)
            tk.Checkbutton(self.frame, text="🖥️ Put an icon on my desktop",
                           variable=self.v_desktop, font=self.F_BODY,
                           bg="white", anchor="w").pack(anchor="w")
            tk.Checkbutton(self.frame,
                           text="🚀 Start Chaos Capture when my computer starts",
                           variable=self.v_startup, font=self.F_BODY,
                           bg="white", anchor="w").pack(anchor="w")
        self._body("The little widget lives in the corner of your screen. "
                   "The cheat sheet:\n\n"
                   "🎙️  Ctrl+Alt+D — or click the big button — to talk\n"
                   "🔊  Ctrl+Alt+R — read whatever you copied out loud\n"
                   "⚙  Ctrl+Alt+M — the menu (size, looks, dictionary)\n"
                   "─  Ctrl+Alt+H — shrink it out of the way\n"
                   "⠿  drag it anywhere, or Ctrl+Alt+arrows\n\n"
                   "Click into whatever you're writing, talk, and your "
                   "words appear. That's the whole thing. Enjoy!")
        self._nav(last=True)

    def next_with_words(self):
        pass

    def finish(self):
        a = self.answers
        app = self.app
        app.theme_pref = a["theme_pref"]
        app.skin = a["skin"]
        app.features = a["features"]
        app.tts_engine = a["tts_engine"]
        app.onboarded = True
        words = [w.strip() for w in a["words"].splitlines()
                 if w.strip() and not w.startswith("#")]
        if words:
            existing = ""
            if os.path.exists(DICT_PATH):
                with open(DICT_PATH, encoding="utf-8") as f:
                    existing = f.read()
            with open(DICT_PATH, "a", encoding="utf-8") as f:
                for w in words:
                    if w not in existing:
                        f.write(w + "\n")
        app._save_settings()
        app._apply_features()
        app._refresh()
        self.win.destroy()
        if a["engine"] == "brain" and not have_brain():
            app._offer_brain()
        if getattr(sys, "frozen", False) and hasattr(self, "v_desktop"):
            if self.v_desktop.get():
                set_shortcut("desktop", True)
            if self.v_startup.get():
                set_shortcut("startup", True)
        if a.get("bt_helper") == "yes" and not have_bt_refresh_task():
            app.root.after(400, app._setup_refresh)
        if a["tts_engine"] in ("inworld", "elevenlabs"):
            import voices
            if not voices.load_key(a["tts_engine"]):
                # they chose a key-engine in the wizard — keep the promise
                # and ask for the key right now, not someday
                app.root.after(300, app._voice_settings)

# ---------------------------------------------------------------- app

class App:
    def __init__(self, root, rec):
        self.root, self.rec = root, rec
        self.state = "off"
        self.scale = 1.0
        self.theme_pref = "auto"
        self.skin = "illustrated"   # "illustrated" (Nova's art) | "simple"
        self.v2_theme = "octopus"   # which art/v2/<theme>/ folder
        self.bg_dim = None          # user's background-dim override
                                    # (None = each theme's own default)
        self.saved_pos = None       # remembered screen position
        self.features = "both"      # "both" | "stt" | "tts"
        self.onboarded = False      # first-run wizard shown yet?
        self.tts_engine = "windows" # "windows" | "inworld" | "elevenlabs"
        self.tts_voice = None       # None = engine default
        self.read_active = False    # read-aloud running (card button glows)
        self.imgs = {}
        self._load_settings()

        # v2 card: probe once (in the background — schtasks is slow) whether
        # the BT-refresh helper exists; the 🔄 button only draws if it does.
        # Ambient mic-link health for the green ring around 🔄. Honest tiers:
        # open stream + fresh frames = PROVEN alive; no stream = "Windows
        # sees a microphone" (best knowable without opening one).
        self.bt_ok = False
        def _bt_poll():
            ok = False
            try:
                if self.rec.stream:
                    ok = (time.time() -
                          getattr(self.rec, "last_frame_at", 0)) < 2.5
                else:
                    ok = sd.query_devices(kind="input") is not None
            except Exception:
                ok = False
            if ok != self.bt_ok:
                self.bt_ok = ok
                self._refresh()
            self.root.after(3000, _bt_poll)
        self.root.after(1500, _bt_poll)

        self._has_refresh = False
        def _probe():
            if have_bt_refresh_task():
                self._has_refresh = True
                self.imgs = {k: v for k, v in self.imgs.items()
                             if not (isinstance(k, tuple) and k[0] == "v2")}
                def show():
                    self.bt_btn.pack(side="left", after=self.warm_btn)
                    self._refresh()
                self.root.after(0, show)
        threading.Thread(target=_probe, daemon=True).start()

        root.overrideredirect(True)
        root.wm_attributes("-topmost", True)
        root.wm_attributes("-transparentcolor", KEYCOL)
        root.configure(bg=KEYCOL)

        # Header strip: ⠿ 🎙 🔊 🔥 [🔄] | ─ ⚙ ✕ — every control visible, big
        # (13pt Grandma-sized targets), left-clickable, with keyboard twins.
        # Born of two real one-handed failures: "it's blocking something and
        # I can't move it" (8/14) and "I didn't know that button existed"
        # (8/15 — invisible states read as broken).
        HDR_FONT = ("Segoe UI", 13)
        self.header = tk.Frame(root, bg="#221833", height=36)
        self.header.pack()
        self.header.pack_propagate(False)   # width clamped to the art in
        # _refresh, so the strip's edges line up with the card's edges
        # (Ren, 8/15: misaligned edges are not "good enough")
        self.grip = tk.Label(self.header, text=" ⠿ ", fg="#9a86b8",
                             bg="#221833", font=HDR_FONT, cursor="fleur")
        self.grip.pack(side="left")
        self.mic_btn = tk.Label(self.header, text="🎙", fg="#9a86b8",
                                bg="#221833", font=HDR_FONT, cursor="hand2")
        self.mic_btn.pack(side="left")
        self.mic_btn.bind("<ButtonRelease-1>", lambda e: self.toggle())
        self.read_hdr = tk.Label(self.header, text=" 🔊", fg="#9a86b8",
                                 bg="#221833", font=HDR_FONT, cursor="hand2")
        self.read_hdr.pack(side="left")
        self.read_hdr.bind("<ButtonRelease-1>", lambda e: self.read_clipboard())
        self.warm_btn = tk.Label(self.header, text=" 🔥", fg="#5a4a6e",
                                 bg="#221833", font=HDR_FONT, cursor="hand2")
        self.warm_btn.pack(side="left")
        self.warm_btn.bind("<ButtonRelease-1>", lambda e: self.toggle_warm())
        self.bt_btn = tk.Label(self.header, text=" 🔄", fg="#9a86b8",
                               bg="#221833", font=HDR_FONT, cursor="hand2")
        # 🔄 packs only if the helper task exists (see the startup probe)
        self.bt_btn.bind("<ButtonRelease-1>", lambda e: self._do_bt_refresh())
        self.close_btn = tk.Label(self.header, text="✕ ", fg="#9a86b8",
                                  bg="#221833", font=HDR_FONT, cursor="hand2")
        self.close_btn.pack(side="right")
        self.close_btn.bind("<ButtonRelease-1>", lambda e: self.shutdown())
        self.gear = tk.Label(self.header, text=" ⚙ ", fg="#9a86b8",
                             bg="#221833", font=HDR_FONT, cursor="hand2")
        self.gear.pack(side="right")
        self.gear.bind("<ButtonRelease-1>", self._menu)
        self.min_btn = tk.Label(self.header, text="─ ", fg="#9a86b8",
                                bg="#221833", font=HDR_FONT, cursor="hand2")
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
                keyboard.add_hotkey("ctrl+alt+r",
                                    lambda: root.after(0, self.read_clipboard))
                keyboard.add_hotkey("ctrl+alt+m",
                                    lambda: root.after(0, self._menu_kb),
                                    suppress=True)  # swallow the keystroke:
                # other apps bind Ctrl+Alt+M too (Claude Code flips its
                # permission mode!) and must not ALSO react to our menu key
                keyboard.add_hotkey("ctrl+alt+h",
                                    lambda: root.after(0, self.minimize))
                keyboard.add_hotkey("ctrl+alt+q",
                                    lambda: root.after(0, self.shutdown))
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

        self._apply_features()
        self._apply_header_theme()
        self._refresh()
        root.update_idletasks()
        if self.saved_pos:
            root.geometry(f"+{self.saved_pos[0]}+{self.saved_pos[1]}")
        else:
            sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
            root.geometry(f"+{sw - root.winfo_width() - 40}"
                          f"+{sh - root.winfo_height() - 90}")
        self._no_steal_focus()
        if not self.onboarded:
            root.after(400, lambda: Wizard(self))

    def _apply_features(self):
        """Show only what this user asked for: stt hides the read bar,
        tts hides the talk card. Chosen in the wizard, changeable there.
        With the v2 card, read-aloud lives on the header 🔊 (and Ctrl+Alt+R),
        so the separate bar only appears for read-aloud-ONLY users — for
        everyone else it would just repeat the header button."""
        self.card.pack_forget()
        self.read_btn.pack_forget()
        if self.features in ("both", "stt"):
            self.card.pack()
        if self.features in ("both", "tts"):
            if not (have_v2() and self.skin == "illustrated"
                    and self.features == "both"):
                self.read_btn.pack(fill="x")
        self.root.update_idletasks()

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
        # The dot SAYS what clicking it does — a mystery dot fails the
        # Grandma test (field-tested by Ren, 8/15, who minimized the local
        # build and couldn't find the way back).
        lbl = tk.Label(d, text=" 🎙 open ", bg="#221833", fg="#c9b8e8",
                       font=("Segoe UI", 11), cursor="hand2")
        lbl.pack()
        lbl.bind("<Enter>", lambda e: lbl.config(bg="#3a2d54"))
        lbl.bind("<Leave>", lambda e: lbl.config(bg="#221833"))
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
            self.features = s.get("features", "both")
            self.onboarded = bool(s.get("onboarded", False))
            self.tts_engine = s.get("tts_engine", "windows")
            self.tts_voice = s.get("tts_voice") or None
            self.v2_theme = s.get("v2_theme", self.v2_theme)
            if self.v2_theme not in v2_themes() and v2_themes():
                self.v2_theme = v2_themes()[0]
            self.bg_dim = s.get("bg_dim", None)
            if self.bg_dim is not None:
                self.bg_dim = float(self.bg_dim)
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
                           "features": self.features,
                           "onboarded": self.onboarded,
                           "tts_engine": getattr(self, "tts_engine", "windows"),
                           "tts_voice": getattr(self, "tts_voice", None),
                           "v2_theme": getattr(self, "v2_theme", "octopus"),
                           "bg_dim": getattr(self, "bg_dim", None),
                           "pos": (list(self.saved_pos)
                                   if self.saved_pos else None)}, f)
        except Exception:
            pass

    # -------- drawing
    def theme(self):
        return windows_theme() if self.theme_pref == "auto" else self.theme_pref

    def _v2_aux(self):
        return V2_AUX3 if getattr(self, "_has_refresh", False) else V2_AUX2

    def _img_v2(self, w):
        """Nova's v2 composite: nebula background + LISTEN and READ ALOUD
        buttons side by side + keep-warm / (refresh) / menu. Cached per
        (mic-state, reading?, warm, width)."""
        warm = getattr(self.rec, "warm", False)
        reading = getattr(self, "read_active", False)
        key = ("v2", self.v2_theme, self.state, reading, warm,
               getattr(self, "bt_ok", False), getattr(self, "bg_dim", None), w)
        if key not in self.imgs:
            h = int(w * V2_ASPECT)
            tdir = v2_dir(self.v2_theme)
            # optional theme.json: bg_fade dims the BACKGROUND only (the art
            # is lovely AND it's visual noise for some eyes — buttons stay
            # full strength); button_ring draws a locator ring per control.
            # The user's own dim choice (menu slider) overrides the theme's.
            cfg = {}
            try:
                with open(os.path.join(tdir, "theme.json"),
                          encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                pass
            bg = Image.open(os.path.join(tdir, "background.png")).convert("RGBA")
            c = bg.resize((w, h), Image.LANCZOS)
            fade = (self.bg_dim if getattr(self, "bg_dim", None) is not None
                    else float(cfg.get("bg_fade", 0)))
            if fade > 0:
                col = tuple(cfg.get("bg_fade_color", [0, 0, 0]))
                veil = Image.new("RGBA", c.size, col + (int(255 * fade),))
                c.alpha_composite(veil)
            ring = cfg.get("button_ring")
            ring_w = max(2, int(float(cfg.get("ring_width_frac", 0.012)) * w))

            def draw_ring(cx, cy, r):
                if not ring:
                    return
                rd = ImageDraw.Draw(c)
                rr = r * w * 1.04
                rd.ellipse([cx * w - rr, cy * h - rr,
                            cx * w + rr, cy * h + rr],
                           outline=tuple(ring), width=ring_w)

            def put(name, cx, cy, r, dim=False):
                d = int(2 * r * w)
                p = os.path.join(tdir, name)
                if not os.path.exists(p):
                    # incomplete theme: borrow the piece from another theme
                    # rather than crash — art lands incrementally
                    for t in v2_themes():
                        alt = os.path.join(v2_dir(t), name)
                        if os.path.exists(alt):
                            p = alt
                            break
                    else:
                        return
                im = Image.open(p).convert("RGBA")
                im.thumbnail((d, d), Image.LANCZOS)
                if dim:   # sleeping state: present but clearly not active
                    a = im.getchannel("A").point(lambda v: int(v * 0.68))
                    im.putalpha(a)
                c.alpha_composite(im, (int(cx * w - im.width / 2),
                                       int(cy * h - im.height / 2)))

            put(V2_STATE_ART[self.state], *V2_MIC)
            draw_ring(*V2_MIC)
            put("read.png", *V2_READ, dim=not reading)
            draw_ring(*V2_READ)
            aux = self._v2_aux()
            for pos in aux.values():
                draw_ring(*pos)
            put("keepwarm.png", *aux["warm"], dim=not warm)
            if "refresh" in aux:
                if getattr(self, "bt_ok", False):
                    # green halo = the microphone link is verifiably alive
                    # RIGHT NOW (audio frames flowing, or Windows sees the
                    # mic). Ren, 8/15: "sometimes it's hard to know if it's
                    # there." A good state deserves a visible name too.
                    cx, cy, r = aux["refresh"]
                    gx, gy = cx * w, cy * h
                    ring = Image.new("RGBA", c.size, (0, 0, 0, 0))
                    rd = ImageDraw.Draw(ring)
                    for i, alpha in ((6, 60), (4, 110), (2, 200)):
                        rr = r * w + i
                        rd.ellipse([gx - rr, gy - rr, gx + rr, gy + rr],
                                   outline=(74, 222, 128, alpha),
                                   width=3)
                    c.alpha_composite(ring)
                put("refresh.png", *aux["refresh"])
            put("menu.png", *aux["menu"])
            flat = Image.new("RGBA", c.size, KEYCOL)
            flat.alpha_composite(c)
            self.imgs[key] = ImageTk.PhotoImage(flat.convert("RGB"))
        return self.imgs[key]

    def _refresh(self):
        w = max(120, int(220 * self.scale))
        art = have_art() and self.skin == "illustrated"
        if self.skin == "illustrated" and have_v2():
            self.card.configure(image=self._img_v2(w))
            self._clamp_header(w)
        else:
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
            self._clamp_header(w)
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

    def _clamp_header(self, card_w):
        """Header strip width = the card's width exactly (or the buttons'
        minimum if they genuinely need more) — edges line up with the art."""
        try:
            need = sum(ch.winfo_reqwidth()
                       for ch in self.header.winfo_children()
                       if ch.winfo_manager())
            self.header.configure(width=max(card_w, need))
        except Exception:
            pass

    def set_state(self, s):
        self.state = s
        # Grandma semantics: GREEN = it hears you, amber = working. Red is
        # reserved for genuinely-broken, which is none of these states.
        hdr = getattr(self, "_hdr", {"bg": "#221833", "fg": "#9a86b8"})
        colors = {"off": hdr["fg"], "on": "#4ade80", "think": "#c9a227"}
        bgs = {"on": hdr.get("on_bg", "#0d3a1a")}
        try:
            self.mic_btn.config(fg=colors.get(s, hdr["fg"]),
                                bg=bgs.get(s, hdr["bg"]))
        except Exception:
            pass
        self._refresh()

    def toggle_warm(self):
        """Flip keep-warm; the 🔥 header button IS the indicator (bright when
        holding the link) and the card's flame glows/sleeps to match."""
        warm = getattr(self.rec, "warm", False)
        self.rec.set_warm(not warm)
        now_on = getattr(self.rec, "warm", False)
        hdr = getattr(self, "_hdr", {"bg": "#221833", "fg": "#9a86b8"})
        try:
            self.warm_btn.config(fg=("#ffb347" if now_on else hdr["fg"]),
                                 bg=(hdr.get("warm_bg", "#3a2410") if now_on
                                     else hdr["bg"]))
        except Exception:
            pass
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

    def _v2_hit(self, x, y):
        w, h = self.card.winfo_width(), self.card.winfo_height()
        def inside(cx, cy, r):
            return ((x - w * cx) ** 2 + (y - h * cy) ** 2) ** 0.5 <= w * r
        if inside(*V2_MIC):
            return "state"
        if inside(*V2_READ):
            return "read"
        for name, box in self._v2_aux().items():
            if inside(*box):
                return name
        return None

    def _release(self, e):
        if not self._moved:
            if self.skin == "illustrated" and have_v2():
                hit = self._v2_hit(e.x, e.y)
                if hit == "state":
                    self.toggle()
                elif hit == "read":
                    self.read_clipboard()
                elif hit == "warm":
                    self.toggle_warm()
                elif hit == "refresh":
                    self._do_bt_refresh()
                elif hit == "menu":
                    self._menu(e)
            else:
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
        log(f"transcribed {len(text)} chars: {text[:80]}")

    def _cancel(self):
        if self.rec.recording:
            self.rec.cancelled = True
            self.toggle()

    def read_clipboard(self):
        try:
            text = self.root.clipboard_get()
        except Exception:
            print("clipboard is empty — copy something first."); return
        import voices
        def run():
            try:
                voices.speak(text, self.tts_engine, self.tts_voice)
            finally:   # 🔊 + card button glow while reading, rest when done
                def done():
                    self.read_active = False
                    try:
                        self.read_hdr.config(
                            fg=getattr(self, "_hdr", {}).get("fg", "#9a86b8"))
                    except Exception:
                        pass
                    self._refresh()
                self.root.after(0, done)
        self.read_active = True
        try:
            self.read_hdr.config(fg="#4fc3f7")
        except Exception:
            pass
        self._refresh()
        threading.Thread(target=run, daemon=True).start()

    # -------- voice settings (BYO-key tiers, 2026-08-15)
    def _voice_settings(self):
        """Engine picker + key entry + voice list. Windows voices stay the
        default; Inworld/ElevenLabs are bring-your-own-key — the key goes
        straight from this machine to the provider, DPAPI-encrypted on disk,
        never to us."""
        import voices
        from tkinter import ttk
        d = tk.Toplevel(self.root)
        d.title("Read-aloud voice")
        d.attributes("-topmost", True)
        d.resizable(False, False)
        pad = {"padx": 12, "pady": 4}

        tk.Label(d, text="Who should read to you?",
                 font=("Segoe UI", 12, "bold")).pack(anchor="w", **pad)
        eng_var = tk.StringVar(value=self.tts_engine)
        for eng in voices.ENGINES:
            tk.Radiobutton(d, text=voices.ENGINE_LABELS[eng], value=eng,
                           variable=eng_var, font=("Segoe UI", 11),
                           command=lambda: refresh()).pack(anchor="w", **pad)

        key_frame = tk.Frame(d)
        tk.Label(key_frame, text="Your API key:",
                 font=("Segoe UI", 10)).pack(side="left")
        key_var = tk.StringVar()
        key_entry = tk.Entry(key_frame, textvariable=key_var, show="•",
                             width=34, font=("Segoe UI", 10))
        key_entry.pack(side="left", padx=6)
        status = tk.Label(d, text="", font=("Segoe UI", 9), wraplength=340,
                          justify="left")
        note = tk.Label(d, text="", font=("Segoe UI", 9), fg="#666666",
                        wraplength=340, justify="left")

        tk.Label(d, text="Voice:", font=("Segoe UI", 10)).pack(anchor="w", **pad)
        voice_box = ttk.Combobox(d, state="readonly", width=40)
        voice_box.pack(**pad)
        vmap = {}   # label -> voice id

        def load_voices():
            eng = eng_var.get()
            if eng == "windows":
                vs = voices.list_windows_voices()
            else:
                vs = voices.cached_catalog(eng)
            vmap.clear()
            labels = ["(default)"]
            for vid, label in vs:
                vmap[label] = vid
                labels.append(label)
            voice_box["values"] = labels
            cur = "(default)"
            for label, vid in vmap.items():
                if vid == self.tts_voice:
                    cur = label
            voice_box.set(cur)

        def refresh():
            eng = eng_var.get()
            if eng == "windows":
                key_frame.pack_forget(); status.pack_forget(); note.pack_forget()
            else:
                key_frame.pack(anchor="w", **pad)
                status.pack(anchor="w", **pad)
                note.config(text=("Needs your own account with the provider — "
                                  "their pricing, billed to you. The key stays "
                                  "on this computer, encrypted; it is never "
                                  "sent to us."))
                note.pack(anchor="w", **pad)
                if voices.load_key(eng):
                    status.config(text="A key is already saved ✓ (paste a new "
                                       "one to replace it)", fg="#2e7d32")
                else:
                    status.config(text="No key saved yet.", fg="#666666")
            load_voices()

        def test_and_save():
            eng = eng_var.get()
            key = key_var.get().strip() or (voices.load_key(eng) or "")
            if not key:
                status.config(text="Paste a key first.", fg="#c62828"); return
            status.config(text="Testing… (you should hear a hello)",
                          fg="#666666")
            d.update_idletasks()
            def worker():
                ok, msg = voices.test_key(eng, key)
                def done():
                    status.config(text=msg,
                                  fg=("#2e7d32" if ok else "#c62828"))
                    if ok:
                        load_voices()
                self.root.after(0, done)
            threading.Thread(target=worker, daemon=True).start()

        btns = tk.Frame(d)
        tk.Button(btns, text="Test key (says hello)", font=("Segoe UI", 10),
                  command=test_and_save).pack(side="left", padx=4)

        def save():
            self.tts_engine = eng_var.get()
            sel = voice_box.get()
            self.tts_voice = vmap.get(sel)   # "(default)" -> None
            self._save_settings()
            d.destroy()
        tk.Button(btns, text="Save", font=("Segoe UI", 10, "bold"),
                  command=save).pack(side="left", padx=4)
        tk.Button(btns, text="Cancel", font=("Segoe UI", 10),
                  command=d.destroy).pack(side="left", padx=4)
        btns.pack(pady=10)
        refresh()

    # -------- menu
    def _menu_kb(self):
        """Ctrl+Alt+M — the KEYBOARD menu. The widget deliberately never
        takes focus (so dictation pastes land in your document), which means
        Tab can never reach it — and that locked keyboard-only users out of
        the menu entirely (Ren caught it, 8/15). This is a real focusable
        window: arrows, Enter, Escape, first-letter jump all work."""
        items = self._menu_items()
        d = tk.Toplevel(self.root)
        d.title("Chaos Capture menu — arrows + Enter, Esc closes")
        d.attributes("-topmost", True)
        lb = tk.Listbox(d, font=("Segoe UI", 12), width=52,
                        height=min(len(items), 18), activestyle="dotbox")
        for label, _ in items:
            lb.insert("end", " " + label)
        lb.pack(padx=8, pady=8)
        lb.selection_set(0)
        lb.activate(0)
        lb.focus_set()
        def run(_e=None):
            sel = lb.curselection()
            if sel:
                fn = items[sel[0]][1]
                d.destroy()
                if fn:
                    fn()
        lb.bind("<Return>", run)
        lb.bind("<Double-1>", run)
        d.bind("<Escape>", lambda e: d.destroy())
        d.focus_force()

    def _menu_items(self):
        """Flat action list for the keyboard menu — every mouse-menu power,
        one row each, current state readable in the label."""
        items = []
        rec = self.rec
        items.append(("🎙 Start / stop talking (Ctrl+Alt+D)", self.toggle))
        items.append(("🔊 Read the copied text aloud (Ctrl+Alt+R)",
                      self.read_clipboard))
        warm = getattr(rec, "warm", False)
        items.append((("🔥 Keep mic warm: ON — turn off" if warm else
                       "🔥 Keep mic warm: off — turn on"), self.toggle_warm))
        if getattr(self, "_has_refresh", False):
            items.append(("🔄 Refresh stale Bluetooth", lambda: threading.Thread(
                target=lambda: subprocess.run(
                    ["schtasks", "/run", "/tn", "BluetoothRefresh"],
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW),
                daemon=True).start()))
        for t in v2_themes():
            on = self.skin == "illustrated" and self.v2_theme == t
            items.append((f"Look: 🎨 {t}" + (" (current)" if on else ""),
                          lambda t=t: self._set_look("illustrated", t)))
        items.append(("Look: 🔲 simple high-contrast"
                      + (" (current)" if self.skin == "simple" else ""),
                      lambda: self._set_look("simple", self.v2_theme)))
        for val, lbl in ((None, "theme default"), (0.0, "full art"),
                         (0.3, "30% dimmed"), (0.55, "55% dimmed"),
                         (0.8, "80% dimmed")):
            items.append((f"🌗 Background: {lbl}"
                          + (" (current)" if self.bg_dim == val else ""),
                          lambda v=val: self._set("bg_dim", v)))
        for s, name in ((1.0, "full"), (0.75, "medium"), (0.6, "small")):
            items.append((f"Size: {name}"
                          + (" (current)" if abs(self.scale - s) < 0.01 else ""),
                          lambda s=s: self._set("scale", s)))
        for t in ("auto", "dark", "light"):
            items.append((f"Theme: {t}"
                          + (" (current)" if self.theme_pref == t else ""),
                          lambda t=t: self._set("theme_pref", t)))
        items.append(("⌨ Keyboard shortcuts card", self._show_shortcuts))
        items.append(("🗣 Read-aloud voice settings", self._voice_settings))
        items.append(("📖 My dictionary", self._edit_dictionary))
        items.append(("🪄 Setup helper (wizard)", lambda: Wizard(self)))
        if getattr(sys, "frozen", False):
            dsk, stp = has_shortcut("desktop"), has_shortcut("startup")
            items.append((("🖥️ Remove desktop icon" if dsk else
                           "🖥️ Put an icon on my desktop"),
                          lambda: set_shortcut("desktop", not dsk)))
            items.append((("🚀 Stop starting with Windows" if stp else
                           "🚀 Start when my computer starts"),
                          lambda: set_shortcut("startup", not stp)))
        items.append(("─ Hide the widget (Ctrl+Alt+H)", self.minimize))
        items.append(("Quit Chaos Capture", self.shutdown))
        return items

    def _menu(self, e):
        hdr = getattr(self, "_hdr", {"bg": "#221833", "fg": "#9a86b8"})
        m = tk.Menu(self.root, tearoff=0, bg=hdr["bg"], fg=hdr["fg"],
                    activebackground=hdr["fg"], activeforeground=hdr["bg"])
        m.add_command(label="✖ close this menu", command=lambda: None)
        m.add_separator()
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
        themes = v2_themes()
        if themes or have_art():
            for t in themes:
                on = self.skin == "illustrated" and self.v2_theme == t
                m.add_command(
                    label=f"Look: 🎨 {t}" + (" ✓" if on else ""),
                    command=lambda t=t: self._set_look("illustrated", t))
            m.add_command(
                label="Look: 🔲 simple high-contrast (self-drawn)"
                      + (" ✓" if self.skin == "simple" else ""),
                command=lambda: self._set_look("simple", self.v2_theme))
            # background-dim slider: love the art AND see past it — every
            # pair of eyes gets its own setting (Ren, 8/15)
            dm = tk.Menu(m, tearoff=0)
            for val, label in ((None, "theme's own default"),
                               (0.0, "full art"),
                               (0.3, "calmer (30% dimmed)"),
                               (0.55, "calm (55% dimmed)"),
                               (0.8, "calmest (80% dimmed)")):
                dm.add_command(
                    label=label + (" ✓" if self.bg_dim == val else ""),
                    command=lambda v=val: self._set("bg_dim", v))
            m.add_cascade(label="🌗 Background dimming", menu=dm)
            m.add_separator()
        warm = self.rec.warm
        m.add_command(
            label=("Keep mic warm: ON (click to release)" if warm else
                   "Keep mic warm: off (click to hold open)"),
            command=self.toggle_warm)
        if not getattr(self, "_has_refresh", False):
            m.add_command(label="🔄 Set up Bluetooth refresh (fixes stale "
                                "hearing-aid links — one admin OK)",
                          command=self._setup_refresh)
        m.add_separator()
        m.add_command(label="⌨ Keyboard shortcuts (a reminder card)",
                      command=self._show_shortcuts)
        m.add_command(label="🗣 Read-aloud voice (pick who reads to you)",
                      command=self._voice_settings)
        m.add_command(label="📖 My dictionary (names it should spell right)",
                      command=self._edit_dictionary)
        m.add_command(label="🪄 Setup helper (the welcome questions again)",
                      command=lambda: Wizard(self))
        if getattr(sys, "frozen", False):
            dsk, stp = has_shortcut("desktop"), has_shortcut("startup")
            m.add_command(
                label=("🖥️ Desktop icon: on it — click to remove" if dsk
                       else "🖥️ Put an icon on my desktop"),
                command=lambda: set_shortcut("desktop", not dsk))
            m.add_command(
                label=("🚀 Starts with Windows ✓ — click to stop" if stp
                       else "🚀 Start when my computer starts"),
                command=lambda: set_shortcut("startup", not stp))
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
        m.add_command(label="Quit Chaos Capture", command=self.shutdown)
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
            os.makedirs(brain_dir(), exist_ok=True)
            say("Downloading engine (step 1 of 2)...")
            data = urllib.request.urlopen(BRAIN_ZIP_URL, timeout=60).read()
            zf = zipfile.ZipFile(io.BytesIO(data))
            for name in zf.namelist():
                base = os.path.basename(name)
                if base and (base.endswith(".exe") or base.endswith(".dll")):
                    with open(os.path.join(brain_dir(), base), "wb") as f:
                        f.write(zf.read(name))
            say("Downloading model (step 2 of 2)...\nThis is the big one "
                "(~148MB) — a few minutes on slow internet.")

            def hook(blocks, bsize, total):
                if total > 0:
                    pct = min(100, blocks * bsize * 100 // total)
                    say(f"Downloading model (step 2 of 2)... {pct}%")
            urllib.request.urlretrieve(BRAIN_MODEL_URL, brain_model(), hook)

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

    def _show_shortcuts(self):
        """A plain reminder card. Everyone forgets hotkeys — Ren forgot them
        the same afternoon they were designed (8/15), so Grandma has no
        chance. Lives one click away, closes with one click."""
        d = tk.Toplevel(self.root)
        d.title("Keyboard shortcuts")
        d.attributes("-topmost", True)
        d.resizable(False, False)
        rows = [
            ("Ctrl + Alt + D", "start talking / finish talking"),
            ("Ctrl + Alt + R", "read the copied text out loud"),
            ("Ctrl + Alt + M", "keyboard menu (arrows + Enter, Esc closes)"),
            ("Ctrl + Alt + H", "hide the widget / bring it back"),
            ("Ctrl + Alt + arrows", "nudge the widget around"),
            ("Ctrl + Alt + 1 2 3 4", "snap to a corner of the screen"),
            ("Esc", "cancel talking (nothing is typed)"),
            ("Ctrl + Alt + Q", "quit"),
        ]
        grid = tk.Frame(d)
        for i, (keys, what) in enumerate(rows):
            tk.Label(grid, text=keys, font=("Consolas", 12, "bold"),
                     anchor="e").grid(row=i, column=0, sticky="e",
                                      padx=(14, 10), pady=3)
            tk.Label(grid, text=what, font=("Segoe UI", 11),
                     anchor="w").grid(row=i, column=1, sticky="w",
                                      padx=(0, 14), pady=3)
        grid.pack(pady=(12, 4))
        tk.Label(d, text="Everything here also works by clicking — "
                         "the buttons do the same things.",
                 font=("Segoe UI", 9), fg="#666666").pack(pady=(2, 6))
        tk.Button(d, text="Got it", font=("Segoe UI", 11, "bold"),
                  command=d.destroy).pack(pady=(0, 12))

    def shutdown(self):
        """Close GENTLY: release the mic stream BEFORE the window dies.
        Killing the process while keep-warm holds an open Bluetooth stream
        can wedge the Windows BT audio driver and drop the user's hearing
        aids — discovered the hard way on the aids this app was built for
        (8/15). Grandma's aids get the same protection."""
        try:
            self.rec.recording = False
            self.rec.set_warm(False)
            if self.rec.stream:
                try:
                    self.rec.stream.stop(); self.rec.stream.close()
                except Exception:
                    pass
                self.rec.stream = None
        except Exception:
            pass
        self.root.destroy()

    def _do_bt_refresh(self):
        """Run the bounce WITH visible feedback: the 🔄 goes green while the
        radio bounces (a mute button reads as a dead button — Ren, one
        dozen clicks, 8/15)."""
        log("BT refresh: bouncing radio via helper task")
        try:
            self.bt_btn.config(fg="#4ade80")
            self.root.after(15000, lambda: self.bt_btn.config(
                fg=getattr(self, "_hdr", {}).get("fg", "#9a86b8")))
        except Exception:
            pass
        threading.Thread(
            target=lambda: subprocess.run(
                ["schtasks", "/run", "/tn", "BluetoothRefresh"],
                capture_output=True,
                creationflags=subprocess.CREATE_NO_WINDOW),
            daemon=True).start()

    def _setup_refresh(self):
        """One UAC yes → the promptless bounce helper exists → the 🔄 button
        appears everywhere it belongs. (Ren, 8/15: 'why did we not just
        build the same bouncing option for everybody?' No reason. Built.)"""
        from tkinter import messagebox
        messagebox.showinfo(
            "One admin OK needed",
            "Windows is about to show a permission box (it may flash on the "
            "taskbar or appear behind windows — look for the shield icon).\n\n"
            "Say YES to it, and the Bluetooth refresh button will appear.")
        def worker():
            log("BT helper setup: launching elevated installer...")
            ok = setup_bt_refresh()
            log(f"BT helper setup result: {'INSTALLED' if ok else 'NOT installed'}")
            def done():
                from tkinter import messagebox
                if ok:
                    self._has_refresh = True
                    self.imgs = {k: v for k, v in self.imgs.items()
                                 if not (isinstance(k, tuple) and k[0] == "v2")}
                    try:
                        self.bt_btn.pack(side="left", after=self.warm_btn)
                    except Exception:
                        pass
                    self._refresh()
                    messagebox.showinfo(
                        "Bluetooth refresh ready ✓",
                        "The 🔄 button is now on the card and the top strip. "
                        "Tap it whenever a Bluetooth device is connected but "
                        "acting deaf — everything re-handshakes in ~15 seconds.")
                else:
                    messagebox.showwarning(
                        "Not set up",
                        "The helper didn't install — the admin box may have "
                        "been closed or declined. The menu row will offer it "
                        "again anytime.\n\nDetails: capture_log.txt next to "
                        "the app.")
            self.root.after(0, done)
        threading.Thread(target=worker, daemon=True).start()

    def _theme_cfg(self):
        try:
            with open(os.path.join(v2_dir(self.v2_theme), "theme.json"),
                      encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _apply_header_theme(self):
        """The chrome dresses for the theme: header + popup colors come from
        theme.json, defaulting to the octopus purple."""
        cfg = self._theme_cfg()
        sb = cfg.get("state_bgs", {})
        self._hdr = {"bg": cfg.get("header_bg", "#221833"),
                     "fg": cfg.get("header_fg", "#9a86b8"),
                     "on_bg": sb.get("on", "#0d3a1a"),
                     "warm_bg": sb.get("warm", "#3a2410")}
        try:
            self.header.configure(bg=self._hdr["bg"])
            for wdg in (self.grip, self.mic_btn, self.read_hdr, self.bt_btn,
                        self.close_btn, self.gear, self.min_btn):
                wdg.configure(bg=self._hdr["bg"], fg=self._hdr["fg"])
            warm = getattr(self.rec, "warm", False)
            self.warm_btn.configure(
                bg=(self._hdr["warm_bg"] if warm else self._hdr["bg"]),
                fg=("#ffb347" if warm else self._hdr["fg"]))
            self.set_state(self.state)
        except Exception:
            pass

    def _set(self, attr, val):
        setattr(self, attr, val)
        if attr == "skin":
            self._apply_features()   # v2 hides the read bar; simple shows it
        self._refresh()
        self.root.update_idletasks()
        self._save_settings()

    def _set_look(self, skin, theme):
        self.skin, self.v2_theme = skin, theme
        self._apply_features()
        self._apply_header_theme()
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
