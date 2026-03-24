"""
Helper: Run the entire narration tour loop without Claude in the hot path.

Usage:
    python3 tour.py <port> [start_section_idx] << 'END_SECTIONS'
    [
      {"text": "narration text with [CODE:...] tags", "question": "Any questions?", "current": 1, "total": 6},
      ...
    ]
    END_SECTIONS

Exits printing one JSON line to stdout:
    {"type": "done"}
    {"type": "question", "section": N, "text": "user's question text"}

Between-section latency: ~50ms (pure Python HTTP, no Claude inference).
Claude is only invoked when the user asks a question.
"""
import json
import sys
import urllib.request

port = sys.argv[1] if len(sys.argv) > 1 else "8766"
start_idx = int(sys.argv[2]) if len(sys.argv) > 2 else 0

sections = json.loads(sys.stdin.read())
total = len(sections)

i = start_idx
while 0 <= i < total:
    section = sections[i]

    # ── POST segment to server (blocks until TTS finishes + pause shown) ──
    data = json.dumps({
        "text": section.get("text", ""),
        "pause_question": section.get("question", "Any questions about this?"),
        "section_current": section.get("current", i + 1),
        "section_total": section.get("total", total),
    }).encode()
    req = urllib.request.Request(
        f"http://localhost:{port}/api/segment",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=600)
    except Exception as e:
        print(json.dumps({"type": "error", "message": str(e)}), flush=True)
        sys.exit(1)

    # ── Poll for user response (blocks until continue / question / back) ──
    req2 = urllib.request.Request(f"http://localhost:{port}/api/question?timeout=300")
    try:
        resp = urllib.request.urlopen(req2, timeout=310)
        result = json.loads(resp.read())
    except Exception:
        # Timeout or error — treat as continue
        result = {"type": "continue"}

    if result["type"] == "continue":
        i += 1
    elif result["type"] == "back":
        i = max(0, i - 1)
    elif result["type"] == "repeat":
        pass  # replay same section — i unchanged
    elif result["type"] == "question":
        # Hand back to Claude to answer, then Claude will call tour.py again
        print(json.dumps({
            "type": "question",
            "section": i,
            "text": result.get("text", ""),
        }), flush=True)
        sys.exit(0)

# All sections done
print(json.dumps({"type": "done"}), flush=True)
