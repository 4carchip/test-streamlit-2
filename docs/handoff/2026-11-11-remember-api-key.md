# API 설정 기억하기 (선택형) (2026-11-11)

## 목표 / Goal
새로고침하면 사이드바의 API 주소와 키가 사라진다. 사용자가 **원할 때만** 이 브라우저에 기억해 두었다가 다음 방문 때 복원한다.

## 결정과 근거 / Decisions & Why
- **기본값은 꺼짐(opt-in) + 공용 PC 경고**: localStorage는 평문이라, 공용 PC에서는 다음 사람이 키를 꺼내 볼 수 있다.
- **키만이 아니라 연결 방식·API 주소·키를 함께 기억한다**: 키만 남고 주소가 비면 쓸 수 없기 때문이다. 모델 ID 직접 지정 값은 범위 밖이라 넣지 않았다.
- **복원값은 PENDING에 보관 → rerun → `render_controls()` 맨 위 `apply_pending()`에서 반영**: Streamlit은 이미 만들어진 위젯의 상태를 같은 실행 안에서 바꿀 수 없다.
- **숨은 컴포넌트를 하나로 합침 (`core/browser_storage.py`)**: 처음 구현은 오답노트용과 API용 컴포넌트를 따로 두었다. 그러자 브라우저에서 아래 버그가 재현됐다.
  1. 두 iframe이 처음 값을 각각 보내 rerun이 겹친다.
  2. 늦게 온 rerun이 프런트엔드의 옛 위젯 값(체크 해제, 빈 칸)으로 세션을 덮어쓴다.
  3. 앱은 '기억하기 꺼짐'으로 판단하고 **저장분을 삭제**한다. 화면에는 키가 보이는데 상태는 "데모 응답"으로 나왔다.

  지금은 컴포넌트 하나가 `{저장 키: 값}`을 한 번에 보내므로 rerun이 겹치지 않는다.
- **PDF는 저장하지 않는다**: 저작권 문제와 용량(약 5MB) 때문이다. 이전 대화에서 사용자에게 설명했다.

## 변경 사항 / Changes
- `core/apikeys.py` (신규): payload / save_arg / apply_loaded(검증·필터) / apply_pending
- `core/browser_storage.py` (신규): 단일 숨은 컴포넌트, items / handle / sync
- `core/storage_component/index.html`: 단일 키 방식에서 `args.items` 여러 키 방식으로 바꿨다. 처음 값을 객체 하나로 보낸다.
- `core/persist.py`: 컴포넌트·sync를 browser_storage로 옮겼다. 저장·복원 로직은 그대로다.
- `core/sidebar.py`: 맨 위에서 `apikeys.apply_pending()`, 체크박스와 체크 여부에 따른 안내 문구
- `app.py`: `persist.sync()` 호출을 `browser_storage.sync()`로 바꿨다.
- `app_pages/api_guide.py`: "새로고침하면 사라진다" 문장에 선택형 기억하기를 덧붙였다.
- `tests/test_apikeys.py` (15개), `tests/test_browser_storage.py` (7개) 신규

## 테스트 상태 / Test State
- RED 1 (Sonnet): `ImportError: cannot import name 'apikeys'` → GREEN `29 passed`
- 브라우저 재현 (버그): 기억하기를 켜고 새로고침하면 localStorage의 `certmate.api.v1`이 `null`이 되었다. 디버그 로그에서 API 컴포넌트가 `""`를 돌려준 것을 확인했다.
- RED 2 (Opus): `ImportError: cannot import name 'browser_storage'` → GREEN `36 passed`
- 최종: `36 passed`
- 로컬 브라우저 수동 검증:
  - 체크 → 저장
  - 새로고침 3회와 페이지 이동 → 주소·키·체크·"연결 준비됨" 유지, 오답노트도 함께 복원, iframe 1개
  - 체크 해제 → API 저장분만 삭제, 오답노트는 유지, 현재 세션 연결은 유지
- **Streamlit Cloud 배포 앱에서는 아직 확인하지 않았다.**

