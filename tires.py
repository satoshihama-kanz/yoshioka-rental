# -*- coding: utf-8 -*-
"""吉岡商会 タイヤ管理モジュール

要望書「300台分 タイヤ管理方法【完成版】」に沿った実装。
既存の車両管理（app.py）には一切手を入れず、テーブルと画面を追加するだけにしてある。

  ・タイヤ4本＝1セット。夏＝S-001／冬＝W-001（車両ごとの連番）
  ・QRラベルは https://<既存ドメイン>/t/S-001 を指す
  ・現場はスマホ標準カメラでQRを読む → 装着／取り外し／状態変更 → 送信
  ・保管場所は 拠点（滋賀支店・工場）× 棚（A〜E）の2段
"""

import os, json
from datetime import datetime, timedelta, timezone
from flask import Blueprint, request, jsonify, send_from_directory

JST = timezone(timedelta(hours=9))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WWW_DIR  = os.path.join(BASE_DIR, 'www')

# ── 選択肢（要望書 3．管理台帳に入れる項目 のとおり） ──────────────
PLACE_ON        = '装着中'
DEFAULT_SITES   = ['滋賀支店', '工場']
DEFAULT_SHELVES = ['A', 'B', 'C', 'D', 'E']
STATUSES        = ['使用中', '保管中', '交換必要', '廃棄予定', '廃棄済み']
REPLACE_FLAGS   = ['問題なし', 'そろそろ交換', '要交換']
DISPOSE_REASONS = ['溝不足', '劣化', 'パンク・損傷', 'その他']
SEASON_LABEL    = {'summer': '夏', 'winter': '冬'}
SEASON_PREFIX   = {'summer': 'S', 'winter': 'W'}

bp = Blueprint('tires', __name__)

# 依存は app.py から注入する（循環importを避けるため）
_get_db = None
_login_required = None
_today = None


# ── 小物 ────────────────────────────────────────────────────
def _now():
    return datetime.now(JST).strftime('%Y-%m-%d %H:%M')


def _get_setting(conn, key, default=None):
    r = conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    if not r or r[0] in (None, ''):
        return default
    return r[0]


def _save_setting(conn, key, value):
    conn.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)', (key, value))


def _config(conn):
    """拠点・棚・現在シーズンの設定。未設定なら既定値を返す"""
    try:
        sites = json.loads(_get_setting(conn, 'tire_sites') or '[]') or DEFAULT_SITES
    except Exception:
        sites = DEFAULT_SITES
    try:
        shelves = json.loads(_get_setting(conn, 'tire_shelves') or '[]') or DEFAULT_SHELVES
    except Exception:
        shelves = DEFAULT_SHELVES
    season = _get_setting(conn, 'tire_season') or 'summer'
    if season not in SEASON_LABEL:
        season = 'summer'
    return {'sites': sites, 'shelves': shelves, 'season': season}


TIRE_SELECT = '''
    SELECT t.tire_id, t.seq, t.vehicle_id, t.season, t.place, t.shelf, t.status,
           t.replace_flag, t.disposed_date, t.disposed_reason, t.note, t.updated_at,
           v.number, v.car_type, v.full_number, v.department
      FROM tires t
      LEFT JOIN vehicles v ON v.id = t.vehicle_id
'''


def _row(r):
    d = dict(r)
    d['season_label'] = SEASON_LABEL.get(d.get('season'), '')
    d['mounted'] = (d.get('place') == PLACE_ON)
    d['disposed'] = (d.get('status') == '廃棄済み')
    # 保管場所の表示用（拠点＋棚）
    if d['mounted']:
        d['location_label'] = PLACE_ON
    elif d.get('disposed'):
        d['location_label'] = '廃棄済み'
    elif d.get('place'):
        d['location_label'] = d['place'] + (('　棚' + d['shelf']) if d.get('shelf') else '')
    else:
        d['location_label'] = '未設定'
    return d


def _fetch(conn, tire_id):
    r = conn.execute(TIRE_SELECT + ' WHERE t.tire_id=?', (tire_id,)).fetchone()
    return _row(r) if r else None


