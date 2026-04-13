"""Flask 웹 서버 + 4시간 자동 크롤링 스케줄러"""
import math
import subprocess
import sys
import os
import threading
from flask import Flask, render_template, jsonify, request
from storage.database import (
    init_db, get_notices, get_notice_with_attachments,
    count_notices, get_unread_alerts, mark_alerts_read, add_alert,
    upsert_notice, save_attachment, update_summary,
    get_verification_with_modules, update_verification, upsert_module_check,
    get_dashboard_stats, get_prev_notice_attachment, get_existing_notice_ids,
    find_mohw_notice_by_issued_no,
    VERIFICATION_STATUSES, VERIFICATION_PRIORITIES, EMR_MODULES
)


def _do_crawl(app_ctx):
    """백그라운드 크롤링 (새 게시물 → alerts 등록)."""
    with app_ctx:
        import urllib3
        urllib3.disable_warnings()
        from scraper import mohw, hira

        new_count = 0
        try:
            known_mohw = get_existing_notice_ids('mohw')
            for item in mohw.crawl(known_ids=known_mohw):
                db_id, is_new = upsert_notice(
                    item['source'], item['notice_id'], item['category'],
                    item['title'], item['issued_no'], item['posted_date'], item['detail_url']
                )
                if is_new:
                    new_count += 1
                    try:
                        for att in mohw.fetch_attachments(item['notice_id']):
                            save_attachment(db_id, att['filename'], att['file_type'], att['download_url'])
                    except Exception:
                        pass
                    add_alert(db_id)
        except Exception as e:
            print(f"[스케줄] MOHW 오류: {e}")

        try:
            for item in hira.crawl():
                db_id, is_new = upsert_notice(
                    item['source'], item['notice_id'], item['category'],
                    item['title'], item['issued_no'], item['posted_date'], item['detail_url']
                )
                if is_new:
                    new_count += 1
                    try:
                        for att in hira.fetch_attachments(item['notice_id']):
                            save_attachment(db_id, att['filename'], att['file_type'], att['download_url'])
                    except Exception:
                        pass
                    add_alert(db_id)
        except Exception as e:
            print(f"[스케줄] HIRA 오류: {e}")

        print(f"[스케줄] 크롤링 완료 — 신규 {new_count}건")


def _extract_search_keyword(title: str) -> str:
    """고시 제목에서 핵심 검색 키워드 추출."""
    import re
    # 불용어 제거 (고시 제목에 자주 나오는 행정 단어)
    stopwords = {'관한', '위한', '대한', '따른', '고시', '개정', '공고', '훈령', '예규',
                 '지침', '안내', '통보', '알림', '시행', '관련', '사항', '및', '에', '의',
                 '을', '를', '이', '가', '은', '는', '로', '으로', '에서', '에게'}
    # 특수문자·괄호 제거
    clean = re.sub(r'[\[\]()（）「」『』<>《》【】\-·~\s]+', ' ', title).strip()
    words = [w for w in clean.split() if len(w) >= 2 and w not in stopwords]
    # 앞 2개 핵심 단어 사용
    return ' '.join(words[:2]) if words else title[:10]