## 검증 / Cross-check
### Copilot 응답 전문
```
1. **중간 — 구버전/손상된 localStorage 값을 조용히 덮어씀**  
   - **시나리오:** `certmate.api.v1`이 JSON 형식 오류이거나 지원하지 않는 구조여도 `apply_loaded()`가 `LOADED=True`로 표시한다. 이후 현재 세션값(대개 빈 값)으로 `payload()`가 생성되어 기존 저장값을 삭제한다. 사용자는 복원이 실패한 사실이나 설정 손실을 알 수 없다.  
   - **수정:** 파싱·버전·구조 검증 실패 시 저장값을 삭제하지 말고, 별도 오류 상태를 표시하거나 해당 키를 유지한다. `version == 1` 검증도 추가한다.

2. **낮음 — localStorage 읽기/쓰기 실패를 사용자에게 알리지 않음**  
   - **시나리오:** 브라우저 정책, 사생활 보호 모드, 저장공간 초과 등으로 `readStored`/`setItem`이 실패해도 JS가 예외를 삼킨다. 체크박스가 켜져 있어 사용자는 저장됐다고 생각하지만 새로고침 후 복원되지 않는다.  
   - **수정:** 컴포넌트가 저장 성공/실패 상태를 반환하고, 실패 시 “브라우저 저장소를 사용할 수 없습니다”라는 안내를 표시한다.

3. **보안 범주: 명시적 요구사항 범위 내에서는 추가 문제 없음**  
   - API 키를 평문 localStorage에 저장하는 위험은 존재하지만, 구현이 이를 경고하고 선택형으로 제한하며 서버 저장도 하지 않으므로 요구사항 위반으로 보이는 별도 문제는 없습니다.

```
### 분류
1. 손상된 API 저장값을 조용히 지움, version 검증 없음 → **보류**. 사유: 손상된 값은 어차피 복원할 수 없으므로 지워도 잃는 정보가 없고, 키를 다시 입력하면 된다. version은 알 수 없는 필드를 버리는 필터로 충분히 막힌다.
2. localStorage 실패를 알리지 않음 → **보류**. 사유: 저장소가 차단되는 경우는 드물고, 그때도 '저장분 없음'처럼 정상 동작한다. 실패 상태를 Python으로 돌려보내는 프로토콜을 추가하는 것은 이번 범위를 넘는다.
3. 보안: 범위 안에서는 추가 문제 없음 → **해당 없음**

### Opus 최종 검토
- diff 전체와 테스트를 다시 실행해 확인했다. Sonnet 1차 구현의 브라우저 버그(저장분 삭제)를 직접 재현·진단·수정했다.
- 판정: 병합 가능
- 남은 우려:
  - (a) 첫 1초 안에(복원 전에) 사용자가 위젯을 조작하면 비슷한 덮어쓰기 경쟁이 이론상 남아 있다. 사람이 하기는 어렵다.
  - (b) 평문 저장 위험은 안내 문구로만 대응한다.
- 절차 메모: 컴포넌트 통합 수정은 원인을 진단한 Opus가 직접 TDD로 진행했다.

## 미해결 & 다음 단계 / Open & Next
- 배포 앱에서 확인: 체크 → F5 → 주소·키 유지 → 체크 해제 → F5 → 비어 있음
- (선택) 학교 멀티AI 주소를 Secrets 기본값으로 넣으면 주소 입력 자체가 필요 없다.

## 함정 / Gotchas
- **숨은 저장 컴포넌트를 두 개 이상 두지 말 것.** 저장할 것이 늘면 `browser_storage.items()`와 `handle()`에 키를 추가한다.
- 복원값을 위젯에 넣을 때는 반드시 위젯이 만들어지기 전(`apply_pending`)에 넣어야 한다. `_kept_input`은 `"_w_"+키`와 `키` 둘 다 써야 한다.
- 컴포넌트 이름이 `wn_storage`에서 `browser_storage`로 바뀌었다. localStorage 키(`certmate.wrongnote.v1`)는 그대로라 기존 오답노트 저장분은 이어진다.

## 정정 / Corrections
- `docs/handoff/2026-11-11-wrongnote-localstorage.md`의 "Streamlit Cloud에서는 아직 확인하지 않았다"에 정정 기록을 추가했다(사용자가 배포 앱에서 동작 확인).
- `core/sidebar.py` 캡션: 기존 "어디에도 저장되지 않습니다"는 체크박스가 생기면서 맞지 않게 되었다. 체크 여부에 따라 다른 문구를 보여주도록 고쳤다.
- `app_pages/api_guide.py`: "새로고침하면 사라집니다"에 선택형 기억하기를 덧붙였다.
