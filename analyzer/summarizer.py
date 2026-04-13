"""
고시 내용 요약 — 의사랑 EMR 적용 이슈 기준
- ANTHROPIC_API_KEY 환경변수가 있으면 Claude API (고품질)
- 없으면 로컬 규칙 기반 추출 (API 키 불필요)
"""
import os
import re
import zipfile
import xml.etree.ElementTree as ET
import pdfplumber

CLAUDE_MODEL = "claude-sonnet-4-6"


# ══════════════════════════════════════════════════════
#  텍스트 추출
# ══════════════════════════════════════════════════════

def extract_text_from_pdf(file_path: str) -> str:
    text = []
    try:
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text.append(t)
    except Exception as e:
        print(f"[요약] PDF 추출 오류: {e}")
    return '\n'.join(text)


def extract_text_from_hwpx(file_path: str) -> str:
    texts = []
    try:
        with zipfile.ZipFile(file_path, 'r') as z:
            names = sorted([n for n in z.namelist() if n.startswith('Contents/section')])
            if not names:
                names = [n for n in z.namelist() if n.endswith('.xml')]
            for name in names[:10]:
                with z.open(name) as f:
                    try:
                        tree = ET.parse(f)
                        for elem in tree.getroot().iter():
                            if elem.text and elem.text.strip():
                                texts.append(elem.text.strip())
                            if elem.tail and elem.tail.strip():
                                texts.append(elem.tail.strip())
                    except ET.ParseError:
                        pass
    except Exception as e:
        print(f"[요약] HWPX 추출 오류: {e}")
    return '\n'.join(texts)


def extract_text_from_file(file_path: str) -> str:
    ext = file_path.rsplit('.', 1)[-1].lower()
    if ext == 'pdf':
        return extract_text_from_pdf(file_path)
    elif ext == 'hwpx':
        return extract_text_from_hwpx(file_path)
    elif ext == 'txt':
        with open(file_path, encoding='utf-8', errors='ignore') as f:
            return f.read()
    return ''


# ══════════════════════════════════════════════════════
#  텍스트 정제
# ══════════════════════════════════════════════════════

