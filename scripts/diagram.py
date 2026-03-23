"""
Helper: POST a Mermaid diagram to the local server.
Usage: echo "MERMAID_SRC" | python diagram.py <port> <title> <subtitle>
"""
import json
import sys
import urllib.request

port = sys.argv[1] if len(sys.argv) > 1 else "8765"
title = sys.argv[2] if len(sys.argv) > 2 else ""
subtitle = sys.argv[3] if len(sys.argv) > 3 else ""

mermaid = sys.stdin.read().strip()
if not mermaid:
    sys.exit(0)

data = json.dumps({
    "title": title,
    "subtitle": subtitle,
    "mermaid": mermaid,
}).encode()
req = urllib.request.Request(
    f"http://localhost:{port}/api/diagram",
    data=data,
    headers={"Content-Type": "application/json"},
)
try:
    resp = urllib.request.urlopen(req, timeout=30)
    print(resp.read().decode())
except Exception as e:
    print(f"diagram error: {e}", file=sys.stderr)
    sys.exit(1)
