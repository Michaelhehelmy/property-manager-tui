#!/usr/bin/env python3
"""Generate a sanitized community native-build report and prefilled GitHub issue URL.

Privacy-first: reuses app_core.support redaction/sanitization. No secrets, DB data,
or runtime records are included. Review before sharing.
"""
from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path


def _load_text(path: str | None, limit: int = 4000) -> str:
    if not path:
        return ""
    try:
        p = Path(path)
        if not p.exists():
            return ""
        txt = p.read_text(encoding="utf-8", errors="replace")
        if len(txt) > limit:
            txt = txt[-limit:]
            txt = "...(log tail truncated)\n" + txt
        return txt
    except Exception:
        return ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", default="")
    ap.add_argument("--artifact", default="")
    ap.add_argument("--checksum", default="")
    ap.add_argument("--build-status", default="")
    ap.add_argument("--app-ref", default="")
    ap.add_argument("--commit", default="")
    ap.add_argument("--build-cmd", default="")
    ap.add_argument("--nuitka-version", default="")
    ap.add_argument("--notes", default="")
    ap.add_argument("--log-file", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    # Ensure src is importable when run from repo root
    repo = Path(__file__).resolve().parents[1]
    src = repo / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))

    from app_core import support, paths

    ctx = {"source": "community_native_build"}
    bundle = support.generate_support_bundle(context=ctx, user={"title": args.title}, include_update_check=False)

    extra = {
        "community_native_build": {
            "status": args.build_status or "unknown",
            "artifact_name": Path(args.artifact).name if args.artifact else "",
            "artifact_path_hint": "<local path omitted>",
            "sha256": args.checksum or "",
            "app_ref": args.app_ref or "",
            "commit": args.commit or "",
            "build_command": args.build_cmd or "",
            "nuitka_version": args.nuitka_version or "",
            "os": platform.system(),
            "arch": platform.machine(),
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "notes": args.notes or "",
            "log_tail": paths.redact_sensitive(_load_text(args.log_file), 4000),
        },
        "sharing_guidance": [
            "Do not attach runtime data (DB/exports/invoices) or secrets.",
            "Attach the built artifact/checksum only if you are comfortable sharing it publicly; maintainers may request it via a trusted channel.",
            "Review and remove any sensitive/business information before submitting.",
        ],
    }
    # merge sanitized extra into diagnostics
    try:
        diag = bundle.get("diagnostics") or {}
        diag = dict(diag)
        diag["community_native_build"] = support.sanitize_for_report(extra["community_native_build"], "community_native_build")
        diag["sharing_guidance"] = extra["sharing_guidance"]
        bundle["diagnostics"] = diag
        # rebuild markdown/issue with extra section
        md = support.format_support_report_markdown(diag, user={"title": args.title, "actual": args.notes or ""})
        # append explicit build section for readability
        b = diag.get("community_native_build") or {}
        lines = [md.rstrip(), "", "### Community native build details", "```json", json.dumps(b, ensure_ascii=False, indent=2, sort_keys=True), "```", ""]
        md = "\n".join(lines)
        bundle["markdown"] = md
        bundle["issue"] = support.build_github_issue_url(title=args.title or "Community native build report", body=md)
    except Exception:
        pass

    if args.out:
        outp = Path(args.out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(bundle.get("markdown") or "", encoding="utf-8")

    if args.json:
        print(json.dumps({"issue_url": (bundle.get("issue") or {}).get("url"), "report_path": args.out, "artifact": Path(args.artifact).name if args.artifact else "", "checksum": args.checksum}, ensure_ascii=False))
    else:
        print((bundle.get("issue") or {}).get("url") or "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
