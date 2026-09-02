#!/usr/bin/env python
"""Scan Claude Code session transcripts for repeated-failure patterns."""
import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterator

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
REPEAT_THRESHOLD = 3

# order matters: strip identifiers before the generic \d+ mask eats their digits
MASK_PATTERNS = [
    (re.compile(r"toolu_[A-Za-z0-9]+"), "<TOOLU>"),
    (re.compile(r"[A-Za-z]:\\[^\s\"']+|/(?:home|Users|c|mnt)/[^\s\"']+"), "<PATH>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}T[\d:.]+Z?"), "<TS>"),
    (re.compile(r"\b[0-9a-f]{7,64}\b", re.I), "<HASH>"),
    (re.compile(r"\(\d+(?:\.\d+)?\s?[KMG]?B\)", re.I), "<SIZE>"),
    (re.compile(r":\d+:\d+|\bline \d+\b", re.I), "<LINE>"),
    (re.compile(r"\d+"), "<N>"),
]


def fingerprint(content: Any) -> str:
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, sort_keys=True)
    for pat, repl in MASK_PATTERNS:
        text = pat.sub(repl, text)
    return text.strip()[:500]


def target_key(tool_input: Any) -> str:
    """Raw (unmasked) identity of what a call acted on — used for no_progress, where
    the signal is 'same target, called again', not 'same output text', because short
    success/no-op messages (e.g. Edit's fixed confirmation) collide across unrelated
    targets once masked and would otherwise look like a stuck loop."""
    return json.dumps(tool_input, ensure_ascii=False, sort_keys=True)[:300]


def iter_records(path: Path) -> Iterator[dict]:
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def session_cwd(path: Path) -> str | None:
    for rec in iter_records(path):
        cwd = rec.get("cwd")
        if cwd:
            return cwd
    return None


def find_session_files() -> list[Path]:
    return list(CLAUDE_PROJECTS.glob("*/*.jsonl")) + list(CLAUDE_PROJECTS.glob("*/*/subagents/*.jsonl"))


def select_sessions(scan_all: bool) -> list[Path]:
    files = find_session_files()
    if scan_all:
        return files
    target = os.path.normcase(os.path.normpath(str(Path.cwd())))
    matches = [f for f in files if session_cwd(f) and os.path.normcase(os.path.normpath(session_cwd(f))) == target]
    if not matches:
        return []
    return [max(matches, key=lambda f: f.stat().st_mtime)]


def extract_tool_events(path: Path) -> Iterator[tuple[str, bool, str, Any]]:
    """yield (tool_name, is_error, output_fingerprint, tool_input) per tool_result, in order."""
    pending = {}
    for rec in iter_records(path):
        content = rec.get("message", {}).get("content") if isinstance(rec.get("message"), dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                pending[block.get("id")] = (block.get("name", "?"), block.get("input"))
            elif block.get("type") == "tool_result":
                name, tool_input = pending.get(block.get("tool_use_id"), ("?", None))
                yield (name, bool(block.get("is_error")), fingerprint(block.get("content")), tool_input)


def iter_runs(events) -> Iterator[tuple[tuple[str, str], bool, int]]:
    """Collapse events into maximal consecutive runs of the same grouping key,
    yielding (key, is_error, count) — including the trailing run at end of input,
    which the hook command uses to check 'is a loop happening right now'."""
    run_key, run_is_error, run_count = None, False, 0
    for name, is_error, output_fp, tool_input in events:
        key = (name, output_fp) if is_error else (name, target_key(tool_input))
        if key == run_key:
            run_count += 1
        else:
            if run_key:
                yield run_key, run_is_error, run_count
            run_key, run_is_error, run_count = key, is_error, 1
    if run_key:
        yield run_key, run_is_error, run_count


def detect_repeated_runs(events) -> list[dict]:
    """A retry loop is the same tool repeatedly failing with the same (masked) error.
    A no-progress run is the same tool repeatedly called on the same target with no
    error — grouped by raw input, since success/no-op output text is often fixed
    boilerplate that would otherwise collide across unrelated targets."""
    findings = []
    for key, is_error, count in iter_runs(events):
        if count >= REPEAT_THRESHOLD:
            kind = "repeat_failure" if is_error else "no_progress"
            findings.append({"type": kind, "tool": key[0], "count": count, "fingerprint": key[1]})
    return findings


def scan_session(path: Path) -> list[dict]:
    events = list(extract_tool_events(path))
    return detect_repeated_runs(events)


def trailing_repeat_failure(events) -> tuple[str, int] | None:
    """(tool_name, count) if the run still open at the end of events is a failing
    retry loop at/above threshold, else None. Used by the PostToolUseFailure hook to
    ask 'is the call that just failed part of a loop right now' without a full scan."""
    runs = list(iter_runs(events))
    if not runs:
        return None
    key, is_error, count = runs[-1]
    if is_error and count >= REPEAT_THRESHOLD:
        return key[0], count
    return None


def cmd_scan(args) -> None:
    sessions = select_sessions(args.all)
    if not sessions:
        print("no matching session files found", file=sys.stderr)
        sys.exit(1)

    results = {str(p): f for p in sessions if (f := scan_session(p))}

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    if not results:
        print("no repeated-failure patterns found")
        return
    for path, findings in results.items():
        print(f"\n{path}")
        for f in findings:
            print(f"  [{f['type']}] {f['tool']} x{f['count']}: {f['fingerprint'][:200]}")


def cmd_hook() -> None:
    """PostToolUseFailure hook entry point. Must never break the agent's turn: any
    internal error or missing/unreadable transcript falls through to exit 0 silently."""
    try:
        payload = json.load(sys.stdin)
        events = list(extract_tool_events(Path(payload["transcript_path"])))
        hit = trailing_repeat_failure(events)
    except Exception:
        hit = None
    if hit:
        tool_name, count = hit
        print(
            f"[loop-detector] {tool_name} has failed the same way {count} times in a row. "
            "Stop retrying the same fix — reconsider the approach.",
            file=sys.stderr,
        )
        sys.exit(2)
    sys.exit(0)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    scan_p = sub.add_parser("scan", help="scan current project's latest session (or --all)")
    scan_p.add_argument("--all", action="store_true", help="scan every session across all projects")
    scan_p.add_argument("--json", action="store_true", help="emit JSON instead of text")
    sub.add_parser("hook", help="PostToolUseFailure hook entry point (reads hook JSON from stdin)")
    args = ap.parse_args()

    if args.cmd == "hook":
        cmd_hook()
    else:
        cmd_scan(args)


if __name__ == "__main__":
    main()
