import os, json, sqlite3, hashlib, hmac, re, requests
from datetime import datetime, date, timedelta
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder='www')

# ── 設定（環境変数） ────────────────────────────────────────
LINE_CHANNEL_SECRET  = os.environ.get('LINE_CHANNEL_SECRET', '')
LINE_CHANNEL_TOKEN   = os.environ.get('LINE_CHANNEL_TOKEN', '')
LINE_NOTIFY_TOKEN    = os.environ.get('LINE_NOTIFY_TOKEN', '')   # 通知用

DB = os.path.join(os.path.dirname(__file__), 'data', 'fleet.db')

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
    ''')
    conn.commit()

    base = os.path.dirname(__file__)
    if c.execute('SELECT COUNT(*) FROM vehicles').fetchone()[0] == 0:
        vf = os.path.join(base, 'data', 'vehicles.json')
        if os.path.exists(vf):
            for v in json.load(open(vf, encoding='utf-8-sig')):
                c.execute('INSERT OR IGNORE INTO vehicles VALUES (?,?,?,?,?,?)',
                    (v['id'], v['number'], v.get('car_type',''), v.get('year',''),
                     v.get('full_number',''), v.get('inspection_date','')))
            conn.commit()

    if c.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 0:
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
    '予約': '予約済', '予約済': '予約済',
    '返却': '在庫',  '在庫': '在庫',
    '車検': '車検中', '車検中': '車検中',
    '点検': '点検中', '点検中': '点検中',
    '修理': '修理中', '修理中': '修理中',
}

def parse_date(s):
    """5/6 や 2025/5/6 → YYYY-MM-DD"""
    s = s.strip()
    now = date.today()
    m = re.match(r'^(\d{1,2})[/月](\d{1,2})日?$', s)
    if m:
        return date(now.year, int(m.group(1)), int(m.group(2))).isoformat()
    m = re.match(r'^(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})$', s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    return None

def process_line_message(text, user_name=''):
    """
    受信メッセージを解析して車両ステータスを更新する
    例）
      1234?                        → 現在のステータスを返す
      1234 貸出 田中 ABC商事 5/6〜5/10   → 貸出登録
      1234 返却                    → 在庫に戻す
      一覧                         → 本日の貸出中台数を返す
    """
    text = text.strip()

    # ─ 一覧照会 ─
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

    # ─ 車番照会 1234? ─
    m = re.match(r'^(\d{3,5})[?？]$', text)
    if m:
        number = m.group(1)
        v = find_vehicle_by_number(number)
        if not v:
            return f"❌ 車番 {number} は見つかりません"
        ev = get_vehicle_status(v['id'])
        if not ev:
            return f"🚗 {number} {v.get('car_type','')} → 在庫"
        return (f"🚗 {number} {v.get('car_type','')}\n"
                f"状態: {ev['status']}\n"
                f"担当: {ev.get('staff','')}\n"
                f"顧客: {ev.get('client','')}\n"
                f"期間: {ev.get('start_date','')} 〜 {ev.get('end_date','')}")

    # ─ ステータス登録 ─
    # 書式: 車番 ステータス [担当] [顧客] [開始〜終了]
    # 例: 1234 貸出 田中 ABC商事 5/6〜5/10
    m = re.match(r'^(\d{3,5})\s+([ぁ-鿿\w]+)(.*)?$', text)
    if m:
        number   = m.group(1)
        stat_key = m.group(2)
        rest     = (m.group(3) or '').strip()

        status = STATUS_MAP.get(stat_key)
        if not status:
            return f"❓ 「{stat_key}」は不明なステータスです\n（貸出/返却/予約/車検/点検/修理）"

        v = find_vehicle_by_number(number)
        if not v:
            return f"❌ 車番 {number} は見つかりません"

        # 残りのトークンを分解
        tokens   = rest.split() if rest else []
        staff    = tokens[0] if len(tokens) > 0 else user_name
        client   = tokens[1] if len(tokens) > 1 else ''
        start_d  = date.today().isoformat()
        end_d    = None

        # 日付範囲 5/6〜5/10 を探す
        for t in tokens:
            dm = re.search(r'(\S+)[〜~～\-](\S+)', t)
            if dm:
                sd = parse_date(dm.group(1))
                ed = parse_date(dm.group(2))
                if sd: start_d = sd
                if ed: end_d   = ed
                break
            # 単独の日付
            sd = parse_date(t)
            if sd and t not in tokens[:2]:
                start_d = sd

        # DB登録
        conn = get_db()
        conn.execute(
            'INSERT INTO events (vehicle_id,status,start_date,end_date,staff,client,category,notes,created_at) VALUES (?,?,?,?,?,?,?,?,?)',
            (v['id'], status, start_d, end_d, staff, client, 'LINE', f'LINE:{user_name}', datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
        conn.close()

        period = f"{start_d}" + (f" 〜 {end_d}" if end_d else "〜")
        return (f"✅ 登録しました\n"
                f"🚗 {number} {v.get('car_type','')}\n"
                f"状態: {status}\n"
                f"担当: {staff}　顧客: {client}\n"
                f"期間: {period}")

    # ─ ヘルプ ─
    return ("📖 使い方\n"
            "• 1234? → 現在の状況確認\n"
            "• 一覧 → 全体の状況確認\n"
            "• 1234 貸出 田中 ABC商事 5/6〜5/10\n"
            "• 1234 返却\n"
            "• 1234 車検 山田 5/6〜5/20")

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

def send_line_notify(message):
    if not LINE_NOTIFY_TOKEN:
        return
    requests.post(
        'https://notify-api.line.me/api/notify',
        headers={'Authorization': f'Bearer {LINE_NOTIFY_TOKEN}'},
        data={'message': message},
        timeout=5)

# ── API ────────────────────────────────────────────────────
@app.route('/api/vehicles')
def api_vehicles():
    conn = get_db()
    rows = [dict(r) for r in conn.execute('SELECT * FROM vehicles ORDER BY CAST(number AS INTEGER)').fetchall()]
    conn.close()
    return jsonify(rows)

@app.route('/api/events', methods=['GET'])
def api_events_get():
    conn = get_db()
    rows = [dict(r) for r in conn.execute('SELECT * FROM events ORDER BY id').fetchall()]
    conn.close()
    return jsonify(rows)

@app.route('/api/events', methods=['POST'])
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
    # LINE Notify
    send_line_notify(
        f"\n[車両管理] イベント登録\n"
        f"車番: {d.get('vehicle_id')}　状態: {d['status']}\n"
        f"担当: {d.get('staff','')}　顧客: {d.get('client','')}\n"
        f"期間: {d.get('start_date','')}〜{d.get('end_date','')}")
    return jsonify(row)

@app.route('/api/events/<int:eid>', methods=['PUT'])
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
def api_events_delete(eid):
    conn = get_db()
    conn.execute('DELETE FROM events WHERE id=?', (eid,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# ── LINE Webhook ───────────────────────────────────────────
@app.route('/webhook/line', methods=['POST'])
def line_webhook():
    # 署名検証
    body = request.get_data(as_text=True)
    if LINE_CHANNEL_SECRET:
        sig = request.headers.get('X-Line-Signature', '')
        expected = hmac.new(LINE_CHANNEL_SECRET.encode(), body.encode(), hashlib.sha256).digest()
        import base64
        if not hmac.compare_digest(sig, base64.b64encode(expected).decode()):
            return 'Invalid signature', 400

    data = request.get_json(silent=True) or {}
    for event in data.get('events', []):
        if event.get('type') == 'message' and event['message'].get('type') == 'text':
            text        = event['message']['text']
            reply_token = event.get('replyToken', '')
            user_name   = event.get('source', {}).get('userId', '')[:8]
            reply = process_line_message(text, user_name)
            send_line_reply(reply_token, reply)
    return 'OK'

# ── 静的ファイル ─────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory('www', 'index.html')

@app.route('/<path:p>')
def static_files(p):
    return send_from_directory('www', p)

# ── 起動 ─────────────────────────────────────────────────
init_db()
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
