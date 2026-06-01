import os, json, sqlite3, hashlib, hmac, re, requests, base64
from datetime import datetime, date, timedelta
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, session, redirect, url_for, render_template_string

app = Flask(__name__, static_folder='www')

# ── 設定（環境変数） ────────────────────────────────────────
app.secret_key        = os.environ.get('SECRET_KEY', 'yoshioka-fleet-secret-2024')
ADMIN_USER            = os.environ.get('ADMIN_USER', 'yoshioka')
ADMIN_PASS            = os.environ.get('ADMIN_PASS', 'rental2024')
LINE_CHANNEL_SECRET   = os.environ.get('LINE_CHANNEL_SECRET', '')
LINE_CHANNEL_TOKEN    = os.environ.get('LINE_CHANNEL_TOKEN', '')
LINE_NOTIFY_TOKEN     = os.environ.get('LINE_NOTIFY_TOKEN', '')

DB = os.path.join(os.path.dirname(__file__), 'data', 'fleet.db')

# ── ログイン画面HTML ────────────────────────────────────────
LOGIN_HTML = '''<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>吉岡商会 車両管理システム - ログイン</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: 'Meiryo', sans-serif;
  background: linear-gradient(135deg, #1a3a5c 0%, #2196F3 100%);
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
}
.card {
  background: white;
  border-radius: 16px;
  padding: 40px 36px;
  width: 100%;
  max-width: 380px;
  box-shadow: 0 20px 60px rgba(0,0,0,0.3);
}
.logo { text-align: center; margin-bottom: 28px; }
.logo h1 { font-size: 18px; color: #1a3a5c; margin-top: 10px; line-height: 1.5; }
.logo .car { font-size: 48px; }
label { display: block; font-size: 13px; color: #666; margin-bottom: 6px; font-weight: bold; }
input {
  width: 100%;
  padding: 12px 14px;
  border: 2px solid #e0e0e0;
  border-radius: 8px;
  font-size: 15px;
  margin-bottom: 16px;
  transition: border-color .2s;
  outline: none;
}
input:focus { border-color: #2196F3; }
button {
  width: 100%;
  padding: 14px;
  background: linear-gradient(135deg, #1a3a5c, #2196F3);
  color: white;
  border: none;
  border-radius: 8px;
  font-size: 16px;
  font-weight: bold;
  cursor: pointer;
  margin-top: 4px;
  transition: opacity .2s;
}
button:hover { opacity: 0.9; }
.error {
  background: #ffebee;
  color: #c62828;
  padding: 10px 14px;
  border-radius: 8px;
  font-size: 13px;
  margin-bottom: 16px;
  text-align: center;
}
.note { text-align: center; color: #999; font-size: 12px; margin-top: 20px; }
</style>
</head>
<body>
<div class="card">
  <div class="logo">
    <div class="car">🚗</div>
    <h1>吉岡商会<br>車両管理システム</h1>
  </div>
  {% if error %}
  <div class="error">⚠️ {{ error }}</div>
  {% endif %}
  <form method="post">
    <label>ユーザー名</label>
    <input type="text" name="username" placeholder="ユーザー名を入力" autocomplete="username" required>
    <label>パスワード</label>
    <input type="password" name="password" placeholder="パスワードを入力" autocomplete="current-password" required>
    <button type="submit">ログイン</button>
  </form>
  <div class="note">吉岡商会 車両管理システム</div>
</div>
</body>
</html>'''

# ── 認証デコレータ ────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            # APIリクエストの場合は401を返す
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated

