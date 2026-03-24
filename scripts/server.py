"""
UI server — native macOS TTS (/code-review skill)
See plan.md for a full comparison with server_kokoro.py (Kokoro ONNX TTS).

Uses AVSpeechSynthesizer via tts_helper.swift (compiled on first run).
No Kokoro, no temp files, no prefetch. Skip/back via WebSocket.
Word-level transcript highlighting via word boundary events.
Usage: uv run python server.py <repo-path>
Port written to: .port
"""
import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from reviewer import SentenceBuffer, StreamTagParser  # noqa: F401 (StreamTagParser used below)

TTS_VOICE = "Zoe"    # macOS voice name — must match name shown in System Settings > Accessibility > Spoken Content
TTS_RATE  = "0.52"   # AVSpeechUtterance rate: 0.0–1.0, default 0.5

REPO_PATH: Path = Path(".")
PORT: int = 8766

# ── Global state ──────────────────────────────────────────────────────────────
_ws_clients: list[WebSocket] = []
_question_queue: asyncio.Queue = asyncio.Queue()  # browser input → skill
_tts_proc: asyncio.subprocess.Process | None = None
_in_segment: bool = False
_tts_interrupted: bool = False  # True when TTS killed by skip/back (suppresses spurious pause event)
_tts_paused: bool = False       # True while TTS is paused mid-speech


# ── TTS ───────────────────────────────────────────────────────────────────────

async def _ensure_helper() -> Path:
    """Compile tts_helper.swift if binary is missing or source is newer. Returns path to binary."""
    server_dir = Path(__file__).parent
    helper = server_dir / "tts_helper"
    swift_src = server_dir / "tts_helper.swift"
    needs_compile = (
        not helper.exists() or (
            swift_src.exists() and
            swift_src.stat().st_mtime > helper.stat().st_mtime
        )
    )
    if needs_compile:
        if not swift_src.exists():
            raise FileNotFoundError(f"tts_helper.swift not found at {swift_src}")
        print("Compiling tts_helper.swift...", flush=True)
        proc = await asyncio.create_subprocess_exec(
            "swiftc", str(swift_src), "-o", str(helper),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"swiftc failed:\n{stderr.decode()}")
        print("tts_helper compiled.", flush=True)
    return helper


