"""Fetch GDTF files from GDTF Share (https://gdtf-share.com) for bundling with the add-on.

Credentials come from the environment (GDTF_USER, GDTF_PASS); they are never stored.

    python3 tools/gdtf_share.py list   OUT.json            # save the full revision list
    python3 tools/gdtf_share.py fetch  LIST.json WANT.txt  # download the best revision of every wanted fixture

WANT.txt has one "Manufacturer | Fixture name" per line; a line "## Category" sets the library category of what follows. Matching is by normalised words; ambiguous or missing
fixtures are reported instead of guessed. Files are written to lighting_visualizer/library/gdtf/ with an index.json
that keeps manufacturer, uploader, revision and the share's rid so every file can be labelled as the API terms demand.
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

API = "https://gdtf-share.com/apis/public/"
OUT_DIR = Path(__file__).resolve().parents[1] / "lighting_visualizer" / "library" / "gdtf"


def _opener():
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    op.addheaders = [("User-Agent", "lighting_visualizer-fetch/1.0")]
    body = json.dumps({"user": os.environ["GDTF_USER"], "password": os.environ["GDTF_PASS"]}).encode()
    req = urllib.request.Request(API + "login.php", body, {"Content-Type": "application/json"})
    reply = json.load(op.open(req, timeout=30))
    if not reply.get("result"):
        sys.exit(f"login failed: {reply.get('error')}")
    return op


def get_list(op) -> list:
    reply = json.load(op.open(API + "getList.php", timeout=120))
    if not reply.get("result"):
        sys.exit(f"getList failed: {reply.get('error')}")
    return reply["list"]


def _words(text: str) -> list:
    return re.findall(r"[a-z0-9]+", text.lower().replace("\u00b0", ""))


def _candidates(entries, manufacturer, name):
    mw, nw = _words(manufacturer), _words(name)
    out = []
    for e in entries:
        mf = _words(e["manufacturer"])
        if not all(w in mf or w in _words(e["fixture"]) for w in mw[:1]):
            continue
        fw = _words(e["fixture"])
        if all(w in fw for w in nw):
            out.append(e)
    return out


def _rank(e):
    # manufacturer uploads first, then newest
    return (e.get("uploader") == "Manuf.", int(e.get("lastModified") or 0))


def match(entries, manufacturer, name):
    found = _candidates(entries, manufacturer, name)
    if not found:
        return None, []
    by_uuid = {}
    for e in found:
        by_uuid.setdefault(e["uuid"], []).append(e)
    exact = [v for v in by_uuid.values() if any(_words(x["fixture"]) == _words(name) for x in v)]
    pool = exact or list(by_uuid.values())
    best = max((max(v, key=_rank) for v in pool), key=_rank)
    return best, [e for v in pool for e in v if e is not best]


def cmd_fetch(list_path, want_path):
    entries = json.loads(Path(list_path).read_text(encoding="utf-8"))
    op = _opener()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    index_path = OUT_DIR / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    missing = []
    category = ""
    for line in Path(want_path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("##"):
            category = line.lstrip("#").strip()
            continue
        if not line or line.startswith("#"):
            continue
        manufacturer, _, name = (p.strip() for p in line.partition("|"))
        best, others = match(entries, manufacturer, name)
        if best is None:
            missing.append(line)
            print(f"MISSING   {line}")
            continue
        fname = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{best['manufacturer']}_{best['fixture']}_{best['rid']}") + ".gdtf"
        if str(best["rid"]) not in {str(v["rid"]) for v in index.values()} or not (OUT_DIR / fname).exists():
            try:
                data = op.open(API + f"downloadFile.php?rid={best['rid']}", timeout=120).read()
            except urllib.error.URLError as e:
                print(f"FAILED    {line}: {e}")
                missing.append(line)
                continue
            if data[:2] != b"PK":
                print(f"FAILED    {line}: not a GDTF archive ({data[:80]!r})")
                missing.append(line)
                continue
            (OUT_DIR / fname).write_bytes(data)
            time.sleep(0.5)
        index[fname] = {k: best.get(k) for k in (
            "rid", "uuid", "manufacturer", "fixture", "revision", "uploader", "version", "lastModified",
            "filesize", "modes")}
        index[fname]["wanted"] = line
        index[fname]["category"] = category
        print(f"OK        {line}  ->  {best['manufacturer']} / {best['fixture']} [{best['uploader']}] "
              f"{best['filesize'] // 1024} kB" + (f"  (+{len(others)} other candidates)" if others else ""))
    index_path.write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\n{len(index)} files in index, {len(missing)} missing")


def main(argv):
    if len(argv) >= 3 and argv[1] == "list":
        Path(argv[2]).write_text(json.dumps(get_list(_opener())), encoding="utf-8")
        print("saved", argv[2])
    elif len(argv) >= 4 and argv[1] == "fetch":
        cmd_fetch(argv[2], argv[3])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