# ── ログイン/ログアウト ────────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('logged_in'):
        return redirect('/')
    error = None
    if request.method == 'POST':
        u = request.form.get('username', '')
        p = request.form.get('password', '')
        if u == ADMIN_USER and p == ADMIN_PASS:
            session['logged_in'] = True
            session['username']  = u
            session.permanent    = True
            app.permanent_session_lifetime = timedelta(days=7)
            return redirect('/')
        error = 'ユーザー名またはパスワードが違います'
    return render_template_string(LOGIN_HTML, error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# ── DB ─────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    conn = get_db()
    c = conn.cursor()
    c.executescript('''
        CREATE TABLE IF NOT EXISTS vehicles (
            id INTEGER PRIMARY KEY,
            number TEXT,
            car_type TEXT,
            year TEXT,
            full_number TEXT,
            inspection_date TEXT
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vehicle_id INTEGER,
            status TEXT,
            start_date TEXT,
            end_date TEXT,
            staff TEXT,
            client TEXT,
            category TEXT,
            notes TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS pending_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message TEXT,
            source_id TEXT,
            sender TEXT,
            created_at TEXT,
            resolved INTEGER DEFAULT 0,
            resolved_at TEXT
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
    ''')
    conn.commit()

    base = os.path.dirname(__file__)
    if c.execute('SELECT COUNT(*) FROM vehicles').fetchone()[0] == 0:
        vf = os.path.join(base, 'vehicles.json')
        if not os.path.exists(vf):
            vf = os.path.join(base, 'data', 'vehicles.json')
        if os.path.exists(vf):
            for v in json.load(open(vf, encoding='utf-8-sig')):
                c.execute('INSERT OR IGNORE INTO vehicles VALUES (?,?,?,?,?,?)',
                    (v['id'], v['number'], v.get('car_type',''), v.get('year',''),
                     v.get('full_number',''), v.get('inspection_date','')))
            conn.commit()

    if c.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 0:
        ef = os.path.join(base, 'events.json')
        if not os.path.exists(ef):
            ef = os.path.join(base, 'data', 'events.json')
        if os.path.exists(ef):
            for e in json.load(open(ef, encoding='utf-8-sig')):
                vid = e.get('vehicle_id')
                if isinstance(vid, list): vid = vid[0]
                c.execute('''INSERT OR IGNORE INTO events
                    (id,vehicle_id,status,start_date,end_date,staff,client,category,notes,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)''',
                    (e.get('id'), vid, e.get('status',''), e.get('start_date'),
                     e.get('end_date'), e.get('staff',''), e.get('client',''),
                     e.get('category',''), e.get('notes',''), e.get('created_at','')))
            conn.commit()
    conn.close()

# ── 車両現在ステータス取得 ──────────────────────────────────
def get_vehicle_status(vehicle_id):
    today = date.today().isoformat()
    conn = get_db()
    row = conn.execute(
        '''SELECT * FROM events WHERE vehicle_id=? AND start_date<=? AND (end_date IS NULL OR end_date>=?)
           ORDER BY start_date DESC LIMIT 1''',
        (vehicle_id, today, today)).fetchone()
    conn.close()
    return dict(row) if row else None

def find_vehicle_by_number(number):
    conn = get_db()
    row = conn.execute('SELECT * FROM vehicles WHERE number=?', (number,)).fetchone()
    conn.close()
    return dict(row) if row else None

# ── LINE メッセージ解析・送信 ───────────────────────────────
STATUS_MAP = {
    '貸出': '貸出中', '貸出中': '貸出中',
    '配車': '貸出中',                        # 配車完了＝貸出開始
    '予約': '予約済', '予約済': '予約済',
    '返却': '在庫',   '在庫': '在庫',
    'キャンセル': '在庫', 'キャンセル済': '在庫',
    '車検': '車検中', '車検中': '車検中',
    '点検': '点検中', '点検中': '点検中',
    '修理': '修理中', '修理中': '修理中',
}
STAFF_NAMES = ['平田','内田','山本','吉岡','市川','福田','奥谷','川上','田中']
CATEGORIES  = ['損保','代車','マンスリー','通常']

# 会話状態管理（未完了コマンドの継続）
CONV_STATE = {}   # source_id → state dict

def parse_date(s):
    s = s.strip().rstrip('〜~～')
    now = date.today()
    m = re.match(r'^(\d{1,2})[/月](\d{1,2})日?$', s)
    if m:
        return date(now.year, int(m.group(1)), int(m.group(2))).isoformat()
    m = re.match(r'^(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})$', s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    return None

def extract_mileage(tokens):
    """5〜6桁の数字を走行距離として抽出"""
    for t in tokens:
        if re.match(r'^\d{5,6}$', t):
            return t
    return None

def extract_category(tokens):
    for t in tokens:
        if t in CATEGORIES:
            return t
    return None

def extract_dates(tokens):
    """日付・期間を抽出。(start_date, end_date) を返す"""
    start_d = end_d = None
    for t in tokens:
        dm = re.search(r'([^\s〜~～]+)[〜~～]([^\s〜~～]*)', t)
        if dm:
            sd = parse_date(dm.group(1))
            ed = parse_date(dm.group(2)) if dm.group(2) else None
            if sd: start_d = sd
            if ed: end_d   = ed
            break
        sd = parse_date(t)
        if sd and not start_d:
            start_d = sd
    return start_d, end_d

def is_date_token(t):
    return bool(re.search(r'\d{1,2}[/月]\d{1,2}', t) or '〜' in t or '~' in t or '～' in t)

def register_event(v, status, state):
    """DBにイベントを登録し、確認メッセージを返す"""
    today   = date.today().isoformat()
    start_d = state.get('start_date') or today
    end_d   = state.get('end_date')
    staff   = state.get('staff') or ''
    client  = state.get('client') or ''
    category= state.get('category') or ''
    mileage = state.get('mileage') or ''
    remarks = state.get('notes') or ''

    # notes列に走行距離と備考をまとめて保存
    notes_parts = []
    if mileage:
        notes_parts.append(f"走行距離:{mileage}km")
    if remarks:
        notes_parts.append(remarks)
    notes_str = ' / '.join(notes_parts)

    conn = get_db()
    conn.execute(
        'INSERT INTO events (vehicle_id,status,start_date,end_date,staff,client,category,notes,created_at) VALUES (?,?,?,?,?,?,?,?,?)',
        (v['id'], status, start_d, end_d, staff, client, category, notes_str,
         datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    conn.close()

    period = start_d + (' 〜 ' + end_d if end_d else '〜（返却未定）')
    msg = (f"✅ 登録しました\n"
           f"🚗 {v['number']} {v.get('car_type','')}\n"
           f"状態: {status}\n"
           f"担当: {staff}　顧客: {client}\n"
           f"期間: {period}")
    if category: msg += f"\n区分: {category}"
    if mileage:  msg += f"\n走行距離: {mileage}km"
    if remarks:  msg += f"\n備考: {remarks}"
    return msg

def ask_all_missing(state):
    """不足情報をまとめて一度に聞く"""
    v       = state['vehicle']
    missing = state['missing']
    hdr     = f"🚗 {v['number']} {v.get('car_type','')} [{state['status']}]\n"
    lines   = [hdr + "以下をまとめて教えてください👇"]
    examples = []
    if 'staff' in missing:
        lines.append("👤 担当者名")
        examples.append("田中")
    if 'client' in missing:
        lines.append("🏢 顧客名")
        examples.append("滋賀トヨタ")
    if 'category' in missing:
        lines.append("📋 種別（損保/代車/マンスリー/通常）")
        examples.append("損保")
    lines.append(f"\n例:「{' '.join(examples)}」")
    lines.append("※「キャンセル」で中止")
    return '\n'.join(lines)

def process_conv_state(text, source_id):
    """会話継続：不足情報をまとめて解析・補完"""
    state   = CONV_STATE[source_id]
    missing = state['missing']

    if text.strip() in ['キャンセル', 'cancel', 'やめる', 'ヤメル']:
        del CONV_STATE[source_id]
        return "❌ 登録をキャンセルしました"

    tokens = text.strip().split()

    # ① カテゴリを先に抽出（明確なキーワード）
    cat_found    = None
    non_cat_tokens = []
    for t in tokens:
        if t in CATEGORIES:
            cat_found = t
        else:
            non_cat_tokens.append(t)

    if 'category' in missing and cat_found:
        state['category'] = cat_found
        missing.remove('category')
    elif 'category' in missing and len(missing) == 1:
        # カテゴリのみ残っていてキーワード以外が来た場合
        return f"❓「損保」「代車」「マンスリー」「通常」のどれかを教えてください\n※「キャンセル」で中止"

    # ② スタッフ名を抽出（リスト照合→なければ先頭トークン）
    staff_found  = None
    non_staff_tokens = []
    for t in non_cat_tokens:
        if t in STAFF_NAMES and not staff_found:
            staff_found = t
        else:
            non_staff_tokens.append(t)

    if 'staff' in missing:
        if staff_found:
            state['staff'] = staff_found
        elif non_cat_tokens:
            # リストにない名前でも先頭トークンを担当者として受け付ける
            state['staff'] = non_cat_tokens[0]
            non_staff_tokens = non_cat_tokens[1:]
        missing.remove('staff') if 'staff' in missing else None

    # ③ 顧客名
    if 'client' in missing and non_staff_tokens:
        state['client'] = ' '.join(non_staff_tokens)
        missing.remove('client')

    state['missing'] = missing

    if missing:
        return ask_all_missing(state)

    # 全情報が揃ったので登録
    del CONV_STATE[source_id]
    return register_event(state['vehicle'], state['status'], state)

def process_line_message(text, source_id='', user_name=''):
    text = text.strip()

    # ── 会話継続 ──
    if source_id in CONV_STATE:
        return process_conv_state(text, source_id)

    # ── 一覧・状況 ──
    if text in ['一覧', '状況', 'status']:
        today = date.today().isoformat()
        conn = get_db()
        total    = conn.execute('SELECT COUNT(*) FROM vehicles').fetchone()[0]
        rentals  = conn.execute(
            "SELECT COUNT(DISTINCT vehicle_id) FROM events WHERE status='貸出中' AND start_date<=? AND (end_date IS NULL OR end_date>=?)",
            (today, today)).fetchone()[0]
        reserved = conn.execute(
            "SELECT COUNT(DISTINCT vehicle_id) FROM events WHERE status='予約済' AND start_date<=? AND (end_date IS NULL OR end_date>=?)",
            (today, today)).fetchone()[0]
        repairs  = conn.execute(
            "SELECT COUNT(DISTINCT vehicle_id) FROM events WHERE status IN ('車検中','点検中','修理中') AND start_date<=? AND (end_date IS NULL OR end_date>=?)",
            (today, today)).fetchone()[0]
        conn.close()
        return (f"📊 {date.today().strftime('%m/%d')} 車両状況\n"
                f"総台数: {total}台\n"
                f"🔴 貸出中: {rentals}台\n"
                f"🟡 予約済: {reserved}台\n"
                f"🔧 整備中: {repairs}台\n"
                f"🟢 在庫: {total - rentals - reserved - repairs}台")

    # ── 車番? ──
    m = re.match(r'^(\d{3,5})[?？]$', text)
    if m:
        number = m.group(1)
        v = find_vehicle_by_number(number)
        if not v:
            return f"❌ 車番 {number} は見つかりません"
        ev = get_vehicle_status(v['id'])
        if not ev:
            return f"🚗 {number} {v.get('car_type','')} → 在庫"
        notes = ev.get('notes','')
        msg = (f"🚗 {number} {v.get('car_type','')}\n"
               f"状態: {ev['status']}\n"
               f"担当: {ev.get('staff','')}\n"
               f"顧客: {ev.get('client','')}\n"
               f"期間: {ev.get('start_date','')} 〜 {ev.get('end_date','') or '（返却未定）'}")
        if ev.get('category'): msg += f"\n区分: {ev['category']}"
        if notes:              msg += f"\n備考: {notes}"
        return msg

    # ── メインコマンド：車番＋操作 ──
    m = re.match(r'^(\d{3,5})\s+(.+)$', text)
    if m:
        number = m.group(1)
        rest   = m.group(2).strip()
        v = find_vehicle_by_number(number)
        if not v:
            return f"❌ 車番 {number} は見つかりません"

        tokens = rest.split()

        # ステータスキーワードを探す
        status = None
        for i, t in enumerate(tokens):
            if t in STATUS_MAP:
                status = STATUS_MAP[t]
                tokens.pop(i)
                break

        if not status:
            return (f"❓ 操作が分かりませんでした\n"
                    f"使い方: {number} [配車/予約/返却/キャンセル/車検/点検/修理] ...\n"
                    f"例: {number} 配車 田中 滋賀トヨタ 損保 28026")

        # ── 返却・キャンセル（シンプル）──
        if status == '在庫':
            mileage = extract_mileage(tokens)
            notes   = ' '.join(t for t in tokens if t != mileage) if mileage else ' '.join(tokens)
            state   = dict(vehicle=v, status=status, staff='', client='', category='',
                           start_date=None, end_date=None, mileage=mileage, notes=notes.strip())
            return register_event(v, status, state)

        # ── 車検・点検・修理 ──
        if status in ['車検中', '点検中', '修理中']:
            mileage         = extract_mileage(tokens)
            start_d, end_d  = extract_dates(tokens)
            rest_tokens     = [t for t in tokens
                               if t != mileage and not is_date_token(t)]
            notes = ' '.join(rest_tokens).strip()
            state = dict(vehicle=v, status=status, staff='', client='', category='',
                         start_date=start_d, end_date=end_d, mileage=mileage, notes=notes)
            return register_event(v, status, state)

        # ── 貸出中・予約済（担当者・顧客・種別が必要）──
        mileage         = extract_mileage(tokens)
        category        = extract_category(tokens)
        start_d, end_d  = extract_dates(tokens)

        # 走行距離・種別・日付トークンを除いた残りから担当者・顧客・備考を取得
        leftover = [t for t in tokens
                    if t != mileage
                    and t not in CATEGORIES
                    and not is_date_token(t)]

        staff = None
        for t in leftover[:]:
            if t in STAFF_NAMES:
                staff = t
                leftover.remove(t)
                break

        client  = leftover[0] if leftover else None
        notes   = ' '.join(leftover[1:]).strip() if len(leftover) > 1 else ''

        state = dict(vehicle=v, status=status, staff=staff, client=client,
                     category=category, start_date=start_d, end_date=end_d,
                     mileage=mileage, notes=notes, missing=[])

        # 不足情報をチェック
        missing = []
        if not staff:    missing.append('staff')
        if not client:   missing.append('client')
        if not category: missing.append('category')
        state['missing'] = missing

        if missing:
            CONV_STATE[source_id] = state
            return ask_all_missing(state)

        return register_event(v, status, state)

    # ── 解析不能 → None を返して未対応リストへ ──
    return None

def send_line_reply(reply_token, message):
    if not LINE_CHANNEL_TOKEN:
        return
    requests.post(
        'https://api.line.me/v2/bot/message/reply',
        headers={'Authorization': f'Bearer {LINE_CHANNEL_TOKEN}',
                 'Content-Type': 'application/json'},
        json={'replyToken': reply_token,
              'messages': [{'type': 'text', 'text': message}]},
        timeout=5)

def send_line_push(to, message):
    """LINE グループ・ユーザーへのプッシュ送信"""
    if not LINE_CHANNEL_TOKEN or not to:
        return
    requests.post(
        'https://api.line.me/v2/bot/message/push',
        headers={'Authorization': f'Bearer {LINE_CHANNEL_TOKEN}',
                 'Content-Type': 'application/json'},
        json={'to': to, 'messages': [{'type': 'text', 'text': message}]},
        timeout=5)

def store_pending(text, source_id, sender):
    """未対応メッセージをDBに保存"""
    conn = get_db()
    conn.execute(
        'INSERT INTO pending_items (message, source_id, sender, created_at) VALUES (?,?,?,?)',
        (text, source_id, sender, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    conn.close()

def get_setting(key):
    conn = get_db()
    row = conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    conn.close()
    return row[0] if row else None

def save_setting(key, value):
    conn = get_db()
    conn.execute('INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)', (key, value))
    conn.commit()
    conn.close()

def send_line_notify(message):
    if not LINE_NOTIFY_TOKEN:
        return
    requests.post(
        'https://notify-api.line.me/api/notify',
        headers={'Authorization': f'Bearer {LINE_NOTIFY_TOKEN}'},
        data={'message': message},
        timeout=5)

# ── API（要ログイン） ────────────────────────────────────────
@app.route('/api/vehicles')
@login_required
def api_vehicles():
    conn = get_db()
    rows = [dict(r) for r in conn.execute('SELECT * FROM vehicles ORDER BY CAST(number AS INTEGER)').fetchall()]
    conn.close()
    return jsonify(rows)

@app.route('/api/events', methods=['GET'])
@login_required
def api_events_get():
    conn = get_db()
    rows = [dict(r) for r in conn.execute('SELECT * FROM events ORDER BY id').fetchall()]
    conn.close()
    return jsonify(rows)

@app.route('/api/events', methods=['POST'])
@login_required
def api_events_post():
    d = request.get_json()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn = get_db()
    cur = conn.execute(
        'INSERT INTO events (vehicle_id,status,start_date,end_date,staff,client,category,notes,created_at) VALUES (?,?,?,?,?,?,?,?,?)',
        (d['vehicle_id'], d['status'], d.get('start_date'), d.get('end_date'),
         d.get('staff',''), d.get('client',''), d.get('category',''), d.get('notes',''), now))
    conn.commit()
    row = dict(conn.execute('SELECT * FROM events WHERE id=?', (cur.lastrowid,)).fetchone())
    conn.close()
    send_line_notify(
        f"\n[車両管理] イベント登録\n"
        f"車番: {d.get('vehicle_id')}　状態: {d['status']}\n"
        f"担当: {d.get('staff','')}　顧客: {d.get('client','')}\n"
        f"期間: {d.get('start_date','')}〜{d.get('end_date','')}")
    return jsonify(row)

@app.route('/api/events/<int:eid>', methods=['PUT'])
@login_required
def api_events_put(eid):
    d = request.get_json()
    conn = get_db()
    conn.execute(
        'UPDATE events SET vehicle_id=?,status=?,start_date=?,end_date=?,staff=?,client=?,category=?,notes=? WHERE id=?',
        (d['vehicle_id'], d['status'], d.get('start_date'), d.get('end_date'),
         d.get('staff',''), d.get('client',''), d.get('category',''), d.get('notes',''), eid))
    conn.commit()
    row = conn.execute('SELECT * FROM events WHERE id=?', (eid,)).fetchone()
    conn.close()
    return jsonify(dict(row) if row else {})

@app.route('/api/events/<int:eid>', methods=['DELETE'])
@login_required
def api_events_delete(eid):
    conn = get_db()
    conn.execute('DELETE FROM events WHERE id=?', (eid,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ── 未処理メッセージ管理 ────────────────────────────────────
@app.route('/api/pending', methods=['GET'])
@login_required
def api_pending_get():
    conn = get_db()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM pending_items WHERE resolved=0 ORDER BY created_at DESC"
    ).fetchall()]
    conn.close()
    return jsonify(rows)

@app.route('/api/pending/<int:pid>/resolve', methods=['POST'])
@login_required
def api_pending_resolve(pid):
    conn = get_db()
    conn.execute("UPDATE pending_items SET resolved=1, resolved_at=? WHERE id=?",
                 (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), pid))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/pending/remind', methods=['GET', 'POST'])
def api_pending_remind():
    key = request.headers.get('X-Admin-Key','') or request.args.get('key','')
    if key != ADMIN_PASS:
        return jsonify({'error': 'Unauthorized'}), 401
    conn = get_db()
    items = conn.execute(
        "SELECT * FROM pending_items WHERE resolved=0 ORDER BY created_at ASC"
    ).fetchall()
    conn.close()
    if not items:
        return jsonify({'sent': False, 'reason': '未処理なし'})
    group_id = get_setting('line_group_id')
    if not group_id:
        return jsonify({'sent': False, 'reason': 'グループID未設定'})
    lines = [f"⚠️ 未対応メッセージ {len(items)}件\n{'─'*18}"]
    for i, item in enumerate(items, 1):
        dt = (item['created_at'] or '')[:16]
        msg = (item['message'] or '')[:60]
        lines.append(f"{i}. [{dt}]\n{msg}")
    lines.append(f"{'─'*18}\n📋 Webシステムで処理済みにしてください\nhttps://yoshioka-rental-1.onrender.com")
    send_line_push(group_id, '\n'.join(lines))
    return jsonify({'sent': True, 'count': len(items)})

# ── 車両マスタ一括追加（管理者専用） ────────────────────────
@app.route('/api/admin/add-vehicles', methods=['POST'])
def admin_add_vehicles():
    key = request.headers.get('X-Admin-Key', '')
    if key != ADMIN_PASS:
        return jsonify({'error': 'Unauthorized'}), 401
    items = request.get_json() or []
    conn = get_db()
    c = conn.cursor()
    added, skipped = 0, 0
    for item in items:
        exists = c.execute('SELECT id FROM vehicles WHERE number=?', (item['number'],)).fetchone()
        if exists:
            skipped += 1
            continue
        # 新規IDを最大値+1で割り当て
        max_id = c.execute('SELECT MAX(id) FROM vehicles').fetchone()[0] or 0
        c.execute('INSERT INTO vehicles (id,number,car_type,year,full_number,inspection_date) VALUES (?,?,?,?,?,?)',
            (max_id + 1, item['number'], item.get('car_type',''),
             item.get('year',''), item.get('full_number',''), item.get('inspection_date','')))
        added += 1
    conn.commit()
    conn.close()
    return jsonify({'added': added, 'skipped': skipped})

# ── 稼働実績一括インポート（管理者専用） ────────────────────
@app.route('/api/admin/import-status', methods=['POST'])
def admin_import_status():
    key = request.headers.get('X-Admin-Key', '')
    if key != ADMIN_PASS:
        return jsonify({'error': 'Unauthorized'}), 401
    items = request.get_json() or []
    conn = get_db()
    c = conn.cursor()
    c.execute('DELETE FROM events')
    inserted, not_found = 0, []
    today = '2026-05-27'
    for item in items:
        v = c.execute('SELECT id FROM vehicles WHERE number=?', (item['number'],)).fetchone()
        if not v:
            not_found.append(item['number'])
            continue
        c.execute('''INSERT INTO events
            (vehicle_id,status,start_date,end_date,staff,client,category,notes,created_at)
            VALUES (?,?,?,?,?,?,?,?,?)''',
            (v[0], item['status'], today, None,
             item.get('staff',''), '', 'Excel取込',
             item.get('notes',''),
             datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        inserted += 1
    conn.commit()
    conn.close()
    return jsonify({'inserted': inserted, 'not_found_count': len(not_found),
                    'not_found': not_found[:20]})

# ── LINE Webhook（認証不要・公開） ─────────────────────────
@app.route('/webhook/line', methods=['POST'])
def line_webhook():
    body = request.get_data(as_text=True)
    if LINE_CHANNEL_SECRET:
        sig = request.headers.get('X-Line-Signature', '')
        expected = hmac.new(LINE_CHANNEL_SECRET.encode(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(sig, base64.b64encode(expected).decode()):
            return 'Invalid signature', 400
    data = request.get_json(silent=True) or {}
    for event in data.get('events', []):
        source   = event.get('source', {})
        user_id  = source.get('userId', '')
        group_id = source.get('groupId', '')
        # グループIDを自動保存（初回受信時）
        if group_id and not get_setting('line_group_id'):
            save_setting('line_group_id', group_id)
        if event.get('type') == 'message' and event['message'].get('type') == 'text':
            text        = event['message']['text']
            reply_token = event.get('replyToken', '')
            source_id   = (group_id + '_' + user_id) if group_id else user_id
            reply = process_line_message(text, source_id, user_id[:8])
            if reply is None:
                # 未対応 → DBに保存して短い返信
                store_pending(text, source_id, user_id[:8])
                reply = '⚠️ 未対応メッセージへ追加しました'
            send_line_reply(reply_token, reply)
    return 'OK'

# ── 静的ファイル（要ログイン） ──────────────────────────────
@app.route('/')
@login_required
def index():
    return send_from_directory('www', 'index.html')

@app.route('/<path:p>')
@login_required
def static_files(p):
    if p == 'login':
        return redirect('/login')
    return send_from_directory('www', p)

# ── 起動 ─────────────────────────────────────────────────
init_db()
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
