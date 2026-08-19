"""Read-aloud voice engines for Chaos Capture.

Three tiers (Ren's design, 2026-08-15):
  windows     — built-in, free, zero setup. The default. Grandma never
                sees the word "API".
  inworld     — bring-your-own-key, ~283 voices (api.inworld.ai).
  elevenlabs  — bring-your-own-key, their voice library (api.elevenlabs.io).

Keys NEVER touch us: calls go straight from this machine to the provider,
billed to the user's own account. Keys are stored DPAPI-encrypted (the same
Windows user-account crypto the Credential Manager uses) so they can't be
read from a copied/emailed settings folder — and they deliberately live in
their own file, not capture_settings.json, because people paste settings
files into bug reports.
"""
import os, sys, json, base64, ctypes, ctypes.wintypes, subprocess, tempfile

HERE = (os.path.dirname(os.path.abspath(sys.executable))
        if getattr(sys, "frozen", False)
        else os.path.dirname(os.path.abspath(__file__)))
KEYS_PATH = os.path.join(HERE, "voice_keys.dat")
CATALOG_PATH = os.path.join(HERE, "voice_catalog.json")

ENGINES = ("windows", "inworld", "elevenlabs")
ENGINE_LABELS = {"windows": "Windows voices (free, built in)",
                 "inworld": "Inworld (your own key)",
                 "elevenlabs": "ElevenLabs (your own key)"}

# ------------------------------------------------------------------ DPAPI

class _BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]

def _to_blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))

def _dpapi(data, protect):
    src, out = _to_blob(data), _BLOB()
    fn = (ctypes.windll.crypt32.CryptProtectData if protect
          else ctypes.windll.crypt32.CryptUnprotectData)
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("DPAPI failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)

def save_key(engine, key):
    keys = {}
    try:
        with open(KEYS_PATH, encoding="utf-8") as f:
            keys = json.load(f)
    except Exception:
        pass
    keys[engine] = base64.b64encode(_dpapi(key.strip().encode(), True)).decode()
    with open(KEYS_PATH, "w", encoding="utf-8") as f:
        json.dump(keys, f)

def load_key(engine):
    try:
        with open(KEYS_PATH, encoding="utf-8") as f:
            blob = json.load(f).get(engine)
        return _dpapi(base64.b64decode(blob), False).decode() if blob else None
    except Exception:
        return None

# ------------------------------------------------------------------ powershell

def run_ps(script, *args, timeout=None):
    """Run a PowerShell snippet WITH arguments via a temp .ps1 and -File.
    Never use -Command with trailing args: PowerShell glues them onto the
    script text, and a bare file path 'executed' that way ShellExecutes —
    which is how Ctrl+Alt+R once opened the clipboard in Notepad instead of
    reading it aloud (found by Ren, 8/15). -File passes args as $args."""
    f = tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False,
                                    encoding="utf-8-sig")
    f.write(script); f.close()
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", f.name, *[str(a) for a in args]],
        capture_output=True, text=True, timeout=timeout,
        creationflags=subprocess.CREATE_NO_WINDOW)

# ------------------------------------------------------------------ playback

_PLAY_PS = r"""
Add-Type -AssemblyName PresentationCore
$p = New-Object System.Windows.Media.MediaPlayer
$p.Open([Uri]$args[0]); $p.Play()
$t = 0
while (-not $p.NaturalDuration.HasTimeSpan -and $t -lt 100) { Start-Sleep -m 100; $t++ }
if ($p.NaturalDuration.HasTimeSpan) {
  Start-Sleep -m ([int]$p.NaturalDuration.TimeSpan.TotalMilliseconds + 200)
}
$p.Close()
"""

def _play_mp3_blocking(path):
    run_ps(_PLAY_PS, path)

def _chunks(text, max_len=1800):
    """Split on paragraph, then sentence boundaries, under provider limits."""
    out, cur = [], ""
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if len(cur) + len(para) < max_len:
            cur += " " + para
        else:
            if cur.strip():
                out.append(cur.strip())
            if len(para) < max_len:
                cur = para
            else:
                cur = ""
                import re
                acc = ""
                for s in re.split(r"(?<=[.!?])\s+", para):
                    if len(acc) + len(s) < max_len:
                        acc += " " + s
                    else:
                        if acc.strip():
                            out.append(acc.strip())
                        acc = s
                if acc.strip():
                    out.append(acc.strip())
    if cur.strip():
        out.append(cur.strip())
    return out

# ------------------------------------------------------------------ engines

