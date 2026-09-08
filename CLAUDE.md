# loop-detector

AI 코딩 에이전트 세션의 반복 실패 패턴을 탐지하는 CLI 도구. Phase 1은 Claude Code transcript 스캐너이고, Phase 2는 Claude Code와 Codex의 공식 훅으로 실시간 경고한다.

## 구조

```
loop_detector.py              # 단일 stdlib 스크립트
test_loop_detector.py         # assert 기반 self-check
.codex-plugin/plugin.json     # Codex 플러그인 manifest
.claude-plugin/plugin.json    # Claude Code 플러그인 manifest
hooks/hooks.json              # Claude Code/Codex 기본 번들 훅
```

패키지 분리(`pyproject.toml`, `detectors/` 서브패키지, `windowing.py`, `report.py` 등)는 하지 않는다.
로직이 실제로 한 파일을 넘어설 때만 쪼갠다.

## 실행

```
python loop_detector.py scan          # 현재 cwd 프로젝트 최신 세션
python loop_detector.py scan --all    # 전체 세션
python loop_detector.py scan --json
python loop_detector.py hook          # Claude Code/Codex 훅 진입점, stdin으로 훅 JSON 받음
python loop_detector.py install       # 현재 프로젝트에 Claude Code/Codex 훅 설치
python loop_detector.py install --target C:\path\to\project
```

### 공식 훅 설치

- Claude Code는 커밋된 `.claude/settings.json`을 프로젝트 설정으로 읽는다. `PostToolUseFailure`에서 기존 transcript tail을 판정하고, 반복 실패면 stderr + exit 2로 Claude에게 경고한다.
- Codex는 커밋된 `.codex/hooks.json`을 공식 프로젝트 훅 위치로 읽는다. 새 Codex 세션에서 `/hooks`로 훅을 검토·신뢰하면 `PostToolUse`의 Bash non-zero 결과를 세션별로 3회 추적해 `additionalContext` 경고를 준다. 전역 설정 수정은 필요 없다.
- `install`은 대상 프로젝트의 두 JSON 설정을 병합해 이 스크립트의 절대 경로를 등록한다. 기존 훅은 보존하고, 이미 등록된 loop-detector 훅은 중복 추가하지 않는다.
- `install`은 두 설정을 먼저 모두 읽고 검증한 뒤 변경 파일을 기록한다. 한쪽 JSON이 잘못되면 기록하지 않는다.

### 플러그인 포장

- Codex는 `.codex-plugin/plugin.json`과 기본 경로 `hooks/hooks.json`의 `PostToolUse` 훅을 사용한다. 공통 셸 명령은 양쪽이 제공하는 `CLAUDE_PLUGIN_ROOT`, Codex Windows 명령은 `PLUGIN_ROOT`를 사용한다. 플러그인은 Python 3.10+의 `python` 명령이 필요하다.
- Claude Code는 `.claude-plugin/plugin.json`과 기본 경로 `hooks/hooks.json`의 `PostToolUseFailure` 훅을 사용한다. 훅 명령은 `CLAUDE_PLUGIN_ROOT`로 플러그인 루트를 참조한다.
- 기존 `.claude/settings.json`과 `.codex/hooks.json`은 프로젝트 설정을 직접 설치하는 호환 경로로 유지한다. 플러그인과 프로젝트 설정을 동시에 켜면 같은 이벤트가 중복 실행될 수 있다.
- MCP 서버는 포함하지 않는다. 현재 기능은 훅과 CLI로 충분하며, 별도 온디맨드 도구가 필요해질 때 추가한다.

## 테스트

detector 로직(파서/fingerprint/각 탐지기)마다 assert 기반 최소 케이스 1개. 프레임워크·픽스처 없음.
`python test_loop_detector.py` 로 실행.

## 환경

- OS: Windows 10, PowerShell. `python` 사용 (`python3`는 깨진 스토어 스텁).
- 외부 의존성 없음, stdlib만.
- 세션 소스: `~/.claude/projects/*/*.jsonl` (메인) + `~/.claude/projects/*/*/subagents/*.jsonl` (서브에이전트) 둘 다 스캔.

