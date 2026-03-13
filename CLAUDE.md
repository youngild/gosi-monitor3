# 고시 통합 분석 시스템 (Gosi Monitor)

> 의사랑 EMR PM을 위한 보건의료 고시 자동 수집·분석·검증 플랫폼

---

## 1. 프로젝트 개요 및 배경

### 문제 정의 (Pain Point)

EMR 제품 PM은 보건복지부·건강보험심사평가원에서 수시로 발표되는 수가 기준, 청구 방식, 서식 변경, 신고 의무 등의 고시를 놓치면 병원에서 청구 오류·법적 의무 위반이 발생한다.

| 현재 Pain Point | 구체적 문제 |
|---|---|
| **분산된 정보** | 복지부·심평원 등 사이트를 매일 수동으로 확인해야 함 |
| **첨부파일 분석 부담** | PDF/HWPX 고시문 수십 페이지를 직접 읽고 EMR 적용 여부 판단 |
| **누락 위험** | 하루 수십 건 고시 중 EMR 관련 건을 놓치면 청구 오류 발생 |
| **대응 기한 관리 부재** | 시행일 기준으로 EMR 개발 일정을 역산해야 하나 추적 도구 없음 |
| **팀 간 공유 불가** | 분석 결과를 메모장·이메일로 공유 → 히스토리 소실 |

### 솔루션

- 수집 대상 사이트를 4시간마다 자동 크롤링 → 신규 고시 알람
- PDF/HWPX 첨부파일 텍스트 추출 후 **Claude AI**가 EMR 모듈(청구/처방/의무기록 등) 관점으로 요약
- 검증 시스템: 상태(대기→검토중→적용완료), 우선순위, EMR 모듈 체크리스트, 메모 → DB 저장

---

## 2. 성공 기준 및 KPI

### 핵심 가설

> **"EMR PM이 고시 1건을 분석하는 데 걸리는 시간을 평균 30분 → 5분 이내로 단축할 수 있다"**

### 정량 KPI

| 지표 | 현재(Before) | 목표(After) | 측정 방법 |
|---|---|---|---|
| 고시 1건 분석 소요 시간 | ~30분 (수동 열람) | ≤5분 | 사용자 타이머 측정 |
| 고시 누락률 | 추정 10~20% (수동 확인) | 0% | 크롤링 커버리지 |
| 요약 EMR 적용 정확도 | - | ≥80% (사용자 평가) | 검증 패널 "적용완료" 비율 |
| 고시 대응 기한 준수율 | 추적 불가 | 100% 추적 가능 | 검증 시스템 시행일 필드 |
| 신규 고시 탐지 지연 | 최대 1일 (수동 확인 주기) | ≤4시간 | 스케줄러 주기 |

### 정성 KPI

- EMR 담당 PM이 고시 관련 업무를 "능동적 대응"에서 "시스템 알람 기반 대응"으로 전환
- 분석 결과 팀 공유 가능 (URL + 검증 패널 메모)

---

## 3. 기술 스택

| 레이어 | 기술 | 선택 이유 |
|---|---|---|
| 크롤링 (표준 HTML) | Python + requests + BeautifulSoup4 + lxml | 보건복지부 정적 HTML 파싱에 충분 |
| 크롤링 (JS 앱) | Playwright (Chromium) | HIRA InfoBank가 Nexacro14 JS 프레임워크 → 브라우저 렌더링 필수 |
| 텍스트 추출 | pdfplumber (PDF), zipfile+xml (HWPX) | 한국어 의료 문서에 최적화 |
| AI 요약 | Anthropic Claude API (claude-sonnet-4-6) | EMR PM 전용 프롬프트로 모듈별 적용 사항 분석 |
| AI 폴백 | 로컬 규칙 기반 (regex 섹션 추출 + EMR 키워드 탐지) | API 키 없이도 동작 보장 |
| 백엔드 | Flask 3.1 + APScheduler | 경량 REST API + 4시간 자동 크롤링 스케줄러 |
| DB | SQLite | 단일 파일 DB로 별도 서버 불필요, 향후 PostgreSQL 마이그레이션 용이 |
| 프론트엔드 | Vanilla JS + Pretendard | 빌드 도구 불필요, 즉시 실행 가능 |

---

## 4. AI 활용 전략