_WIN_TTS_PS = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.Rate = 0
if ($args.Count -gt 1 -and $args[1]) { try { $s.SelectVoice($args[1]) } catch {} }
$s.Speak([IO.File]::ReadAllText($args[0]))
"""

def _speak_windows(text, voice):
    f = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                    encoding="utf-8")
    f.write(text); f.close()
    if voice:
        run_ps(_WIN_TTS_PS, f.name, voice)
    else:
        run_ps(_WIN_TTS_PS, f.name)

def _speak_inworld(text, voice, key):
    import urllib.request
    for chunk in _chunks(text):
        payload = json.dumps({
            "text": chunk, "voiceId": voice or "Ashley",
            "modelId": "inworld-tts-2",
            "audioConfig": {"audioEncoding": "MP3"},
            "deliveryMode": "BALANCED", "language": "AUTO"}).encode()
        req = urllib.request.Request(
            "https://api.inworld.ai/tts/v1/voice", data=payload,
            headers={"Authorization": "Basic " + key,
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            resp = json.load(r)
        audio = resp.get("audioContent") or resp.get("result", {}).get("audioContent")
        if not audio:
            raise RuntimeError("no audio in Inworld response")
        f = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        f.write(base64.b64decode(audio)); f.close()
        _play_mp3_blocking(f.name)

def _speak_elevenlabs(text, voice, key):
    import urllib.request
    vid = voice or "21m00Tcm4TlvDq8ikWAM"   # Rachel, their classic default
    for chunk in _chunks(text, 2400):
        payload = json.dumps({"text": chunk,
                              "model_id": "eleven_multilingual_v2"}).encode()
        req = urllib.request.Request(
            f"https://api.elevenlabs.io/v1/text-to-speech/{vid}"
            "?output_format=mp3_44100_128",
            data=payload,
            headers={"xi-api-key": key, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            audio = r.read()
        f = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        f.write(audio); f.close()
        _play_mp3_blocking(f.name)

def speak(text, engine="windows", voice=None):
    """Blocking — call from a worker thread. Falls back to Windows voices on
    any paid-engine failure so the read button NEVER just does nothing."""
    try:
        if engine == "inworld":
            key = load_key("inworld")
            if not key:
                raise RuntimeError("no Inworld key saved")
            _speak_inworld(text, voice, key)
        elif engine == "elevenlabs":
            key = load_key("elevenlabs")
            if not key:
                raise RuntimeError("no ElevenLabs key saved")
            _speak_elevenlabs(text, voice, key)
        else:
            _speak_windows(text, voice)
    except Exception as e:
        print(f"({engine} voice failed: {e} — using Windows voice instead)")
        _speak_windows(text, None)

# ------------------------------------------------------------------ catalogs

def list_windows_voices():
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Add-Type -AssemblyName System.Speech; "
             "(New-Object System.Speech.Synthesis.SpeechSynthesizer)."
             "GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }"],
            capture_output=True, text=True, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW)
        return [(v.strip(), v.strip()) for v in out.stdout.splitlines()
                if v.strip()]
    except Exception:
        return []

def list_provider_voices(engine, key):
    """[(voice_id, label)] fetched with the USER'S key; cached to disk."""
    import urllib.request
    if engine == "inworld":
        req = urllib.request.Request(
            "https://api.inworld.ai/tts/v1/voices",
            headers={"Authorization": "Basic " + key})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r)
        voices = sorted(
            {(v["voiceId"], (v.get("languages") or ["?"])[0])
             for v in data.get("voices", []) if v.get("voiceId")})
        return [(vid, f"{vid} ({lang})" if lang != "en" else vid)
                for vid, lang in voices]
    if engine == "elevenlabs":
        req = urllib.request.Request(
            "https://api.elevenlabs.io/v1/voices",
            headers={"xi-api-key": key})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r)
        return [(v["voice_id"], v.get("name", v["voice_id"]))
                for v in data.get("voices", [])]
    return []

def cache_catalog(engine, voices):
    cat = {}
    try:
        with open(CATALOG_PATH, encoding="utf-8") as f:
            cat = json.load(f)
    except Exception:
        pass
    cat[engine] = voices
    with open(CATALOG_PATH, "w", encoding="utf-8") as f:
        json.dump(cat, f)

def cached_catalog(engine):
    try:
        with open(CATALOG_PATH, encoding="utf-8") as f:
            return [tuple(v) for v in json.load(f).get(engine, [])]
    except Exception:
        return []

def test_key(engine, key):
    """Validate by fetching the voice list, then speak a hello with the first
    voice. Returns (ok, message)."""
    try:
        voices = list_provider_voices(engine, key)
        if not voices:
            return False, "The key worked but no voices came back."
        save_key(engine, key)
        cache_catalog(engine, voices)
        first = voices[0][0]
        if engine == "inworld":
            _speak_inworld("Hi! I'm one of your new voices.", first, key)
        else:
            _speak_elevenlabs("Hi! I'm one of your new voices.", first, key)
        return True, f"It works! {len(voices)} voices available."
    except Exception as e:
        msg = str(e)
        if "401" in msg or "403" in msg:
            msg = ("That key didn't work — check it copied completely, "
                   "with no extra spaces.")
        return False, msg
