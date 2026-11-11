# 오답노트 브라우저 자동 저장 (2026-11-11)

## 목표 / Goal
외부 사용자가 새로고침하거나 다시 들어와도 오답노트(오답 + 문제 은행)가 유지되게 한다. 서버에는 저장하지 않고 사용자 브라우저의 localStorage에만 둔다.

## 결정과 근거 / Decisions & Why
- **localStorage를 쓴 이유**: 서버 파일은 Streamlit Community Cloud에서 재시작·재배포 때 지워지고 모든 사용자가 같은 파일을 공유한다. 외부 DB와 로그인은 범위가 크고 개인 기록이 서버에 남는다. 기존 방침("개인 기록은 서버에 모으지 않는다")과 맞는 것은 브라우저 저장뿐이다.
- **새 pip 의존성 없이 components v1을 손으로 구현한 이유**: `streamlit-local-storage` 같은 서드파티 패키지에 의존하지 않으려고. `core/storage_component/index.html`이 postMessage 프로토콜(componentReady / render / setComponentValue / setFrameHeight)을 직접 처리하며 빌드 단계가 없다.
- **저장 대상은 `wn.export_json()` 내용뿐**: PDF, 대화, API 키는 저장하지 않는다(저작권·키 유출 문제).
- **덮어쓰기 방지**: 브라우저가 기존 값을 돌려주기 전까지는 `save_arg()`가 `None`이라 아무것도 쓰지 않는다. 빈 새 세션이 저장분을 지우지 못한다.
- **`sync()`는 `pg.run()` 뒤 사이드바에서 호출**: 페이지에서 바꾼 내용까지 같은 실행에서 저장하기 위해서다. 그래서 복원했을 때는 `st.rerun()`으로 화면을 다시 그린다.
- **불러올 때 형식 검증**: 화면 코드가 바로 쓰는 필드(choices 4개, answer 1~4, box 범위, cause가 CAUSES 안 등)를 확인하고, 맞지 않는 항목은 건너뛰고 안내한다. 검증하지 않으면 스키마가 바뀐 뒤 옛 저장분 때문에 **새로고침할 때마다 앱이 멈추는 상태**가 될 수 있다(Copilot 지적 3).
- 버린 접근: 서버 `.cache/` JSON 파일(사용자끼리 공유되고 재시작 때 사라짐), `st.html` + JS(값을 Python으로 돌려받을 수 없음).

## 변경 사항 / Changes
- `core/persist.py` (신규): payload / save_arg / apply_loaded(형식 검증 포함) / clear / sync
- `core/storage_component/index.html` (신규): localStorage 읽기·쓰기 전용 숨은 컴포넌트
- `app.py`: `pg.run()` 뒤 `with st.sidebar: persist.sync()`
- `app_pages/wrongnote.py`: 복원 안내 메시지, 백업 탭 안내 문구 변경(자동 저장, 다른 기기 안 됨, 여러 탭 주의), "이 브라우저에서 오답노트 지우기" 버튼
- `core/wrongnote.py`: docstring의 "저장:" 설명 갱신
- `tests/conftest.py`, `tests/test_persist.py` (신규, 14개): 저장소에 있는 첫 테스트
- `requirements-dev.txt` (신규): `-r requirements.txt` + `pytest`. 배포용 `requirements.txt`는 그대로다.

## 테스트 상태 / Test State
- RED 1 (Sonnet 구현 전): `ImportError: cannot import name 'persist' from 'core'` → `1 error`
- GREEN 1: `10 passed`
- RED 2 (Copilot 지적 반영 테스트 추가 후): `3 failed, 11 passed`. 실패한 테스트: bank_only_restore / note_missing_fields / unknown_cause
- GREEN 2 / 최종: `14 passed`
- 실행: `pip install -r requirements-dev.txt` 후 `python -m pytest -q`
- **로컬 브라우저 수동 검증**(streamlit 1.51, localhost):
  - 저장분 주입 → 오답노트 페이지에서 복원과 안내 표시
  - 복습 정답 → localStorage의 box 0→1, due 반영
  - 새로고침 → 바뀐 일정으로 복원
  - 지우기 버튼 → localStorage 키 삭제
  - 깨진 항목 1개 + 정상 1개 → 정상 1개만 복원되고 건너뜀 안내, 앱 정상
- **Streamlit Community Cloud에서는 아직 확인하지 않았다.** 병합한 뒤 배포 앱에서 같은 순서로 한 번 확인해야 한다.

