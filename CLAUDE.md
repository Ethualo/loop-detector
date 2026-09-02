# claude-loop-detector

AI 코딩 에이전트(Claude Code 우선, 추후 Cursor 등) 세션 transcript(JSONL)에서 반복 실패 패턴을 탐지하는 CLI 도구. Phase 1(사후 스캔) 완료, 599개 세션 실측 코퍼스로 튜닝됨. Phase 2(실시간 훅) 스크립트 구현·테스트 완료, `settings.json` 등록은 아직 안 함(유저 승인 대기).

## 구조

```
claude_loop_detector.py        # 단일 stdlib 스크립트
test_claude_loop_detector.py   # assert 기반 self-check
```

패키지 분리(`pyproject.toml`, `detectors/` 서브패키지, `windowing.py`, `report.py` 등)는 하지 않는다.
로직이 실제로 한 파일을 넘어설 때만 쪼갠다.

## 실행

```
python claude_loop_detector.py scan          # 현재 cwd 프로젝트 최신 세션
python claude_loop_detector.py scan --all    # 전체 세션
python claude_loop_detector.py scan --json
python claude_loop_detector.py hook          # PostToolUseFailure 훅 진입점, stdin으로 훅 JSON 받음
```

### 훅 등록 (Phase 2, 수동 적용 필요)

`~/.claude/settings.json`의 `hooks.PostToolUseFailure`에 추가:

```json
{
  "hooks": {
    "PostToolUseFailure": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python C:\\Users\\user\\Desktop\\git_projects\\claude-loop-detector\\claude_loop_detector.py hook",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

같은 도구가 같은 마스킹된 에러로 연속 3회 이상 실패하면 stderr로 경고 + exit 2(Claude에게 컨텍스트로 노출, 차단은 아님). 그 외엔 exit 0 무음.

## 테스트

detector 로직(파서/fingerprint/각 탐지기)마다 assert 기반 최소 케이스 1개. 프레임워크·픽스처 없음.
`python test_claude_loop_detector.py` 로 실행.

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
- **2단계(실시간)**: 증분 state 파일 없이 매번 재파싱 + `PostToolUseFailure` 훅(실패 시에만 발화, Claude Code 실제 이벤트로 확인됨 — 2026-09 기준 문서 직접 조회)에 얹기. 훅 스크립트는 내부 실패 시 반드시 exit 0(fail-open) — `cmd_hook()`에서 전체를 try/except로 감싸고 예외 시 hit=None.
- **훅 판정 로직**: 매 실패 시 transcript 전체 재파싱 후 `iter_runs()`로 얻은 맨 끝(tail) run만 확인 — 그 run이 error이고 count>=3이면 경고. 과거에 있었다가 이미 끊긴 루프는 무시(tail 기준이라 자연히 걸러짐). `no_progress`는 훅 대상 아님(성공 케이스라 PostToolUseFailure에 안 걸림) — `scan`에서만 감지.
- **stdout/stderr 인코딩**: Windows cp949 콘솔에서 한글/이모지 dash(—) 등 출력 시 `UnicodeEncodeError` 발생 — `main()` 진입 시 `sys.stdout`/`sys.stderr` 둘 다 `.reconfigure(encoding="utf-8", errors="replace")` 필수(훅 경고 메시지도 stderr로 나가므로 stderr도 필요).

## 주의사항

- 599개 세션 전체 스캔(`--all --json`) 실측 8초. 최종 결과: 11개 세션에서 12건(repeat_failure 8, no_progress 4) — 노이즈 없이 신호만 남은 상태.
- gateguard 훅의 `[Fact-Forcing Gate]` 반복 차단 메시지가 repeat_failure로 잡히는 건 정상(버그 아님) — 실제로 확인됨.
- 2단계 훅을 실시간으로 붙일 때 gateguard 사례처럼 "탐지기가 자기 자신의 루프를 만드는" 자기증폭 위험 있음 — exit 0 폴백 필수.
- 새 tool 추가 시 짧은 고정 성공/무출력 메시지("완료", "no output", "not found" 류)를 리턴하는지 확인 — no_progress는 input 기반이라 안전하지만, 향후 repeat_failure 쪽에 같은 tool의 에러 메시지가 지나치게 짧고 고정적이면 동일 오탐 재발 가능.
- 훅은 실제 settings.json 등록 전, 합성 transcript(3연속 동일 Bash 실패)로만 end-to-end 검증됨 — 실제 Claude Code 세션에서의 타이밍(현재 실패 레코드가 hook 발화 시점에 transcript 파일에 이미 써져 있는지)은 미검증 가정. 안 써져 있으면 그 호출에서만 감지 놓치고 다음 실패 때 카운트됨 — 크래시 아님, 성능/카운트 오프바이원 정도.
