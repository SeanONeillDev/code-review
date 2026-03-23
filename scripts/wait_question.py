"""
Helper: Long-poll the server for the next user question or "continue".
Usage: python wait_question.py <port>
Prints JSON: {"type": "question", "text": "..."} or {"type": "continue"}
"""
import json
import sys
import urllib.request

port = sys.argv[1] if len(sys.argv) > 1 else "8765"

req = urllib.request.Request(f"http://localhost:{port}/api/question?timeout=300")
try:
    resp = urllib.request.urlopen(req, timeout=310)
    print(resp.read().decode())
except Exception as e:
    # Timeout or error → treat as continue
    print(json.dumps({"type": "continue"}))
