"""
고시 통합 수집/분석 실행 스크립트

사용법:
  python main.py crawl        # 크롤링만 실행
  python main.py summarize    # 미요약 게시물 요약 (PDF 첨부파일 있는 경우)
  python main.py all          # 크롤링 + 요약
  python main.py serve        # 웹 서버 실행
"""
import sys
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from storage.database import init_db, upsert_notice, save_attachment, update_summary, get_notices, get_conn
from scraper import mohw, hira
from analyzer.summarizer import extract_text_from_file, summarize


def run_crawl() -> None:
    init_db()
    new_total: int = 0

    # 보건복지부
    mohw_items = mohw.crawl()
    for item in mohw_items:
        db_id, is_new = upsert_notice(
            source=item['source'],
            notice_id=item['notice_id'],
            category=item['category'],
            title=item['title'],
            issued_no=item['issued_no'],
            posted_date=item['posted_date'],
            detail_url=item['detail_url'],
        )
        if is_new:
            new_total += 1
            # 첨부파일 수집
            try:
                attachments = mohw.fetch_attachments(item['notice_id'])
                for att in attachments:
                    save_attachment(db_id, att['filename'], att['file_type'], att['download_url'])
            except Exception as e:
                print(f"[MOHW] 첨부파일 오류 {item['notice_id']}: {e}")

    # HIRA
    hira_items = hira.crawl()
    for item in hira_items:
        db_id, is_new = upsert_notice(
            source=item['source'],
            notice_id=item['notice_id'],
            category=item['category'],
            title=item['title'],
            issued_no=item['issued_no'],
            posted_date=item['posted_date'],
            detail_url=item['detail_url'],
        )
        if is_new:
            new_total += 1

    print(f"\n신규 수집: {new_total}건")


def run_summarize() -> None:
    """미요약 게시물의 첨부파일(PDF 우선, HWPX 차선)을 다운로드·추출 후 AI 요약."""
    import os
    # 게시물별로 최적 첨부파일 1개 선택: PDF > HWPX > 기타
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                n.id   AS notice_db_id,
                n.title,
                a.id   AS att_id,
                a.download_url,
                a.local_path,
                a.filename,
                a.file_type
            FROM notices n
            JOIN attachments a ON a.notice_id = n.id
            WHERE n.summary IS NULL
              AND a.file_type IN ('pdf', 'hwpx')
              AND n.posted_date >= '2026-03-01'
            ORDER BY n.posted_date DESC,
                     CASE a.file_type WHEN 'pdf' THEN 0 ELSE 1 END
        """).fetchall()

    # 게시물 ID 기준 중복 제거 (첫 번째 = 최적 파일)
    seen = set()
    targets = []
    for row in rows:
        if row['notice_db_id'] not in seen:
            seen.add(row['notice_db_id'])
            targets.append(row)

    print(f"요약 대상: {len(targets)}건")
    for row in targets:
        print(f"  처리 중: {row['title'][:50]} [{row['file_type']}]")
        local_path = row['local_path']
        if not local_path or not os.path.exists(local_path):
            local_path = mohw.download_file(row['download_url'], row['filename'])
            if local_path:
                with get_conn() as conn:
                    conn.execute(
                        "UPDATE attachments SET local_path=? WHERE id=?",
                        (local_path, row['att_id'])
                    )

        if not local_path:
            print(f"    → 다운로드 실패, 건너뜀")
            continue

        text = extract_text_from_file(local_path)
        if not text.strip():
            print(f"    → 텍스트 추출 실패, 건너뜀")
            continue

        print(f"    텍스트 {len(text)}자 추출 → 요약 중...")
        summary = summarize(row['title'], text)
        update_summary(row['notice_db_id'], summary)
        print(f"    → 완료")


def run_serve() -> None:
    import os
    from web.app import create_app
    app = create_app()
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'all'

    if cmd == 'crawl':
        run_crawl()
    elif cmd == 'summarize':
        run_summarize()
    elif cmd == 'all':
        run_crawl()
        run_summarize()
    elif cmd == 'serve':
        run_serve()
    else:
        print(__doc__)
