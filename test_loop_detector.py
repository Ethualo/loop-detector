"""Minimal assert-based checks. No framework, per project convention."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from loop_detector import claude_hook_warning, codex_hook_warning, detect_repeated_runs, extract_tool_events, fingerprint, hook_state_path, install_configs, install_hook, iter_records, read_settings, target_key, trailing_repeat_failure


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


def test_target_key_distinguishes_inputs_after_300_characters():
    prefix = "x" * 300
    assert target_key({"command": prefix + "A"}) != target_key({"command": prefix + "B"})


def test_detect_repeated_runs_distinguishes_long_targets_and_error_class():
    prefix = "x" * 300
    events = [
        ("Read", False, "ok", {"command": prefix + "A"}),
    ] * 3 + [
        ("Read", False, "ok", {"command": prefix + "B"}),
    ] * 3
    findings = detect_repeated_runs(events)
    assert [finding["count"] for finding in findings] == [3, 3]

    collision = [
        ("Bash", True, '{"x": "same"}', {"command": "x"}),
        ("Bash", False, "ok", {"x": "same"}),
        ("Bash", False, "ok", {"x": "same"}),
    ]
    assert detect_repeated_runs(collision) == []


def test_fingerprint_masks_quoted_windows_and_posix_paths():
    windows_a = fingerprint(r'failed at "C:\\work dir\\a.py:12"')
    windows_b = fingerprint(r'failed at "C:\\other dir\\b.py:99"')
    posix_a = fingerprint(r'failed at "/home/user/project one/a.py:12"')
    posix_b = fingerprint(r'failed at "/home/user/other project/b.py:99"')
    assert windows_a == windows_b
    assert posix_a == posix_b


def test_iter_records_skips_invalid_utf8_and_non_objects():
    with TemporaryDirectory(dir=Path(__file__).parent) as directory:
        transcript = Path(directory) / "session.jsonl"
        transcript.write_bytes(b"\xff\n[]\n{}\n")
        assert list(iter_records(transcript)) == [{}]


def test_extract_tool_events_skips_orphan_and_incomplete_pairs():
    with TemporaryDirectory(dir=Path(__file__).parent) as directory:
        transcript = Path(directory) / "session.jsonl"
        records = [
            {"message": {"content": [{"type": "tool_result", "tool_use_id": "missing", "is_error": True, "content": "orphan"}]}},
            {"message": {"content": [{"type": "tool_use", "id": "missing-name", "name": "", "input": {}}]}},
            {"message": {"content": [{"type": "tool_use", "id": "missing-input", "name": "Bash"}]}},
            {"message": {"content": [{"type": "tool_result", "tool_use_id": "missing-input", "is_error": False, "content": "ignored"}]}},
            {"message": {"content": [{"type": "tool_use", "id": "bad-error", "name": "Bash", "input": {"command": "x"}}]}},
            {"message": {"content": [{"type": "tool_result", "tool_use_id": "bad-error", "is_error": "false", "content": "ignored"}]}},
            {"message": {"content": [{"type": "tool_use", "id": "valid", "name": "Read", "input": {"file_path": "a.py"}}]}},
            {"message": {"content": [{"type": "tool_result", "tool_use_id": "valid", "content": "ok"}]}},
        ]
        transcript.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
        assert list(extract_tool_events(transcript)) == [("Read", False, "ok", {"file_path": "a.py"})]


def test_extract_tool_events_bounds_unmatched_tool_use_memory():
    with TemporaryDirectory(dir=Path(__file__).parent) as directory:
        transcript = Path(directory) / "session.jsonl"
        pending_limit = 4096
        records = [
            {"message": {"content": [{"type": "tool_use", "id": f"u{number}", "name": "Bash", "input": {"command": str(number)}}]}}
            for number in range(pending_limit + 1)
        ]
        records.extend([
            {"message": {"content": [{"type": "tool_result", "tool_use_id": "u0", "is_error": False, "content": "evicted"}]}},
            {"message": {"content": [{"type": "tool_use", "id": "valid", "name": "Read", "input": {"file_path": "a.py"}}]}},
            {"message": {"content": [{"type": "tool_result", "tool_use_id": "valid", "is_error": False, "content": "ok"}]}},
        ])
        transcript.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
        assert list(extract_tool_events(transcript)) == [("Read", False, "ok", {"file_path": "a.py"})]


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
        assert codex_hook_warning(failure, state_dir)
        success = {**failure, "tool_response": {"metadata": {"exit_code": 0}}}
        assert codex_hook_warning(success, state_dir) is None
        assert not hook_state_path("session-1", state_dir).exists()
        assert codex_hook_warning(failure, state_dir) is None


def test_codex_hook_warning_only_reports_once_and_migrates_legacy_state():
    with TemporaryDirectory(dir=Path(__file__).parent) as directory:
        state_dir = Path(directory)
        failure = {
            "hook_event_name": "PostToolUse",
            "session_id": "session-1",
            "tool_name": "Bash",
            "tool_response": {"output": "test failed", "metadata": {"exit_code": 1}},
        }
        assert codex_hook_warning(failure, state_dir) is None
        assert codex_hook_warning(failure, state_dir) is None
        warning = codex_hook_warning(failure, state_dir)
        assert warning and "3회" in warning
        assert codex_hook_warning(failure, state_dir) is None
        state_path = hook_state_path("session-1", state_dir)
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["schema_version"] == 1
        assert state["warned"] is True

        legacy = {"tool": "Bash", "fingerprint": fingerprint(failure["tool_response"]), "count": 2}
        state_path.write_text(json.dumps(legacy), encoding="utf-8")
        assert codex_hook_warning(failure, state_dir) is None
        migrated = json.loads(state_path.read_text(encoding="utf-8"))
        assert migrated["schema_version"] == 1
        assert migrated["count"] == 1
        assert codex_hook_warning(failure, state_dir) is None
        warning = codex_hook_warning(failure, state_dir)
        assert warning and "3회" in warning
        success = {**failure, "tool_response": {"output": "ok", "metadata": {"exit_code": 0}}}
        assert codex_hook_warning(success, state_dir) is None
        assert codex_hook_warning(failure, state_dir) is None


def test_install_hook_merges_without_duplicate_or_overwrite():
    settings = {"hooks": {"PostToolUseFailure": [{"matcher": "*", "hooks": [{"type": "command", "command": "keep-me"}]}]}}
    handler = {"type": "command", "command": "python", "args": ["C:/tools/loop_detector.py", "hook"]}
    assert install_hook(settings, "PostToolUseFailure", "*", handler)
    handlers = settings["hooks"]["PostToolUseFailure"][0]["hooks"]
    assert [handler["command"] for handler in handlers] == ["keep-me", "python"]
    assert not install_hook(settings, "PostToolUseFailure", "*", handler)


def test_install_configs_creates_both_configs_and_is_idempotent():
    with TemporaryDirectory(dir=Path(__file__).parent) as directory:
        target = Path(directory)
        results = install_configs(target)
        assert all(changed for _, changed in results)
        assert read_settings(target / ".claude" / "settings.json")["hooks"]["PostToolUseFailure"]
        assert read_settings(target / ".codex" / "hooks.json")["hooks"]["PostToolUse"]
        assert not any(changed for _, changed in install_configs(target))


def test_install_hook_rejects_invalid_existing_args():
    settings = {"hooks": {"PostToolUseFailure": [{"matcher": "*", "hooks": [{"args": "not-an-array"}]}]}}
    try:
        install_hook(settings, "PostToolUseFailure", "*", {"type": "command"})
    except ValueError as exc:
        assert "args" in str(exc)
    else:
        assert False, "invalid hook args must not be overwritten"


def test_install_hook_detects_loop_detector_in_command_windows():
    settings = {"hooks": {"PostToolUse": [{"matcher": "^Bash$", "hooks": [{"type": "command", "command": "python3", "commandWindows": "python C:/tools/loop_detector.py hook"}]}]}}
    handler = {"type": "command", "command": "python3", "commandWindows": "python C:/other.py hook"}
    assert not install_hook(settings, "PostToolUse", "^Bash$", handler)
    assert len(settings["hooks"]["PostToolUse"][0]["hooks"]) == 1


def test_install_configs_preflights_before_writing_any_file():
    with TemporaryDirectory(dir=Path(__file__).parent) as directory:
        target = Path(directory)
        claude_path = target / ".claude" / "settings.json"
        codex_path = target / ".codex" / "hooks.json"
        claude_path.parent.mkdir()
        codex_path.parent.mkdir()
        original = {"hooks": {"PostToolUseFailure": []}}
        claude_path.write_text(json.dumps(original), encoding="utf-8")
        codex_path.write_text("{broken", encoding="utf-8")
        try:
            install_configs(target)
        except ValueError:
            pass
        else:
            assert False, "malformed second config must fail"
        assert json.loads(claude_path.read_text(encoding="utf-8")) == original


def test_plugin_manifests_and_bundled_hooks():
    root = Path(__file__).parent
    codex = json.loads((root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    claude = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    codex_hooks = json.loads((root / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    assert codex["name"] == claude["name"] == "loop-detector"
    assert codex["version"] == claude["version"] == "0.1.1"
    assert "hooks" not in codex
    assert codex_hooks["hooks"]["PostToolUse"][0]["matcher"] == "^Bash$"
    assert codex_hooks["hooks"]["PostToolUseFailure"][0]["matcher"] == "*"
    assert "${CLAUDE_PLUGIN_ROOT}" in codex_hooks["hooks"]["PostToolUseFailure"][0]["hooks"][0]["args"][0]
    assert "${CLAUDE_PLUGIN_ROOT}" in codex_hooks["hooks"]["PostToolUse"][0]["hooks"][0]["command"]


def test_bundled_hook_commands_execute_from_space_path():
    root = Path(__file__).parent.resolve()
    hooks = json.loads((root / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    with TemporaryDirectory(prefix="loop detector ") as directory:
        plugin = Path(directory)
        shutil.copy2(root / "loop_detector.py", plugin / "loop_detector.py")
        env = dict(os.environ, TMP=directory, TEMP=directory, TMPDIR=directory)
        env.pop("PLUGIN_ROOT", None)
        env["CLAUDE_PLUGIN_ROOT"] = str(plugin)
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")

        def invoke(command, payload):
            return subprocess.run(command, input=json.dumps(payload), text=True,
                                  encoding="utf-8", capture_output=True, env=env, timeout=10)

        post = hooks["PostToolUse"][0]["hooks"][0]
        command = post["command"].replace("${CLAUDE_PLUGIN_ROOT}", str(plugin).replace("\\", "/"))
        shell = ["powershell.exe", "-NoProfile", "-Command"] if os.name == "nt" else ["sh", "-c"]
        result = invoke([*shell, command], {"hook_event_name": "PostToolUse", "session_id": "claude-success", "tool_name": "Bash", "tool_response": {"stdout": "ok"}})
        assert (result.returncode, result.stdout, result.stderr) == (0, "", ""), result.stderr

        probe = {"hook_event_name": "PostToolUse", "session_id": "shared-command-probe", "tool_name": "Bash", "tool_response": {"exit_code": 1, "output": "probe failed"}}
        probes = [invoke([*shell, command], probe) for _ in range(3)]
        assert all(item.returncode == 0 and not item.stderr for item in probes), probes
        assert probes[2].stdout, "shared hook command must execute the detector"
        assert json.loads(probes[2].stdout)["hookSpecificOutput"]["hookEventName"] == "PostToolUse"

        failure = {"hook_event_name": "PostToolUse", "session_id": "codex-integration", "tool_name": "Bash", "tool_response": {"output": "test failed", "metadata": {"exit_code": 1}}}
        env["PLUGIN_ROOT"] = str(plugin)
        command = post["commandWindows"] if os.name == "nt" else post["command"]
        codex_command = command if os.name == "nt" else ["sh", "-c", command]
        results = [invoke(codex_command, failure) for _ in range(4)]
        assert all(result.returncode == 0 and not result.stderr for result in results), results
        assert [bool(result.stdout) for result in results] == [False, False, True, False]
        assert json.loads(results[2].stdout)["hookSpecificOutput"]["hookEventName"] == "PostToolUse"

        transcript = plugin / "session.jsonl"
        records = []
        for number in range(3):
            records.extend([
                {"message": {"content": [{"type": "tool_use", "id": str(number), "name": "Bash", "input": {}}]}},
                {"message": {"content": [{"type": "tool_result", "tool_use_id": str(number), "is_error": True, "content": "same error"}]}},
            ])
        transcript.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")
        handler = hooks["PostToolUseFailure"][0]["hooks"][0]
        args = [arg.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin)) for arg in handler["args"]]
        result = invoke([handler["command"], *args], {"hook_event_name": "PostToolUseFailure", "transcript_path": str(transcript)})
        assert result.returncode == 2 and not result.stdout and "3 times" in result.stderr
        result = invoke([sys.executable, str(plugin / "loop_detector.py"), "hook"], [])
        assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


if __name__ == "__main__":
    test_fingerprint_masks_placeholders()
    test_detect_repeated_runs_finds_retry_loop()
    test_detect_repeated_runs_ignores_below_threshold()
    test_detect_repeated_runs_flags_no_progress_on_same_target_repeated()
    test_detect_repeated_runs_ignores_boilerplate_success_across_different_targets()
    test_target_key_distinguishes_inputs_after_300_characters()
    test_detect_repeated_runs_distinguishes_long_targets_and_error_class()
    test_fingerprint_masks_quoted_windows_and_posix_paths()
    test_iter_records_skips_invalid_utf8_and_non_objects()
    test_extract_tool_events_skips_orphan_and_incomplete_pairs()
    test_extract_tool_events_bounds_unmatched_tool_use_memory()
    test_trailing_repeat_failure_detects_active_loop_at_tail()
    test_trailing_repeat_failure_none_when_loop_already_broken()
    test_trailing_repeat_failure_none_below_threshold()
    test_claude_hook_warning_uses_failed_transcript_tail()
    test_codex_hook_warning_reports_three_matching_bash_failures_and_clears_on_success()
    test_codex_hook_warning_only_reports_once_and_migrates_legacy_state()
    test_install_hook_merges_without_duplicate_or_overwrite()
    test_install_configs_creates_both_configs_and_is_idempotent()
    test_install_hook_rejects_invalid_existing_args()
    test_install_hook_detects_loop_detector_in_command_windows()
    test_install_configs_preflights_before_writing_any_file()
    test_plugin_manifests_and_bundled_hooks()
    test_bundled_hook_commands_execute_from_space_path()
    print("all checks passed")
