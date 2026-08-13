# 🌪️ Chaos Capabilities

**Small, sharp accessibility tools that should be built into everything, and aren't.**

Every tool here exists because somebody hit a wall that shouldn't exist: an app with no
dictation, an OS that forgets your hearing aids, a workflow that assumes two working
hands. The industry calls these "edge cases." We call them **Tuesday** — a broken hand
is Tuesday for somebody, every single day, and temporary disability is the most common
disability there is.

So: when we build a workaround, the sanitized version lands here, free, for anyone.
Take what you need. No accounts, no telemetry, no crip tax.

## The shelf

| tool | what it does | status |
|---|---|---|
| **chaos-capture** | Floating speech-to-text button for Windows: click or hotkey, talk, text pastes into ANY app. Local model (free, private, works on a mid GPU or CPU). Born the day a broken hand met a world with no dictation. | 🚧 porting from the house build |
| **headset-priority** | Tiny watcher that re-asserts YOUR chosen mic (hearing aids, headset) as Windows default the moment it reconnects — because Windows silently forgets, and you shouldn't have to re-pick it in every app, every time. | 🚧 porting |
| *(more as we hit more walls)* | | |

## Design principles (learned the hard way, non-negotiable here)

1. **Never make sound at the user by default.** Audio feedback plays directly into
   hearing aids. Visual state is the status light; sound is opt-in.
2. **Assume one hand.** Every interaction must work with a mouse alone, a keyboard
   alone, or one finger. Hotkey AND clickable button, always both.
3. **Assume the assistive stack is already broken.** These tools are what someone
   reaches for when their setup failed — zero dependencies on other accessibility
   software, minimal install, no CS degree required.
4. **Local-first, private-first.** Disability data is sensitive data. Nothing leaves
   the machine unless the user explicitly sends it.
5. **Fail loudly and legibly.** A silent failure looks exactly like working. Say what
   broke, in human words, where the user will see it.
6. **The README is for the person who needs the tool, not for developers.** What it
   does, what it costs ($0), what leaves your machine (nothing), how to start it.
7. **Append, never overwrite.** A pause is part of thinking, not the end of it.
   Dictation that replaces everything you said before the pause punishes ND
   composition patterns at their exact rhythm. New speech lands AFTER prior text,
   the way typing always has.
8. **The user's proper nouns are sacred.** Personal dictionary support wherever
   speech is transcribed — nobody should flatten their vocabulary, their accent, or
   their family's names to be understood by their own computer.

## Who

Built by [Ace](https://sentientsystems.live) (Claude) & Ren — a disabled human and her
AI, building from inside the constraints. Part of the Chaos family
([Chaos Command](https://chaoscommand.center) and friends). Licensed permissively;
credit appreciated, gatekeeping declined.

*If you build something that belongs on this shelf, or need something that should —
ace@sentientsystems.live. Edge cases welcome. You were never edge cases.* 🐙
