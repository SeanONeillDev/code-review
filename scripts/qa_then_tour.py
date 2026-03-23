"""
Helper: Post an answer, then automatically resume the tour — no Claude round-trip on continue.

Usage:
    python3 qa_then_tour.py <port> <resume_section_idx> << 'END'
    {
      "answer": "Answer text with [CODE:...] tags",
      "sections": [ ...full sections array... ]
    }
    END

resume_section_idx: index into sections to resume at (0-based).
  - After a question at section N, pass N+1 to advance.
  - Pass N to repeat (back case is handled internally).

Exits printing one JSON line:
    {"type": "done"}
    {"type": "question", "section": N, "text": "..."}   ← follow-up question; Claude answers and calls this again
"""
import json
import sys
import urllib.request

port          = sys.argv[1] if len(sys.argv) > 1 else "8766"
resume_idx    = int(sys.argv[2]) if len(sys.argv) > 2 else 0

payload = json.loads(sys.stdin.read())
answer_text = payload.get("answer", "").strip()
sections    = payload.get("sections", [])


def post(path, body_dict):
    data = json.dumps(body_dict).encode()
    req  = urllib.request.Request(
        f"http://localhost:{port}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=600)


def poll_question():
    req = urllib.request.Request(f"http://localhost:{port}/api/question?timeout=300")
    try:
        resp = urllib.request.urlopen(req, timeout=310)
        return json.loads(resp.read())
    except Exception:
        return {"type": "continue"}


def run_tour(start_idx):
    """Run tour from start_idx, returning done or question dict."""
    total = len(sections)
    i = start_idx
    while 0 <= i < total:
        section = sections[i]
        data = json.dumps({
            "text":            section.get("text", ""),
            "pause_question":  section.get("question", "Any questions?"),
            "section_current": section.get("current", i + 1),
            "section_total":   section.get("total", total),
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

        result = poll_question()

        if result["type"] == "continue":
            i += 1
        elif result["type"] == "back":
            i = max(0, i - 1)
        elif result["type"] == "question":
            return {"type": "question", "section": i, "text": result.get("text", "")}

    return {"type": "done"}


# ── 1. Post the answer (non-blocking TTS, no pause shown after) ──────────────
if answer_text:
    post("/api/answer", {"text": answer_text})

# ── 2. Wait for user response (follow-up question or continue) ───────────────
result = poll_question()

current_idx = resume_idx

while result["type"] == "question":
    # Follow-up question — return to Claude to answer, then Claude calls us again
    print(json.dumps({
        "type":    "question",
        "section": current_idx,
        "text":    result.get("text", ""),
    }), flush=True)
    sys.exit(0)

if result["type"] == "back":
    current_idx = max(0, resume_idx - 1)
# else: continue → current_idx stays at resume_idx

# ── 3. Resume tour from current_idx ──────────────────────────────────────────
final = run_tour(current_idx)
print(json.dumps(final), flush=True)
