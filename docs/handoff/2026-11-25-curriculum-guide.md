# 진로 로드맵 · 선수 과목 가이드 (2026-11-25)

## 목표 / Goal
계획서의 핵심 기능 ①진로별 자격증 로드맵 ②선수 과목 가이드를 기존 Streamlit 앱에 추가한다.

## 결정과 근거 / Decisions & Why
- **AI가 만들지 않고 CSV에서 읽는다**: 출제기준 매핑(`core/mapping.py`)과 같은 원칙. 진로·과목 안내가 틀리면 학생의 수강 계획이 틀어지므로 근거 없는 생성을 피한다.
- **취득 순서는 계산한다**: 각 자격증의 선수 과목 중 가장 늦게 개설되는 학기(ready_term)가 이른 순, 같으면 '핵심' 먼저. 순서를 사람이 따로 적지 않아도 과목 데이터만 고치면 바뀐다.
- **검토 상태를 화면에 드러낸다**: `verified=FALSE`면 "검토 전"으로 표시한다.
- **이수 체크는 모든 자격증에 공통**: 같은 과목(예: 컴퓨터네트워크)이 여러 자격증에 걸쳐 있어 과목 ID 하나로 관리한다.
- **위젯 값 보존**: Streamlit은 페이지를 옮기면 위젯 키를 지우므로 `_guide_cert`, `_current_term`, `taken_courses`를 따로 두고 매번 위젯 값을 다시 채운다.

## 변경 사항 / Changes
- `data/curriculum/courses.csv` (신규): 학과 과목 16개와 개설 학기 (계획서 2.2 표)
- `data/curriculum/cert_courses.csv` (신규): 자격증 4개 × 선수 과목 (계획서 2.2 초안, verified=FALSE)
- `data/curriculum/career_certs.csv` (신규): 진로 4개 × 핵심·함께 자격증 **후보** (verified=FALSE, 팀 검토 필요)
- `core/curriculum.py` (신규): 학기 비교, 자격증별 과목 계획, 준비도·놓친 과목·다음 과목, 진로별 취득 순서
- `app_pages/roadmap.py`, `app_pages/guide.py` (신규)
- `app.py`: 내비게이션을 '공부 / 준비 계획 / 설정' 구역으로 나눔 (기본 페이지는 그대로 공부하기)
- `tests/test_curriculum.py` (신규, 19개)
- `README.md`: 기능 표, 폴더 구조, 알려진 한계

## 테스트 상태 / Test State
- `python -m pytest -q` → 33 passed (기존 14 + 신규 19)
- 로컬 브라우저 확인(streamlit 1.67, 데모 모드): 진로 전환, 과목 체크 → 준비도 반영, 페이지 이동 후 체크 유지, 로드맵 '선수 과목 보기' → 해당 자격증 선택된 가이드로 이동. 콘솔 오류 없음.

## 미해결 & 다음 단계 / Open & Next
- `career_certs.csv`의 진로 ↔ 자격증 연결은 **초안 후보**다. 팀·교수님·선배 검토 후 `verified`, `checked_by`, `checked_at`을 채운다.
- `cert_courses.csv`도 교수님 검토 후 확정한다.
- (선택) 이수 체크를 localStorage에 저장 (`core/persist.py` 확장)
- (선택) 오답노트 약점 개념 → 관련 학과 과목 연결: 출제기준 항목 ↔ 학과 과목 매핑 데이터가 먼저 필요하다.

## 함정 / Gotchas
- term 형식은 `학년-학기`("3-2"), 학기 미정은 학년만("4"). "4"는 같은 4학년 학기보다 앞으로 보지 않는다.
- 새 자격증을 추가할 때는 `cert_courses.csv`에 과목을 넣어야 로드맵 순서 계산에 들어간다. 과목이 없으면 맨 뒤로 간다.