def _pair(conn, vehicle_id, season):
    """対になるタイヤ（同じ車両の逆シーズン）"""
    other = 'winter' if season == 'summer' else 'summer'
    r = conn.execute(TIRE_SELECT + ' WHERE t.vehicle_id=? AND t.season=?',
                     (vehicle_id, other)).fetchone()
    return _row(r) if r else None


def _log(conn, tire_id, vehicle_id, action, **kw):
    conn.execute('''INSERT INTO tire_events
        (tire_id, vehicle_id, action, place, shelf, status, replace_flag,
         disposed_reason, staff, note, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
        (tire_id, vehicle_id, action,
         kw.get('place', ''), kw.get('shelf', ''), kw.get('status', ''),
         kw.get('replace_flag', ''), kw.get('disposed_reason', ''),
         kw.get('staff', ''), kw.get('note', ''), _now()))


# ── スキーマ ────────────────────────────────────────────────
def init_tire_schema():
    conn = _get_db()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS tires (
            tire_id         TEXT PRIMARY KEY,
            seq             INTEGER,
            vehicle_id      INTEGER,
            season          TEXT,
            place           TEXT DEFAULT '',
            shelf           TEXT DEFAULT '',
            status          TEXT DEFAULT '保管中',
            replace_flag    TEXT DEFAULT '問題なし',
            disposed_date   TEXT DEFAULT '',
            disposed_reason TEXT DEFAULT '',
            note            TEXT DEFAULT '',
            updated_at      TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS tire_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            tire_id         TEXT,
            vehicle_id      INTEGER,
            action          TEXT,
            place           TEXT DEFAULT '',
            shelf           TEXT DEFAULT '',
            status          TEXT DEFAULT '',
            replace_flag    TEXT DEFAULT '',
            disposed_reason TEXT DEFAULT '',
            staff           TEXT DEFAULT '',
            note            TEXT DEFAULT '',
            created_at      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_tires_vehicle    ON tires(vehicle_id);
        CREATE INDEX IF NOT EXISTS idx_tires_place      ON tires(place);
        CREATE INDEX IF NOT EXISTS idx_tire_events_tid  ON tire_events(tire_id);
    ''')
    conn.commit()
    conn.close()


# ── タイヤセットの生成（管理側の初期セットアップ） ────────────────
@bp.route('/api/tires/generate', methods=['POST'])
def api_tires_generate():
    """車両マスタから夏・冬のタイヤセットを作る。既存のタイヤには触れない。"""
    d = request.get_json() or {}
    dept = (d.get('dept') or 'rental').strip()
    mounted_season = d.get('mounted_season') or ''
    conn = _get_db()
    cfg = _config(conn)
    if mounted_season not in SEASON_LABEL:
        mounted_season = cfg['season']

    existing = {r['vehicle_id']: r['seq'] for r in
                conn.execute('SELECT vehicle_id, seq FROM tires').fetchall()}
    max_seq = max(existing.values()) if existing else 0

    rows = conn.execute(
        "SELECT id, number FROM vehicles WHERE department=? "
        "ORDER BY CAST(number AS INTEGER), id", (dept,)).fetchall()

    created = 0
    skipped = 0
    for v in rows:
        vid = v['id']
        if vid in existing:
            skipped += 1
            continue
        max_seq += 1
        seq = max_seq
        for season in ('summer', 'winter'):
            tid = '%s-%03d' % (SEASON_PREFIX[season], seq)
            if season == mounted_season:
                place, status = PLACE_ON, '使用中'
            else:
                place, status = '', '保管中'
            conn.execute('''INSERT OR IGNORE INTO tires
                (tire_id, seq, vehicle_id, season, place, shelf, status,
                 replace_flag, disposed_date, disposed_reason, note, updated_at)
                VALUES (?,?,?,?,?,'',?,'問題なし','','','',?)''',
                (tid, seq, vid, season, place, status, _now()))
            created += 1
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'created': created, 'skipped_vehicles': skipped,
                    'mounted_season': mounted_season})


# ── 設定（拠点・棚・現在シーズン） ────────────────────────────
@bp.route('/api/tires/config', methods=['GET'])
def api_tires_config_get():
    conn = _get_db()
    cfg = _config(conn)
    conn.close()
    cfg.update({'statuses': STATUSES, 'replace_flags': REPLACE_FLAGS,
                'dispose_reasons': DISPOSE_REASONS, 'place_on': PLACE_ON})
    return jsonify(cfg)