def _clean(text: str) -> str:
    text = re.sub(r'\(cid:\d+\)', '', text)
    text = re.sub(r'발\s*간\s*등\s*록\s*번\s*호[^\n]*', '', text)
    text = re.sub(r'[ \t]{2,}', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


# ══════════════════════════════════════════════════════
#  섹션 추출 (번호 접두어 포함 지원)
# ══════════════════════════════════════════════════════

def _extract_section(text: str, *keywords: str, max_chars: int = 600) -> str:
    """
    '1. 개정이유', '가. 주요내용' 등 번호 접두어가 붙은 헤더도 인식.
    헤더 이후 내용을 최대 max_chars 글자까지 수집.

    핵심: ■/▶/○/※ 는 본문 bullet이므로 중단하지 않음.
    중단 조건: 상위 레벨 번호 헤더(가. 나. / 1. 2.) 또는 붙임/부칙/별표.
    """
    pattern = (
        r'(?:^|\n)'
        r'[ \t]*(?:\d+\.|[가-힣]\.|[①-⑳])?'
        r'[ \t]*(?:' + '|'.join(keywords) + r')'
        r'[ \t]*[：:\.]?\s*\n?'
        r'([\s\S]{10,})'
    )
    m = re.search(pattern, text, re.MULTILINE | re.IGNORECASE)
    if not m:
        return ''

    raw = m.group(1)
    lines = []
    total = 0
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        # 항상 중단: 붙임/부칙/별표 등 문서 말미 섹션
        if re.match(r'^(?:붙임|부칙|별표|별첨|참고|합\s*의|예산)\s*', s):
            if lines:
                break
        # 짧은 추출(max_chars<=1000)일 때만: 번호/글자 헤더 패턴에서 중단
        # (■/▶/○/※/제N조는 본문 bullet이므로 중단 안 함)
        if max_chars <= 1000 and re.match(
            r'^(?:\d+\s*\.\s*[가-힣]|[가-힣]\s*\.\s*[가-힣]|[①-⑳]\s*[가-힣])',
            s
        ):
            if lines:
                break
        lines.append(s)
        total += len(s)
        if total >= max_chars:
            break

    result = '\n'.join(lines).strip()
    if len(re.findall(r'[가-힣]', result)) < 10:
        return ''
    return result[:max_chars]


def _extract_section_block(text: str, *keywords: str, max_chars: int = 5000) -> str:
    """
    섹션 헤더부터 다음 동급 섹션까지 전체 블록을 추출.
    ■/▶/○ 내부 bullet은 그대로 포함, 다음 numbered 섹션에서만 중단.
    """
    kw_pat = '|'.join(keywords)
    header_re = re.compile(
        r'(?:^|\n)[ \t]*(?:(?:\d+|[가-힣])\s*[\.）]\s*)?(?:' + kw_pat + r')\s*[：:\.」]?\s*\n?',
        re.MULTILINE | re.IGNORECASE
    )
    m = header_re.search(text)
    if not m:
        return ''

    start = m.end()
    # 다음 동급 헤더: "1. XXX" 또는 "가. XXX" 형태 줄
    next_re = re.compile(
        r'\n[ \t]*(?:\d+\s*\.\s*[가-힣가-힣]|[가-힣]\s*\.\s*[가-힣])',
        re.MULTILINE
    )
    end_m = next_re.search(text, start)
    # 붙임/부칙/별표 도 중단
    end_m2 = re.search(r'\n(?:붙임|부칙|별표|별첨)', text[start:])

    candidates = []
    if end_m:
        candidates.append(end_m.start())
    if end_m2:
        candidates.append(start + end_m2.start())
    end = min(candidates) if candidates else min(start + max_chars, len(text))

    block = text[start:end].strip()
    if len(re.findall(r'[가-힣]', block)) < 5:
        return ''
    return block[:max_chars]


def _extract_date(text: str) -> str:
    patterns = [
        r'이\s*(?:규정|고시|지침|훈령|예규)은?\s*([\d]{4}년\s*\d+월\s*\d+일)\s*부터?\s*시행',
        r'시행일\s*[：:]\s*([^\n]{5,60})',
        r'(\d{4}년\s*\d+월\s*\d+일)\s*부터\s*시행',
        r'공포한?\s*날부터\s*시행',
        r'공포일부터\s*시행',
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            g = m.group(0) if '공포' in p else m.group(1)
            return g.strip()[:80]
    return ''


def _extract_target(text: str) -> str:
    return _extract_section(
        text,
        r'적용\s*대상', r'지원\s*대상', r'대상\s*기관', r'이용\s*대상',
        max_chars=150
    )


# ══════════════════════════════════════════════════════
#  EMR 영향 분석 (키워드 기반)
# ══════════════════════════════════════════════════════

# (키워드, EMR 영향 설명, 모듈)
EMR_IMPACT_RULES = [
    (r'수가|행위\s*코드|급여\s*기준|비급여',  '수가코드·급여기준 변경 → 수가 테이블 업데이트 필요', '청구/수납'),
    (r'처방전|처방\s*전달|처방\s*코드',       '처방 관련 변경 → 처방 모듈 검토 필요', '처방'),
    (r'의약품|약품\s*코드|급여\s*중지|급여\s*중지\s*해제', '의약품 코드·급여 변동 → 약품 마스터 업데이트', '처방/약품'),
    (r'서식|기재\s*항목|필수\s*입력|진료\s*기록|의무\s*기록', '서식·필수 기재항목 변경 → 화면·서식 수정 필요', '의무기록'),
    (r'전자\s*바우처|바우처\s*결제|전자\s*청구',  '전자바우처 처리 절차 변경 → 수납/청구 연동 확인', '수납/청구'),
    (r'청구|심사\s*청구|요양\s*급여\s*비용',     '청구 방식·코드 변경 → 청구 모듈 검토 필요', '청구'),
    (r'신고|통보\s*의무|보고\s*의무',            '신고·통보 의무 추가·변경 → 신고 기능 확인', '신고/보고'),
    (r'인터페이스|연동|API|표준\s*코드',         '시스템 연동·인터페이스 변경 가능성', '연동'),
    (r'동의서|설명문|동의\s*서식',               '동의서 서식 변경 → 동의서 모듈 확인', '동의서'),
    (r'원무|수납|납부',                          '원무·수납 프로세스 변경 가능성', '원무/수납'),
]


def _analyze_emr_impact(text: str) -> list[str]:
    """텍스트에서 EMR 영향 키워드를 찾아 이슈 목록 반환."""
    hits = []
    seen_modules = set()
    for pattern, message, module in EMR_IMPACT_RULES:
        if re.search(pattern, text) and module not in seen_modules:
            hits.append(f"• [{module}] {message}")
            seen_modules.add(module)
    return hits


# ══════════════════════════════════════════════════════
#  로컬 요약 (EMR 이슈 기준)
# ══════════════════════════════════════════════════════

def _local_summarize(title: str, content: str, prev_text: str = '') -> str:
    text = _clean(content)
    if len(text) < 50:
        return "[텍스트 내용이 너무 짧아 요약할 수 없습니다]"

    sections = []

    if prev_text:
        # 비교 모드: 개정이유/개요 제외, 변경사항 중심
        sections.append("📋 변경된 내용 (이전 대비)\n• 이전 고시와 비교 분석 (로컬 규칙 기반 — API 키 설정 시 상세 비교 가능)")
    else:
        # ① 주요내용 (개정이유 제외)
        main = _extract_section(text, r'주요\s*내용', r'개정\s*주요내용', max_chars=500)
        if main:
            sections.append(f"📋 주요 변경 내용\n{main}")

        # ② 목적 (주요내용 없는 지침류)
        if not main:
            purpose = _extract_section(text, r'목\s*적', r'사업\s*목적', r'추진\s*배경', max_chars=300)
            if purpose:
                sections.append(f"🎯 목적 / 추진배경\n{purpose}")

    # EMR 영향 분석
    emr_hits = _analyze_emr_impact(text)
    if emr_hits:
        sections.append("🏥 EMR 적용 검토 항목\n" + '\n'.join(emr_hits))
    else:
        sections.append("🏥 EMR 적용 검토 항목\n• 직접적인 EMR 시스템 변경 키워드 미감지\n• 원문 확인 후 업무 프로세스 변경 여부 확인 권장")

    # 시행일
    date = _extract_date(text)
    if date:
        sections.append(f"📅 시행일\n{date}")

    # 적용 대상
    target = _extract_target(text)
    if target:
        sections.append(f"👥 적용 대상\n{target}")

    # 아무것도 못 찾은 경우
    if len(sections) <= 1:
        sections.insert(0,
            f"📄 문서 유형\n{_doc_type_desc(title)}\n\n"
            "⚠️ 이 문서는 비교표·목록 형식으로 자동 추출이 어렵습니다.\n"
            "첨부파일 원문을 직접 확인하세요."
        )

    sections.append(
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "ℹ️ ANTHROPIC_API_KEY 설정 시 Claude AI가 EMR 적용 사항을 상세 분석합니다."
    )

    return '\n\n'.join(sections)


def _doc_type_desc(title: str) -> str:
    t = re.sub(r'새글|\[.*?\]|「|」', '', title).strip()
    if '일부개정' in title:  return f'고시 일부개정 — {t[:60]}'
    if '전부개정' in title:  return f'고시 전부개정 — {t[:60]}'
    if '제정' in title:       return f'신규 제정 고시 — {t[:60]}'
    if '폐지' in title:       return f'고시 폐지 — {t[:60]}'
    if '급여중지' in title:   return f'보험급여 급여중지 안내 — {t[:60]}'
    if '해제' in title:       return f'급여중지 해제 안내 — {t[:60]}'
    return t[:80]


# ══════════════════════════════════════════════════════
#  Claude API 요약 (EMR PM 전용 프롬프트)
# ══════════════════════════════════════════════════════

def _claude_summarize(title: str, content: str, api_key: str, prev_text: str = '') -> str:
    import anthropic

    text = _clean(content)
    if len(text) > 20000:
        text = text[:14000] + '\n\n...(중략)...\n\n' + text[-5000:]

    if prev_text:
        prev_clean = _clean(prev_text)
        if len(prev_clean) > 8000:
            prev_clean = prev_clean[:8000] + '\n...(중략)...'
        prompt = f"""당신은 "의사랑" EMR(전자의무기록) 시스템의 제품 PM입니다.
아래 이전 고시와 현재 고시를 항목별로 철저히 비교 분석하세요.

분석 지침:
- 개정이유·개요·배경 섹션은 작성하지 마세요
- 조문별·항목별로 구체적인 수치, 코드번호, 기재항목을 모두 나열하세요
- "이전 → 현재" 형식으로 변경 전후를 명확히 대비하세요
- 신규 추가, 삭제, 수정 사항을 각각 구분하여 기술하세요
- EMR 개발/운영 관점에서 어떤 화면·로직·DB를 수정해야 하는지 구체적으로 기술하세요
- 미적용 시 발생하는 청구 오류, 법적 제재, 심사 반려 등의 위험을 구체적으로 서술하세요

제목: {title}

[이전 고시 전문]
{prev_clean}

[현재 고시 전문]
{text}

---
아래 형식으로 빠짐없이 상세히 작성하세요:

### 📊 이전 고시 핵심 사항 (개정 전 기준점)
- 주요 수가코드·급여기준·조건·금액·비율 등을 항목별로 모두 나열
- 처방·청구·서식 관련 기재항목 및 필수값 명시
- 적용 대상 기관·조건 명시

### 📋 변경된 내용 상세 (이전 → 현재)
**[신규 추가]**
- 항목명: 내용 (조문/별표 번호 포함)
**[수정/변경]**
- [항목] 이전: 구체적 내용 → 현재: 구체적 내용
- (수치, 코드, 비율, 기간 등 모든 변경사항 포함)
**[삭제]**
- 삭제된 항목 및 내용

### 🏥 EMR 모듈별 적용 필요 사항
- [청구/수납] 수가코드·청구항목 변경 시: 코드 테이블·청구화면·EDI 전송 로직 수정 내용
- [처방] 처방전·약품코드·기재항목 변경 시: 처방화면·필수입력 검증 로직 수정 내용
- [의무기록] 서식·기재항목 변경 시: 진료기록 화면·서식 양식 수정 내용
- [원무/수납] 원무·바우처·본인부담 변경 시: 수납화면·계산 로직 수정 내용
- [신고/보고] 신고의무·통보기한 변경 시: 신고화면·자동화 로직 수정 내용
- [OCS/검사] 오더·검사 관련 변경 시: OCS 화면·검사 연동 수정 내용
(해당하는 모듈만 작성)

### ⚠️ 미적용 시 위험 및 영향
- 청구 오류 발생 가능성 및 심사 반려 기준
- 법적 제재·과태료·행정처분 내용
- 환자 불이익 또는 민원 발생 가능성

### 📅 시행일 및 EMR 대응 기한
- 시행일: (정확한 날짜)
- 경과조치: (있는 경우 기간 및 내용)
- EMR 반영 권장 기한: 시행일 기준 역산

### 👥 적용 대상 기관 및 범위
- 의료기관 종류·규모·지역 등 적용 범위
- 제외 기관·예외 조항"""
    else:
        prompt = f"""당신은 "의사랑" EMR(전자의무기록) 시스템의 제품 PM입니다.
아래 보건의료 고시/지침 문서를 EMR 적용 관점에서 철저히 분석하세요.

분석 지침:
- 조문별·항목별로 구체적인 수치, 코드번호, 기재항목을 빠짐없이 추출하세요
- EMR 개발/운영 관점에서 어떤 화면·로직·DB를 수정해야 하는지 구체적으로 기술하세요
- 미적용 시 발생하는 청구 오류, 법적 제재, 심사 반려 위험을 구체적으로 서술하세요
- 없는 항목은 생략하되, 있는 내용은 최대한 상세히 작성하세요

제목: {title}

문서 전문:
{text}

---
아래 형식으로 빠짐없이 상세히 작성하세요:

### 📋 주요 변경·신설 내용 상세
- 핵심 변경사항을 조문/항목별로 모두 나열
- 수가코드, 금액, 비율, 기간, 기재항목 등 수치를 구체적으로 명시
- 신규 추가·삭제·수정 사항을 구분하여 기술

### 🏥 EMR 모듈별 적용 필요 사항
- [청구/수납] 수가코드·청구항목·EDI 관련 수정 사항
- [처방] 처방전·약품코드·기재항목 관련 수정 사항
- [의무기록] 서식·기재의무 관련 수정 사항
- [원무/수납] 원무·바우처·본인부담 관련 수정 사항
- [신고/보고] 신고의무·통보기한 관련 수정 사항
- [OCS/검사] 오더·검사 연동 관련 수정 사항
(해당하는 모듈만 작성, 각 항목별 구체적 조치 내용 포함)

### ⚠️ 미적용 시 위험 및 영향
- 청구 오류 발생 가능성 및 심사 반려 기준
- 법적 제재·과태료·행정처분 내용
- 환자 불이익 또는 민원 발생 가능성

### 📅 시행일 및 EMR 대응 기한
- 시행일: (정확한 날짜)
- 경과조치: (있는 경우 기간 및 내용)
- EMR 반영 권장 기한

### 👥 적용 대상 기관 및 범위
- 의료기관 종류·규모 등 적용 범위
- 제외 기관·예외 조항"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text
    except Exception as e:
        return f"[Claude 오류: {e}]"


# ══════════════════════════════════════════════════════
#  공개 인터페이스
# ══════════════════════════════════════════════════════

def summarize(title: str, content: str, prev_text: str = '') -> str:
    if not content or not content.strip():
        return "[첨부파일에서 텍스트를 추출할 수 없습니다]"

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        mode = "이전 대비 변경사항 비교" if prev_text else "EMR 분석"
        print(f"    (Claude API - {mode})")
        return _claude_summarize(title, content, api_key, prev_text=prev_text)
    else:
        print("    (로컬 규칙 기반 - EMR 이슈 추출)")
        return _local_summarize(title, content, prev_text=prev_text)


def compare_files(title: str, new_text: str, prev_text: str) -> str:
    """
    파일 업로드 비교 분석 전용 진입점.
    EMR PM을 위한 극도로 상세한 3섹션 분석 반환:
      [1] 전체 요약  [2] 신설 사항  [3] 변경 사항
    """
    if not new_text or not new_text.strip():
        return "[업로드 파일에서 텍스트를 추출할 수 없습니다]"

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        print("    (Claude API - 파일 상세 비교 분석)")
        return _claude_compare_files(title, new_text, prev_text, api_key)
    else:
        print("    (로컬 규칙 기반 - 파일 비교)")
        return _local_compare_files(title, new_text, prev_text)


def _claude_compare_files(title: str, new_text: str, prev_text: str, api_key: str) -> str:
    import anthropic

    new_clean  = _clean(new_text)
    prev_clean = _clean(prev_text) if prev_text else ''

    # 각 문서 최대 25000자 (원문 상세 분석을 위해 확대)
    if len(new_clean) > 25000:
        new_clean = new_clean[:18000] + '\n\n...(중략)...\n\n' + new_clean[-6000:]
    if prev_clean and len(prev_clean) > 25000:
        prev_clean = prev_clean[:18000] + '\n\n...(중략)...\n\n' + prev_clean[-6000:]

    if prev_clean:
        compare_block = f"""[이전 고시]
{prev_clean}

[현재 고시]
{new_clean}"""
        mode = 'compare'
    else:
        compare_block = f"""[현재 고시]
{new_clean}"""
        mode = 'new'

    if mode == 'compare':
        prompt = f"""당신은 "의사랑" EMR 시스템 수석 PM입니다.
이전 고시와 현재 고시를 비교해 **변경된 내용만** 아래 4개 섹션으로 작성하세요.
변경이 없는 조문은 절대 언급하지 마세요.

작성 규칙:
- 신설·변경·삭제 항목만 기술 (동일한 내용은 생략)
- 수치(금액·비율·횟수·기간)·코드번호는 반드시 이전→현재 대비
- EMR에서 실제로 수정해야 할 화면·로직·DB를 모듈별로 구체적으로 기술
- 미적용 시 발생하는 청구오류·법적제재·심사반려 위험 명시

제목: {title}

{compare_block}

---

## 📋 섹션 1: 전체 요약

| 항목 | 내용 |
|------|------|
| 시행일 | (날짜) |
| 적용 대상 | (기관 종류) |
| 신설 조문 | N개 |
| 변경 조문 | N개 |
| 삭제 조문 | N개 |
| EMR 긴급 대응 | (가장 시급한 1~3가지) |

이전 대비 핵심 변화를 3~5줄로 서술:

---

## 🆕 섹션 2: 신설 사항

이전 고시에 없던 조문·항목만 기술.

### [조문번호 / 항목명]
- **신설 내용**: 핵심 문장 원문 인용
- **EMR 적용**: [모듈] 구체적으로 추가해야 할 화면·기능·DB 항목
- **우선순위**: 긴급 / 보통 / 낮음

---

## 🔄 섹션 3: 변경 사항

변경·삭제된 조문·항목만 조문 순서대로 기술.

### [조문번호 / 항목명]
| 구분 | 이전 | 현재 |
|------|------|------|
| (변경 항목) | 이전 값/내용 | 현재 값/내용 |

- **EMR 영향**: [모듈] 수정해야 할 구체적 내용 (화면명·필드명·로직 포함)
- **미적용 위험**: (청구오류·심사반려·과태료 등)

### [삭제] [조문번호 / 항목명]
- **삭제 내용**: 기존 내용 요약
- **EMR 영향**: 관련 기능 제거·비활성화 필요 여부

---

## 📄 섹션 4: 원문 주요 고시 사항

변경된 조문의 현재 전체 원문을 EMR 적용 관점에서 기술.
(변경 없는 조문은 생략)

### [조문번호 / 항목명]
> 원문 핵심 문장 인용
- 세부 항목 (수치·코드·조건 포함)
- **EMR 적용 필요 사항**: [모듈] 조치 내용"""

    else:  # 이전 고시 없음 — 신규 고시 전체 분석
        prompt = f"""당신은 "의사랑" EMR 시스템 수석 PM입니다.
아래 신규 고시를 EMR 적용 관점에서 4개 섹션으로 분석하세요.

작성 규칙:
- 조문·항목별 수치(금액·비율·코드)를 빠짐없이 명시
- EMR에서 신규로 구현해야 할 화면·로직·DB를 모듈별로 구체적으로 기술
- 미적용 시 발생하는 청구오류·법적제재·심사반려 위험 명시

제목: {title}

{compare_block}

---

## 📋 섹션 1: 전체 요약

| 항목 | 내용 |
|------|------|
| 시행일 | (날짜) |
| 적용 대상 | (기관 종류) |
| 주요 내용 | (핵심 1줄) |
| EMR 긴급 대응 | (가장 시급한 1~3가지) |

핵심 내용을 3~5줄로 서술:

---

## 🆕 섹션 2: 신설 사항

전체 조문·항목을 기술.

### [조문번호 / 항목명]
- **내용**: 핵심 문장 원문 인용
- **EMR 적용**: [모듈] 구체적으로 구현해야 할 화면·기능·DB
- **우선순위**: 긴급 / 보통 / 낮음

---

## 🔄 섹션 3: 변경 사항

신규 고시이므로 이전 대비 변경 없음.

---

## 📄 섹션 4: 원문 주요 고시 사항

전체 조문 원문을 EMR 적용 관점에서 기술.

### [조문번호 / 항목명]
> 원문 핵심 문장 인용
- 세부 항목 (수치·코드·조건 포함)
- **EMR 적용 필요 사항**: [모듈] 조치 내용"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text
    except Exception as e:
        return f"[Claude 오류: {e}]"


# ══════════════════════════════════════════════════════
#  조문 추출 + difflib 비교 (로컬 AI 엔진)
# ══════════════════════════════════════════════════════

def _extract_articles(text: str, max_articles: int = 40, max_chars_per: int = 1000) -> list[dict]:
    """제N조 형식 조문 제목과 내용 추출."""
    pattern = r'(?:^|\n)(제\s*\d+\s*조(?:\s*의\s*\d+)?\s*(?:\([^)]{1,40}\))?[^\n]*)\n([\s\S]*?)(?=\n제\s*\d+\s*조|\Z)'
    matches = re.findall(pattern, text, re.MULTILINE)
    articles = []
    for heading, body in matches[:max_articles]:
        heading = heading.strip()
        body_lines = [l.strip() for l in body.splitlines() if l.strip()]
        body_text = '\n'.join(body_lines)
        if heading:
            articles.append({'heading': heading, 'body': body_text})
    return articles


def _articles_as_dict(articles: list[dict]) -> dict:
    """조문 번호(제N조)를 키로 하는 딕셔너리 반환."""
    result = {}
    for a in articles:
        # 제N조 숫자만 추출해 키 생성 (공백 무시)
        m = re.match(r'제\s*(\d+)\s*조(?:\s*의\s*(\d+))?', a['heading'])
        if m:
            key = f"제{m.group(1)}조" + (f"의{m.group(2)}" if m.group(2) else "")
        else:
            key = re.sub(r'\s+', '', a['heading'])[:10]
        result[key] = a
    return result


def _normalize_line(line: str) -> str:
    """줄 정규화: 공백·구두점 통일 (diff 노이즈 제거용)."""
    line = re.sub(r'\s+', ' ', line).strip()
    line = re.sub(r'[．。·•]', '.', line)
    return line


# EMR 관련 없는 순수 분류 레이블 패턴 (01: 주사, 99: 기타 류)
_NOISE_PATTERNS = re.compile(
    r'^[\d]+\s*[:：]\s*[가-힣]{1,10}$'              # "01: 주사", "99: 기타", "03: 처치및수술"
    r'|^[가-힣]{1,4}\s*[:：]\s*[가-힣]{1,10}$'      # "가: 초진"
    r'|^[\d]+\s*[:：]\s*[가-힣]{1,10}\s*\(.*\)$'    # "01: 주사 (행위분류)"
    r'|^\d{1,3}\s+[가-힣]{1,10}$'                   # "01 주사", "99 기타"
    r'|^[가-힣]{1,10}\s+\d{1,3}$'                   # "주사 01"
    r'|^[-–—=_*·]+$'                                # 구분선
    r'|^\d+\s*$'                                     # 숫자만
    r'|^[가-힣]{1,2}\s*\.\s*$'                       # "가." "나."
    r'|^제\s*\d+\s*[조항호]\s*$'                     # 빈 조문 번호
    r'|^[가-힣]{1,6}$'                               # 짧은 한글 단어만 ("주사", "기타" 등)
    r'|^\([가-힣\d\s]{1,20}\)$'                      # "(주사)", "(기타 행위)"
)

# EMR 실제 조치가 필요한 패턴 (수치·코드·행위분류 포함 여부)
_EMR_ACTIONABLE = re.compile(
    r'수가|급여|청구|처방|행위\s*코드|의약품|서식|기재|신고|통보|수납|바우처|OCS|검사'
    r'|[A-Z]\d{3,}|[가-힣]\s*항\s*\d|[가-힣]\s*목\s*\d|\d+\s*목|\d+\s*항'
    r'|산정|삭제|신설|추가|변경|폐지|개정|원\b|%|점수|코드'
)


def _emr_module_for_line(line: str) -> str:
    """한 줄에서 EMR 모듈 결정 (actionable 라인에만 적용)."""
    if not _EMR_ACTIONABLE.search(line):
        return ''
    for pattern, _, module in EMR_IMPACT_RULES:
        if re.search(pattern, line):
            return module
    if re.search(r'[가-힣]\s*항\s*\d|[가-힣]\s*목\s*\d|\d+\s*목|\d+\s*항|산정|급여\s*기준|수가|행위\s*코드', line):
        return '청구/수납'
    return ''


def _diff_lines(prev_text: str, new_text: str):
    """두 문서의 실제 변경 라인 추출 — 정규화 후 비교, 노이즈 제거."""
    import difflib

    def _prep(text):
        lines = []
        for l in text.splitlines():
            n = _normalize_line(l)
            if len(n) < 4:
                continue
            if _NOISE_PATTERNS.match(n):
                continue
            lines.append(n)
        return lines

    old = _prep(prev_text)
    new = _prep(new_text)
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    removed, added = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            continue
        if tag in ('delete', 'replace'):
            removed.extend(old[i1:i2])
        if tag in ('insert', 'replace'):
            added.extend(new[j1:j2])
    # EMR actionable 아닌 줄 제거
    removed = [l for l in removed if _EMR_ACTIONABLE.search(l)]
    added   = [l for l in added   if _EMR_ACTIONABLE.search(l)]
    return removed, added


def _group_emr_actions(lines: list, action_label: str) -> str:
    """EMR 모듈별로 그룹화 — 생략 없이 전체 출력."""
    by_module: dict = {}
    for line in lines:
        module = _emr_module_for_line(line)
        if not module:
            continue
        by_module.setdefault(module, []).append(line)

    if not by_module:
        return ''

    parts = []
    for module, items in by_module.items():
        parts.append(f"**[{module}] {action_label}** ({len(items)}건)")
        for item in items:          # 생략 없음
            parts.append(f"  • {item}")
    return '\n'.join(parts)


def _extract_reason(text: str) -> str:
    """개정이유·목적·추진배경 추출."""
    for kws in [
        [r'개정\s*이유', r'제정\s*이유'],
        [r'추진\s*배경', r'추진\s*경위'],
        [r'목\s*적'],
    ]:
        val = _extract_section_block(text, *kws, max_chars=2000)
        if val:
            return val
    return ''


def _extract_grace_period(text: str) -> str:
    """경과조치·부칙 추출."""
    return _extract_section(text, r'경과\s*조치', r'부\s*칙', max_chars=600)


def _extract_main_changes(text: str) -> list[dict]:
    """주요내용 하위 항목을 As-Is/To-Be 구조로 추출.

    1단계: (현행)/(개정) 쌍을 전체 문서에서 직접 탐색
    2단계: 주요내용 섹션 하위 항목을 ▷/가./나. 로 분리
    """
    results = []

    # ── 1단계: 전체 문서에서 (현행)/(개정) 쌍 직접 탐색 ──────────────
    pair_pat = re.compile(
        r'[\(\（](?:현행|기존|As-?Is|이전)[\)\）]\s*([\s\S]*?)'
        r'[\(\（](?:개정|변경|To-?Be|이후)[\)\）]\s*([\s\S]*?)'
        r'(?=[\(\（](?:현행|기존|As-?Is|이전)[\)\）]|붙임|부칙|$)',
        re.IGNORECASE
    )
    for pm in pair_pat.finditer(text):
        old = pm.group(1).strip()
        new = pm.group(2).strip()
        if not old and not new:
            continue
        # 앞에 제목 줄이 있으면 가져옴
        pre = text[max(0, pm.start() - 120):pm.start()]
        pre_lines = [l.strip() for l in pre.splitlines() if l.strip()]
        title = ''
        for l in reversed(pre_lines):
            if re.match(r'^(?:[가-힣]\.|[①-⑳]|\d+\.|▷|○)\s*.{3,}', l):
                title = re.sub(r'^(?:[가-힣]\.|[①-⑳]|\d+\.|▷|○)\s*', '', l).strip()
                break
        if not title:
            title = pre_lines[-1][:60] if pre_lines else '변경 사항'
        # 텍스트 정리 (최대 800자)
        results.append({
            'title':    title,
            'old_text': old[:800],
            'new_text': new[:800],
        })

    if results:
        return results

    # ── 2단계: 주요내용 섹션 블록에서 하위 항목 분리 ─────────────────
    full = _extract_section_block(
        text,
        r'주요\s*내용', r'개정\s*주요내용', r'주요\s*개정\s*사항',
        max_chars=8000
    )
    if not full:
        full = text

    sub_pattern = re.compile(
        r'(?:^|\n)'
        r'(?:[가-힣]\s*\.|[①-⑳]|\d+\s*\.|▷\s*|○\s*)'
        r'\s*([^\n]{3,100})',
        re.MULTILINE
    )
    splits = list(sub_pattern.finditer(full))
    if not splits:
        return results

    for i, m in enumerate(splits):
        title = m.group(1).strip()
        start = m.end()
        end   = splits[i + 1].start() if i + 1 < len(splits) else len(full)
        body  = full[start:end].strip()
        if not body:
            continue
        # (현행)/(개정) 추출 시도
        asis_m = re.search(
            r'[\(\（](?:현행|기존|As-?Is)[\)\）]\s*([\s\S]*?)(?=[\(\（](?:개정|변경|To-?Be)[\)\）]|$)',
            body, re.IGNORECASE
        )
        tobe_m = re.search(
            r'[\(\（](?:개정|변경|To-?Be)[\)\）]\s*([\s\S]*)',
            body, re.IGNORECASE
        )
        old_text = asis_m.group(1).strip()[:600] if asis_m else ''
        new_text = tobe_m.group(1).strip()[:600] if tobe_m else body[:600]

        if title and (old_text or new_text):
            results.append({'title': title, 'old_text': old_text, 'new_text': new_text})

    return results


def _local_compare_files(title: str, new_text: str, prev_text: str) -> str:
    """로컬 분석 엔진 — ■배경 / ▷As-Is·To-Be / ■적용일자 / ■의사랑 적용 형식."""
    new_clean  = _clean(new_text)
    prev_clean = _clean(prev_text) if prev_text else ''

    new_arts_list = _extract_articles(new_clean, max_articles=200, max_chars_per=99999)
    new_arts  = _articles_as_dict(new_arts_list)
    prev_arts = _articles_as_dict(
        _extract_articles(prev_clean, max_articles=200, max_chars_per=99999)
    ) if prev_clean else {}

    removed_lines, added_lines = (
        _diff_lines(prev_clean, new_clean) if prev_clean else ([], [])
    )

    eff_date     = _extract_date(new_clean)
    target       = _extract_target(new_clean)
    reason       = _extract_reason(new_clean)
    grace        = _extract_grace_period(new_clean)
    main_changes = _extract_main_changes(new_clean)
    by_module    = _emr_lines_by_module(new_clean)

    sections = []

    # ══════════════════════════════════════════════════
    # 섹션 1: 전체 요약  (■ 배경 + ▷변경항목 + ■ 적용일자)
    # ══════════════════════════════════════════════════
    s1 = []

    # ■ 배경
    if reason:
        reason_lines = [l.strip() for l in reason.splitlines() if l.strip()]
        s1.append("■ 배경")
        for i, l in enumerate(reason_lines, 1):
            s1.append(f"{i}. {l}")
        s1.append("")
    else:
        paras = [p.strip() for p in re.split(r'\n{2,}', new_clean[:3000]) if len(p.strip()) > 30]
        if paras:
            s1.append("■ 배경")
            for i, l in enumerate([ll.strip() for ll in paras[0].splitlines() if ll.strip()][:5], 1):
                s1.append(f"{i}. {l}")
            s1.append("")

    # ▷ 변경 항목 (현행/개정 쌍)
    if main_changes:
        for chg in main_changes:
            s1.append(_fmt_asistobe_block(chg))
            s1.append("")
    elif prev_clean and (removed_lines or added_lines):
        # 이전 고시와 diff가 있으면 주요 변경 라인을 변경 항목으로 표시
        chg = {
            'title': '주요 변경 사항 (이전 대비)',
            'old_text': '\n'.join(removed_lines[:10]),
            'new_text': '\n'.join(added_lines[:10]),
        }
        s1.append(_fmt_asistobe_block(chg))
        s1.append("")
    else:
        content_lines = _main_content_lines(new_clean)
        if content_lines:
            s1.append("▷ 주요 변경/신설 사항")
            for l in content_lines[:15]:
                s1.append(f"  {l}")
            s1.append("")

    # ■ 적용일자
    if eff_date:
        s1.append(f"■ 적용일자: {eff_date}")
    if target:
        s1.append(f"- 적용 대상: {target}")
    if grace:
        for gl in grace.splitlines():
            if gl.strip():
                s1.append(f"- {gl.strip()}")

    if not s1:
        s1.append("(요약 정보 추출 불가 — 원문 탭 확인)")

    sections.append("## 📋 섹션 1: 전체 요약\n\n" + '\n'.join(s1))

    # ══════════════════════════════════════════════════
    # 섹션 2: 신설 사항
    # ══════════════════════════════════════════════════
    s2 = []

    if prev_clean and prev_arts:
        added_art_keys = [k for k in new_arts if k not in prev_arts]
        if added_art_keys:
            for k in added_art_keys:
                a = new_arts[k]
                s2.append(_fmt_asistobe_block({'title': a['heading'], 'old_text': '', 'new_text': a['body']}))
                s2.append("")
        if not s2:
            emr_added = _group_emr_actions(added_lines, '신설/추가')
            s2.append(emr_added if emr_added else "이전 고시 대비 신설 조문 없음")
    else:
        if main_changes:
            for chg in main_changes:
                s2.append(_fmt_asistobe_block(chg))
                s2.append("")
        elif new_arts_list:
            for a in new_arts_list:
                combined = a['heading'] + ' ' + a['body']
                if any(re.search(p, combined) for p, _, _ in EMR_IMPACT_RULES):
                    s2.append(_fmt_asistobe_block({'title': a['heading'], 'old_text': '', 'new_text': a['body']}))
                    s2.append("")
        if not s2:
            content_lines = _main_content_lines(new_clean)
            if content_lines:
                s2.append("▷ 주요 내용 전문")
                for l in content_lines:
                    s2.append(f"  {l}")

    if not s2:
        s2.append("(신설 항목 미감지)")

    sections.append("## 🆕 섹션 2: 신설 사항\n\n" + '\n'.join(s2))

    # ══════════════════════════════════════════════════
    # 섹션 3: 변경 사항  (▷ As-Is → To-Be)
    # ══════════════════════════════════════════════════
    s3 = []

    if prev_clean:
        changed_keys = [k for k in new_arts if k in prev_arts
                        and new_arts[k]['body'].strip() != prev_arts[k]['body'].strip()]
        deleted_keys = [k for k in prev_arts if k not in new_arts]

        for k in changed_keys:
            a_new, a_old = new_arts[k], prev_arts[k]
            rem, add = _diff_lines(a_old['body'], a_new['body'])
            s3.append(_fmt_asistobe_block({
                'title':    a_new['heading'],
                'old_text': '\n'.join(rem) if rem else a_old['body'][:500],
                'new_text': '\n'.join(add) if add else a_new['body'][:500],
            }))
            s3.append("")

        for k in deleted_keys:
            a = prev_arts[k]
            s3.append(_fmt_asistobe_block({
                'title': f"{a['heading']} (삭제)",
                'old_text': a['body'],
                'new_text': '(해당 조문 삭제됨)',
            }))
            s3.append("")

        if not s3:
            if main_changes:
                for chg in main_changes:
                    s3.append(_fmt_asistobe_block(chg))
                    s3.append("")
            elif removed_lines or added_lines:
                s3.append(_fmt_asistobe_block({
                    'title': '변경된 항목 (이전 대비 diff)',
                    'old_text': '\n'.join(removed_lines),
                    'new_text': '\n'.join(added_lines),
                }))
            else:
                s3.append("(자동 감지된 변경 사항 없음 — 원문 탭 확인)")
    else:
        if main_changes:
            for chg in main_changes:
                s3.append(_fmt_asistobe_block(chg))
                s3.append("")
        if not s3:
            s3.append("이전 고시가 없어 비교 불가")

    sections.append("## 🔄 섹션 3: 변경 사항\n\n" + '\n'.join(s3))

    # ══════════════════════════════════════════════════
    # 섹션 4: 원문 주요 고시 / ■ 의사랑 적용
    # ══════════════════════════════════════════════════
    s4 = []

    # ■ 의사랑 적용
    if by_module:
        s4.append("■ 의사랑 적용\n")
        for mod, items in by_module.items():
            s4.append(f"▷ {mod}")
            seen = set()
            idx = 1
            for item in items:
                if item in seen:
                    continue
                seen.add(item)
                s4.append(f"{idx}. {item}")
                idx += 1
            s4.append("")
    else:
        emr_hits = _analyze_emr_impact(new_clean)
        if emr_hits:
            s4.append("■ EMR 적용 검토 항목 (키워드 기반)")
            s4.extend(emr_hits)
            s4.append("")

    # 원문 조문
    if new_arts_list:
        s4.append("---\n■ 원문 전체 조문\n")
        for a in new_arts_list:
            s4.append(f"### {a['heading']}\n{a['body']}")
    else:
        paras = [p.strip() for p in re.split(r'\n{2,}', new_clean) if len(p.strip()) > 20]
        if paras:
            for p in paras:
                s4.append(p)
                s4.append("")
        else:
            s4.append(new_clean)

    sections.append("## 📄 섹션 4: 원문 주요 고시 사항\n\n" + '\n'.join(s4))

    return '\n\n'.join(sections)


# ══════════════════════════════════════════════════════
#  단독 EMR 분석 (비교 없이 파일 하나만)
# ══════════════════════════════════════════════════════

def analyze_emr(title: str, content: str) -> str:
    """단독 파일 EMR 적용 사항 분석 (이전 고시 비교 없음)."""
    if not content or not content.strip():
        return "[파일에서 텍스트를 추출할 수 없습니다]"

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        print("    (Claude API - EMR 단독 분석)")
        return _claude_analyze_emr(title, content, api_key)
    else:
        print("    (로컬 규칙 기반 - EMR 단독 분석)")
        return _local_analyze_emr(title, content)


def _fmt_asistobe_block(chg: dict) -> str:
    """▷ title / (As-Is) / (To-Be) 블록 포맷."""
    block = [f"▷ {chg['title']}"]
    if chg.get('old_text'):
        lines = [l.strip() for l in chg['old_text'].splitlines() if l.strip()]
        block.append(f"(As-Is) {lines[0]}" if lines else "(As-Is)")
        for l in lines[1:]:
            block.append(f"  {l}")
    if chg.get('new_text'):
        lines = [l.strip() for l in chg['new_text'].splitlines() if l.strip()]
        block.append(f"(To-Be) {lines[0]}" if lines else "(To-Be)")
        for l in lines[1:]:
            block.append(f"  {l}")
    return '\n'.join(block)


def _emr_lines_by_module(text: str) -> dict:
    """전체 텍스트에서 EMR 관련 문장을 모듈별로 수집."""
    by_module: dict = {}
    for line in text.splitlines():
        s = line.strip()
        if len(s) < 8:
            continue
        # EMR_IMPACT_RULES 패턴으로 모듈 결정 (actionable 필터 완화)
        for pattern, _, module in EMR_IMPACT_RULES:
            if re.search(pattern, s):
                if not _NOISE_PATTERNS.match(s):
                    by_module.setdefault(module, []).append(s)
                break
        else:
            # 추가 패턴: 코드/수치 포함 라인
            if re.search(r'[가-힣]\s*항\s*\d|[가-힣]\s*목\s*\d|\d+\s*목|\d+\s*항|행위\s*코드|수가\s*코드', s):
                if not _NOISE_PATTERNS.match(s):
                    by_module.setdefault('청구/수납', []).append(s)
    return by_module


def _main_content_lines(text: str) -> list[str]:
    """주요내용 섹션 또는 전체에서 의미 있는 줄 추출 (bullet 포함)."""
    block = _extract_section_block(
        text,
        r'주요\s*내용', r'개정\s*주요내용', r'주요\s*개정\s*사항',
        max_chars=6000
    )
    if not block:
        block = text
    lines = []
    for l in block.splitlines():
        s = l.strip()
        if len(s) < 5:
            continue
        if _NOISE_PATTERNS.match(s):
            continue
        lines.append(s)
    return lines[:100]


def _local_analyze_emr(title: str, text: str) -> str:
    """로컬 규칙 기반 EMR 단독 분석 — ■배경/▷변경/■적용일자/■의사랑 적용 형식."""
    clean = _clean(text)

    reason       = _extract_reason(clean)
    main_changes = _extract_main_changes(clean)
    eff_date     = _extract_date(clean)
    target       = _extract_target(clean)
    grace        = _extract_grace_period(clean)
    arts_list    = _extract_articles(clean, max_articles=200, max_chars_per=99999)
    by_module    = _emr_lines_by_module(clean)

    # ──────────────────────────────────────────────────
    # 섹션 1: 전체 요약 (■배경 + ▷변경항목 + ■적용일자)
    # ──────────────────────────────────────────────────
    s1 = []

    # ■ 배경
    if reason:
        reason_lines = [l.strip() for l in reason.splitlines() if l.strip()]
        s1.append("■ 배경")
        for i, l in enumerate(reason_lines, 1):
            s1.append(f"{i}. {l}")
        s1.append("")
    else:
        # fallback: 문서 앞부분에서 의미 있는 첫 단락
        paras = [p.strip() for p in re.split(r'\n{2,}', clean[:3000]) if len(p.strip()) > 30]
        if paras:
            s1.append("■ 배경")
            first_para_lines = [l.strip() for l in paras[0].splitlines() if l.strip()]
            for i, l in enumerate(first_para_lines[:5], 1):
                s1.append(f"{i}. {l}")
            s1.append("")

    # ▷ 변경 항목 (As-Is / To-Be)
    if main_changes:
        for chg in main_changes:
            s1.append(_fmt_asistobe_block(chg))
            s1.append("")
    else:
        # fallback: 주요내용 섹션에서 bullet 항목들을 변경 항목으로 표시
        content_lines = _main_content_lines(clean)
        if content_lines:
            s1.append("▷ 주요 변경/신설 사항")
            for l in content_lines[:20]:
                s1.append(f"  {l}")
            s1.append("")

    # ■ 적용일자
    if eff_date:
        s1.append(f"■ 적용일자: {eff_date}")
    if target:
        s1.append(f"- 적용 대상: {target}")
    if grace:
        for gl in grace.splitlines():
            if gl.strip():
                s1.append(f"- {gl.strip()}")

    sec1 = "## 📋 섹션 1: EMR 적용 사항 요약\n\n" + '\n'.join(s1)

    # ──────────────────────────────────────────────────
    # 섹션 2: 신설/변경 사항 상세
    # ──────────────────────────────────────────────────
    s2 = []

    if main_changes:
        # main_changes가 있으면 상세 표시
        for chg in main_changes:
            s2.append(_fmt_asistobe_block(chg))
            s2.append("")
    elif arts_list:
        # 조문 구조가 있으면 EMR 관련 조문 표시
        for a in arts_list:
            combined = a['heading'] + ' ' + a['body']
            if any(re.search(p, combined) for p, _, _ in EMR_IMPACT_RULES):
                block = [f"▷ {a['heading']}"]
                for bl in a['body'].splitlines():
                    if bl.strip() and not _NOISE_PATTERNS.match(bl.strip()):
                        block.append(f"  {bl.strip()}")
                s2.append('\n'.join(block))
                s2.append("")
    else:
        # 마지막 fallback: 주요내용 전체 줄
        content_lines = _main_content_lines(clean)
        if content_lines:
            s2.append("▷ 주요 내용 전문")
            for l in content_lines:
                s2.append(f"  {l}")

    if not s2:
        s2.append("(상세 내용 추출 불가 — 원문 탭 확인)")

    sec2 = "## 🆕 섹션 2: 신설 사항\n\n" + '\n'.join(s2)

    # ──────────────────────────────────────────────────
    # 섹션 3: ■ 의사랑 적용 (EMR 모듈별)
    # ──────────────────────────────────────────────────
    s3 = []

    if by_module:
        s3.append("■ 의사랑 적용\n")
        for mod, items in by_module.items():
            s3.append(f"▷ {mod}")
            seen = set()
            idx = 1
            for item in items:
                if item in seen:
                    continue
                seen.add(item)
                s3.append(f"{idx}. {item}")
                idx += 1
            s3.append("")
    else:
        # fallback: EMR_IMPACT_RULES 키워드 히트 요약
        emr_hits = _analyze_emr_impact(clean)
        if emr_hits:
            s3.append("■ EMR 적용 검토 항목 (키워드 기반)")
            s3.extend(emr_hits)
        else:
            s3.append("(EMR 적용 항목을 자동 감지하지 못했습니다 — 원문 탭을 확인하세요)")

    sec3 = "## 🔄 섹션 3: 변경 사항\n\n" + '\n'.join(s3)

    # ──────────────────────────────────────────────────
    # 섹션 4: 원문 전체 조문
    # ──────────────────────────────────────────────────
    s4 = []

    if arts_list:
        for a in arts_list:
            s4.append(f"### {a['heading']}\n{a['body']}")
    else:
        # 단락 단위로 분리하여 전체 표시
        paras = [p.strip() for p in re.split(r'\n{2,}', clean) if len(p.strip()) > 20]
        if paras:
            for p in paras:
                s4.append(p)
                s4.append("")
        else:
            s4.append(clean)

    sec4 = "## 📄 섹션 4: 원문 주요 고시 사항\n\n" + '\n'.join(s4)

    return '\n\n'.join([sec1, sec2, sec3, sec4])


def _claude_analyze_emr(title: str, text: str, api_key: str) -> str:
    import anthropic

    clean = _clean(text)
    if len(clean) > 25000:
        clean = clean[:18000] + '\n\n...(중략)...\n\n' + clean[-6000:]

    prompt = f"""당신은 "의사랑" EMR 시스템 수석 PM입니다.
아래 고시를 EMR 적용 관점에서 분석하세요. 이전 고시와 비교하지 않고 이 문서 자체를 분석합니다.

작성 규칙:
- 개정이유/배경을 ■ 배경 섹션에 번호 항목으로 정리
- 변경/신설 사항은 ▷ 항목명 + (As-Is)/(To-Be) 형식으로 기술
- EMR 모듈별 적용 사항은 ■ 의사랑 적용 > ▷ 모듈명 > 번호 항목 형식
- 수치·코드·기재항목은 빠짐없이 명시
- 없는 항목은 생략

제목: {title}

문서 전문:
{clean}

---

아래 형식으로 작성하세요:

## 📋 섹션 1: EMR 적용 사항 요약

■ 배경
1. (개정 이유/배경 항목)

▷ (변경 항목명)
(As-Is) 변경 전 내용
(To-Be) 변경 후 내용

■ 적용일자: (날짜)
- 적용 대상: (기관)

---

## 🆕 섹션 2: 신설 사항

▷ (신설 조문/항목)
  (세부 내용)

---

## 🔄 섹션 3: 변경 사항

■ 의사랑 적용

▷ (EMR 모듈명, 예: DRG 청구, 처방, 수납 등)
1. (구체적 적용 사항)
2. (구체적 적용 사항)

---

## 📄 섹션 4: 원문 주요 고시 사항

### (조문번호/항목명)
(원문 내용)"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text
    except Exception as e:
        return f"[Claude 오류: {e}]"
