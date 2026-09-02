"""Minimal assert-based checks. No framework, per project convention."""
from claude_loop_detector import fingerprint, detect_repeated_runs, trailing_repeat_failure


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


if __name__ == "__main__":
    test_fingerprint_masks_placeholders()
    test_detect_repeated_runs_finds_retry_loop()
    test_detect_repeated_runs_ignores_below_threshold()
    test_detect_repeated_runs_flags_no_progress_on_same_target_repeated()
    test_detect_repeated_runs_ignores_boilerplate_success_across_different_targets()
    test_trailing_repeat_failure_detects_active_loop_at_tail()
    test_trailing_repeat_failure_none_when_loop_already_broken()
    test_trailing_repeat_failure_none_below_threshold()
    print("all checks passed")