## 주요 패턴 / 결정

- **프로젝트 식별**: 디렉토리명 역매핑(손실 있음) 대신 레코드의 `cwd` 필드 사용 — 100% 존재, 무손실.
- **repeat_failure (재시도 루프) 판정**: `(tool, 마스킹된 output fingerprint)` 동일값 연속 3회+. 마스킹 대상 — 줄번호/절대경로/16진해시/타임스탬프/`toolu_[A-Za-z0-9]+`/`\(\d+(\.\d+)?KB\)`(persisted-output 플레이스홀더).
- **no_progress (무진행) 판정**: `(tool, 원본 미마스킹 input)` 동일값 연속 3회+ — **output 텍스트가 아니라 input으로 그룹화**. 이유: Edit/Write 성공 메시지("The file has been updated successfully...")나 Bash "no output", Grep "No matches found" 같은 짧은 고정 문구는 마스킹하면 서로 다른 대상(다른 파일/명령/패턴)인데도 동일 텍스트로 수렴해 대량 오탐 발생 — 599개 실제 세션 첫 스캔에서 no_progress 580건 중 상당수가 이 버그였음(Bash 379건). input 기반으로 바꾸니 진짜 신호만 남음(no_progress 4건). repeat_failure는 output 마스킹 유지 — 에러 텍스트는 재시도마다 자연히 달라지는 내용이 있어 이 문제가 없었고, 오히려 output 마스킹이 있어야 (동일 원인, 다른 placeholder) 케이스를 잡음.
- **파서 경계**: "raw jsonl → 정규화 Event" 단계만 교체 가능하게 분리. Claude Code 파서만 지금 구현, 다른 에이전트 포맷은 필요해질 때 추가.
- **1단계 = 사후 스캔(on-demand CLI)**, 실시간 훅은 2단계. 근거: 전체 재파싱 9ms 실측, 상태 파일 관리가 오히려 버그 표면 늘림.
- **2단계(실시간, 0.1.2부터)**: Claude Code와 Codex 둘 다 transcript를 재파싱하지 않고, 훅 payload 자체의 필드(Claude: `session_id`/`tool_name`/`error`, Codex: `session_id`/`tool_name`/`tool_response`)만으로 세션별 임시 상태 파일에 연속 실패 카운트를 직접 추적한다(`record_consecutive_failure` 공용 헬퍼). transcript_path를 다시 읽던 구 버전(0.1.1 이하)은 파일이 실제 호출보다 지연돼 있어 3회가 아니라 5~6회 실패해야 발화하는 버그가 있었다 — [주의사항]의 2026-09-08 항목 참고. 두 경로 모두 내부 예외 시 fail-open이다.
- **훅 판정 로직**: Claude/Codex 둘 다 같은 도구+같은 마스킹 오류가 3회 연속이면 그 순간 경고한다(payload 기반이라 lag 없음). Codex는 성공 응답이 오면 상태를 비운다(`PostToolUse`가 성공도 받으므로). Claude는 `PostToolUseFailure`가 실패만 받으므로 성공으로 스트릭을 끊는 기능이 없다 — 트레이드오프로 감수. `no_progress`는 성공도 포함하는 오프라인 `scan`에서만 감지한다.
- **탐색기 경계**: `target_key`는 정렬된 전체 JSON 입력으로 그룹화하고 화면 출력만 300자로 자른다. JSONL 파서는 잘못된 UTF-8·비객체·orphan/incomplete tool pair를 무시하며 보류 tool_use는 4096개로 제한한다.
- **Codex 상태**: 상태 파일은 schema version 1과 SHA-256 fingerprint를 저장하고, 동일 루프에는 임계치 경고를 한 번만 보낸다. 이전·손상 상태는 새 카운트로 초기화한다.
- **stdout/stderr 인코딩**: Windows cp949 콘솔에서 한글/이모지 dash(—) 등 출력 시 `UnicodeEncodeError` 발생 — `main()` 진입 시 `sys.stdout`/`sys.stderr` 둘 다 `.reconfigure(encoding="utf-8", errors="replace")` 필수(훅 경고 메시지도 stderr로 나가므로 stderr도 필요).

