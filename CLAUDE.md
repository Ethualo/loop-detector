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
- **2단계(실시간)**: Claude Code는 `PostToolUseFailure`에서 transcript tail을 재파싱한다. Codex는 `PostToolUse` payload의 `session_id`, `tool_name`, `tool_response`만 사용해 Bash non-zero를 세션별 임시 상태로 판정한다. Codex transcript 형식은 공식 안정 인터페이스가 아니므로 의존하지 않는다. 두 경로 모두 내부 예외 시 fail-open이다.
- **훅 판정 로직**: Claude Code는 tail run이 error이고 count>=3일 때만 경고한다. Codex는 같은 마스킹된 응답을 3회 연속 실패로 세고 성공 결과에서 상태를 비운다. `no_progress`는 성공도 포함하는 오프라인 `scan`에서만 감지한다.
- **탐색기 경계**: `target_key`는 정렬된 전체 JSON 입력으로 그룹화하고 화면 출력만 300자로 자른다. JSONL 파서는 잘못된 UTF-8·비객체·orphan/incomplete tool pair를 무시하며 보류 tool_use는 4096개로 제한한다.
- **Codex 상태**: 상태 파일은 schema version 1과 SHA-256 fingerprint를 저장하고, 동일 루프에는 임계치 경고를 한 번만 보낸다. 이전·손상 상태는 새 카운트로 초기화한다.
- **stdout/stderr 인코딩**: Windows cp949 콘솔에서 한글/이모지 dash(—) 등 출력 시 `UnicodeEncodeError` 발생 — `main()` 진입 시 `sys.stdout`/`sys.stderr` 둘 다 `.reconfigure(encoding="utf-8", errors="replace")` 필수(훅 경고 메시지도 stderr로 나가므로 stderr도 필요).

## 주의사항

- 599개 세션 전체 스캔(`--all --json`) 실측 8초. 최종 결과: 11개 세션에서 12건(repeat_failure 8, no_progress 4) — 노이즈 없이 신호만 남은 상태.
- gateguard 훅의 `[Fact-Forcing Gate]` 반복 차단 메시지가 repeat_failure로 잡히는 건 정상(버그 아님) — 실제로 확인됨.
- 2단계 훅을 실시간으로 붙일 때 gateguard 사례처럼 "탐지기가 자기 자신의 루프를 만드는" 자기증폭 위험 있음 — exit 0 폴백 필수.
- 새 tool 추가 시 짧은 고정 성공/무출력 메시지("완료", "no output", "not found" 류)를 리턴하는지 확인 — no_progress는 input 기반이라 안전하지만, 향후 repeat_failure 쪽에 같은 tool의 에러 메시지가 지나치게 짧고 고정적이면 동일 오탐 재발 가능.
- 합성 transcript/payload를 설정된 명령에 전달하는 별도 프로세스 통합 테스트가 공백 경로에서 통과했다. 2026-09-04 Codex `hooks/list`에서 프로젝트 훅이 enabled/trusted임을 확인했지만 현재 세션의 자동 경고는 관찰되지 않았다. Claude CLI는 이 환경에 없으며, 양쪽 네이티브 자동 발화 검증은 남아 있다.
- 설치의 사전 검증은 테스트했지만, 두 파일을 기록하는 중 디스크 오류가 발생할 때의 교차 파일 롤백과 같은 세션 훅의 동시 실행 잠금은 아직 구현하지 않았다.

<!-- handoff:learnings:begin -->
## Session Learnings (auto-updated by handoff)

### Implicit Rules
- Windows 10, PowerShell primary shell, Python 3.13.9 invoked as `python` (never `python3` — broken Windows Store stub).
- No external dependencies — stdlib only, single flat script (explicit project convention per user's global CLAUDE.md).
- Session transcripts sourced from `~/.claude/projects/*/*.jsonl` (main) + `~/.claude/projects/*/*/subagents/*.jsonl` (subagents) — both paths scanned.
- Gateguard hook requires 4 stated facts before first Bash/Edit/Write call per turn-block — normal, not bug.
- Claude Code has 33 distinct hook events including separate PostToolUse (success-only) and PostToolUseFailure (failure-only), verified via direct docs fetch (code.claude.com/docs/en/hooks, 2026-09).
- Settings.json hook registration format: `hooks.<EventName>[].matcher` + `.hooks[].{type:'command', command, timeout}`. Currently registered top-level hook keys: SessionStart, UserPromptSubmit, PreToolUse, SubagentStart — no PostToolUseFailure yet.
- Editing `~/.claude/settings.json` from within session blocked by auto-mode permission classifier — hard environment constraint, not something to route around; needs user manual application or explicit permission-rule grant.
- User's GitHub account: `Ethualo`.

### Key Decisions
- Decision: repeat_failure grouped by (tool_name, masked_output_fingerprint); no_progress grouped by (tool_name, RAW unmasked tool_input) — Reason: masking correct for recurring-error text (naturally varies without over-collision) but wrong for non-error boilerplate (many tools return fixed text becoming identical after masking regardless of real target). Input-based grouping restores real identity.
- Decision: REPEAT_THRESHOLD unified to 3 for both finding types — Reason: dropped unnecessary separate NO_PROGRESS_THRESHOLD=5; single value clarifies intent.
- Decision: Phase 2 hook implements ONLY repeat_failure, not no_progress — Reason: wired to PostToolUseFailure (failure-only event), so non-error no_progress signal unobservable there. no_progress stays scan-only/offline.
- Decision: Hook re-parses whole transcript on every failure, inspects only trailing/still-open run via iter_runs() generator — Reason: avoids flagging loop already broken earlier in session; matches 'happening right now' intent. Reparsing cheap (~9ms measured).
- Decision: Verified PostToolUseFailure as distinct Claude Code hook event via direct docs fetch (code.claude.com/docs/en/hooks) — Reason: independently re-confirmed rather than trusted from prior session memory or subagent first-pass claim.
- Decision: Dropped `claude_` prefix from both script filenames per explicit user request — Reason: cleaner naming, matches user direction.
- Decision: stdout AND stderr reconfigured with utf-8 encoding on Windows — Reason: console cp949 throws UnicodeEncodeError on Korean text or em-dash otherwise.
- Decision: When settings.json edit blocked by permission classifier, escalated to user with exact snippet instead of attempting workaround — Reason: denial message warns against routing-around 'in malicious ways'; editing global security-adjacent config goes through user hands or explicit permission grant, not forced retry.

<!-- handoff:learnings:end -->
