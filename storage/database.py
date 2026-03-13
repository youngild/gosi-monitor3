"""SQLite 데이터베이스 관리"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'notices.db')

# 검증 시스템 상수
VERIFICATION_STATUSES  = ('pending', 'reviewing', 'applied', 'na')
VERIFICATION_PRIORITIES = ('urgent', 'normal', 'low')
EMR_MODULES = ('billing', 'prescription', 'emr_record', 'payment', 'report', 'ocs', 'lab')


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS notices (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                source      TEXT NOT NULL,
                notice_id   TEXT NOT NULL,
                category    TEXT,
                title       TEXT NOT NULL,
                issued_no   TEXT,
                posted_date TEXT NOT NULL,
                detail_url  TEXT,
                summary     TEXT,
                created_at  TEXT DEFAULT (datetime('now','localtime')),
                UNIQUE(source, notice_id)
            );

            CREATE TABLE IF NOT EXISTS attachments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                notice_id   INTEGER NOT NULL REFERENCES notices(id),
                filename    TEXT NOT NULL,
                file_type   TEXT,
                download_url TEXT,
                local_path  TEXT,
                created_at  TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                notice_id   INTEGER NOT NULL REFERENCES notices(id),
                is_read     INTEGER DEFAULT 0,
                created_at  TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS verifications (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                notice_id   INTEGER NOT NULL UNIQUE REFERENCES notices(id),
                status      TEXT NOT NULL DEFAULT 'pending',
                priority    TEXT NOT NULL DEFAULT 'normal',
                memo        TEXT DEFAULT '',
                updated_at  TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS module_checks (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                verification_id INTEGER NOT NULL REFERENCES verifications(id),
                module_name     TEXT NOT NULL,
                is_checked      INTEGER DEFAULT 0,
                UNIQUE(verification_id, module_name)
            );
        """)


def upsert_notice(source, notice_id, category, title, issued_no, posted_date, detail_url):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM notices WHERE source=? AND notice_id=?",
            (source, notice_id)
        ).fetchone()
        if row:
            return row['id'], False
        cur = conn.execute(
            """INSERT INTO notices (source, notice_id, category, title, issued_no, posted_date, detail_url)
               VALUES (?,?,?,?,?,?,?)""",
            (source, notice_id, category, title, issued_no, posted_date, detail_url)
        )
        return cur.lastrowid, True


def add_alert(notice_db_id):
    with get_conn() as conn:
        conn.execute("INSERT INTO alerts (notice_id) VALUES (?)", (notice_db_id,))


def get_unread_alerts():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT a.id, a.notice_id, a.created_at,
                   n.title, n.source, n.category, n.posted_date
            FROM alerts a
            JOIN notices n ON n.id = a.notice_id
            WHERE a.is_read = 0
            ORDER BY a.created_at DESC
        """).fetchall()
        return [dict(r) for r in rows]


def mark_alerts_read(alert_ids: list):
    if not alert_ids:
        return
    placeholders = ','.join('?' * len(alert_ids))
    with get_conn() as conn:
        conn.execute(f"UPDATE alerts SET is_read=1 WHERE id IN ({placeholders})", alert_ids)


def save_attachment(notice_db_id, filename, file_type, download_url, local_path=None):
    with get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO attachments (notice_id, filename, file_type, download_url, local_path)
               VALUES (?,?,?,?,?)""",
            (notice_db_id, filename, file_type, download_url, local_path)
        )


def update_summary(notice_db_id, summary):
    with get_conn() as conn:
        conn.execute("UPDATE notices SET summary=? WHERE id=?", (summary, notice_db_id))


def count_notices(source=None, from_date='2026-03-01', has_summary=None,
                  v_status=None, v_priority=None):
    sql = """SELECT COUNT(*) FROM notices n
             LEFT JOIN verifications v ON v.notice_id = n.id
             WHERE n.posted_date >= ?"""
    params = [from_date]
    if source:
        sql += " AND n.source=?"
        params.append(source)
    if has_summary is True:
        sql += " AND n.summary IS NOT NULL"
    elif has_summary is False:
        sql += " AND n.summary IS NULL"
    if v_status:
        if v_status == 'pending':
            sql += " AND (v.status='pending' OR v.status IS NULL)"
        else:
            sql += " AND v.status=?"
            params.append(v_status)
    if v_priority:
        sql += " AND v.priority=?"
        params.append(v_priority)
    with get_conn() as conn:
        return conn.execute(sql, params).fetchone()[0]