## 주의사항

- 599개 세션 전체 스캔(`--all --json`) 실측 8초. 최종 결과: 11개 세션에서 12건(repeat_failure 8, no_progress 4) — 노이즈 없이 신호만 남은 상태.
- gateguard 훅의 `[Fact-Forcing Gate]` 반복 차단 메시지가 repeat_failure로 잡히는 건 정상(버그 아님) — 실제로 확인됨.
- 2단계 훅을 실시간으로 붙일 때 gateguard 사례처럼 "탐지기가 자기 자신의 루프를 만드는" 자기증폭 위험 있음 — exit 0 폴백 필수.
- 새 tool 추가 시 짧은 고정 성공/무출력 메시지("완료", "no output", "not found" 류)를 리턴하는지 확인 — no_progress는 input 기반이라 안전하지만, 향후 repeat_failure 쪽에 같은 tool의 에러 메시지가 지나치게 짧고 고정적이면 동일 오탐 재발 가능.
- 합성 transcript/payload를 설정된 명령에 전달하는 별도 프로세스 통합 테스트가 공백 경로에서 통과했다. 2026-09-04 Codex `hooks/list`에서 프로젝트 훅이 enabled/trusted임을 확인했지만 현재 세션의 자동 경고는 관찰되지 않았다.
- 설치의 사전 검증은 테스트했지만, 두 파일을 기록하는 중 디스크 오류가 발생할 때의 교차 파일 롤백과 같은 세션 훅의 동시 실행 잠금은 아직 구현하지 않았다.
- **2026-09-07 라이브 검증**: `.claude/settings.json` 프로젝트 훅 경로는 실제 대화형 Claude Code 세션(desktop app)에서 Bash 동일 실패 3회 연속 시 정확히 `[loop-detector] Bash has failed the same way 3 times in a row...` stderr+exit2로 발화함을 실측 확인 — 경고가 3번째 실패 직후가 아니라 다음 턴에 붙어 나타나는 1턴 지연은 있으나(하네스 렌더링 지연 추정, `scan`/`trailing_repeat_failure` 재현으로 로직 자체는 정확함을 별도 확인), 발화 자체는 정상.
- **`claude -p ...`로 `PostToolUseFailure` 훅 발화 여부를 스크립트 테스트할 때 nested 세션의 최종 텍스트 답변을 믿지 말 것** — 훅 경고는 `tool_result` 블록 안이 아니라 transcript jsonl에 별도 `{"type":"attachment","attachment":{"type":"hook_blocking_error","hookName":"PostToolUseFailure:..."}}` 레코드로 찍힌다. nested 세션한테 "tool_result 내용 그대로 보고해"라고 물으면 이 attachment를 보고 안 해서 "무발화"로 오판하기 쉽다 — 반드시 nested 세션의 실제 transcript 파일(`~/.claude/projects/<mangled-cwd>/*.jsonl`)에서 `attachment.type == "hook_blocking_error"` 레코드를 직접 확인할 것.
- **정정된 결론 #2 (틀림, #3에서 재정정됨)**: 유저가 대화형 세션(v2.1.263, `claude --plugin-dir <이 리포>`)에서 exec-form `PostToolUseFailure` 3회 실패로 재현 — 무발화. exec-form을 shell-form(`"command": "python \"${CLAUDE_PLUGIN_ROOT}/loop_detector.py\" hook"`)으로 바꾸면 될 거라 판단해 수정, 테스트도 `sh -c`(Git Bash)로 맞춤(PowerShell은 `sys.exit(2)`를 자체 종료코드 1로 뭉갬 — 별개 함정, Claude Code 기본 shell은 bash라 실사용 무관).
- **최종 확정 원인 #3**: shell-form으로 고친 뒤 유저가 새 대화형 세션에서 재검증 — **여전히 무발화**. `/hooks` 확인 결과 PostToolUseFailure에 "No hooks configured for this event". `/hooks`가 플러그인 훅을 아예 안 보여주는 UI 한계일 수 있다는 의심으로, 무조건 즉시 발화하는 최소 테스트 플러그인(`echo TESTHOOKFIRED >&2; exit 2`, 임계치 없음, 실패 1회면 바로 떠야 함)을 새 대화형 세션에서 `--plugin-dir`로 테스트 — **이것도 무발화**. exec-form/shell-form/`description` 필드/스크립트 내용/임계치 전부 배제됨. **결론: 이 Claude Code CLI 버전(v2.1.263)은 `--plugin-dir`로 로드한 `PostToolUseFailure` 훅을 대화형 세션에서 아예 등록하지 않는다 — 이 리포 설정 문제가 아니라 CLI 자체 한계/버그.** 더 이상 hooks.json을 만지며 이 증상을 쫓지 말 것 — 원인이 이 리포 바깥(`--plugin-dir`의 훅 등록 단계)에 있다. shell-form/`description` 제거는 공식 스키마에 맞는 정리로 남겨두되(둘 다 실제 수정 원인은 아니었음), 실시간 경고가 필요하면 `install` 명령의 project-hook(`.claude/settings.json`) 경로를 쓸 것 — 이건 두 번 실측 확인됨. README.md에도 이 한계 명시함.
- **2026-09-08 재확인 (마켓플레이스 설치 경로, 무발화)**: `--plugin-dir` 대신 `/plugin`으로 마켓플레이스 설치(`loop-detector@loop-detector-marketplace`)한 대화형 세션(v2.1.263)에서도 동일 증상 재현 — Bash `sys.exit(1)` 3연속 실패 후 permission recap에 `PostToolUseFailure - Matcher: * / [command] python "${CLAUDE_PLUGIN_ROOT}/loop_detector.py" hook`가 실제로 뜨고 Enter로 confirm까지 됐으나, confirm 후에도 `[loop-detector] ...` stderr 경고 무발화 (raw transcript 직접 확인, `hook_blocking_error` 레코드 0건).
- **2026-09-08 최종 정정 (원인 확정: transcript-tail lag, "간헐적" 아님)**: 바로 다음 세션(같은 cwd, 같은 마켓플레이스 캐시 버전 `0.1.1`)에서 `sys.exit(1)` 5회 요청 → 유저 지시 무시하고 실제로는 6회 호출됨. raw transcript를 호출 순서대로 직접 추적한 결과: **1~4번째 호출 뒤엔 `hook_blocking_error` 없음. 5번째 호출 뒤에야 처음 발생하는데, 메시지 내용은 `"failed the same way 3 times in a row"`. 6번째 호출 뒤 두 번째 발생, 메시지는 `"4 times in a row"`.** 즉 훅이 참조하는 `transcript_path` 파일이 실제 호출 시점보다 최소 2턴 지연돼 있음 — 5번째 실패 시점에 훅이 읽은 transcript엔 아직 3번째 실패까지만 기록되어 있었던 것. 따라서 "3회 연속 실패"를 훅이 인지하려면 실제로는 threshold(3) + lag(최소 2) = 최소 5회 실패가 필요함. 이걸로 위 문단의 "무발화" 세션(정확히 3회만 호출하고 종료)도 완전히 설명됨 — 무발화가 아니라 "그 세션에선 5회까지 안 갔을 뿐". **"간헐적/원인미상" 결론 폐기, "transcript-tail lag로 인한 최소 시도 횟수 증가"로 확정.** line 75의 "1턴 지연(하네스 렌더링 지연 추정)"도 이 현상이었을 가능성 높음 — 그때는 정확한 호출수를 안 세서 원인을 잘못 짚었을 뿐. **실전 함의**: project-hook/마켓플레이스 경로 둘 다 3회 실패로는 못 믿는다 — 최소 5회 안팎 실패가 이어져야 훅이 뜬다고 가정할 것. lag의 근본 원인(훅 실행 시점에 transcript 파일 flush 안 됨 등)은 리포 밖 CLI 내부 동작이라 추가 조사 안 함.
- **2026-09-08 수정 완료 (0.1.2)**: 위 lag는 리포 쪽에서 우회 가능 — `claude_hook_warning`을 Codex 훅과 같은 방식(payload의 `session_id`/`tool_name`/`error`만으로 세션별 임시 상태 파일 추적)으로 재작성해 transcript 재파싱을 아예 없앴다. `trailing_repeat_failure`/`extract_tool_events` 기반 판정은 삭제(오프라인 `scan`용 `iter_runs`/`detect_repeated_runs`는 그대로 유지, 영향 없음). 대가: Codex와 달리 Claude Code엔 성공을 알려주는 훅이 없어(`PostToolUseFailure`는 실패만 옴) 성공이 스트릭을 못 끊는다 — 동일 오류가 성공 사이에 끼어도 계속 카운트됨(허용된 트레이드오프, ponytail 주석 남김). 플러그인/마켓플레이스 캐시가 이 수정을 받으려면 버전 bump 필요해서 `.claude-plugin/plugin.json`·`.codex-plugin/plugin.json`·`.claude-plugin/marketplace.json` 모두 0.1.2로 올림. `python test_loop_detector.py` 전체 통과 확인.