## 검증 / Cross-check
### Copilot 응답 전문
```
1. **중간 — 복원된 문제 은행만 UI에 반영되지 않음**  
   - **시나리오:** localStorage에 `bank`만 있고 `notes`는 비어 있으면 `wn.import_json()`의 반환값이 `0`이어서 `st.rerun()`을 호출하지 않는다. `sync()`는 페이지 렌더링 뒤 실행되므로, 현재 화면에는 복원된 문제 은행이 표시되지 않고 다음 사용자 상호작용 전까지 오래된 상태가 보인다.  
   - **수정:** `import_json()`의 추가 결과가 노트 수뿐이므로, 복원 전후의 `bank` 길이 또는 전체 상태 변화를 비교해 변경 시 항상 `st.rerun()`한다.

2. **중간 — 여러 탭 사용 시 최신 기록이 조용히 덮어써질 수 있음**  
   - **시나리오:** 탭 A와 탭 B가 같은 localStorage를 각각 복원한 뒤, 탭 A에서 복습 결과를 저장한다. 이후 탭 B가 자신의 오래된 세션 상태를 자동 저장하면 탭 A의 변경이 사라진다. `localStorage`의 동일 키에 대한 버전/변경 시각 검사가 없다.  
   - **수정:** 저장 payload에 revision/timestamp를 포함하고 저장 직전 브라우저의 최신 값과 병합하거나, `storage` 이벤트 및 충돌 감지를 구현한다. 다중 탭을 지원하지 않을 방침이면 UI에 명시해야 한다.

3. **낮음 — 손상 데이터 테스트가 JSON 문법 오류에만 한정됨**  
   - **시나리오:** `{"version":1,"notes":[...잘못된 필드/타입...]}`처럼 JSON 문법은 맞지만 구조가 비정상인 값이 `import_json()`을 통과하거나 부분 반영되면 이후 페이지 렌더링에서 앱이 실패할 수 있다. 현재 테스트는 `"{not json"`만 검증한다.  
   - **수정:** 필드 타입·필수 키·배열 구조가 잘못된 payload 테스트를 추가하고, import 전에 스키마 검증 또는 원자적 검증 후 반영을 보장한다.

**그 외 분류:** 서버 저장 방지, 빈 세션의 기존 저장분 덮어쓰기 방지, 저장소 접근 예외 처리, 삭제 버튼 및 기존 JSON 백업 유지에 대해서는 diff상 즉시 확인되는 추가 문제는 없습니다.

```
### 분류
1. 문제 은행만 복원될 때 rerun이 일어나지 않음 → **반영함**. 은행 증가분도 세어 안내하고, `sync()`가 안내가 있으면 항상 rerun한다. 테스트 `test_bank_only_restore_returns_message`.
2. 여러 탭에서 마지막으로 저장한 탭이 덮어씀 → **보류**(사유: revision 병합이나 storage 이벤트는 JS↔Python 양방향 동기화가 필요해 이번 범위를 넘는다). 대신 백업 탭에 "탭 하나만 쓰세요" 안내를 넣었다.
3. 문법은 맞지만 구조가 깨진 저장분 → **반영함**. `_valid_note` / `_valid_question`으로 검증하고 맞지 않는 항목은 건너뛴다. 테스트 3개 추가.

### Opus 최종 검토
- diff 전체와 테스트를 직접 다시 실행해 확인하고, 브라우저에서 수동 검증했다. 판정: 병합 가능.
- 남은 우려:
  - (a) 다중 탭 덮어쓰기(보류 항목)
  - (b) 공용 PC에서 지우기를 누르지 않으면 다음 사람이 오답노트를 볼 수 있음(안내 문구로만 대응)
  - (c) 저장분 전체를 매 rerun마다 프런트로 보냄. 문제 은행이 수천 개로 커지면 느려질 수 있다.
  - (d) localStorage 용량(~5MB)을 넘으면 저장이 조용히 실패하고 JSON 백업만 남는다.
- 절차 메모: Copilot 반영 수정(형식 검증·문구)은 범위가 작아 Sonnet에게 다시 맡기지 않고 Opus가 직접 TDD로 진행했다.
- 기존 테스트 `test_apply_loaded_imports_notes_and_bank`의 문제 은행 더미 데이터 `{"id","question"}`를 실제 형식(choices·answer 포함)으로 바꿨다. 검증 기준을 느슨하게 한 것이 아니라 데이터를 실제 형식에 맞춘 것이다. **사용자 확인이 필요한 변경으로 승인 요청에 표기했다.**

## 미해결 & 다음 단계 / Open & Next
- 배포된 Streamlit Cloud 앱에서 복원·저장·지우기를 확인한다.
- (선택) 다중 탭 충돌 감지
- (선택) 공부하기 대화 기록도 저장할지 결정한다. 교재 내용이 브라우저에 남는 문제를 같이 검토해야 한다.

## 함정 / Gotchas
- 컴포넌트 iframe은 앱과 같은 출처라 localStorage가 **앱 주소(도메인)별**로 나뉜다. 앱 URL이 바뀌면 이전 저장분은 보이지 않는다.
- `STORAGE_KEY = "certmate.wrongnote.v1"`이다. 노트 스키마를 바꿀 때는 `_valid_note`도 함께 고쳐야 한다. 그렇지 않으면 옛 저장분이 전부 "건너뜀" 처리된다.
- 건너뛴 항목은 다음 저장 때 사라진다(복구 불가).
- 테스트는 `streamlit.session_state`를 dict로 바꿔 끼운다(conftest autouse). 컴포넌트와 `sync()`는 단위 테스트가 없어서 브라우저에서 직접 확인해야 한다.

## 정정 / Corrections
- `core/wrongnote.py` docstring: 기존 "저장: 세션 메모리. 새로고침해도 이어 가려면 JSON으로 내려받았다가 다시 올린다"는 이번 변경으로 맞지 않게 되었다. "세션 메모리 + localStorage 자동 저장(core/persist.py), JSON은 기기 이동용"으로 고쳤다.
- `app_pages/wrongnote.py` 백업 탭: 기존 "오답노트는 이 브라우저 세션에만 있습니다"를 자동 저장 설명으로 고쳤다.
