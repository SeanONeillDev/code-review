# code-review

A Claude Code skill that walks you through any codebase out loud. Claude reads the repo, narrates each section with native macOS voice, highlights the relevant code in a Monaco editor, and pauses for questions — all in your browser.

- **< 1s latency** between sections (tour runs in Python, no Claude round-trips)
- **Word-level transcript highlighting** synced to speech
- **Skip / Back / Pause / Resume** controls
- **Q&A** — ask a question mid-tour, Claude answers, tour resumes automatically

## Requirements

- macOS (uses AVSpeechSynthesizer via a compiled Swift helper)
- [Claude Code](https://claude.ai/code)
- [uv](https://docs.astral.sh/uv/) — `brew install uv`
- **Zoe Enhanced voice** — download it for best quality:
  System Settings → Accessibility → Spoken Content → System Voice → Manage Voices → Zoe → Enhanced

## Install

```bash
git clone https://github.com/SeanONeillDev/code-review ~/.claude/skills/code-review
```

Restart Claude Code. That's it.

## Usage

```
/code-review <path>
```

If no path is given, reviews the current working directory.

## Update

```bash
git -C ~/.claude/skills/code-review pull
```

## Configuration

Voice and speed are set at the top of `scripts/server.py`:

```python
TTS_VOICE = "Zoe"   # any macOS voice name from System Settings > Accessibility > Spoken Content
TTS_RATE  = "0.52"  # 0.0–1.0, default 0.5
```