def get_notices(source=None, from_date='2026-03-01', limit=50, offset=0,
                has_summary=None, v_status=None, v_priority=None):
    sql = """SELECT n.*, v.status AS v_status, v.priority AS v_priority
             FROM notices n
             LEFT JOIN verifications v ON v.notice_id = n.id
             WHERE n.posted_date >= ?"""
    params = [from_date]
    if source:
        sql += " AND n.source=?"
        params.append(source)
    if has_summary is True:
        sql += " AND n.summary IS NOT NULL"
    elif has_summary is False:
        sql += " AND n.summary IS NULL"
    if v_status:
        if v_status == 'pending':
            sql += " AND (v.status='pending' OR v.status IS NULL)"
        else:
            sql += " AND v.status=?"
            params.append(v_status)
    if v_priority:
        sql += " AND v.priority=?"
        params.append(v_priority)
    sql += " ORDER BY n.posted_date DESC, n.id DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_notice_with_attachments(notice_db_id):
    with get_conn() as conn:
        notice = dict(conn.execute("SELECT * FROM notices WHERE id=?", (notice_db_id,)).fetchone())
        attachments = [dict(r) for r in conn.execute(
            "SELECT * FROM attachments WHERE notice_id=?", (notice_db_id,)
        ).fetchall()]
        notice['attachments'] = attachments
        return notice


# ── 검증 시스템 ────────────────────────────────────────────

def get_or_create_verification(notice_db_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM verifications WHERE notice_id=?", (notice_db_id,)
        ).fetchone()
        if row:
            return row['id']
        cur = conn.execute(
            "INSERT INTO verifications (notice_id) VALUES (?)", (notice_db_id,)
        )
        return cur.lastrowid


def update_verification(notice_db_id, status=None, priority=None, memo=None):
    vid = get_or_create_verification(notice_db_id)
    sets, params = [], []
    if status is not None:
        sets.append("status=?"); params.append(status)
    if priority is not None:
        sets.append("priority=?"); params.append(priority)
    if memo is not None:
        sets.append("memo=?"); params.append(memo)
    if sets:
        sets.append("updated_at=datetime('now','localtime')")
        params.append(vid)
        with get_conn() as conn:
            conn.execute(f"UPDATE verifications SET {', '.join(sets)} WHERE id=?", params)
    return vid


def upsert_module_check(notice_db_id, module_name, is_checked):
    vid = get_or_create_verification(notice_db_id)
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO module_checks (verification_id, module_name, is_checked)
               VALUES (?,?,?)
               ON CONFLICT(verification_id, module_name)
               DO UPDATE SET is_checked=excluded.is_checked""",
            (vid, module_name, 1 if is_checked else 0)
        )


def get_verification_with_modules(notice_db_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM verifications WHERE notice_id=?", (notice_db_id,)
        ).fetchone()
        if not row:
            return {'status': 'pending', 'priority': 'normal', 'memo': '',
                    'modules': {m: False for m in EMR_MODULES}}
        v = dict(row)
        checks = conn.execute(
            "SELECT module_name, is_checked FROM module_checks WHERE verification_id=?",
            (v['id'],)
        ).fetchall()
        modules = {m: False for m in EMR_MODULES}
        for c in checks:
            modules[c['module_name']] = bool(c['is_checked'])
        v['modules'] = modules
        return v


def get_dashboard_stats():
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM notices").fetchone()[0]
        rows = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM verifications GROUP BY status"
        ).fetchall()
        stats = {'pending': 0, 'reviewing': 0, 'applied': 0, 'na': 0}
        verified_count = 0
        for r in rows:
            stats[r['status']] = r['cnt']
            verified_count += r['cnt']
        stats['pending'] += max(0, total - verified_count)
        stats['total'] = total

        urgent = conn.execute(
            "SELECT COUNT(*) FROM verifications WHERE priority='urgent' AND status NOT IN ('applied','na')"
        ).fetchone()[0]
        stats['urgent'] = urgent
        return stats
