"""
보건복지부 훈령/예규/고시/지침 크롤러
https://www.mohw.go.kr/board.es?mid=a10409020000&bid=0026
"""
import re
import os
import requests
from bs4 import BeautifulSoup
from datetime import date, datetime

BASE_URL = "https://www.mohw.go.kr"
LIST_URL = f"{BASE_URL}/board.es?mid=a10409020000&bid=0026"
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'files', 'mohw')

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122 Safari/537.36"
}

FROM_DATE = date(2026, 3, 1)


def fetch_list_page(page: int) -> list[dict]:
    """목록 1페이지 크롤링. 2026-03-01 이전 날짜가 나오면 중단 신호 반환."""
    params = {"mid": "a10409020000", "bid": "0026", "act": "list", "nPage": page}
    resp = requests.get(LIST_URL, params=params, headers=HEADERS, verify=False, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, 'lxml')

    rows = soup.select("table.tstyle_list tbody tr")
    items = []
    stop = False

    for row in rows:
        # 미리보기 행 무시
        if row.get('id', '').startswith('preView'):
            continue

        tds = row.find_all('td')
        if len(tds) < 5:
            continue

        # 등록일 파싱
        date_str = tds[4].get_text(strip=True)  # YYYY-MM-DD
        try:
            posted = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            continue

        if posted < FROM_DATE:
            stop = True
            break

        # 제목 & 상세 링크
        title_td = row.find('td', attrs={'data-label': '제목'})
        if not title_td:
            continue
        a_tag = title_td.find('a')
        title = a_tag.get_text(strip=True)
        detail_url = BASE_URL + a_tag['href']

        # list_no 추출
        m = re.search(r'list_no=(\d+)', a_tag['href'])
        notice_id = m.group(1) if m else ''

        # 구분
        category = tds[1].get_text(strip=True) if len(tds) > 1 else ''
        # 발령번호
        issued_no = tds[2].get_text(strip=True) if len(tds) > 2 else ''

        items.append({
            'source': 'mohw',
            'notice_id': notice_id,
            'category': category,
            'title': title,
            'issued_no': issued_no,
            'posted_date': date_str,
            'detail_url': detail_url,
        })

    return items, stop


def fetch_attachments(notice_id: str) -> list[dict]:
    """상세 페이지에서 첨부파일 목록 반환."""
    url = f"{BASE_URL}/board.es?mid=a10409020000&bid=0026&act=view&list_no={notice_id}"
    resp = requests.get(url, headers=HEADERS, verify=False, timeout=10)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, 'lxml')

    attachments = []
    for li in soup.select("div.file ul.list li"):
        a_download = li.find('a', href=re.compile(r'/boardDownload\.es'))
        if not a_download:
            continue
        filename = a_download.get('title', '').strip()
        if not filename:
            # alt 텍스트에서 확장자만 있을 경우 img alt 사용
            img = li.find('img')
            filename = li.get_text(separator=' ').strip().split()[0] if img else ''
        download_url = BASE_URL + a_download['href']
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        attachments.append({
            'filename': filename,
            'file_type': ext,
            'download_url': download_url,
        })
    return attachments


def download_file(download_url: str, filename: str) -> str | None:
    """파일 다운로드 후 로컬 경로 반환."""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    # 파일명 안전처리
    safe_name = re.sub(r'[\\/:*?"<>|]', '_', filename)
    local_path = os.path.join(DOWNLOAD_DIR, safe_name)
    if os.path.exists(local_path):
        return local_path
    try:
        resp = requests.get(download_url, headers=HEADERS, verify=False, timeout=30, stream=True)
        resp.raise_for_status()
        with open(local_path, 'wb') as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)
        return local_path
    except Exception as e:
        print(f"[MOHW] 다운로드 실패: {filename} - {e}")
        return None


def search_notices(keyword: str, max_pages: int = 20) -> list[dict]:
    """키워드로 복지부 고시 검색 (최근 5년). 첨부파일 포함 반환."""
    from datetime import timedelta
    today      = date.today()
    start_date = today - timedelta(days=365 * 5)
    start_str  = start_date.strftime('%Y-%m-%d')
    end_str    = today.strftime('%Y-%m-%d')

    all_items = []
    seen = set()
    for page in range(1, max_pages + 1):
        params = {
            "mid": "a10409020000", "bid": "0026", "act": "list",
            "nPage": page,
            "searchKey": "title", "searchWord": keyword,
            # 날짜 범위 (board.es 표준 파라미터)
            "startDt":    start_str,
            "endDt":      end_str,
            "searchSdate": start_str,
            "searchEdate": end_str,
        }
        try:
            resp = requests.get(LIST_URL, params=params, headers=HEADERS, verify=False, timeout=10)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, 'lxml')
            rows = soup.select("table.tstyle_list tbody tr")
            if not rows:
                break
            found_any = False
            for row in rows:
                if row.get('id', '').startswith('preView'):
                    continue
                tds = row.find_all('td')
                if len(tds) < 5:
                    continue
                date_str = tds[4].get_text(strip=True)
                title_td = row.find('td', attrs={'data-label': '제목'})
                if not title_td:
                    continue
                a_tag = title_td.find('a')
                if not a_tag:
                    continue
                title = a_tag.get_text(strip=True)
                m = re.search(r'list_no=(\d+)', a_tag['href'])
                notice_id = m.group(1) if m else ''
                if not notice_id or notice_id in seen:
                    continue
                seen.add(notice_id)
                found_any = True
                category  = tds[1].get_text(strip=True) if len(tds) > 1 else ''
                issued_no = tds[2].get_text(strip=True) if len(tds) > 2 else ''
                detail_url = BASE_URL + a_tag['href']
                try:
                    atts = fetch_attachments(notice_id)
                except Exception:
                    atts = []
                all_items.append({
                    'notice_id':   notice_id,
                    'title':       title,
                    'category':    category,
                    'issued_no':   issued_no,
                    'posted_date': date_str,
                    'detail_url':  detail_url,
                    'attachments': atts,
                })
            if not found_any:
                break
        except Exception as e:
            print(f"[MOHW] 검색 오류 (page {page}): {e}")
            break
    return all_items


def crawl(max_pages: int = 10, known_ids: set = None) -> list[dict]:
    """2026-03-01 이후 게시물 수집. known_ids에 있는 항목 발견 시 조기 중단."""
    all_items = []
    for page in range(1, max_pages + 1):
        print(f"[MOHW] 페이지 {page} 크롤링 중...")
        try:
            items, stop = fetch_list_page(page)
        except Exception as e:
            print(f"[MOHW] 페이지 {page} 오류: {e}")
            break
        # 이미 DB에 있는 항목 발견 시 중단 (최신순 정렬이므로 이후는 모두 기존 항목)
        if known_ids:
            new_items = []
            for item in items:
                if item['notice_id'] in known_ids:
                    stop = True
                    break
                new_items.append(item)
            all_items.extend(new_items)
        else:
            all_items.extend(items)
        if stop:
            if known_ids:
                print(f"[MOHW] 기존 항목 감지 - 수집 중단 (신규 {len(all_items)}건)")
            else:
                print(f"[MOHW] 2026-03-01 이전 날짜 감지 - 수집 중단")
            break
    print(f"[MOHW] 총 {len(all_items)}건 수집")
    return all_items