def create_app():
    app = Flask(__name__, template_folder='templates')
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    init_db()

    # ── 4시간 스케줄러 ──────────────────────────────────────
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler(daemon=True)
        scheduler.add_job(
            lambda: threading.Thread(target=_do_crawl, args=(app.app_context(),), daemon=True).start(),
            trigger='interval',
            hours=4,
            id='auto_crawl',
            replace_existing=True
        )
        scheduler.start()
        print("[스케줄] 4시간 자동 크롤링 시작")
    except Exception as e:
        print(f"[스케줄] 스케줄러 초기화 실패: {e}")

    # ── 라우트 ──────────────────────────────────────────────

    @app.route('/health')
    def health():
        try:
            from storage.database import get_dashboard_stats
            get_dashboard_stats()
            return jsonify({'status': 'ok'}), 200
        except Exception as e:
            return jsonify({'status': 'error', 'detail': str(e)}), 500

    @app.route('/')
    def index():
        return render_template('index.html')

    @app.route('/api/notices')
    def api_notices():
        source     = request.args.get('source')
        from_date  = request.args.get('from_date', '2026-03-01')
        page       = int(request.args.get('page', 1))
        page_size  = int(request.args.get('page_size', 50))
        has_summary_param = request.args.get('has_summary')  # 'true'|'false'|''
        has_summary = None
        if has_summary_param == 'true':
            has_summary = True
        elif has_summary_param == 'false':
            has_summary = False
        v_status   = request.args.get('v_status') or None
        v_priority = request.args.get('v_priority') or None
        category   = request.args.get('category') or None
        to_date    = request.args.get('to_date') or None
        keyword    = request.args.get('keyword') or None

        offset = (page - 1) * page_size
        total  = count_notices(source=source, from_date=from_date, to_date=to_date,
                               has_summary=has_summary, v_status=v_status,
                               v_priority=v_priority, category=category, keyword=keyword)
        notices = get_notices(source=source, from_date=from_date, to_date=to_date,
                              limit=page_size, offset=offset, has_summary=has_summary,
                              v_status=v_status, v_priority=v_priority, category=category,
                              keyword=keyword)
        pages  = max(1, math.ceil(total / page_size))

        return jsonify({'notices': notices, 'total': total,
                        'page': page, 'page_size': page_size, 'pages': pages})

    @app.route('/api/notices/<int:notice_id>')
    def api_notice_detail(notice_id):
        import re
        data = get_notice_with_attachments(notice_id)
        data['verification'] = get_verification_with_modules(notice_id)
        # HIRA 고시이고 첨부파일 없으면 복지부 고시번호로 매칭
        if data['source'] == 'hira' and not data.get('attachments'):
            m = re.search(r'제(\d{4}-\d+호)', data.get('title', ''))
            if m:
                matched = find_mohw_notice_by_issued_no(m.group(1))
                if matched:
                    data['mohw_match'] = matched
        return jsonify(data)

    @app.route('/api/notices/<int:notice_id>/verification', methods=['GET'])
    def api_get_verification(notice_id):
        return jsonify(get_verification_with_modules(notice_id))

    @app.route('/api/notices/<int:notice_id>/verification', methods=['PUT'])
    def api_put_verification(notice_id):
        body = request.get_json() or {}
        status   = body.get('status')
        priority = body.get('priority')
        memo     = body.get('memo')
        if status and status not in VERIFICATION_STATUSES:
            return jsonify({'error': 'invalid status'}), 400
        if priority and priority not in VERIFICATION_PRIORITIES:
            return jsonify({'error': 'invalid priority'}), 400
        update_verification(notice_id, status=status, priority=priority, memo=memo)
        return jsonify({'ok': True})

    @app.route('/api/notices/<int:notice_id>/modules', methods=['PATCH'])
    def api_patch_modules(notice_id):
        body = request.get_json() or {}
        for module, checked in body.items():
            if module in EMR_MODULES:
                upsert_module_check(notice_id, module, checked)
        return jsonify({'ok': True})

    @app.route('/api/dashboard')
    def api_dashboard():
        return jsonify(get_dashboard_stats())

    @app.route('/api/alerts')
    def api_alerts():
        return jsonify(get_unread_alerts())

    @app.route('/api/alerts/read', methods=['POST'])
    def api_alerts_read():
        ids = request.json.get('ids', [])
        mark_alerts_read(ids)
        return jsonify({'ok': True})

    @app.route('/api/notices/<int:notice_id>/summarize', methods=['POST'])
    def api_summarize_notice(notice_id):
        """개별 고시 AI 분석 — 선택 첨부파일 기준, 이전 고시 비교 (개요 제외)"""
        import os
        from analyzer.summarizer import extract_text_from_file, summarize as do_summarize
        from scraper import mohw as mohw_scraper
        from storage.database import get_conn

        body         = request.get_json(silent=True) or {}
        att_id       = body.get('att_id')       # 선택한 첨부파일 ID
        src_notice_id = body.get('src_notice_id')  # HIRA→복지부 매칭 notice id

        data = get_notice_with_attachments(notice_id)

        # 분석 대상 첨부파일 결정
        # 1) att_id 지정 시 해당 파일 (복지부 매칭 파일 포함)
        # 2) 미지정 시 현재 고시의 첫 번째 PDF
        att = None
        att_source = data['source']  # 파일 다운로드에 사용할 출처

        if att_id:
            with get_conn() as conn:
                row = conn.execute("SELECT * FROM attachments WHERE id=?", (att_id,)).fetchone()
                if row:
                    att = dict(row)
                    # 복지부 매칭(src_notice_id)이면 복지부 다운로더 사용
                    if src_notice_id:
                        att_source = 'mohw'
        else:
            pdfs = [a for a in data.get('attachments', []) if a.get('file_type') == 'pdf']
            if pdfs:
                att = pdfs[0]

        if not att:
            return jsonify({'error': '분석 가능한 첨부파일이 없습니다'}), 400

        local_path = att.get('local_path')
        if not local_path or not os.path.exists(local_path):
            if att_source == 'hira':
                from scraper import hira as hira_scraper
                local_path = hira_scraper.download_file(att['download_url'], att['filename'])
            else:
                local_path = mohw_scraper.download_file(att['download_url'], att['filename'])
            if local_path:
                with get_conn() as conn:
                    conn.execute("UPDATE attachments SET local_path=? WHERE id=?",
                                 (local_path, att['id']))

        if not local_path or not os.path.exists(local_path):
            return jsonify({'error': '첨부파일 다운로드 실패'}), 400

        text = extract_text_from_file(local_path)
        if not text.strip():
            return jsonify({'error': '첨부파일에서 텍스트를 추출할 수 없습니다'}), 400

        # 이전 고시 텍스트 — 같은 출처(복지부 매칭 시 mohw)에서 직전 날짜 PDF
        prev_text  = ''
        cmp_source = 'mohw' if src_notice_id else data['source']
        cmp_date   = data['posted_date']
        cmp_id     = src_notice_id if src_notice_id else notice_id
        prev_att   = get_prev_notice_attachment(cmp_source, cmp_date, cmp_id)
        if prev_att:
            prev_local = prev_att.get('local_path')
            if not prev_local or not os.path.exists(prev_local):
                prev_local = mohw_scraper.download_file(
                    prev_att['download_url'], prev_att['filename'])
                if prev_local:
                    with get_conn() as conn:
                        conn.execute("UPDATE attachments SET local_path=? WHERE id=?",
                                     (prev_local, prev_att['id']))
            if prev_local and os.path.exists(prev_local):
                try:
                    prev_text = extract_text_from_file(prev_local)
                except Exception:
                    pass

        summary = do_summarize(data['title'], text, prev_text=prev_text)
        update_summary(notice_id, summary)
        return jsonify({'summary': summary})

    @app.route('/api/web-search')
    def api_web_search():
        """복지부 사이트에서 키워드로 고시 직접 검색."""
        keyword = request.args.get('keyword', '').strip()
        if not keyword:
            return jsonify({'error': '키워드를 입력하세요'}), 400
        from scraper import mohw as mohw_scraper
        import urllib3; urllib3.disable_warnings()
        try:
            results = mohw_scraper.search_notices(keyword, max_pages=3)
            return jsonify({'results': results})
        except Exception as e:
            return jsonify({'error': str(e)}), 500

    @app.route('/api/web-search/download', methods=['POST'])
    def api_web_search_download():
        """검색 결과에서 선택한 첨부파일 다운로드 후 경로 반환."""
        body = request.get_json() or {}
        download_url = body.get('download_url', '')
        filename     = body.get('filename', 'prev.pdf')
        if not download_url:
            return jsonify({'error': 'download_url 필요'}), 400
        from scraper import mohw as mohw_scraper
        import urllib3; urllib3.disable_warnings()
        local_path = mohw_scraper.download_file(download_url, filename)
        if not local_path:
            return jsonify({'error': '다운로드 실패'}), 500
        return jsonify({'local_path': local_path, 'filename': filename})

    @app.route('/api/analyze-upload', methods=['POST'])
    def api_analyze_upload():
        """파일 업로드 상세 비교 분석."""
        import tempfile, uuid
        from analyzer.summarizer import extract_text_from_file, compare_files

        upload_files_list = request.files.getlist('file')
        if not upload_files_list or not any(f.filename for f in upload_files_list):
            return jsonify({'error': '파일이 없습니다'}), 400

        title_input = request.form.get('title', '')
        prev_notice_id = request.form.get('prev_notice_id', type=int)

        tmp_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               'data', 'files', 'upload')
        os.makedirs(tmp_dir, exist_ok=True)

        # 다중 신규 파일 텍스트 추출
        new_parts = []
        for uf in upload_files_list:
            if not uf.filename:
                continue
            ext = uf.filename.rsplit('.', 1)[-1].lower()
            tmp_path = os.path.join(tmp_dir, f"upload_{uuid.uuid4().hex}.{ext}")
            try:
                uf.save(tmp_path)
                t = extract_text_from_file(tmp_path)
                if t.strip():
                    new_parts.append(f"[ {uf.filename} ]\n{t}")
            finally:
                try: os.remove(tmp_path)
                except Exception: pass

        new_text = '\n\n'.join(new_parts)
        if not new_text.strip():
            return jsonify({'error': '파일에서 텍스트를 추출할 수 없습니다'}), 400

        title = title_input or ', '.join(f.filename for f in upload_files_list if f.filename)

        # 이전 고시 텍스트 가져오기
        prev_text = ''
        prev_info = {}
        if prev_notice_id:
            prev_data = get_notice_with_attachments(prev_notice_id)
            prev_pdfs = [a for a in prev_data.get('attachments', []) if a.get('file_type') == 'pdf']
            if prev_pdfs:
                from scraper import mohw as mohw_scraper
                from scraper import hira as hira_scraper
                pa = prev_pdfs[0]
                pl = pa.get('local_path')
                if not pl or not os.path.exists(pl):
                    dl = mohw_scraper.download_file if prev_data['source'] == 'mohw' else hira_scraper.download_file
                    pl = dl(pa['download_url'], pa['filename'])
                    if pl:
                        from storage.database import get_conn
                        with get_conn() as conn:
                            conn.execute("UPDATE attachments SET local_path=? WHERE id=?",
                                         (pl, pa['id']))
                if pl and os.path.exists(pl):
                    try:
                        prev_text = extract_text_from_file(pl)
                    except Exception:
                        pass
            prev_info = {
                'id': prev_data['id'],
                'title': prev_data['title'],
                'posted_date': prev_data['posted_date'],
                'source': prev_data['source'],
            }

        # 웹 검색으로 선택한 이전 고시 파일 처리
        prev_web_url  = request.form.get('prev_web_url', '')
        prev_web_name = request.form.get('prev_web_name', 'prev.pdf')
        if prev_web_url and not prev_text:
            from scraper import mohw as mohw_scraper
            import urllib3; urllib3.disable_warnings()
            pl = mohw_scraper.download_file(prev_web_url, prev_web_name)
            if pl and os.path.exists(pl):
                try:
                    prev_text = extract_text_from_file(pl)
                    prev_info = {'title': prev_web_name.rsplit('.', 1)[0]}
                except Exception:
                    pass

        # 다중 이전 파일 직접 업로드 처리
        prev_files_list = request.files.getlist('prev_file')
        if prev_files_list and not prev_text:
            prev_parts = []
            for pf in prev_files_list:
                if not pf.filename:
                    continue
                prev_ext = pf.filename.rsplit('.', 1)[-1].lower()
                prev_tmp = os.path.join(tmp_dir, f"prev_{uuid.uuid4().hex}.{prev_ext}")
                try:
                    pf.save(prev_tmp)
                    t = extract_text_from_file(prev_tmp)
                    if t.strip():
                        prev_parts.append(f"[ {pf.filename} ]\n{t}")
                finally:
                    try: os.remove(prev_tmp)
                    except Exception: pass
            if prev_parts:
                prev_text = '\n\n'.join(prev_parts)
                prev_info = {'title': ', '.join(f.filename for f in prev_files_list if f.filename)}

        # 이전 고시가 없으면 제목 키워드로 복지부 사이트에서 자동 검색
        auto_searched = False
        if not prev_text:
            import urllib3; urllib3.disable_warnings()
            from scraper import mohw as mohw_scraper
            keyword = _extract_search_keyword(title)
            print(f"[자동검색] 키워드: {keyword}")
            try:
                candidates = mohw_scraper.search_notices(keyword, max_pages=5)
                for cand in candidates:
                    # 같은 고시가 아닌 것 중 PDF 있는 첫 번째 선택
                    pdfs = [a for a in cand.get('attachments', []) if a.get('file_type') == 'pdf']
                    if not pdfs:
                        continue
                    pl = mohw_scraper.download_file(pdfs[0]['download_url'], pdfs[0]['filename'])
                    if not pl or not os.path.exists(pl):
                        continue
                    try:
                        prev_text = extract_text_from_file(pl)
                    except Exception:
                        continue
                    if prev_text.strip():
                        prev_info = {
                            'title':       cand['title'],
                            'posted_date': cand['posted_date'],
                            'source':      'mohw',
                            'auto':        True,
                        }
                        auto_searched = True
                        print(f"[자동검색] 이전 고시 발견: {cand['title'][:50]}")
                        break
            except Exception as e:
                print(f"[자동검색] 오류: {e}")

        result = compare_files(title, new_text, prev_text)
        return jsonify({
            'result': result, 'title': title, 'prev_info': prev_info,
            'auto_searched': auto_searched,
        })

    @app.route('/api/analyze-emr-only', methods=['POST'])
    def api_analyze_emr_only():
        """단독 파일 EMR 적용 사항 분석 (이전 고시 비교 없음)."""
        import tempfile, uuid
        from analyzer.summarizer import extract_text_from_file, analyze_emr

        upload_files_list = request.files.getlist('file')
        if not upload_files_list or not any(f.filename for f in upload_files_list):
            return jsonify({'error': '파일이 없습니다'}), 400

        title_input = request.form.get('title', '')
        tmp_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               'data', 'files', 'upload')
        os.makedirs(tmp_dir, exist_ok=True)

        parts = []
        for uf in upload_files_list:
            if not uf.filename:
                continue
            ext = uf.filename.rsplit('.', 1)[-1].lower()
            tmp_path = os.path.join(tmp_dir, f"emr_{uuid.uuid4().hex}.{ext}")
            try:
                uf.save(tmp_path)
                t = extract_text_from_file(tmp_path)
                if t.strip():
                    parts.append(f"[ {uf.filename} ]\n{t}")
            finally:
                try: os.remove(tmp_path)
                except Exception: pass

        new_text = '\n\n'.join(parts)
        if not new_text.strip():
            return jsonify({'error': '파일에서 텍스트를 추출할 수 없습니다'}), 400

        title = title_input or ', '.join(f.filename for f in upload_files_list if f.filename)
        result = analyze_emr(title, new_text)
        return jsonify({'result': result, 'title': title})

    @app.route('/api/run/<task>', methods=['POST'])
    def api_run_task(task):
        if task not in ('crawl', 'summarize', 'all'):
            return jsonify({'error': 'invalid task'}), 400
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        main_py = os.path.join(project_root, 'main.py')
        proc = subprocess.Popen(
            [sys.executable, main_py, task],
            cwd=project_root,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace'
        )
        output, _ = proc.communicate()
        return jsonify({'exit_code': proc.returncode, 'output': output})

    return app
