"""
건강보험심사평가원 크롤러
https://biz.hira.or.kr/popup.ndo?formname=qya_bizcom%3A%3AInfoBank.xfdl&framename=InfoBank

Playwright로 InfoBank 팝업을 열고, Nexacro가 내부적으로 호출하는
SSV API 응답을 가로채어 업무공지·자료 수집.
첨부파일도 동일 브라우저 세션에서 개별 고시 상세 진입 후 수집.
"""
import re
import os
import requests
import urllib3
from datetime import date, datetime

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'files', 'hira')
FROM_DATE = date(2026, 3, 1)
BASE_URL  = "https://biz.hira.or.kr"
INFOBANK_URL = (
    BASE_URL
    + "/popup.ndo?formname=qya_bizcom%3A%3AInfoBank.xfdl&framename=InfoBank"
)

# Nexacro가 내부 호출하는 SSV 엔드포인트 → 카테고리명 매핑
SSV_ENDPOINT_MAP = {
    '/qya/main/noticeList.ndo':    '업무공지',
    '/qya/main/carInformList.ndo': '자료안내',
}


# ══════════════════════════════════════════════════════
#  SSV 파싱
# ══════════════════════════════════════════════════════

def _parse_ssv_board(raw: bytes, category: str) -> list[dict]:
    """
    Nexacro SSV 형식 파싱.
    \x1e = Record Separator, \x1f = Unit Separator
    """
    RS = b'\x1e'
    US = b'\x1f'
    items = []

    try:
        records = raw.split(RS)
        cols = []
        for rec in records:
            rec_str = rec.decode('utf-8', errors='ignore')

            if rec_str.startswith('_RowType_'):
                col_defs = rec.split(US)
                cols = []
                for cd in col_defs[1:]:
                    col_name = cd.decode('utf-8', errors='ignore').split(':')[0].strip()
                    if col_name:
                        cols.append(col_name)
                continue

            if not rec_str.startswith('N') or not cols:
                continue

            vals = rec.split(US)
            vals = [v.decode('utf-8', errors='ignore').strip() for v in vals[1:]]
            row = {cols[i]: vals[i] if i < len(vals) else '' for i in range(len(cols))}

            raw_date = row.get('regDate', '') or row.get('REG_DATE', '')
            posted_date = ''
            if raw_date and len(raw_date) >= 8:
                try:
                    dt = datetime.strptime(raw_date[:8], '%Y%m%d')
                    if dt.date() < FROM_DATE:
                        continue
                    posted_date = dt.strftime('%Y-%m-%d')
                except ValueError:
                    pass

            title = (row.get('title') or row.get('TITLE') or row.get('nttSj') or '').strip()
            if not title:
                continue

            bbs_id  = row.get('bbsId', '')
            item_id = row.get('itemId', '') or row.get('nttId', '')
            notice_id = f"{bbs_id}_{item_id}" if item_id else str(abs(hash(title + posted_date)))

            items.append({
                'source':      'hira',
                'notice_id':   notice_id,
                'category':    category,
                'title':       title,
                'issued_no':   '',
                'posted_date': posted_date,
                'detail_url':  INFOBANK_URL,
            })
    except Exception as e:
        print(f"[HIRA] SSV 파싱 오류: {e}")

    return items


# ══════════════════════════════════════════════════════
#  파일 다운로드 (requests)
# ══════════════════════════════════════════════════════

def _get_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': BASE_URL + '/',
    })
    try:
        session.get(BASE_URL, verify=False, timeout=15)
    except Exception:
        pass
    return session


def download_file(download_url: str, filename: str) -> str | None:
    """파일 다운로드 후 로컬 경로 반환."""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    safe_name = re.sub(r'[\\/:*?"<>|]', '_', filename)
    local_path = os.path.join(DOWNLOAD_DIR, safe_name)
    if os.path.exists(local_path):
        return local_path
    try:
        resp = _get_session().get(download_url, verify=False, timeout=30, stream=True)
        resp.raise_for_status()
        with open(local_path, 'wb') as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
        return local_path
    except Exception as e:
        print(f"[HIRA] 다운로드 실패: {filename} - {e}")
        return None


# ══════════════════════════════════════════════════════
#  첨부파일 수집 (Playwright)
# ══════════════════════════════════════════════════════

def fetch_attachments(notice_id: str) -> list[dict]:
    """
    HIRA 개별 고시 첨부파일 수집 (Playwright).
    notice_id 형식: {bbsId}_{itemId}
    """
    parts = notice_id.rsplit('_', 1)
    if len(parts) != 2:
        return []
    bbs_id, item_id = parts[0], parts[1]

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[HIRA] Playwright 미설치 - 첨부파일 수집 불가")
        return []

    attachments = []
    detail_url = (
        f"{BASE_URL}/popup.ndo"
        f"?formname=qya_bizcom%3A%3AInfoBank.xfdl"
        f"&framename=InfoBank"
        f"&bbsId={bbs_id}&itemId={item_id}"
    )

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(ignore_https_errors=True)
            page = ctx.new_page()
            page.goto(detail_url, timeout=30000, wait_until='networkidle')
            page.wait_for_timeout(3000)

            links = page.evaluate("""() => {
                const results = [];
                document.querySelectorAll('a[href]').forEach(a => {
                    const href = a.href || '';
                    const text = a.textContent.trim();
                    if (href && (href.includes('down') || href.includes('file') ||
                        /\\.(pdf|hwp|hwpx|xlsx|xls|docx|zip)$/i.test(href) ||
                        /\\.(pdf|hwp|hwpx|xlsx|xls|docx|zip)$/i.test(text))) {
                        results.push({url: href, text: text});
                    }
                });
                return results;
            }""")

            for lnk in links:
                url  = lnk.get('url', '')
                text = lnk.get('text', '파일')
                if not url or url.startswith('javascript'):
                    continue
                m   = re.search(r'\.(pdf|hwp|hwpx|xlsx|xls|docx|zip)', url + ' ' + text, re.I)
                ext = m.group(1).lower() if m else 'bin'
                filename = text if text else f"hira_{item_id}.{ext}"
                attachments.append({'filename': filename, 'file_type': ext, 'download_url': url})

            browser.close()
            print(f"[HIRA] {item_id} 첨부파일 {len(attachments)}건")
    except Exception as e:
        print(f"[HIRA] 첨부파일 수집 오류 ({item_id}): {e}")

    return attachments


# ══════════════════════════════════════════════════════
#  크롤링 (InfoBank SSV API 직접 호출)
# ══════════════════════════════════════════════════════

def crawl() -> list[dict]:
    """
    HIRA InfoBank SSV API를 직접 호출하여 목록 수집.
    (Nexacro 앱이 내부적으로 호출하는 동일한 엔드포인트 사용)
    """
    session   = _get_session()
    all_items: list[dict] = []
    seen_ids:  set[str]   = set()

    for endpoint, category in SSV_ENDPOINT_MAP.items():
        try:
            resp = session.post(
                BASE_URL + endpoint,
                verify=False, timeout=15,
                data='',
                headers={'Content-Type': 'application/x-www-form-urlencoded'},
            )
            resp.raise_for_status()
            items = _parse_ssv_board(resp.content, category)
            new   = [i for i in items if i['notice_id'] not in seen_ids]
            for i in new:
                seen_ids.add(i['notice_id'])
            all_items.extend(new)
            print(f"[HIRA] {category}: {len(new)}건 ({endpoint})")
        except Exception as e:
            print(f"[HIRA] {endpoint} 오류: {e}")

    print(f"[HIRA] 총 {len(all_items)}건 수집")
    return all_items