@bp.route('/api/tires/config', methods=['POST'])
def api_tires_config_post():
    d = request.get_json() or {}
    conn = _get_db()
    if isinstance(d.get('sites'), list):
        sites = [str(s).strip() for s in d['sites'] if str(s).strip()]
        if sites:
            _save_setting(conn, 'tire_sites', json.dumps(sites, ensure_ascii=False))
    if isinstance(d.get('shelves'), list):
        shelves = [str(s).strip() for s in d['shelves'] if str(s).strip()]
        if shelves:
            _save_setting(conn, 'tire_shelves', json.dumps(shelves, ensure_ascii=False))
    if d.get('season') in SEASON_LABEL:
        _save_setting(conn, 'tire_season', d['season'])
    conn.commit()
    cfg = _config(conn)
    conn.close()
    cfg['ok'] = True
    return jsonify(cfg)


# ── 一覧 ────────────────────────────────────────────────────
@bp.route('/api/tires', methods=['GET'])
def api_tires_list():
    a = request.args
    where, params = [], []
    if a.get('dept'):
        where.append('v.department=?')
        params.append(a['dept'])
    if a.get('season') in SEASON_LABEL:
        where.append('t.season=?')
        params.append(a['season'])
    if a.get('status'):
        where.append('t.status=?')
        params.append(a['status'])
    if a.get('replace'):
        where.append('t.replace_flag=?')
        params.append(a['replace'])
    if a.get('shelf'):
        where.append('t.shelf=?')
        params.append(a['shelf'])
    place = a.get('place')
    if place == '__unset__':
        where.append("(t.place='' OR t.place IS NULL)")
    elif place:
        where.append('t.place=?')
        params.append(place)
    if a.get('include_disposed') not in ('1', 'true'):
        where.append("t.status<>'廃棄済み'")
    q = (a.get('q') or '').strip()
    if q:
        where.append('(t.tire_id LIKE ? OR v.number LIKE ? OR v.car_type LIKE ? '
                     'OR v.full_number LIKE ?)')
        like = '%' + q + '%'
        params += [like, like, like, like]

    sql = TIRE_SELECT + (' WHERE ' + ' AND '.join(where) if where else '')
    sql += ' ORDER BY t.seq, t.season DESC'
    conn = _get_db()
    rows = [_row(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return jsonify(rows)


# ── ダッシュボード集計 ──────────────────────────────────────
@bp.route('/api/tires/stats', methods=['GET'])
def api_tires_stats():
    dept = request.args.get('dept') or ''
    cond, params = '', []
    if dept:
        cond = ' AND v.department=?'
        params = [dept]
    conn = _get_db()
    cfg = _config(conn)

    def one(extra, ps=()):
        sql = ("SELECT COUNT(*) FROM tires t LEFT JOIN vehicles v ON v.id=t.vehicle_id "
               "WHERE 1=1" + cond + extra)
        return conn.execute(sql, params + list(ps)).fetchone()[0]

    live = " AND t.status<>'廃棄済み'"
    stats = {
        'total':    one(live),
        'mounted':  one(live + " AND t.place=?", (PLACE_ON,)),
        'unset':    one(live + " AND (t.place='' OR t.place IS NULL)"),
        'soon':     one(live + " AND t.replace_flag='そろそろ交換'"),
        'need':     one(live + " AND t.replace_flag='要交換'"),
        'disposed': one(" AND t.status='廃棄済み'"),
        'by_site':  {},
        'season':   cfg['season'],
    }
    for s in cfg['sites']:
        stats['by_site'][s] = one(live + " AND t.place=?", (s,))

    cur = cfg['season']
    vsql = ("SELECT v.id, MAX(CASE WHEN t.place=? THEN t.season ELSE NULL END) AS on_season "
            "FROM vehicles v JOIN tires t ON t.vehicle_id=v.id WHERE 1=1" + cond +
            " GROUP BY v.id")
    done = not_done = unknown = 0
    for r in conn.execute(vsql, [PLACE_ON] + params).fetchall():
        s = r['on_season']
        if not s:
            unknown += 1
        elif s == cur:
            done += 1
        else:
            not_done += 1
    stats['swap'] = {'done': done, 'not_done': not_done, 'unknown': unknown}
    conn.close()
    return jsonify(stats)


# ── 棚マップ ────────────────────────────────────────────────
@bp.route('/api/tires/shelfmap', methods=['GET'])
def api_tires_shelfmap():
    dept = request.args.get('dept') or ''
    cond, params = '', []
    if dept:
        cond = ' AND v.department=?'
        params = [dept]
    conn = _get_db()
    cfg = _config(conn)
    rows = conn.execute(
        "SELECT t.place, t.shelf, COUNT(*) n FROM tires t "
        "LEFT JOIN vehicles v ON v.id=t.vehicle_id "
        "WHERE t.status<>'廃棄済み' AND t.place<>'' AND t.place<>?" + cond +
        " GROUP BY t.place, t.shelf", [PLACE_ON] + params).fetchall()
    conn.close()
    counts = {}
    for r in rows:
        counts.setdefault(r['place'], {})[r['shelf'] or '(棚なし)'] = r['n']
    return jsonify({'sites': cfg['sites'], 'shelves': cfg['shelves'], 'counts': counts})


# ── 個票・履歴 ──────────────────────────────────────────────
@bp.route('/api/tires/<tire_id>', methods=['GET'])
def api_tire_get(tire_id):
    conn = _get_db()
    t = _fetch(conn, tire_id)
    if not t:
        conn.close()
        return jsonify({'error': 'そのタイヤIDは登録されていません', 'tire_id': tire_id}), 404
    cfg = _config(conn)
    pair = _pair(conn, t['vehicle_id'], t['season'])
    events = [dict(r) for r in conn.execute(
        'SELECT * FROM tire_events WHERE tire_id=? ORDER BY id DESC LIMIT 50',
        (tire_id,)).fetchall()]
    conn.close()
    return jsonify({'tire': t, 'pair': pair, 'events': events, 'config': {
        'sites': cfg['sites'], 'shelves': cfg['shelves'], 'season': cfg['season'],
        'statuses': STATUSES, 'replace_flags': REPLACE_FLAGS,
        'dispose_reasons': DISPOSE_REASONS, 'place_on': PLACE_ON}})


# ── 現場からの登録（QR読取後・認証不要） ──────────────────────
@bp.route('/api/tires/<tire_id>/action', methods=['POST'])
def api_tire_action(tire_id):
    """action = mount（車に装着）/ dismount（車から取り外し）/ status（状態変更）"""
    d = request.get_json() or {}
    action = (d.get('action') or '').strip()
    staff = (d.get('staff') or '').strip()
    note = (d.get('note') or '').strip()

    conn = _get_db()
    t = _fetch(conn, tire_id)
    if not t:
        conn.close()
        return jsonify({'error': 'そのタイヤIDは登録されていません'}), 404
    cfg = _config(conn)
    warn = []

    if t['status'] == '廃棄済み':
        conn.close()
        return jsonify({'error': 'このタイヤは廃棄済みです。管理側で取り消してください'}), 400

    if action == 'mount':
        pair = _pair(conn, t['vehicle_id'], t['season'])
        if pair and pair['place'] == PLACE_ON:
            warn.append('対になる%sタイヤ %s がまだ「装着中」です。続けて取り外しを登録してください。'
                        % (pair['season_label'], pair['tire_id']))
        conn.execute("UPDATE tires SET place=?, shelf='', status='使用中', updated_at=? "
                     "WHERE tire_id=?", (PLACE_ON, _now(), tire_id))
        _log(conn, tire_id, t['vehicle_id'], '車に装着',
             place=PLACE_ON, status='使用中', staff=staff, note=note)

    elif action == 'dismount':
        place = (d.get('place') or '').strip()
        shelf = (d.get('shelf') or '').strip()
        if place not in cfg['sites']:
            conn.close()
            return jsonify({'error': '保管場所を選んでください'}), 400
        if shelf and shelf not in cfg['shelves']:
            conn.close()
            return jsonify({'error': '保管棚の指定が不正です'}), 400
        rep = (d.get('replace_flag') or t['replace_flag'] or '問題なし')
        if rep not in REPLACE_FLAGS:
            rep = '問題なし'
        status = '交換必要' if rep == '要交換' else '保管中'
        conn.execute("UPDATE tires SET place=?, shelf=?, status=?, replace_flag=?, "
                     "updated_at=? WHERE tire_id=?",
                     (place, shelf, status, rep, _now(), tire_id))
        _log(conn, tire_id, t['vehicle_id'], '車から取り外し',
             place=place, shelf=shelf, status=status, replace_flag=rep,
             staff=staff, note=note)
        if not shelf:
            warn.append('保管棚が未指定です。後で棚を登録してください。')

    elif action == 'status':
        rep = (d.get('replace_flag') or '').strip()
        if rep and rep not in REPLACE_FLAGS:
            conn.close()
            return jsonify({'error': '買い替え区分が不正です'}), 400
        dispose = bool(d.get('dispose'))
        if dispose:
            reason = (d.get('disposed_reason') or '').strip()
            if reason not in DISPOSE_REASONS:
                conn.close()
                return jsonify({'error': '廃棄理由を選んでください'}), 400
            ddate = (d.get('disposed_date') or '').strip() or _today()
            conn.execute("UPDATE tires SET status='廃棄済み', place='', shelf='', "
                         "disposed_date=?, disposed_reason=?, updated_at=? WHERE tire_id=?",
                         (ddate, reason, _now(), tire_id))
            _log(conn, tire_id, t['vehicle_id'], '廃棄',
                 status='廃棄済み', disposed_reason=reason, staff=staff, note=note)
        else:
            if not rep:
                conn.close()
                return jsonify({'error': '登録する内容を選んでください'}), 400
            status = t['status']
            if rep == '要交換' and status in ('保管中', '使用中'):
                status = '交換必要'
            elif rep == '問題なし' and status == '交換必要':
                status = '使用中' if t['place'] == PLACE_ON else '保管中'
            conn.execute("UPDATE tires SET replace_flag=?, status=?, updated_at=? "
                         "WHERE tire_id=?", (rep, status, _now(), tire_id))
            _log(conn, tire_id, t['vehicle_id'], '状態変更',
                 status=status, replace_flag=rep, staff=staff, note=note)
    else:
        conn.close()
        return jsonify({'error': '作業内容が不正です'}), 400

    conn.commit()
    t2 = _fetch(conn, tire_id)
    pair = _pair(conn, t2['vehicle_id'], t2['season'])
    conn.close()
    return jsonify({'ok': True, 'tire': t2, 'pair': pair, 'warnings': warn})


# ── 管理側の手直し ──────────────────────────────────────────
@bp.route('/api/tires/<tire_id>', methods=['PUT'])
def api_tire_update(tire_id):
    d = request.get_json() or {}
    conn = _get_db()
    t = _fetch(conn, tire_id)
    if not t:
        conn.close()
        return jsonify({'error': 'そのタイヤIDは登録されていません'}), 404
    cfg = _config(conn)
    place = d.get('place', t['place'])
    if place not in ([PLACE_ON, ''] + cfg['sites']):
        conn.close()
        return jsonify({'error': '現在地の指定が不正です'}), 400
    shelf = d.get('shelf', t['shelf']) or ''
    if place in (PLACE_ON, ''):
        shelf = ''
    if shelf and shelf not in cfg['shelves']:
        conn.close()
        return jsonify({'error': '保管棚の指定が不正です'}), 400
    status = d.get('status', t['status'])
    if status not in STATUSES:
        conn.close()
        return jsonify({'error': 'タイヤ状態の指定が不正です'}), 400
    rep = d.get('replace_flag', t['replace_flag'])
    if rep not in REPLACE_FLAGS:
        conn.close()
        return jsonify({'error': '買い替え区分の指定が不正です'}), 400
    ddate = d.get('disposed_date', t['disposed_date']) or ''
    dreason = d.get('disposed_reason', t['disposed_reason']) or ''
    if status == '廃棄済み':
        place, shelf = '', ''
        if not ddate:
            ddate = _today()
    else:
        ddate, dreason = '', ''
    note = d.get('note', t['note']) or ''
    conn.execute("UPDATE tires SET place=?, shelf=?, status=?, replace_flag=?, "
                 "disposed_date=?, disposed_reason=?, note=?, updated_at=? WHERE tire_id=?",
                 (place, shelf, status, rep, ddate, dreason, note, _now(), tire_id))
    _log(conn, tire_id, t['vehicle_id'], '管理側修正',
         place=place, shelf=shelf, status=status, replace_flag=rep,
         disposed_reason=dreason, staff=(d.get('staff') or '管理'), note=note)
    conn.commit()
    t2 = _fetch(conn, tire_id)
    conn.close()
    return jsonify({'ok': True, 'tire': t2})


# ── 保管場所の一括設定（初回の棚卸し用） ────────────────────────
@bp.route('/api/tires/bulk-location', methods=['POST'])
def api_tires_bulk_location():
    d = request.get_json() or {}
    ids = d.get('tire_ids') or []
    place = (d.get('place') or '').strip()
    shelf = (d.get('shelf') or '').strip()
    if not ids:
        return jsonify({'error': 'タイヤが選択されていません'}), 400
    conn = _get_db()
    cfg = _config(conn)
    if place not in cfg['sites']:
        conn.close()
        return jsonify({'error': '保管場所を選んでください'}), 400
    if shelf and shelf not in cfg['shelves']:
        conn.close()
        return jsonify({'error': '保管棚の指定が不正です'}), 400
    n = 0
    for tid in ids:
        t = _fetch(conn, tid)
        if not t or t['status'] == '廃棄済み':
            continue
        status = '交換必要' if t['replace_flag'] == '要交換' else '保管中'
        conn.execute("UPDATE tires SET place=?, shelf=?, status=?, updated_at=? "
                     "WHERE tire_id=?", (place, shelf, status, _now(), tid))
        _log(conn, tid, t['vehicle_id'], '保管場所一括設定',
             place=place, shelf=shelf, status=status, staff='管理')
        n += 1
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'updated': n})