<!-- handoff:learnings:begin -->
## Session Learnings (auto-updated by handoff)

### Implicit Rules
- Windows 10, PowerShell primary shell for the user, but Claude Code's own Bash tool runs via Git Bash — and Claude Code's hook "shell" field defaults to bash (not PowerShell) on Windows whenever Git Bash is present, which is why PowerShell-based test harnesses can give misleading exit-code behavior that doesn't reflect real hook execution.
- Claude Code hook transcript jsonl records a hook's blocking output as a separate top-level line `{"type":"attachment","attachment":{"type":"hook_blocking_error","hookName":"<Event>:<Tool>","blockingError":{...}}}` — never inside the triggering tool_result block. Any future scripted verification of hook firing must grep for this attachment type directly rather than asking an agent to summarize tool_result content.
- `claude plugin validate <path>`, `claude plugin tag <path> [--dry-run] [--force] [--push]`, and interactive `/hooks` (shows per-event registered matchers, or "No hooks configured for this event") are the available official CLI diagnostics for plugin hook issues — no lower-level debug/log flag reliably surfaces plugin hook registration failures (`--debug`/`--debug-file` under -p mode were tried and gave no useful signal for this; `--debug-file` also unpredictably triggered the auto-mode permission classifier).
- This session's Bash tool has no TTY — it cannot drive an interactive Claude Code session itself. Verifying interactive-only behavior (like real PostToolUseFailure firing) requires asking the user to run commands in their own terminal and report/screenshot results.
- GateGuard (`pre:bash:gateguard-fact-force`, `pre:edit-write:gateguard-fact-force`) requires presenting 2-4 short facts before the first Bash call and before first Write/Edit of a given file each session/context-window; triggers are somewhat inconsistent (not always re-triggered on later calls to the same file) but should be answered inline as text immediately before retrying the same tool call, not worked around.