### 시나리오 A: Claude API 사용 (ANTHROPIC_API_KEY 환경변수 설정 시)

```
고시 텍스트 → Claude claude-sonnet-4-6
  프롬프트: "의사랑 EMR PM 관점에서 아래 항목만 작성:
    📌 개정이유 / 📋 주요 변경 내용 / 🏥 EMR 적용 필요 사항 [모듈명]
    ⚠️ 미적용 시 위험 / 📅 시행일 / 👥 적용 대상 기관"
  max_tokens: 1200
```

- EMR 모듈(청구/처방/의무기록/수납/신고/OCS/검사)별 액션 아이템 생성
- "미적용 시 위험" 섹션으로 개발 우선순위 자동 판단

### 시나리오 B: 로컬 규칙 기반 (API 키 없을 때)

```
고시 텍스트 → regex 섹션 추출
  _extract_section(): 개정이유, 주요내용, 목적/추진배경
  _analyze_emr_impact(): EMR_IMPACT_RULES 10개 패턴 매칭
    예) r'수가|행위\s*코드|급여\s*기준' → "[청구/수납] 수가 테이블 업데이트 필요"
  _extract_date(): 시행일 패턴 5종
```

### AI 품질 개선 계획

1. 검증 패널의 "적용완료/해당없음" 피드백을 수집 → fine-tuning 데이터셋 구축
2. 요약 정확도 80% 미만 시: few-shot 예시를 프롬프트에 추가
3. 누적 데이터 100건 이상 시: 모듈별 키워드 자동 추출로 `EMR_IMPACT_RULES` 보강

---

## 5. 파일 구조

```
cursorproject/
├── main.py               # 실행 진입점 (crawl/summarize/all/serve)
├── requirements.txt      # Python 패키지 목록
├── CLAUDE.md             # 프로젝트 문서 (이 파일)
├── scraper/
│   ├── mohw.py           # 보건복지부 크롤러 (requests+BeautifulSoup)
│   └── hira.py           # 심평원 크롤러 (Playwright + Nexacro14)
├── analyzer/
│   └── summarizer.py     # Claude API / 로컬 규칙 기반 EMR 요약
├── storage/
│   └── database.py       # SQLite DB (notices, attachments, alerts, verifications, module_checks)
├── web/
│   ├── app.py            # Flask REST API + APScheduler 4시간 크롤링
│   └── templates/
│       └── index.html    # 웹 UI (목록/검색/상세 팝업/검증 패널/대시보드)
└── data/                 # 런타임 생성 (.gitignore에 포함)
    ├── notices.db
    └── files/mohw/, files/hira/
```

### DB 스키마

```sql
notices        -- source, notice_id, category, title, posted_date, summary
attachments    -- filename, file_type, download_url, local_path
alerts         -- notice_id(FK), is_read
verifications  -- notice_id(UNIQUE), status, priority, memo, updated_at
module_checks  -- verification_id(FK), module_name, is_checked
```

---

## 6. 실행 방법

### 환경 설정

```bash
# Python 3.10+ 필요
pip install -r requirements.txt

# Playwright Chromium 설치 (최초 1회)
playwright install chromium
# SSL 환경 이슈 시:
# NODE_TLS_REJECT_UNAUTHORIZED=0 playwright install chromium
```

### 실행

```bash
# [선택] Claude AI 요약 활성화
set ANTHROPIC_API_KEY=sk-ant-...    # Windows
export ANTHROPIC_API_KEY=sk-ant-...  # Mac/Linux

# 크롤링
python main.py crawl

# 요약 (PDF/HWPX 첨부파일 → AI 요약)
python main.py summarize

# 크롤링 + 요약 한번에
python main.py all

# 웹 서버 (http://localhost:5000)
python main.py serve
```

### API 엔드포인트

| Method | URL | 설명 |
|---|---|---|
| GET | `/api/notices` | 목록 (source, from_date, has_summary, v_status, v_priority, page 필터) |
| GET | `/api/notices/<id>` | 상세 + 첨부파일 + 검증 정보 |
| GET/PUT | `/api/notices/<id>/verification` | 검증 상태/우선순위/메모 |
| PATCH | `/api/notices/<id>/modules` | EMR 모듈 체크 |
| GET | `/api/dashboard` | 검증 상태별 통계 |
| GET | `/api/alerts` | 미읽음 알람 목록 |
| POST | `/api/alerts/read` | 알람 읽음 처리 |
| POST | `/api/run/<task>` | crawl / summarize / all 실행 |

