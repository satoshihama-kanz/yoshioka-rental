import os, json, sqlite3
from datetime import datetime
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='www')
CORS(app)

# ── DB 初期化 ──────────────────────────────────────────────
DB = os.path.join(os.path.dirname(__file__), 'data', 'fleet.db')

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

    # 初回起動時：JSON から DB にデータを移行
    base = os.path.dirname(__file__)
    if c.execute('SELECT COUNT(*) FROM vehicles').fetchone()[0] == 0:
        vf = os.path.join(base, 'data', 'vehicles.json')
        if os.path.exists(vf):
            for v in json.load(open(vf, encoding='utf-8')):
                c.execute('INSERT OR IGNORE INTO vehicles VALUES (?,?,?,?,?,?)',
                    (v['id'], v['number'], v.get('car_type',''), v.get('year',''),
                     v.get('full_number',''), v.get('inspection_date','')))
            conn.commit()

    if c.execute('SELECT COUNT(*) FROM events').fetchone()[0] == 0:
        ef = os.path.join(base, 'data', 'events.json')
        if os.path.exists(ef):
            for e in json.load(open(ef, encoding='utf-8')):
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

# ── 静的ファイル ─────────────────────────────────────────
@app.route('/')
def index():
    return send_from_directory('www', 'index.html')

@app.route('/<path:p>')
def static_files(p):
    return send_from_directory('www', p)

# ── 起動 ─────────────────────────────────────────────────
if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

init_db()