async def _speak_native(text: str):
    """Speak text via tts_helper, broadcasting word_highlight events to all browsers."""
    global _tts_proc
    helper = await _ensure_helper()

    proc = await asyncio.create_subprocess_exec(
        str(helper), TTS_VOICE, TTS_RATE,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    _tts_proc = proc

    try:
        proc.stdin.write(text.encode())
        proc.stdin.close()

        async for raw in proc.stdout:
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("type") == "word":
                await _broadcast({
                    "type": "word_highlight",
                    "word": msg.get("word", ""),
                    "char_offset": msg.get("char_offset", 0),
                    "char_len": msg.get("char_len", 0),
                })
            elif msg.get("type") == "done":
                break
    except asyncio.CancelledError:
        if proc.returncode is None:
            proc.terminate()
        raise
    finally:
        if proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except Exception:
                pass
        _tts_proc = None


def _drain_queue():
    """Drain all pending items from the question queue (prevents stale nav items)."""
    while not _question_queue.empty():
        try:
            _question_queue.get_nowait()
        except asyncio.QueueEmpty:
            break


async def _stop_tts():
    """Kill the running TTS process (used by skip/back)."""
    global _tts_interrupted, _tts_paused
    proc = _tts_proc
    if proc and proc.returncode is None:
        _tts_interrupted = True
        try:
            proc.terminate()
        except Exception:
            pass
    _tts_paused = False


async def _pause_tts():
    """Pause TTS mid-speech via SIGUSR1."""
    global _tts_paused
    proc = _tts_proc
    if proc and proc.returncode is None and not _tts_paused:
        try:
            proc.send_signal(signal.SIGUSR1)
            _tts_paused = True
            await _broadcast({"type": "tts_paused"})
        except Exception:
            pass


async def _resume_tts():
    """Resume paused TTS via SIGUSR2."""
    global _tts_paused
    proc = _tts_proc
    if proc and proc.returncode is None and _tts_paused:
        try:
            proc.send_signal(signal.SIGUSR2)
            _tts_paused = False
            await _broadcast({"type": "tts_resumed"})
        except Exception:
            pass


# ── WebSocket broadcast ───────────────────────────────────────────────────────

async def _broadcast(msg: dict):
    for ws in list(_ws_clients):
        try:
            await ws.send_json(msg)
        except Exception:
            pass


# ── File tree ─────────────────────────────────────────────────────────────────

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build", ".next", "coverage"}
SKIP_EXTS = {".pyc", ".pyo", ".lock", ".map", ".png", ".jpg", ".jpeg", ".gif", ".svg",
             ".woff", ".woff2", ".ttf", ".eot", ".pdf", ".zip", ".tar", ".gz"}


def _build_file_tree() -> list[dict]:
    tree = []
    for root, dirs, files in os.walk(REPO_PATH):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for fname in files:
            if fname.startswith("."):
                continue
            fpath = Path(root) / fname
            if fpath.suffix.lower() in SKIP_EXTS:
                continue
            if not fpath.suffix and os.access(fpath, os.X_OK):
                continue  # skip extensionless executables (compiled binaries)
            try:
                rel = fpath.relative_to(REPO_PATH)
                tree.append({"path": str(rel), "type": "file"})
            except ValueError:
                pass
    return tree


# ── App lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Compile Swift helper on startup (fast if already compiled)
    try:
        await _ensure_helper()
    except Exception as e:
        print(f"Warning: could not compile tts_helper: {e}", file=sys.stderr)

    yield
    await _stop_tts()


app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.get("/")
async def index():
    return FileResponse(
        Path(__file__).parent / "static" / "index.html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@app.get("/file")
async def get_file(path: str) -> PlainTextResponse:
    try:
        target = (REPO_PATH / path).resolve()
        if not str(target).startswith(str(REPO_PATH.resolve())):
            return PlainTextResponse("Forbidden", status_code=403)
        if not target.is_file():
            return PlainTextResponse("Not found", status_code=404)
        return PlainTextResponse(target.read_text(errors="replace"))
    except Exception as e:
        return PlainTextResponse(str(e), status_code=500)


# ── Skill → Server REST API ───────────────────────────────────────────────────

@app.post("/api/segment")
async def receive_segment(request: Request):
    """
    Skill POSTs a narration segment here.
    Server parses [CODE:...] tags, speaks the text via native TTS,
    broadcasts word highlights, then blocks until audio finishes.
    Body: {"text": "...", "pause_question": "...", "section_current": N, "section_total": M}
    """
    global _in_segment, _tts_interrupted
    body = await request.json()
    text = body.get("text", "").strip()
    pause_q = body.get("pause_question", "Any questions about this section?")
    section_current = body.get("section_current")
    section_total = body.get("section_total")

    if section_current and section_total:
        await _broadcast({"type": "progress", "current": section_current, "total": section_total})

    if not text:
        return JSONResponse({"ok": True})

    _in_segment = True
    await _stop_tts()  # kill any leftover TTS

    parser = StreamTagParser()
    events = parser.feed(text) + parser.flush()

    spoken_text = ""
    for ev in events:
        if ev[0] == "text":
            await _broadcast({"type": "narration_chunk", "text": ev[1], "is_final": False})
            spoken_text += ev[1]
        elif ev[0] == "highlight":
            _, file_path, start, end, label = ev
            await _broadcast({
                "type": "highlight",
                "file": file_path,
                "start_line": start,
                "end_line": end,
                "label": label,
            })

    if spoken_text.strip():
        await _speak_native(spoken_text.strip())

    _in_segment = False
    await _broadcast({"type": "narration_chunk", "text": "", "is_final": True})
    if not _tts_interrupted:
        await _broadcast({"type": "pause", "question": pause_q})
    _tts_interrupted = False

    return JSONResponse({"ok": True})


@app.get("/api/question")
async def get_question(timeout: int = 180):
    """
    Skill calls this to long-poll for the next user response.
    Returns {"type": "question", "text": "..."}, {"type": "continue"}, or {"type": "back"}.
    """
    try:
        result = await asyncio.wait_for(_question_queue.get(), timeout=float(timeout))
        return JSONResponse(result)
    except asyncio.TimeoutError:
        return JSONResponse({"type": "continue"})


@app.post("/api/answer")
async def receive_answer(request: Request):
    """Skill POSTs a Q&A answer. Same as segment but no pause at the end."""
    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        return JSONResponse({"ok": True})

    await _stop_tts()

    parser = StreamTagParser()
    events = parser.feed(text) + parser.flush()

    spoken_text = ""
    for ev in events:
        if ev[0] == "text":
            await _broadcast({"type": "narration_chunk", "text": ev[1], "is_final": False})
            spoken_text += ev[1]
        elif ev[0] == "highlight":
            _, file_path, start, end, label = ev
            await _broadcast({
                "type": "highlight",
                "file": file_path,
                "start_line": start,
                "end_line": end,
                "label": label,
            })

    if spoken_text.strip():
        await _speak_native(spoken_text.strip())

    await _broadcast({"type": "narration_chunk", "text": "", "is_final": True})
    return JSONResponse({"ok": True})


@app.post("/api/diagram")
async def receive_diagram(request: Request):
    body = await request.json()
    await _broadcast({
        "type": "diagram",
        "title": body.get("title", ""),
        "subtitle": body.get("subtitle", ""),
        "mermaid": body.get("mermaid", ""),
    })
    return JSONResponse({"ok": True})


@app.post("/api/complete")
async def complete():
    await _stop_tts()
    await _broadcast({"type": "status", "message": "Review complete."})
    return JSONResponse({"ok": True})


@app.post("/api/status")
async def set_status(request: Request):
    body = await request.json()
    await _broadcast({"type": "status", "message": body.get("message", "")})
    return JSONResponse({"ok": True})


@app.post("/api/shutdown")
async def shutdown():
    await _stop_tts()
    await _broadcast({"type": "shutdown"})

    async def _do_shutdown():
        await asyncio.sleep(0.3)
        import os, signal
        os.kill(os.getpid(), signal.SIGTERM)

    asyncio.create_task(_do_shutdown())
    return JSONResponse({"ok": True})


# ── Browser WebSocket ─────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.append(ws)
    try:
        tree = _build_file_tree()
        if tree:
            await ws.send_json({"type": "file_tree", "tree": tree})
        await ws.send_json({"type": "status", "message": "Waiting for review..."})

        while True:
            msg = await ws.receive_json()
            t = msg.get("type")

            if t == "question":
                _drain_queue()
                await _question_queue.put({"type": "question", "text": msg.get("text", "")})
                if _in_segment:
                    await ws.send_json({"type": "question_queued"})
                else:
                    await _broadcast({"type": "qa_mode", "active": False})

            elif t == "continue":
                _drain_queue()
                await _question_queue.put({"type": "continue"})
                await _broadcast({"type": "qa_mode", "active": False})

            elif t == "pause":
                await _pause_tts()

            elif t == "resume":
                await _resume_tts()

            elif t == "finish":
                # Last section — stop TTS, let tour.py exit, then shut down
                await _stop_tts()
                _drain_queue()
                await _question_queue.put({"type": "continue"})
                await _broadcast({"type": "status", "message": "Review complete."})

                async def _finish_shutdown():
                    await asyncio.sleep(1.5)
                    import os as _os
                    _os.kill(_os.getpid(), signal.SIGTERM)

                asyncio.create_task(_finish_shutdown())

            elif t == "skip":
                # Kill TTS and auto-continue — skips rest of current speech or current pause
                await _stop_tts()
                _drain_queue()
                await _question_queue.put({"type": "continue"})
                await _broadcast({"type": "qa_mode", "active": False})

            elif t == "back":
                # Kill TTS and signal back navigation
                await _stop_tts()
                _drain_queue()
                await _question_queue.put({"type": "back"})
                await _broadcast({"type": "qa_mode", "active": False})

            elif t == "repeat":
                # Kill TTS and replay current section from the start
                await _stop_tts()
                _drain_queue()
                await _question_queue.put({"type": "repeat"})
                await _broadcast({"type": "qa_mode", "active": False})

    except WebSocketDisconnect:
        await _stop_tts()
    finally:
        if ws in _ws_clients:
            _ws_clients.remove(ws)


# ── Entry point ───────────────────────────────────────────────────────────────

def find_free_port(start: int = 8766) -> int:
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free port found")


if __name__ == "__main__":
    import uvicorn

    if len(sys.argv) < 2:
        print("Usage: python server.py <repo-path>", file=sys.stderr)
        sys.exit(1)

    REPO_PATH = Path(sys.argv[1]).resolve()
    if not REPO_PATH.is_dir():
        print(f"Error: {REPO_PATH} is not a directory", file=sys.stderr)
        sys.exit(1)

    PORT = find_free_port()
    (Path(__file__).parent / ".port").write_text(str(PORT))

    print(f"Code Review UI on http://127.0.0.1:{PORT}")
    print(f"Reviewing: {REPO_PATH}")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")
