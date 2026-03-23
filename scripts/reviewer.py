"""
Shared utilities: tag parsing and sentence buffering.
The AI narration is driven by the Claude Code skill, not this module.
"""
import re


class StreamTagParser:
    """
    Parses narration text and emits typed events for inline tags.
    Tags: [CODE:path:start-end:label], [PAUSE:question], [DONE]
    """

    def __init__(self):
        self._text_buf: list[str] = []
        self._tag_buf: list[str] = []
        self._in_tag = False

    def feed(self, chunk: str) -> list[tuple]:
        events = []
        for char in chunk:
            if not self._in_tag:
                if char == "[":
                    if self._text_buf:
                        events.append(("text", "".join(self._text_buf)))
                        self._text_buf = []
                    self._in_tag = True
                    self._tag_buf = ["["]
                else:
                    self._text_buf.append(char)
            else:
                self._tag_buf.append(char)
                if char == "]":
                    tag = "".join(self._tag_buf)
                    parsed = self._parse_tag(tag)
                    if parsed:
                        events.append(parsed)
                    else:
                        self._text_buf.extend(self._tag_buf)
                    self._tag_buf = []
                    self._in_tag = False
                elif len(self._tag_buf) > 300:
                    self._text_buf.extend(self._tag_buf)
                    self._tag_buf = []
                    self._in_tag = False
        if self._text_buf:
            events.append(("text", "".join(self._text_buf)))
            self._text_buf = []
        return events

    def flush(self) -> list[tuple]:
        events = []
        if self._tag_buf:
            self._text_buf.extend(self._tag_buf)
            self._tag_buf = []
            self._in_tag = False
        if self._text_buf:
            events.append(("text", "".join(self._text_buf)))
            self._text_buf = []
        return events

    def _parse_tag(self, tag: str) -> tuple | None:
        inner = tag[1:-1].strip()
        if inner == "DONE":
            return ("done",)
        if inner.startswith("PAUSE:"):
            return ("pause", inner[6:].strip())
        if inner.startswith("CODE:"):
            parts = inner[5:].split(":", 2)
            if len(parts) >= 2:
                file_path = parts[0].strip()
                line_part = parts[1].strip()
                label = parts[2].strip() if len(parts) > 2 else ""
                lines = line_part.split("-")
                try:
                    start = int(lines[0])
                    end = int(lines[1]) if len(lines) > 1 else start
                    return ("highlight", file_path, start, end, label)
                except ValueError:
                    return None
        return None


SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class SentenceBuffer:
    """Buffers text until sentence boundaries before sending to Kokoro."""

    def __init__(self, speak_fn):
        self._buf: list[str] = []
        self._speak = speak_fn

    async def feed(self, text: str):
        self._buf.append(text)
        combined = "".join(self._buf)
        parts = SENTENCE_END.split(combined)
        if len(parts) > 1:
            for sentence in parts[:-1]:
                s = sentence.strip()
                if s:
                    await self._speak(s)
            self._buf = [parts[-1]]

    async def flush(self):
        remaining = "".join(self._buf).strip()
        if remaining:
            await self._speak(remaining)
        self._buf = []
