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

플러그인 실행에는 Python 3.10+가 `python` 명령으로 PATH에 등록되어 있어야 합니다. Windows에서는 `python3` 대신 `python`을 사용합니다.

## 사후 스캔

```powershell
python loop_detector.py scan
python loop_detector.py scan --all
python loop_detector.py scan --json
```

기본 `scan`은 현재 작업 디렉터리에 해당하는 가장 최신 Claude Code 세션 하나를 검사합니다. `--all`은 모든 세션을 검사합니다.

## 플러그인 설치

저장소 루트 자체가 Claude Code와 Codex 플러그인 루트입니다. 두 플랫폼이 같은 `loop_detector.py`와 `hooks/hooks.json`을 사용합니다. Claude Code의 성공 `PostToolUse`에서도 공통 명령이 실행되며, 실패 상태가 없는 응답에는 경고하지 않습니다.

### Claude Code

개발 중인 플러그인은 `--plugin-dir`로 바로 로드할 수 있습니다.

```powershell
claude --plugin-dir C:\path\to\loop-detector
```

플러그인에는 Claude Code의 `PostToolUseFailure` 훅이 포함되어 있지만, **2026-09-07 실측 결과 `--plugin-dir`로 로드한 `PostToolUseFailure`는 대화형 Claude Code 세션(v2.1.263)에서 등록 자체가 되지 않습니다** — 무조건 발화하는 최소 테스트 훅으로도 재현되며 `/hooks`에서도 "No hooks configured for this event"로 나타나, 이 저장소의 훅 설정과 무관한 Claude Code CLI 쪽 제약으로 보입니다. 실시간 경고가 필요하면 아래 [공식 훅 설치](#공식-훅-설치)의 `install` 명령(프로젝트 `.claude/settings.json`)을 쓰세요 — 이 경로는 실제 대화형 세션에서 발화가 확인되었습니다.

### Codex

Codex 플러그인은 기본 `hooks/hooks.json`의 Bash `PostToolUse` 훅을 사용합니다. 플러그인을 설치한 뒤 새 세션에서 `/hooks`를 열어 훅을 검토하고 신뢰하세요.

공개 marketplace 등록은 별도 배포 단계입니다. 현재 저장소는 Claude Code의 직접 플러그인 로딩과 기존 프로젝트용 `install` 명령을 함께 지원합니다.

## 탐지 기준

| 결과 | 조건 |
| --- | --- |
| `repeat_failure` | 같은 도구와 같은 마스킹 오류가 연속 3회 이상 |
| `no_progress` | 같은 도구가 같은 원본 입력으로 연속 3회 이상 성공 또는 무변화 |

오류 fingerprint에서는 경로, 줄 번호, 시간, 해시 등 실행마다 달라지는 식별자를 마스킹합니다. 반대로 `no_progress`는 고정 성공 문구가 서로 다른 작업을 하나로 오인하지 않도록 원본 입력으로 묶습니다.
대상 식별자는 정렬된 전체 JSON 입력을 사용하고, 화면 표시만 300자로 줄입니다. JSONL 파서는 잘못된 UTF-8·비객체 레코드·짝이 없는 tool 결과를 건너뜁니다.

## 공식 훅 설치

현재 프로젝트에는 두 설정 파일이 포함되어 있습니다. 다른 프로젝트에도 설치하려면 대상 프로젝트에서 이 저장소의 절대 경로로 실행합니다.

```powershell
python C:\path\to\loop-detector\loop_detector.py install
python C:\path\to\loop-detector\loop_detector.py install --target C:\path\to\another-project
```

설치 명령은 대상의 기존 JSON을 검증한 뒤 Claude Code와 Codex 훅을 병합합니다. 기존 훅은 유지하며, 같은 loop-detector 훅이 이미 있으면 중복 등록하지 않습니다. 설치 뒤에는 `loop_detector.py`를 이동하거나 삭제하지 마세요.
두 설정 파일을 모두 먼저 읽고 검증하므로 하나가 잘못된 JSON이면 어느 파일도 기록하지 않습니다.

### Claude Code

[`.claude/settings.json`](.claude/settings.json)이 프로젝트 설정으로 `PostToolUseFailure` 훅을 등록합니다. 실패 직후 transcript의 마지막 연속 실패 구간을 검사하며, 3회째에 stderr와 exit 2로 경고합니다.

### Codex

[`.codex/hooks.json`](.codex/hooks.json)이 Bash용 `PostToolUse` 훅을 등록합니다. 새 Codex 세션에서 `/hooks`를 열어 훅을 검토하고 신뢰해야 실행됩니다. 같은 마스킹 오류가 세 번 연속이면 모델에 `additionalContext` 경고를 한 번 주고, 이후 같은 루프에서는 반복하지 않으며, 훅 자체는 fail-open으로 종료합니다.

Codex 훅 위치와 신뢰 절차는 [공식 Hooks 문서](https://developers.openai.com/codex/hooks)를, Claude Code 프로젝트 훅 위치와 실패 이벤트는 [공식 Hooks 문서](https://code.claude.com/docs/en/hooks)를 따릅니다.

## 테스트

```powershell
python test_loop_detector.py
```

테스트에는 공백이 있는 플러그인 경로에서 설정된 훅 명령을 별도 프로세스로 실행하는 검증이 포함됩니다. Claude 실패 경고(stderr/exit 2), Codex 3회째 한 번 경고(JSON/exit 0), 잘못된 입력의 무음 종료를 확인합니다. 테스트용 상태는 임시 디렉터리에만 저장합니다.

2026-09-04 Windows 검증: 프로세스 통합 테스트를 통과했고 Codex 공식 `hooks/list`에서 이 프로젝트 훅의 활성화·신뢰 상태를 확인했습니다.

2026-09-07 실측: Claude Code `.claude/settings.json` 프로젝트 훅(`install` 명령이 등록하는 것과 동일)은 실제 대화형 Claude Code 세션에서 Bash 동일 실패 3회 연속 시 정상 발화함을 확인했습니다(사용자가 직접 재현). 반면 `.claude-plugin` + `hooks/hooks.json` 패키지를 `--plugin-dir`로 로드하는 경로는, 무조건 발화하는 최소 테스트 훅으로도 대화형 세션에서 무발화로 재현되어 — 이 저장소의 훅 설정 문제가 아니라 Claude Code CLI가 `--plugin-dir`로 로드한 `PostToolUseFailure`를 등록하지 않는 것으로 보입니다(`/hooks`에서도 "No hooks configured for this event"). 실시간 경고에는 `install` 명령을 쓰세요.

## 한계

- Codex의 실시간 감지는 현재 Bash non-zero 결과만 다룹니다.
- `--plugin-dir`로 로드한 Claude Code `PostToolUseFailure`는 현재 실사용에서 발화하지 않는 것으로 확인되었습니다(위 실측 참고) — 실시간 경고가 필요하면 `install` 명령의 프로젝트 훅을 쓰세요. Codex는 새 세션에서 `/hooks` 상태를 확인하세요.
- Codex 상태 파일은 버전 1과 해시된 fingerprint를 사용하며, 손상되거나 이전 형식이면 새 루프로 초기화합니다.
- 같은 세션에서 훅 프로세스가 동시에 실행되면 카운트 증가가 경합할 수 있습니다. 실제 병렬 실행이 확인될 때 파일 잠금을 추가합니다.
- 두 설정 파일의 사전 검증 뒤 기록 단계에서 디스크 오류가 나면 한 파일만 먼저 기록될 수 있으므로, 실패 시 파일 상태를 확인하세요.
- Cursor 등 다른 에이전트 transcript 파서는 필요해질 때 추가합니다.
