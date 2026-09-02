"""Minimal assert-based checks. No framework, per project convention."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from loop_detector import claude_hook_warning, codex_hook_warning, detect_repeated_runs, fingerprint, trailing_repeat_failure


def test_fingerprint_masks_placeholders():
    a = fingerprint("Error at C:\\Users\\user\\foo.py:12:5 tool_use toolu_ABC123 (4.2KB) 2026-09-02T05:51:21.411Z")
    b = fingerprint("Error at C:\\Users\\user\\bar.py:99:1 tool_use toolu_XYZ789 (9.9KB) 2026-09-02T06:00:00.000Z")
    assert a == b, f"masked fingerprints should collide: {a!r} != {b!r}"


def test_detect_repeated_runs_finds_retry_loop():
    # same tool + same masked error output, three times in a row = retry loop
    events = [
        ("Bash", True, "same error", {"command": "npm test"}),
        ("Bash", True, "same error", {"command": "npm test --verbose"}),
        ("Bash", True, "same error", {"command": "npm test -x"}),
        ("Bash", False, "ok", {"command": "ls"}),
    ]
    findings = detect_repeated_runs(events)
    assert len(findings) == 1
    assert findings[0] == {"type": "repeat_failure", "tool": "Bash", "count": 3, "fingerprint": "same error"}


def test_detect_repeated_runs_ignores_below_threshold():
    events = [("Bash", True, "err", {"command": "x"}), ("Bash", True, "err", {"command": "x"})]
    assert detect_repeated_runs(events) == []


def test_detect_repeated_runs_flags_no_progress_on_same_target_repeated():
    # same file re-read three times with no error = stalled, regardless of output text
    events = [("Read", False, "contents", {"file_path": "a.py"})] * 3 + [
        ("Edit", False, "ok", {"file_path": "b.py"})
    ]
    findings = detect_repeated_runs(events)
    assert len(findings) == 1
    assert findings[0]["type"] == "no_progress"
    assert findings[0]["tool"] == "Read"
    assert findings[0]["count"] == 3


def test_detect_repeated_runs_ignores_boilerplate_success_across_different_targets():
    # Edit's fixed success message is identical for every file; grouping by that
    # text alone would falsely look like a loop. Different targets must not collide.
    events = [
        ("Edit", False, "The file has been updated successfully.", {"file_path": "a.py"}),
        ("Edit", False, "The file has been updated successfully.", {"file_path": "b.py"}),
        ("Edit", False, "The file has been updated successfully.", {"file_path": "c.py"}),
    ]
    assert detect_repeated_runs(events) == []


def test_trailing_repeat_failure_detects_active_loop_at_tail():
    events = [("Bash", True, "same error", {"command": "x"})] * 3
    assert trailing_repeat_failure(events) == ("Bash", 3)


def test_trailing_repeat_failure_none_when_loop_already_broken():
    # the run at the very end is a single success, even though 3 failures preceded it
    events = [("Bash", True, "same error", {"command": "x"})] * 3 + [("Bash", False, "ok", {"command": "y"})]
    assert trailing_repeat_failure(events) is None


def test_trailing_repeat_failure_none_below_threshold():
    events = [("Bash", True, "same error", {"command": "x"})] * 2
    assert trailing_repeat_failure(events) is None


def test_claude_hook_warning_uses_failed_transcript_tail():
    with TemporaryDirectory(dir=Path(__file__).parent) as directory:
        transcript = Path(directory) / "session.jsonl"
        records = []
        for number in range(3):
            records.extend([
                {"message": {"content": [{"type": "tool_use", "id": f"toolu_{number}", "name": "Bash", "input": {"command": "npm test"}}]}},
                {"message": {"content": [{"type": "tool_result", "tool_use_id": f"toolu_{number}", "is_error": True, "content": "test failed"}]}},
            ])
        transcript.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
        warning = claude_hook_warning({"transcript_path": str(transcript)})
        assert warning and "3 times" in warning


def test_codex_hook_warning_reports_three_matching_bash_failures_and_clears_on_success():
    with TemporaryDirectory(dir=Path(__file__).parent) as directory:
        state_dir = Path(directory)
        failure = {
            "hook_event_name": "PostToolUse",
            "session_id": "session-1",
            "tool_name": "Bash",
            "tool_response": {"output": "test failed at C:\\work\\a.py:12", "metadata": {"exit_code": 1}},
        }
        assert codex_hook_warning(failure, state_dir) is None
        assert codex_hook_warning(failure, state_dir) is None
        warning = codex_hook_warning(failure, state_dir)
        assert warning and "3회" in warning
        success = {**failure, "tool_response": {"output": "ok", "metadata": {"exit_code": 0}}}
        assert codex_hook_warning(success, state_dir) is None
        assert codex_hook_warning(failure, state_dir) is None


if __name__ == "__main__":
    test_fingerprint_masks_placeholders()
    test_detect_repeated_runs_finds_retry_loop()
    test_detect_repeated_runs_ignores_below_threshold()
    test_detect_repeated_runs_flags_no_progress_on_same_target_repeated()
    test_detect_repeated_runs_ignores_boilerplate_success_across_different_targets()
    test_trailing_repeat_failure_detects_active_loop_at_tail()
    test_trailing_repeat_failure_none_when_loop_already_broken()
    test_trailing_repeat_failure_none_below_threshold()
    test_claude_hook_warning_uses_failed_transcript_tail()
    test_codex_hook_warning_reports_three_matching_bash_failures_and_clears_on_success()
    print("all checks passed")
