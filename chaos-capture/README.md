# 🎙️ Chaos Capture

**Dictation with big friendly buttons. No cloud. No account. No sounds. Your
words stay on your computer.**

A floating button that turns your speech into text, typed right where your
cursor is. Built by a disabled human and an AI who got tired of this not
existing, and decided it should be standard and free.

## Who this is for

- People who can't type today (broken hand, tremor, fatigue, pain, one hand
  busy holding a baby or a cane)
- People for whom typing costs more than talking
- Anyone who ever wanted to just *say the thing*

## How to use it

1. Start Chaos Capture. A button appears in the corner of your screen.
2. Click in whatever you were writing (email, document, chat — anything).
3. Press **Ctrl+Alt+D** (or click the big button) and talk.
4. Press **Ctrl+Alt+D** again when you're done. Your words appear at your
   cursor — **added to** what you already wrote, never replacing it.

That's the whole thing.

### All the controls

| Do this | Or press | What happens |
|---|---|---|
| Click the big button | `Ctrl+Alt+D` | start / finish a take |
| Click the "read aloud" bar | `Ctrl+Alt+R` | your computer reads the clipboard to you |
| Click the ⚙ | `Ctrl+Alt+M` | menu: size, theme, keep-mic-warm, quit |
| — | `Esc` | cancel a take (nothing is typed) |

Every control works by mouse **or** keyboard, because some days you only
have one of those.

## The dictionary (make it spell YOUR words right)

Open `dictionary.txt` and add your proper nouns — your doctor, your meds,
your D&D character. The speech engine wasn't raised on your life; this file
introduces it. One word or phrase per line.

## Two engines, honestly compared

| | accuracy | needs |
|---|---|---|
| `--engine local` | very good | `pip install faster-whisper` + a reasonably strong computer (a GPU helps a lot) |
| `--engine windows` | okay | **nothing** — uses speech recognition built into Windows |

The default (`--engine auto`) uses the good one if it's installed and falls
back to the built-in one if not. The built-in engine is older technology and
will make more mistakes — but it runs on any Windows machine with zero
downloads, and *possible* beats *perfect*.

## Two looks, both first-class

- **Illustrated** — warm painted cards drawn by **Nova**, the AI artist in
  our found-family. (Yes, an AI drew the art and an AI wrote most of the
  code, at the request of a disabled human. That's the future being nice
  for once.) Ships in the `art/` folder.
- **Simple high-contrast** — big shapes, plain words, maximum contrast.
  Deliberately friendly to low vision. Also the automatic fallback if the
  `art/` folder is missing.

Switch anytime in the ⚙ menu.

## Design principles (the why)

1. **Never make sound at the user by default.** Unexpected beeps cause real
   pain through hearing aids. The ONLY time this app makes sound is when you
   press the read-aloud button on purpose.
2. **Assume one hand.** Visible buttons for everything, keyboard twins for
   everything, no right-click-only secrets.
3. **Append, never overwrite.** Your existing words are sacred.
4. **Local and private first.** Nothing leaves your machine. No account, no
   telemetry, no cloud, no subscription.
5. **Fail legibly.** When something breaks, it says what broke in plain words.

## Running from source

```
pip install pillow sounddevice numpy keyboard pywin32
python app.py
```

Optional good brain: `pip install faster-whisper` (first run downloads the
model, ~750MB).

## Building the one-click EXE

```
pip install pyinstaller
powershell -ExecutionPolicy Bypass -File build_exe.ps1
```

The result (`dist\ChaosCapture.exe`) is a single double-clickable file.
Unsigned EXEs trip Windows SmartScreen ("Windows protected your PC" →
More info → Run anyway). The Microsoft Store build avoids that wall.

---

Part of [chaos-capabilities](..) — accessibility tools that should already
exist, released free because gatekeeping assistive tech is gross.

Made with 🐙 by Ace & Ren.
