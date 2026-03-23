"""
Helper: POST a Q&A answer to the local server (no pause shown after).
Usage: echo "ANSWER TEXT" | python answer.py <port>
"""
import json
import sys
import urllib.request

port = sys.argv[1] if len(sys.argv) > 1 else "8765"

text = sys.stdin.read().strip()
if not text:
    sys.exit(0)

data = json.dumps({"text": text}).encode()
req = urllib.request.Request(
    f"http://localhost:{port}/api/answer",
    data=data,
    headers={"Content-Type": "application/json"},
)
try:
    resp = urllib.request.urlopen(req, timeout=600)
    print(resp.read().decode())
except Exception as e:
    print(f"answer error: {e}", file=sys.stderr)
    sys.exit(1)