# ── ラベル印刷用データ ──────────────────────────────────────
@bp.route('/api/tires/labels', methods=['GET'])
def api_tires_labels():
    a = request.args
    where, params = ["t.status<>'廃棄済み'"], []
    if a.get('dept'):
        where.append('v.department=?')
        params.append(a['dept'])
    if a.get('season') in SEASON_LABEL:
        where.append('t.season=?')
        params.append(a['season'])
    ids = (a.get('ids') or '').strip()
    if ids:
        wanted = [s.strip() for s in ids.split(',') if s.strip()]
        if wanted:
            where.append('t.tire_id IN (%s)' % ','.join('?' * len(wanted)))
            params += wanted
    else:
        try:
            f = int(a.get('seq_from') or 0)
            to = int(a.get('seq_to') or 0)
        except ValueError:
            f = to = 0
        if f:
            where.append('t.seq>=?')
            params.append(f)
        if to:
            where.append('t.seq<=?')
            params.append(to)
    sql = TIRE_SELECT + ' WHERE ' + ' AND '.join(where) + ' ORDER BY t.seq, t.season DESC'
    conn = _get_db()
    rows = [_row(r) for r in conn.execute(sql, params).fetchall()]
    conn.close()
    return jsonify(rows)


# ── 画面 ────────────────────────────────────────────────────
@bp.route('/t/<tire_id>')
def tire_scan_page(tire_id):
    """QRの読み取り先。現場のスマホがここに来る（ログイン不要）"""
    return send_from_directory(WWW_DIR, 'tire-scan.html')


def init_tires(app, get_db, login_required, today_fn):
    """app.py から呼ぶ。依存を注入してスキーマを作り、Blueprint を登録する"""
    global _get_db, _login_required, _today
    _get_db = get_db
    _login_required = login_required
    _today = today_fn

    # 管理側のAPI・画面はログイン必須にする（現場用の2本だけ認証なし）
    open_endpoints = {'tires.tire_scan_page', 'tires.api_tire_get', 'tires.api_tire_action'}

    @bp.before_request
    def _guard():
        if request.endpoint in open_endpoints:
            return None
        from flask import session, redirect
        if not session.get('logged_in'):
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect('/login')
        return None

    @bp.route('/tires')
    def tires_page():
        return send_from_directory(WWW_DIR, 'tires.html')

    @bp.route('/tires/labels')
    def tires_labels_page():
        return send_from_directory(WWW_DIR, 'tire-labels.html')

    app.register_blueprint(bp)
    init_tire_schema()