### Key Decisions
- Decision: keep hooks/hooks.json PostToolUseFailure as shell-form (no args array) and drop the stray top-level "description" key — Reason: matches documented schema exactly even though testing proved neither change was the actual root cause; costs nothing and is more correct.
- Decision: do not file a new GitHub issue for this bug this session — Reason: user noted a previous bug report they filed still has no response, so filing another right now has low near-term expected value; the finding is fully documented in-repo (README/CLAUDE.md/memory) so a future issue can be filed quickly from that record.
- Decision: recommend and rely on the `install` command's project-hook (.claude/settings.json) path as the primary live-warning mechanism, not the packaged plugin's --plugin-dir path — Reason: project-hook path is proven live-firing twice in real interactive Claude Code; --plugin-dir path is proven NOT to fire PostToolUseFailure at all in this CLI version, independent of hook form or content.
- Decision: tag the release with `claude plugin tag . --force` (skipping the dirty-tree check) rather than cleaning the whole worktree — Reason: the only uncommitted files were pre-existing unrelated .handoff/* and AGENTS.md artifacts, which project convention already says to exclude from commits; --force only skips the tag command's own dirty-check guard, it does not touch or discard any files.
- Decision: exclude all Codex-side work from this session's scope — Reason: explicit user instruction ("남은작업 codex작업 제외하고 진행").

<!-- handoff:learnings:end -->