---

## 7. 사이트별 기술 특이사항

### 보건복지부 (MOHW)

- 목록: `table.tstyle_list > tbody > tr`
- 첨부파일: `div.file ul.list li > a[href*="boardDownload"]`
- 다운로드: `/boardDownload.es?bid=0026&list_no={id}&seq={seq}`
- 2026-03-01 이전 게시물 감지 시 자동 중단

### HIRA InfoBank

- Nexacro14 JavaScript 프레임워크 → 브라우저 렌더링 필수
- Playwright로 페이지 로드 후 Nexacro 데이터셋 JavaScript 접근
- 첫 실행 시 스크린샷(`data/files/hira/hira_screenshot.png`) 확인 권장

---

## 8. 차별화 포인트

| 기존 서비스 | 한계 | 이 시스템의 차별점 |
|---|---|---|
| 복지부/심평원 공식 사이트 | 수동 방문, 첨부파일 직접 열람 | 자동 수집·알람·AI 요약 |
| 법제처 국가법령정보센터 | 법령 중심, EMR 적용 분석 없음 | **EMR 모듈별 액션 아이템** 자동 생성 |
| 일반 뉴스 요약 서비스 | 의료 도메인 특화 없음, 첨부파일 미지원 | PDF/HWPX 첨부파일 추출 + 의료 특화 프롬프트 |
| 사내 공유 문서 | 실시간성 없음, 체크리스트 없음 | 실시간 알람 + **검증 체크리스트 + 팀 공유** |

**핵심 차별화**: 단순 요약이 아니라 "청구 오류 예방"이라는 비즈니스 임팩트 관점에서 분석하고, 검증 완료까지 추적하는 **워크플로우 시스템**

---

## 9. 검증 계획

### 가설 (Hypothesis)

| # | 가설 | 검증 방법 | 성공 기준 |
|---|---|---|---|
| H1 | AI 요약으로 고시 분석 시간이 30분 → 5분으로 단축된다 | 사용자 5명 × 10건 시간 측정 A/B 테스트 | 평균 소요 시간 ≤5분 |
| H2 | EMR 모듈 분류 정확도가 80% 이상이다 | PM이 직접 검토 후 "적용완료/해당없음" 평가 | 검증 패널 승인율 ≥80% |
| H3 | 4시간 주기 크롤링으로 고시 누락이 0건이다 | 1개월간 공식 사이트와 DB 비교 | 누락 건수 = 0 |
| H4 | 검증 시스템으로 팀 대응 속도가 향상된다 | 시행일 대비 EMR 반영 완료일 추적 | 시행일 전 반영률 ≥90% |

### 1차 검증 일정 (MVP 기준)

```
Week 1: 실사용 환경 배포 → 크롤링 정상 동작 확인
Week 2: AI 요약 10건 평가 → H2 가설 검증
Week 3-4: 분석 시간 측정 → H1 가설 검증
Month 2: 누락 건수 집계 → H3 가설 검증
```

### 성공 지표 (Success Metrics)

- **사용자 만족도**: SUS(System Usability Scale) 70점 이상
- **요약 정확도**: 검증 패널 "적용완료 + 해당없음" / 전체 처리건 ≥ 80%
- **시스템 가용성**: 크롤링 실패율 < 5% (월 기준)
- **대응 기한 준수**: 시행일 7일 전 EMR 반영 완료율 ≥ 90%

---

## 10. 향후 로드맵

| Phase | 기능 | 우선순위 |
|---|---|---|
| P1 (현재) | 크롤링·요약·검증 시스템 | ✅ 완료 |
| P2 | 배포 (Docker + Nginx) | 높음 |
| P2 | 수집 사이트 확장 (식약처, 질병관리청) | 높음 |
| P3 | 요약 피드백 → 프롬프트 자동 개선 | 중간 |
| P3 | 이메일/Slack 알람 연동 | 중간 |
| P4 | 유사 고시 클러스터링 (임베딩 기반) | 낮음 |
| P4 | 고시 이력 관리 (개정 전/후 비교) | 낮음 |
