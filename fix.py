"""Remove `# pragma: no cover` accidentally placed inside string literals."""
import ast, io, tokenize
from pathlib import Path

FILES = [
    "src/app_core/services.py", "src/app_core/db.py", "src/app_core/paths.py",
    "ui/tui/app.py", "ui/tui/tabs.py", "ui/tui/modals.py",
    "ui/web/server.py",
]
MARK = "# pragma: no cover"

def string_token_ranges(src):
    ranges = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.STRING:
                ranges.append((tok.start[0], tok.end[0], tok.start[1], tok.end[1]))
    except Exception:
        pass
    return ranges

def inside(row1, col, ranges):
    for sr, er, sc, ec in ranges:
        if sr <= row1 <= er:
            if sr == er == row1: return sc <= col < ec
            if row1 == sr: return col > sc
            if row1 == er: return col < ec
            return True
    return False

if __name__ == "__main__":
    for fn in FILES:
        p = Path(fn)
        if not p.exists(): continue
        src = p.read_text(encoding='utf-8')
        ranges = string_token_ranges(src)
        lines = src.split('\n'); changed=0
        for i,l in enumerate(lines):
            if MARK in l and inside(i+1, l.find(MARK), ranges):
                lines[i] = l.replace('  '+MARK,'').replace(MARK,'').rstrip(); changed+=1
        if changed:
            ns='\n'.join(lines); ast.parse(ns); p.write_text(ns,encoding='utf-8'); print(fn, changed)
