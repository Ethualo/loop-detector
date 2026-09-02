# loop-detector

AI 코딩 에이전트가 같은 실패를 반복하거나 같은 대상을 계속 건드리는 상황을 찾는 가벼운 CLI 도구입니다. 외부 의존성 없이 Python 표준 라이브러리만 사용합니다.

## 지원 범위

| 기능 | Claude Code | Codex |
| --- | --- | --- |
| 사후 `scan` | 지원 | 미지원 |
| 실시간 반복 실패 경고 | `PostToolUseFailure` | Bash `PostToolUse` |

- Claude Code 사후 스캔은 `~/.claude/projects`의 JSONL transcript를 읽습니다.
- Codex 훅은 공식 payload의 `session_id`, `tool_name`, `tool_response`만 사용합니다. Codex transcript 형식에는 의존하지 않습니다.

## 요구 사항

- Python 3.10+
- 외부 패키지 없음

Windows에서는 `python3` 대신 `python`을 사용합니다.

## 사후 스캔

```powershell
python loop_detector.py scan
python loop_detector.py scan --all
python loop_detector.py scan --json
```

기본 `scan`은 현재 작업 디렉터리에 해당하는 가장 최신 Claude Code 세션 하나를 검사합니다. `--all`은 모든 세션을 검사합니다.

## 탐지 기준

| 결과 | 조건 |
| --- | --- |
| `repeat_failure` | 같은 도구와 같은 마스킹 오류가 연속 3회 이상 |
| `no_progress` | 같은 도구가 같은 원본 입력으로 연속 3회 이상 성공 또는 무변화 |

오류 fingerprint에서는 경로, 줄 번호, 시간, 해시 등 실행마다 달라지는 식별자를 마스킹합니다. 반대로 `no_progress`는 고정 성공 문구가 서로 다른 작업을 하나로 오인하지 않도록 원본 입력으로 묶습니다.

## 공식 훅 설치

두 설정 파일은 저장소에 포함되어 있습니다. 전역 설정 파일을 수정하지 않습니다.

### Claude Code

[`.claude/settings.json`](.claude/settings.json)이 프로젝트 설정으로 `PostToolUseFailure` 훅을 등록합니다. 실패 직후 transcript의 마지막 연속 실패 구간을 검사하며, 3회째에 stderr와 exit 2로 경고합니다.

### Codex

[`.codex/hooks.json`](.codex/hooks.json)이 Bash용 `PostToolUse` 훅을 등록합니다. 새 Codex 세션에서 `/hooks`를 열어 훅을 검토하고 신뢰해야 실행됩니다. 같은 마스킹 오류가 세 번 연속이면 모델에 `additionalContext` 경고를 주며, 훅 자체는 fail-open으로 종료합니다.

Codex 훅 위치와 신뢰 절차는 [공식 Hooks 문서](https://developers.openai.com/codex/hooks)를, Claude Code 프로젝트 훅 위치와 실패 이벤트는 [공식 Hooks 문서](https://code.claude.com/docs/en/hooks)를 따릅니다.

## 테스트

```powershell
python test_loop_detector.py
```

## 한계

- Codex의 실시간 감지는 현재 Bash non-zero 결과만 다룹니다.
- Codex 훅은 합성 payload로 검증됐습니다. `/hooks` 신뢰 뒤 실제 Codex 세션에서의 라이브 검증은 남아 있습니다.
- Cursor 등 다른 에이전트 transcript 파서는 필요해질 때 추가합니다.
