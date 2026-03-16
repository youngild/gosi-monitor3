"""
건강보험심사평가원 InfoBank 크롤러
https://biz.hira.or.kr/popup.ndo?formname=qya_bizcom::InfoBank.xfdl&framename=InfoBank

Nexacro14 프레임워크 기반 → Playwright로 브라우저 렌더링 후 데이터 추출
"""
import re
import os
import asyncio
from datetime import date, datetime

DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'files', 'hira')
FROM_DATE = date(2026, 3, 1)
PAGE_URL = "https://biz.hira.or.kr/popup.ndo?formname=qya_bizcom%3A%3AInfoBank.xfdl&framename=InfoBank"


async def _scrape_with_playwright() -> list[dict]:
    """Playwright로 InfoBank 렌더링 후 게시물 추출."""
    from playwright.async_api import async_playwright

    items = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu',
                  '--single-process', '--no-zygote']
        )
        context = await browser.new_context(ignore_https_errors=True)
        page = await context.new_page()

        print("[HIRA] 페이지 로딩 중...")
        await page.goto(PAGE_URL, wait_until='networkidle', timeout=30000)

        # Nexacro 렌더링 대기 (Grid 컴포넌트가 로드될 때까지)
        await page.wait_for_timeout(3000)

        # Nexacro Grid 셀 데이터 추출 (JavaScript로 직접 접근)
        result = await page.evaluate("""
            () => {
                const items = [];
                try {
                    // Nexacro14 application 객체에서 데이터셋 접근 시도
                    const app = nexacro.getApplication();
                    if (!app) return { error: 'no app', items };

                    // 컴포넌트 트리 탐색
                    const frames = app._getFrameList ? app._getFrameList() : [];
                    for (const frame of frames) {
                        const comps = frame._getComponentList ? frame._getComponentList() : [];
                        for (const comp of comps) {
                            if (comp._type === 'Grid') {
                                const ds = comp.dataset;
                                if (!ds) continue;
                                const rowCnt = ds.rowcount;
                                for (let i = 0; i < rowCnt; i++) {
                                    const row = {};
                                    for (let j = 0; j < ds.colcount; j++) {
                                        const colId = ds.getColID(j);
                                        row[colId] = ds.getColumn(i, j);
                                    }
                                    items.push(row);
                                }
                            }
                        }
                    }
                } catch(e) {
                    return { error: e.toString(), items };
                }
                return { items };
            }
        """)

        if result.get('error'):
            print(f"[HIRA] Nexacro 직접 접근 실패: {result['error']}")
            # Fallback: 화면에 렌더링된 텍스트 추출
            items = await _extract_from_rendered_dom(page)
        else:
            raw_items = result.get('items', [])
            items = _parse_nexacro_rows(raw_items)

        await browser.close()
    return items


async def _extract_from_rendered_dom(page) -> list[dict]:
    """Nexacro Grid가 렌더링한 DOM에서 텍스트 추출 (fallback)."""
    items = []
    try:
        # Nexacro Grid는 div 기반으로 렌더링됨
        rows = await page.query_selector_all('div[id*="Grid"] div[class*="body-row"]')
        if not rows:
            # 일반 테이블 시도
            rows = await page.query_selector_all('table tr')

        for row in rows:
            text = await row.inner_text()
            cells = [c.strip() for c in text.split('\t') if c.strip()]
            if len(cells) >= 3:
                items.append({'raw_cells': cells})
    except Exception as e:
        print(f"[HIRA] DOM 추출 오류: {e}")

    # 스크린샷 저장 (디버그용)
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    await page.screenshot(path=os.path.join(DOWNLOAD_DIR, 'hira_screenshot.png'))
    print(f"[HIRA] 스크린샷 저장: {DOWNLOAD_DIR}/hira_screenshot.png")
    return items


def _parse_nexacro_rows(raw_rows: list[dict]) -> list[dict]:
    """Nexacro 데이터셋 행을 표준 형식으로 변환."""
    items = []
    for row in raw_rows:
        # 컬럼명은 실제 실행 후 확인 필요 - 일반적인 이름 시도
        title = row.get('TITLE') or row.get('title') or row.get('NTCE_NM') or ''
        date_str = row.get('REG_DT') or row.get('NTCE_DE') or row.get('date') or ''
        notice_id = row.get('SEQ') or row.get('NTCE_NO') or row.get('id') or str(hash(title))

        if not title:
            continue

        # 날짜 필터 (2026-03-01 이후)
        try:
            posted = datetime.strptime(date_str[:10], '%Y-%m-%d').date()
            if posted < FROM_DATE:
                continue
        except (ValueError, TypeError):
            pass  # 날짜 파싱 실패시 포함

        items.append({
            'source': 'hira',
            'notice_id': str(notice_id),
            'category': row.get('CTGRY') or row.get('category') or '',
            'title': title,
            'issued_no': row.get('NTCE_NO') or '',
            'posted_date': date_str[:10] if date_str else '',
            'detail_url': PAGE_URL,
        })
    return items


def crawl() -> list[dict]:
    """InfoBank 게시물 수집 (동기 래퍼)."""
    try:
        items = asyncio.run(_scrape_with_playwright())
        print(f"[HIRA] 총 {len(items)}건 수집")
        return items
    except ImportError:
        print("[HIRA] playwright가 설치되지 않았습니다. 'pip install playwright && playwright install chromium' 실행 필요")
        return []
    except Exception as e:
        print(f"[HIRA] 크롤링 오류: {e}")
        return []
