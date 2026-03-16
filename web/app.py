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
    upsert_notice, save_attachment,
    get_verification_with_modules, update_verification, upsert_module_check,
    get_dashboard_stats, VERIFICATION_STATUSES, VERIFICATION_PRIORITIES, EMR_MODULES
)


def _do_crawl(app_ctx):
    """백그라운드 크롤링 (새 게시물 → alerts 등록)."""
    with app_ctx:
        import urllib3
        urllib3.disable_warnings()
        from scraper import mohw, hira

        new_count = 0
        try:
            for item in mohw.crawl():
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
                    add_alert(db_id)
        except Exception as e:
            print(f"[스케줄] HIRA 오류: {e}")

        print(f"[스케줄] 크롤링 완료 — 신규 {new_count}건")


def create_app():
    app = Flask(__name__, template_folder='templates')
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

        offset = (page - 1) * page_size
        total  = count_notices(source=source, from_date=from_date, has_summary=has_summary,
                               v_status=v_status, v_priority=v_priority)
        notices = get_notices(source=source, from_date=from_date,
                              limit=page_size, offset=offset, has_summary=has_summary,
                              v_status=v_status, v_priority=v_priority)
        pages  = max(1, math.ceil(total / page_size))

        return jsonify({'notices': notices, 'total': total,
                        'page': page, 'page_size': page_size, 'pages': pages})

    @app.route('/api/notices/<int:notice_id>')
    def api_notice_detail(notice_id):
        data = get_notice_with_attachments(notice_id)
        data['verification'] = get_verification_with_modules(notice_id)
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
