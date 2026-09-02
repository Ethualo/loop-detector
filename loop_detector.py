#!/usr/bin/env python
"""Scan Claude Code session transcripts for repeated-failure patterns."""
import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
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


def response_failed(response: Any) -> bool | None:
    if isinstance(response, dict):
        for key in ("is_error", "isError"):
            if isinstance(response.get(key), bool):
                return response[key]
        for key in ("exit_code", "exitCode"):
            value = response.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return value != 0
            if isinstance(value, str) and value.lstrip("-").isdigit():
                return int(value) != 0
        for value in response.values():
            result = response_failed(value)
            if result is not None:
                return result
    elif isinstance(response, list):
        for value in response:
            result = response_failed(value)
            if result is not None:
                return result
    return None


def hook_state_path(session_id: str, state_dir: Path) -> Path:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return state_dir / f"{digest}.json"


def codex_hook_warning(payload: dict, state_dir: Path | None = None) -> str | None:
    if payload.get("hook_event_name") != "PostToolUse":
        return None
    session_id = payload.get("session_id")
    tool_name = payload.get("tool_name")
    failed = response_failed(payload.get("tool_response"))
    if not isinstance(session_id, str) or not isinstance(tool_name, str) or failed is None:
        return None

    state_root = state_dir or Path(tempfile.gettempdir()) / "loop-detector"
    path = hook_state_path(session_id, state_root)
    if not failed:
        path.unlink(missing_ok=True)
        return None

    fingerprint_value = fingerprint(payload["tool_response"])
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    count = state.get("count", 0) + 1 if state.get("tool") == tool_name and state.get("fingerprint") == fingerprint_value else 1
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"tool": tool_name, "fingerprint": fingerprint_value, "count": count}), encoding="utf-8")
    # ponytail: same-session parallel hooks can lose an increment; add file locking if Codex starts them concurrently.
    temporary.replace(path)
    if count >= REPEAT_THRESHOLD:
        return f"[loop-detector] {tool_name}가 같은 오류로 {count}회 연속 실패했습니다. 같은 재시도를 멈추고 접근을 바꾸세요."
    return None


def claude_hook_warning(payload: dict) -> str | None:
    transcript_path = payload.get("transcript_path")
    if not isinstance(transcript_path, str):
        return None
    hit = trailing_repeat_failure(extract_tool_events(Path(transcript_path)))
    if not hit:
        return None
    tool_name, count = hit
    return f"[loop-detector] {tool_name} has failed the same way {count} times in a row. Stop retrying the same fix — reconsider the approach."


def read_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON") from exc
    if not isinstance(settings, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return settings


def install_hook(settings: dict, event: str, matcher: str, handler: dict) -> bool:
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("hooks must be a JSON object")
    groups = hooks.setdefault(event, [])
    if not isinstance(groups, list):
        raise ValueError(f"hooks.{event} must be a JSON array")
    for group in groups:
        if not isinstance(group, dict) or group.get("matcher") != matcher:
            continue
        handlers = group.setdefault("hooks", [])
        if not isinstance(handlers, list):
            raise ValueError(f"hooks.{event}.hooks must be a JSON array")
        for existing in handlers:
            if not isinstance(existing, dict):
                continue
            args = existing.get("args", [])
            if not isinstance(args, list):
                raise ValueError("hook handler args must be a JSON array")
            values = [existing.get("command", ""), *args]
            if any("loop_detector.py" in str(value) for value in values):
                return False
        handlers.append(handler)
        return True
    groups.append({"matcher": matcher, "hooks": [handler]})
    return True


def write_settings(path: Path, settings: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def install_configs(target: Path) -> list[tuple[Path, bool]]:
    source = str(Path(__file__).resolve())
    python = str(Path(sys.executable).resolve())
    command_parts = [python, source, "hook"]
    claude_handler = {"type": "command", "command": python, "args": [source, "hook"], "timeout": 10}
    codex_handler = {
        "type": "command",
        "command": " ".join(shlex.quote(part) for part in command_parts),
        "commandWindows": subprocess.list2cmdline(command_parts),
        "timeout": 10,
    }
    targets = [
        (target / ".claude" / "settings.json", "PostToolUseFailure", "*", claude_handler),
        (target / ".codex" / "hooks.json", "PostToolUse", "^Bash$", codex_handler),
    ]
    results = []
    for path, event, matcher, handler in targets:
        settings = read_settings(path)
        changed = install_hook(settings, event, matcher, handler)
        if changed:
            write_settings(path, settings)
        results.append((path, changed))
    return results


def cmd_hook() -> None:
    """Claude Code and Codex hook entry point. Internal failures stay fail-open."""
    try:
        payload = json.load(sys.stdin)
        event_name = payload.get("hook_event_name")
        if event_name == "PostToolUse":
            warning = codex_hook_warning(payload)
            if warning:
                print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": warning}}, ensure_ascii=False))
            return
        warning = claude_hook_warning(payload) if event_name == "PostToolUseFailure" else None
    except Exception:
        warning = None
    if warning:
        print(warning, file=sys.stderr)
        sys.exit(2)


def cmd_install(args) -> None:
    target = Path(args.target).resolve()
    if not target.is_dir():
        print(f"target directory not found: {target}", file=sys.stderr)
        sys.exit(2)
    try:
        results = install_configs(target)
    except (OSError, ValueError) as exc:
        print(f"installation failed: {exc}", file=sys.stderr)
        sys.exit(2)
    for path, changed in results:
        print(f"{'installed' if changed else 'already installed'}: {path}")
    print("Codex: start a new session, then review and trust the hook with /hooks.")


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    scan_p = sub.add_parser("scan", help="scan current project's latest session (or --all)")
    scan_p.add_argument("--all", action="store_true", help="scan every session across all projects")
    scan_p.add_argument("--json", action="store_true", help="emit JSON instead of text")
    sub.add_parser("hook", help="Claude Code/Codex hook entry point (reads hook JSON from stdin)")
    install_p = sub.add_parser("install", help="install Claude Code and Codex hooks into a project")
    install_p.add_argument("--target", default=".", help="project directory to configure (default: current directory)")
    args = ap.parse_args()

    if args.cmd == "hook":
        cmd_hook()
    elif args.cmd == "install":
        cmd_install(args)
    else:
        cmd_scan(args)


if __name__ == "__main__":
    main()
