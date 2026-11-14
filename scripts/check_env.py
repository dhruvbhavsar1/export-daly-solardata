"""Safe .env check: shows plain values for non-secret keys, and only the LENGTH for secrets.
Run it inside the folder that has the .env file:   python scripts/check_env.py
"""
from pathlib import Path

SECRET = ("PASSWORD", "KEY", "PASS", "TOKEN")
f = Path(".env")
if not f.is_file():
    print("NO .env FILE in this folder:", Path.cwd()); raise SystemExit(1)
print("Folder:", Path.cwd())
for raw in f.read_text(encoding="utf-8-sig").splitlines():
    line = raw.strip()
    if not line or line.startswith(("#", "/")) or "=" not in line:
        continue
    k, v = line.split("=", 1)
    val = v.strip()
    notes = []
    if val == "":
        notes.append("<-- EMPTY!")
    if v != v.strip() and val:
        notes.append("<-- has spaces around it")
    if val[:1] in "\"'" and val:
        notes.append("<-- has quote marks")
    shown = f"(length {len(val)})" if any(s in k.upper() for s in SECRET) else val
    print(f"{k.strip():20} = {shown} {' '.join(notes)}")