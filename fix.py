"""fixPragma.py — remove `# pragma: no cover` from inside string literals.

Bug: an earlier pragma-adding script placed the pragma inside triple-quoted
SQL/strings. SQLite sees `#` → "unrecognized token". This removes only
pragmas that land inside STRING tokens — legitimate pragmas on code lines
are preserved.
"""
import ast
import io
import tokenize
from pathlib import Path


FILES = ["services.py", "db.py", "tabs.py", "modals.py", "sukoon.py"]
MARK = "# pragma: no cover"


def string_token_ranges(src):
    """Return [(start_row, end_row, start_col, end_col)] for every STRING token (1-based rows)."""
    ranges = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.STRING:
                ranges.append((tok.start[0], tok.end[0], tok.start[1], tok.end[1]))
    except (tokenize.TokenError, IndentationError):
        pass
    return ranges


def pragma_inside_string(row1, pragma_col, ranges):
    """row1: 1-based. pragma_col: 0-based column of '#'."""
    for start_row, end_row, start_col, end_col in ranges:
        if start_row <= row1 <= end_row:
            if start_row == end_row == row1:
                if start_col <= pragma_col < end_col:
                    return True
            elif row1 == start_row:
                if pragma_col > start_col:
                    return True
            elif row1 == end_row:
                if pragma_col < end_col:
                    return True
            else:
                return True
    return False


def fix_file(path):
    p = Path(path)
    if not p.exists():
        return 0
    with open(p, "rb") as f:
        src = f.read().replace(b"\r\n", b"\n").decode("utf-8")

    ranges = string_token_ranges(src)
    lines = src.split("\n")
    fixed = 0

    for i, line in enumerate(lines):
        if MARK not in line:
            continue
        pragma_col = line.find(MARK)
        if pragma_inside_string(i + 1, pragma_col, ranges):
            # Strip pragma (and any preceding whitespace before it)
            new_line = line.replace("  " + MARK, "").replace(MARK, "").rstrip()
            lines[i] = new_line
            fixed += 1

    if fixed:
        new_src = "\n".join(lines)
        try:
            ast.parse(new_src)
        except SyntaxError as e:
            print(f"  ✗ {path}: syntax error at line {e.lineno} — NOT written")
            return 0
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_src)
        print(f"  ✓ {path}: removed {fixed} pragma(s) from string literals")
    return fixed


def remaining_pragmas(path):
    p = Path(path)
    if not p.exists():
        return 0
    with open(p, "rb") as f:
        src = f.read().replace(b"\r\n", b"\n").decode("utf-8")
    return src.count(MARK)


if __name__ == "__main__":
    print("=" * 60)
    print("Removing pragmas inside string literals...")
    print("=" * 60)
    total = 0
    for fn in FILES:
        total += fix_file(fn)
    print()
    print(f"Total removed: {total}")
    print()
    print("=" * 60)
    print("Remaining pragmas (all outside strings — safe):")
    print("=" * 60)
    for fn in FILES:
        n = remaining_pragmas(fn)
        if n:
            print(f"  {fn}: {n}")
    print()
    print("=" * 60)
    print("Verify:")
    print("  python -c 'import services, db; print(\"OK\")'")
    print("  pytest -q")
    print("=" * 60)