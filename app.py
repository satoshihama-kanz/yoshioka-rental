import os, json, sqlite3, hashlib, hmac, re, requests, base64, zlib, struct
from datetime import datetime, date, timedelta, timezone
from functools import wraps

# ── 日本時間（JST）で今日の日付を取得 ─────────────────────────
JST = timezone(timedelta(hours=9))
def today_jst():
    return datetime.now(JST).strftime('%Y-%m-%d')
from flask import Flask, request, jsonify, send_from_directory, session, redirect, url_for, render_template_string

app = Flask(__name__, static_folder='www')

# ── 設定（環境変数） ────────────────────────────────────────
app.secret_key        = os.environ.get('SECRET_KEY', 'yoshioka-fleet-secret-2024')
ADMIN_USER            = os.environ.get('ADMIN_USER', 'yoshioka')
ADMIN_PASS            = os.environ.get('ADMIN_PASS', 'rental2024')
# マスタ編集用の合言葉。未設定ならログインパスワードを流用する
MASTER_PASS           = os.environ.get('MASTER_PASS', '') or ADMIN_PASS
LINE_CHANNEL_SECRET   = os.environ.get('LINE_CHANNEL_SECRET', '')
LINE_CHANNEL_TOKEN    = os.environ.get('LINE_CHANNEL_TOKEN', '')
LINE_NOTIFY_TOKEN     = os.environ.get('LINE_NOTIFY_TOKEN', '')

DB = os.path.join(os.path.dirname(__file__), 'data', 'fleet.db')

# ── 部門（事業部） ──────────────────────────────────────────
# rental: 既存のレンタカー事業部（336台）／sales: セールス部門の代車
DEPARTMENTS  = {'rental': 'レンタカー事業部', 'sales': 'セールス部門'}
DEFAULT_DEPT = 'rental'

def norm_dept(value, default=DEFAULT_DEPT):
    """部門コードを正規化する。不正値・空値は default に落とす"""
    v = (value or '').strip()
    return v if v in DEPARTMENTS else default

def req_dept(default=None):
    """リクエストの dept パラメータを取得。未指定なら default（None＝全部門）"""
    v = (request.args.get('dept') or '').strip()
    return v if v in DEPARTMENTS else default

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

# ── マスタ編集ロック ──────────────────────────────────────
# 閲覧は誰でもできるが、追加・変更・削除は合言葉を入れた人だけに限る。
def master_edit_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return jsonify({'error': 'Unauthorized'}), 401
        if not session.get('master_edit'):
            return jsonify({'error': 'マスタ編集はロックされています',
                            'locked': True}), 403
        return f(*args, **kwargs)
    return decorated

@app.route('/api/master/unlock', methods=['POST'])
@login_required
def master_unlock():
    pw = (request.get_json() or {}).get('password', '')
    if pw != MASTER_PASS:
        return jsonify({'error': '合言葉が違います'}), 403
    session['master_edit'] = True
    return jsonify({'ok': True, 'unlocked': True})

@app.route('/api/master/lock', methods=['POST'])
@login_required
def master_lock():
    session.pop('master_edit', None)
    return jsonify({'ok': True, 'unlocked': False})

@app.route('/api/master/lock-state')
@login_required
def master_lock_state():
    return jsonify({'unlocked': bool(session.get('master_edit'))})

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

# ── 取引先マスタ初期データ ──────────────────────────────────
_CLIENT_SEED = [
    ('RSﾕﾆﾃｨｰ', 'アールエスユニティー'),
    ('R’s AUTO', 'アールズオート'),
    ('相根 美樹', 'アイネミキ'),
    ('ｱｲﾌﾟﾗﾝ24', 'アイプランニジュウヨン'),
    ('浅野', 'アサノ'),
    ('ｱｯﾌﾟﾙﾜｰﾙﾄﾞ城陽店', 'アップルワールドジョウヨウテン'),
    ('ATELIER', 'アトリエ'),
    ('ABOUT11', 'アバウトイレブン'),
    ('ｱﾍﾟｯｸｽｸﾞﾛｳ', 'アペックスグロウ'),
    ('㈲雨森ｵｰﾄｻｰﾋﾞｽ', 'アメモリオートサービス'),
    ('安心ｱｼｽﾄ株式会社', 'アンシンアシスト'),
    ('あんしん保険', 'アンシンホケン'),
    ('EAST LAKE', 'イーストレイク'),
    ('有限会社 ｲｸﾏ自動車', 'イクマジドウシャ'),
    ('池田', 'イケダ'),
    ('池田自動車', 'イケダジドウシャ'),
    ('池本ﾓｰﾀｰｽ', 'イケモトモータース'),
    ('石井', 'イシイ'),
    ('ｲﾁﾐﾔ自動車合同会社', 'イチミヤジドウシャ'),
    ('有限会社 市村自動車整備工場', 'イチムラジドウシャセイビコウジョウ'),
    ('伊吹', 'イブキ'),
    ('今井自動車工業', 'イマイジドウシャコウギョウ'),
    ('今林 幸海', 'イマバヤシユキミ'),
    ('有限会社今村製作所', 'イマムラセイサクショ'),
    ('岩井紙器店', 'イワイシキテン'),
    ('岩本自動車工作所', 'イワモトジドウシャコウサクショ'),
    ('ｲﾝｼｭｱﾗﾝｽﾌﾞﾚｰﾝ', 'インシュアランスブレーン'),
    ('ｲﾝｼｭｱﾗﾝｽﾌﾞﾚｰﾝ亀岡', 'インシュアランスブレーンカメオカ'),
    ('ｲﾝｼｭｱﾗﾝｽﾌﾞﾚｰﾝ京都中央支店', 'インシュアランスブレーンキョウトチュウオウシテン'),
    ('ｳｲﾝﾗｲﾌ', 'ウインライフ'),
    ('株式会社ｳｲﾝﾗｲﾌ滋賀北営業所', 'ウインライフシガキタエイギョウショ'),
    ('株式会社ｳｲﾝﾗｲﾌ 伏見営業所', 'ウインライフフシミエイギョウショ'),
    ('ｳｴｽｷﾞ自工', 'ウエスギジコウ'),
    ('WEST', 'ウエスト'),
    ('ｳﾞｪﾙｸｵｰﾄ', 'ヴェルクオート'),
    ('ｳｴﾝｽﾞｵｰﾄ', 'ウエンズオート'),
    ('ｳｶｲﾓｰﾀｰｽ', 'ウカイモータース'),
    ('ｳﾁﾀﾞ自動車工業', 'ウチダジドウシャコウギョウ'),
    ('有限会社 内山車体', 'ウチヤマシャタイ'),
    ('株式会社 UNO', 'ウノ'),
    ('合同会社H’2', 'エイチツー'),
    ('株式会社 ＨＹ－システム', 'エイチワイシステム'),
    ('㈱A.Y', 'エーワイ'),
    ('SR factory', 'エスアールファクトリー'),
    ('S&S MARKET', 'エスアンドエスマーケット'),
    ('株式会社 SG-Auto', 'エスジーオート'),
    ('ST CREATE 株式会社', 'エスティークリエイト'),
    ('SP Auto Service', 'エスピーオートサービス'),
    ('Eternal SKY', 'エターナルスカイ'),
    ('N-BASE', 'エヌベース'),
    ('株式会社 ｴﾌｹｲ', 'エフケイ'),
    ('株式会社FJﾈｯﾄﾜｰｸ', 'エフジェイネットワーク'),
    ('MRT', 'エムアールティー'),
    ('MY商会', 'エムワイショウカイ'),
    ('江本', 'エモト'),
    ('江本 武', 'エモトタケシ'),
    ('株式会社 Area ace', 'エリアエース'),
    ('LRmotor', 'エルアールモーター'),
    ('合同会社 LIF', 'エルアイエフ'),
    ('有限会社 ｴﾙｸﾞｽﾎﾟｰﾂ', 'エルグスポーツ'),
    ('ｴﾙﾌｧｲﾝ', 'エルファイン'),
    ('ｵｳｼﾞﾌｧｸﾄﾘｰ', 'オウジファクトリー'),
    ('近江電機自動車整備工場', 'オウミデンキジドウシャセイビコウジョウ'),
    ('株式会社OS BODY WORK', 'オーエスボディワーク'),
    ('大阪ﾄﾖﾍﾟｯﾄ㈱吹田店', 'オオサカトヨペットスイタテン'),
    ('大島自動車工作所', 'オオシマジドウシャコウサクショ'),
    ('大谷自動車', 'オオタニジドウシャ'),
    ('ｵｰﾄｳﾁﾀﾞ', 'オートウチダ'),
    ('ｵｰﾄｸﾗﾌﾄ', 'オートクラフト'),
    ('ｵｰﾄｺﾚｸｼｮﾝ ｷﾞｱ', 'オートコレクションギア'),
    ('ｵｰﾄｻｰﾋﾞｽ嶋田', 'オートサービスシマダ'),
    ('ｵｰﾄｼﾞｬﾊﾟﾝ', 'オートジャパン'),
    ('ｵｰﾄｼｮｯﾌﾟﾀｹｵｶ', 'オートショップタケオカ'),
    ('ｵｰﾄｼｮｯﾌﾟﾓﾘ', 'オートショップモリ'),
    ('ｵｰﾄｽﾃｰｼｮﾝ', 'オートステーション'),
    ('ｵｰﾄｾﾚｸﾄ･ﾀﾅｶ', 'オートセレクトタナカ'),
    ('ｵｰﾄﾂｰﾙ', 'オートツール'),
    ('株式会社ｵｰﾄﾃｯｸ', 'オートテック'),
    ('ｵｰﾄﾃｯｸ車検整備ｾﾝﾀｰ', 'オートテックシャケンセイビセンター'),
    ('ｵｰﾄﾊﾞｯｸｽ八日市店', 'オートバックスヨウカイチテン'),
    ('ｵｰﾄﾔｰﾄﾞ', 'オートヤード'),
    ('ｵｰﾄﾘｯﾁ古谷', 'オートリッチフルタニ'),
    ('有限会社ｵｰﾄﾘﾍﾟｱ', 'オートリペア'),
    ('株式会社 Auto Works', 'オートワークス'),
    ('有限会社 岡﨑自販', 'オカザキジハン'),
    ('岡田', 'オカダ'),
    ('岡田合同会社', 'オカダ'),
    ('株式会社 ｵｸﾀﾞｵｰﾄ', 'オクダオート'),
    ('㈱奥村ﾓｰﾀｰｽ', 'オクムラモータース'),
    ('有限会社ｵｸﾞﾗｵｰﾄｻｰﾋﾞｽ', 'オグラオートサービス'),
    ('雄琴自工', 'オゴトジコウ'),
    ('office Zen', 'オフィスゼン'),
    ('ｵﾘｯｸｽ自動車㈱大阪港営業所', 'オリックスジドウシャオオサカコウエイギョウショ'),
    ('ｶｰｱｯﾌﾟﾗｲﾄ', 'カーアップライト'),
    ('株式会社 ｶｰｵｰｸ', 'カーオーク'),
    ('ｶｰｵﾌｨｽｶﾅﾓ', 'カーオフィスカナモ'),
    ('ｶｰｵﾌｨｽｼﾞｰｻﾞｰ', 'カーオフィスジーザー'),
    ('car office Rev’O', 'カーオフィスレボ'),
    ('ｶｰｻｰﾋﾞｽﾊｼﾓﾄ', 'カーサービスハシモト'),
    ('株式会社ｶｰｼｪｱｶﾙﾁｬｰ', 'カーシェアカルチャー'),
    ('ｶｰｼｮｯﾌﾟwing', 'カーショップウイング'),
    ('ｶｰｼｮｯﾌﾟｳﾉ', 'カーショップウノ'),
    ('有限会社 ｶｰｼｮｯﾌﾟJUN', 'カーショップジュン'),
    ('ｶｰｼｮｯﾌﾟ道楽車', 'カーショップドウラクシャ'),
    ('Car Shop REXT', 'カーショップレクスト'),
    ('ｶｰｽﾃｰｼｮﾝﾅｶﾑﾗ', 'カーステーションナカムラ'),
    ('有限会社 ｶｰﾃｯｸｳｶｲ', 'カーテックウカイ'),
    ('ｶｰﾊﾟｰﾄﾅｰ', 'カーパートナー'),
    ('ｶｰﾊｳｽﾌﾘｰｾﾚｸｼｮﾝ', 'カーハウスフリーセレクション'),
    ('CARPAC', 'カーパック'),
    ('ｶｰﾊﾟﾚｽ伏見', 'カーパレスフシミ'),
    ('ｶｰ･ﾋﾞｭｰﾃｨ京都', 'カービューティキョウト'),
    ('ｶｰﾌｧｸﾄﾘｰJUN', 'カーファクトリージュン'),
    ('株式会社ｶｰﾌｨｰﾙﾄﾞﾖｼｵｶ', 'カーフィールドヨシオカ'),
    ('CARBOX', 'カーボックス'),
    ('ｶｰﾎﾞﾃﾞｰｲﾅﾊﾞ', 'カーボディイナバ'),
    ('株式会社 ｶｰﾐﾙｽﾞ', 'カーミルズ'),
    ('ｶｰﾗｲﾌ長岡', 'カーライフナガオカ'),
    ('KAGAYAKI GARAGE', 'カガヤキガレージ'),
    ('家具町ﾓｰﾀｰｽ', 'カグマチモータース'),
    ('笠井', 'カサイ'),
    ('株式会社 笠井工務店', 'カサイコウムテン'),
    ('梶本 陸人', 'カジモトリクト'),
    ('customer ORIENTED T.S', 'カスタマーオリエンテッドティーエス'),
    ('勝美自動車株式会社', 'カツミジドウシャ'),
    ('株式会社 葛城ﾓｰﾀｰｽ', 'カツラギモータース'),
    ('活力ある京都をつくる会', 'カツリョクアルキョウトヲツクルカイ'),
    ('加藤石油', 'カトウセキユ'),
    ('有限会社 蒲生自動車', 'ガモウジドウシャ'),
    ('ｶﾞﾘﾊﾞｰ槙島店', 'ガリバーマキシマテン'),
    ('ｶﾞﾚｰｼﾞｱｽﾄﾚｱ', 'ガレージアストレア'),
    ('ｶﾞﾚｰｼﾞM', 'ガレージエム'),
    ('ｶﾞﾚｰｼﾞOKUMURA', 'ガレージオクムラ'),
    ('ｶﾞﾚｰｼﾞｷｯｽﾞ', 'ガレージキッズ'),
    ('ｶﾞﾚｰｼﾞｻｻﾞﾝ', 'ガレージサザン'),
    ('ｶﾞﾚｰｼﾞShine', 'ガレージシャイン'),
    ('ｶﾞﾚｰｼﾞﾀｶﾑﾗ', 'ガレージタカムラ'),
    ('garage Toys', 'ガレージトイズ'),
    ('GARAGE89', 'ガレージハチキュウ'),
    ('Garage88', 'ガレージハチハチ'),
    ('カワイデンキ株式会社', 'カワイデンキ'),
    ('株式会社 ｶﾜｶﾂ', 'カワカツ'),
    ('川正自動車株式会社', 'カワショウジドウシャ'),
    ('川端 芹奈', 'カワバタセリナ'),
    ('川本', 'カワモト'),
    ('有限会社 寒梅社', 'カンバイシャ'),
    ('ｷｰｽｵｰﾄﾌﾟﾛｼﾞｪｸﾄ', 'キースオートプロジェクト'),
    ('株式会社 KIEFER', 'キーファー'),
    ('北川ﾓｰﾀｰｽ', 'キタガワモータース'),
    ('北田自動車', 'キタダジドウシャ'),
    ('貴生川車体工業', 'キブカワシャタイコウギョウ'),
    ('ｷﾞｬﾗﾘｰｵｵｻﾜ', 'ギャラリーオオサワ'),
    ('共栄火災海上保険株式会社', 'キョウエイカサイカイジョウホケン'),
    ('京央自動車 株式会社', 'キョウオウジドウシャ'),
    ('株式会社 共進自動車', 'キョウシンジドウシャ'),
    ('京都ｻｰﾋﾞｽ株式会社', 'キョウトサービス'),
    ('京都ｻｰﾋﾞｽ 吉祥院', 'キョウトサービスキッショウイン'),
    ('京都ｻｰﾋﾞｽ久御山', 'キョウトサービスクミヤマ'),
    ('京都ﾄﾖﾀ自動車㈱宇治店', 'キョウトトヨタジドウシャウジテン'),
    ('京都ﾄﾖﾀ自動車㈱乙訓店', 'キョウトトヨタジドウシャオトクニテン'),
    ('京都ﾄﾖﾀ自動車㈱桂川洛西店', 'キョウトトヨタジドウシャカツラガワラクサイテン'),
    ('京都ﾄﾖﾍﾟｯﾄ㈱宇治店', 'キョウトトヨペットウジテン'),
    ('京都ﾄﾖﾍﾟｯﾄ㈱山科店', 'キョウトトヨペットヤマシナテン'),
    ('京都日産自動車㈱右京店', 'キョウトニッサンジドウシャウキョウテン'),
    ('京都日産自動車㈱宇治店', 'キョウトニッサンジドウシャウジテン'),
    ('京都日産自動車㈱宇治西店', 'キョウトニッサンジドウシャウジニシテン'),
    ('京都日産自動車㈱宇治東店', 'キョウトニッサンジドウシャウジヒガシテン'),
    ('京都日産自動車㈱ｶｰﾒｯｾ久御山', 'キョウトニッサンジドウシャカーメッセクミヤマ'),
    ('京都日産自動車㈱桂店', 'キョウトニッサンジドウシャカツラテン'),
    ('京都日産自動車㈱亀岡大井北店', 'キョウトニッサンジドウシャカメオカオオイキタテン'),
    ('京都日産自動車㈱亀岡大井南店', 'キョウトニッサンジドウシャカメオカオオイミナミテン'),
    ('京都日産自動車㈱木津川台店', 'キョウトニッサンジドウシャキヅガワダイテン'),
    ('京都日産自動車㈱京田辺店', 'キョウトニッサンジドウシャキョウタナベテン'),
    ('京都日産自動車㈱十条店', 'キョウトニッサンジドウシャジュウジョウテン'),
    ('京都日産自動車㈱高野店', 'キョウトニッサンジドウシャタカノテン'),
    ('京都日産自動車㈱長岡京店', 'キョウトニッサンジドウシャナガオカキョウテン'),
    ('京都日産自動車㈱西大路店', 'キョウトニッサンジドウシャニシオオジテン'),
    ('京都日産自動車㈱西舞鶴店', 'キョウトニッサンジドウシャニシマイヅルテン'),
    ('京都日産自動車㈱東舞鶴店', 'キョウトニッサンジドウシャヒガシマイヅルテン'),
    ('京都日産自動車㈱伏見店', 'キョウトニッサンジドウシャフシミテン'),
    ('京都日産自動車㈱法人営業部', 'キョウトニッサンジドウシャホウジンエイギョウブ'),
    ('京都日産自動車㈱本社店', 'キョウトニッサンジドウシャホンシャテン'),
    ('京都日産自動車㈱南店', 'キョウトニッサンジドウシャミナミテン'),
    ('京都日産自動車㈱峰山店', 'キョウトニッサンジドウシャミネヤマテン'),
    ('京都日産自動車㈱向日店', 'キョウトニッサンジドウシャムコウテン'),
    ('京都日産自動車㈱山科音羽店', 'キョウトニッサンジドウシャヤマシナオトワテン'),
    ('京都日産自動車㈱山科店', 'キョウトニッサンジドウシャヤマシナテン'),
    ('京都日産自動車㈱洛北店', 'キョウトニッサンジドウシャラクホクテン'),
    ('京都日産自動車㈱ﾙﾉｰ京都', 'キョウトニッサンジドウシャルノーキョウト'),
    ('京都ﾗｲﾌﾊﾟｰﾄﾅｰ', 'キョウトライフパートナー'),
    ('京鈑金', 'キョウバンキン'),
    ('ｷｮｰｼﾝｵｰﾄ', 'キョーシンオート'),
    ('株式会社 GOODSUN', 'グッドサン'),
    ('株式会社 久保板金', 'クボバンキン'),
    ('株式会社 久保鈑金', 'クボバンキン'),
    ('KLASSE', 'クラッセ'),
    ('株式会社ｸﾗﾌﾄｳﾞｧｰｹﾞﾝ', 'クラフトヴァーゲン'),
    ('くるま工房', 'クルマコウボウ'),
    ('GraceFare', 'グレースフェア'),
    ('ｸﾞﾛｰﾊﾞﾙｱｲﾃﾞｨｱﾙ株式会社', 'グローバルアイディアル'),
    ('ｸﾛｽｵﾌｨｽ(ｱﾍﾟｯｸｽｸﾞﾛｳ㈱)', 'クロスオフィスアペックスグロウ'),
    ('黒瀬', 'クロセ'),
    ('K&R style', 'ケイアンドアールスタイル'),
    ('KNｵｰﾄ', 'ケイエヌオート'),
    ('K.G.S.', 'ケイジーエス'),
    ('株式会社 京滋ﾊｳｼﾞﾝｸﾞ', 'ケイジハウジング'),
    ('K’SMART', 'ケイズマート'),
    ('K2ｵｰﾄﾜｰｸｽ', 'ケイツーオートワークス'),
    ('KTM', 'ケイティーエム'),
    ('K Produce nice', 'ケイプロデュースナイス'),
    ('契約ｾﾝﾀｰ', 'ケイヤクセンター'),
    ('有限会社 契約ｾﾝﾀｰ 浅井 真', 'ケイヤクセンターアサイマコト'),
    ('K.U. STYLE', 'ケイユースタイル'),
    ('ｹﾋｵｰﾄ', 'ケヒオート'),
    ('KEN FACTORY', 'ケンファクトリー'),
    ('古一商会', 'コイチショウカイ'),
    ('江州自動車', 'ゴウシュウジドウシャ'),
    ('公成建設株式会社', 'コウセイケンセツ'),
    ('甲南ﾓｰﾀｰｽ', 'コウナンモータース'),
    ('有限会社 ｺﾄﾌﾞｷ', 'コトブキ'),
    ('株式会社 湖南ｵｰﾄｾﾝﾀｰ', 'コナンオートセンター'),
    ('株式会社 湖南鈑金', 'コナンバンキン'),
    ('小西', 'コニシ'),
    ('小船井 達哉', 'コフナイタツヤ'),
    ('ｺﾞﾘﾗﾊｳｽ', 'ゴリラハウス'),
    ('ｺﾝｾﾌﾟﾄ', 'コンセプト'),
    ('近藤 淳一', 'コンドウジュンイチ'),
    ('齋藤自動車', 'サイトウジドウシャ'),
    ('栄建設工業 株式会社', 'サカエケンセツコウギョウ'),
    ('有限会社ｻｶﾓﾄ自動車', 'サカモトジドウシャ'),
    ('さすてな京都', 'サステナキョウト'),
    ('三軌工業株式会社', 'サンキコウギョウ'),
    ('株式会社三共自動車', 'サンキョウジドウシャ'),
    ('ｻﾝﾌｫｰｽ', 'サンフォース'),
    ('三陽自動車', 'サンヨウジドウシャ'),
    ('株式会社ｼﾞｰｴﾙ', 'ジーエル'),
    ('ｼｰｻﾏｰｶｽﾀﾑｽﾞ', 'シーサマーカスタムズ'),
    ('G-STYLE', 'ジースタイル'),
    ('C4cars', 'シーフォーカーズ'),
    ('ｼｰﾎﾞｰｲ彦根店', 'シーボーイヒコネテン'),
    ('株式会社C-ONE', 'シーワン'),
    ('JA共済連 滋賀', 'ジェイエイキョウサイレンシガ'),
    ('有限会社 J-Breath', 'ジェイブレス'),
    ('汐先', 'シオサキ'),
    ('滋賀県共済協同組合', 'シガケンキョウサイキョウドウクミアイ'),
    ('滋賀ﾀﾞｲﾊﾂ販売㈱愛知川店', 'シガダイハツハンバイエチガワテン'),
    ('滋賀ﾀﾞｲﾊﾂ販売㈱大津店', 'シガダイハツハンバイオオツテン'),
    ('滋賀ﾀﾞｲﾊﾂ販売㈱堅田店', 'シガダイハツハンバイカタタテン'),
    ('滋賀ﾀﾞｲﾊﾂ販売㈱草津店', 'シガダイハツハンバイクサツテン'),
    ('滋賀ﾀﾞｲﾊﾂ販売㈱長浜店', 'シガダイハツハンバイナガハマテン'),
    ('滋賀ﾀﾞｲﾊﾂ販売㈱Newﾓﾋﾞﾘﾃｨ', 'シガダイハツハンバイニューモビリティ'),
    ('滋賀ﾀﾞｲﾊﾂ販売㈱八幡店', 'シガダイハツハンバイハチマンテン'),
    ('滋賀ﾀﾞｲﾊﾂ販売㈱水口店', 'シガダイハツハンバイミナクチテン'),
    ('滋賀ﾀﾞｲﾊﾂ販売㈱栗東店', 'シガダイハツハンバイリットウテン'),
    ('㈱滋賀ﾄﾖﾀ 大津瀬田店', 'シガトヨタオオツセタテン'),
    ('㈱滋賀ﾄﾖﾀ 国道8号栗東店', 'シガトヨタコクドウハチゴウリットウテン'),
    ('㈱滋賀ﾄﾖﾀ 瀬田･草津店', 'シガトヨタセタクサツテン'),
    ('㈱滋賀ﾄﾖﾀ ﾈｯﾂ栗東店', 'シガトヨタネッツリットウテン'),
    ('㈱滋賀ﾄﾖﾀ 水口泉店', 'シガトヨタミナクチイズミテン'),
    ('㈱滋賀ﾄﾖﾀ ﾓﾋﾞﾘﾃｨﾌﾟﾗｻﾞ守山East', 'シガトヨタモビリティプラザモリヤマイースト'),
    ('㈱滋賀ﾄﾖﾀ ﾓﾋﾞﾘﾃｨﾌﾟﾗｻﾞ守山West', 'シガトヨタモビリティプラザモリヤマウエスト'),
    ('滋賀日産自動車㈱安曇川店', 'シガニッサンジドウシャアドガワテン'),
    ('滋賀日産自動車㈱近江八幡店', 'シガニッサンジドウシャオウミハチマンテン'),
    ('滋賀日産自動車㈱大津店', 'シガニッサンジドウシャオオツテン'),
    ('滋賀日産自動車㈱堅田店', 'シガニッサンジドウシャカタタテン'),
    ('滋賀日産自動車㈱ｸﾞﾗﾝ南草津', 'シガニッサンジドウシャグランミナミクサツ'),
    ('滋賀日産自動車㈱瀬田店', 'シガニッサンジドウシャセタテン'),
    ('滋賀日産自動車㈱彦根店', 'シガニッサンジドウシャヒコネテン'),
    ('滋賀日産自動車㈱水口店', 'シガニッサンジドウシャミナクチテン'),
    ('滋賀日産自動車㈱水口西店', 'シガニッサンジドウシャミナクチニシテン'),
    ('滋賀日産自動車㈱南彦根店', 'シガニッサンジドウシャミナミヒコネテン'),
    ('滋賀日産自動車㈱守山店', 'シガニッサンジドウシャモリヤマテン'),
    ('滋賀日産自動車㈱八日市店', 'シガニッサンジドウシャヨウカイチテン'),
    ('滋賀日産自動車㈱栗東店', 'シガニッサンジドウシャリットウテン'),
    ('指定場所', 'シテイバショ'),
    ('株式会社忍自動車', 'シノブジドウシャ'),
    ('島中', 'シマナカ'),
    ('JUST', 'ジャスト'),
    ('ｼﾞｬﾍﾟｯｸｽ', 'ジャペックス'),
    ('車房N', 'シャボウエヌ'),
    ('juice factory', 'ジュースファクトリー'),
    ('ｼﾞｮｲｶﾙ八幡', 'ジョイカルヤワタ'),
    ('株式会社 松樹', 'ショウジュ'),
    ('株式会社 城南自動車', 'ジョウナンジドウシャ'),
    ('白岩自工', 'シライワジコウ'),
    ('株式会社 白野', 'シラノ'),
    ('ｼﾙｴｯﾄｼｮｯﾌﾟ中島', 'シルエットショップナカジマ'),
    ('有限会社 伸', 'シン'),
    ('新宮自動車', 'シングウジドウシャ'),
    ('新谷', 'シンタニ'),
    ('有限会社 新陽', 'シンヨウ'),
    ('株式会社SKY planning', 'スカイプランニング'),
    ('杉林ｵｰﾄｻｰﾋﾞｽ', 'スギバヤシオートサービス'),
    ('杉本自動車', 'スギモトジドウシャ'),
    ('杉山 翔大', 'スギヤマショウダイ'),
    ('鈴木', 'スズキ'),
    ('start', 'スタート'),
    ('株式会社 smile', 'スマイル'),
    ('株式会社 ｽﾘｰｴｽｺｰﾎﾟﾚｰｼｮﾝ', 'スリーエスコーポレーション'),
    ('合同会社SUWANC', 'スワンク'),
    ('青竜ｵｰﾄｻｰﾋﾞｽ', 'セイリュウオートサービス'),
    ('株式会社 ｾﾞﾆｽ', 'ゼニス'),
    ('ｾﾌﾃｨﾛｰﾄﾞ', 'セフティロード'),
    ('善ｵｰﾄ', 'ゼンオート'),
    ('有限会社 園部自動車工業', 'ソノベジドウシャコウギョウ'),
    ('株式会社 第一総合企画', 'ダイイチソウゴウキカク'),
    ('高岡 大丈', 'タカオカダイジョウ'),
    ('株式会社 高島代理店', 'タカシマダイリテン'),
    ('高田', 'タカダ'),
    ('ﾀｷｶﾞﾜｵｰﾄ', 'タキガワオート'),
    ('竹内ﾓｰﾀｰｽ', 'タケウチモータース'),
    ('武田ｵｰﾄｻｰﾋﾞｽ株式会社', 'タケダオートサービス'),
    ('有限会社 武田自動車', 'タケダジドウシャ'),
    ('竹谷教材株式会社', 'タケタニキョウザイ'),
    ('㈱武村商会', 'タケムラショウカイ'),
    ('ﾀｽｸｶﾞﾚｰｼﾞ', 'タスクガレージ'),
    ('田中', 'タナカ'),
    ('田中ｵｰﾄ', 'タナカオート'),
    ('田中電工', 'タナカデンコウ'),
    ('有限会社 ﾀﾅｶﾄﾚｰﾃﾞｨﾝｸﾞ', 'タナカトレーディング'),
    ('田畑', 'タバタ'),
    ('DOUBLE FOUR', 'ダブルフォー'),
    ('たむら屋ﾓｰﾀｰｽ', 'タムラヤモータース'),
    ('中央自工', 'チュウオウジコウ'),
    ('株式会社中央保険ｾﾝﾀｰ橋本', 'チュウオウホケンセンターハシモト'),
    ('ﾂｶｻｵｰﾄ', 'ツカサオート'),
    ('塚本', 'ツカモト'),
    ('株式会社 tsuji', 'ツジ'),
    ('辻内自動車', 'ツジウチジドウシャ'),
    ('土田企画ｵｰﾄｻｰﾋﾞｽ', 'ツチダキカクオートサービス'),
    ('株式会社 D-PROJECT', 'ディープロジェクト'),
    ('ﾃｲﾗｰｽﾞｵｰﾄ', 'テイラーズオート'),
    ('ﾃﾞｭｱﾙﾌﾞﾚｽﾄ', 'デュアルブレスト'),
    ('ﾃﾞﾝｷのいまいﾔﾍﾞ', 'デンキノイマイヤベ'),
    ('東京海上日動火災保険株式会社', 'トウキョウカイジョウニチドウカサイホケン'),
    ('戸梶', 'トカジ'),
    ('戸梶 蒼菜', 'トカジアオナ'),
    ('有限会社 徳本ﾚﾝﾀｶｰ', 'トクモトレンタカー'),
    ('戸塚', 'トツカ'),
    ('ﾄｯﾌﾟ京滋', 'トップキョウジ'),
    ('ﾄﾖﾀｶﾛｰﾗ京都㈱宇治店', 'トヨタカローラキョウトウジテン'),
    ('ﾄﾖﾀｶﾛｰﾗ京都㈱吉祥院店', 'トヨタカローラキョウトキッショウインテン'),
    ('ﾄﾖﾀｶﾛｰﾗ滋賀㈱大津店', 'トヨタカローラシガオオツテン'),
    ('ﾄﾖﾀｶﾛｰﾗ滋賀㈱草津店', 'トヨタカローラシガクサツテン'),
    ('ﾄﾖﾀｶﾛｰﾗ滋賀㈱甲賀店', 'トヨタカローラシガコウカテン'),
    ('ﾄﾖﾀｶﾛｰﾗ滋賀㈱湖南店', 'トヨタカローラシガコナンテン'),
    ('ﾄﾖﾀｶﾛｰﾗ滋賀㈱彦根店', 'トヨタカローラシガヒコネテン'),
    ('ﾄﾖﾀｶﾛｰﾗ滋賀㈱日野店', 'トヨタカローラシガヒノテン'),
    ('ﾄﾖﾀｶﾛｰﾗ滋賀㈱八日市店', 'トヨタカローラシガヨウカイチテン'),
    ('ﾄﾖﾀｶﾛｰﾗ滋賀㈱栗東店', 'トヨタカローラシガリットウテン'),
    ('ﾄﾖﾀﾓﾋﾞﾘﾃｨ滋賀㈱Sat彦根', 'トヨタモビリティシガサットヒコネ'),
    ('ﾄﾖﾀﾓﾋﾞﾘﾃｨ滋賀㈱栗東店', 'トヨタモビリティシガリットウテン'),
    ('ﾄﾖﾍﾟｯﾄｶﾄﾞﾉ八条店', 'トヨペットカドノハチジョウテン'),
    ('株式会社 中井鈑金自動車', 'ナカイバンキンジドウシャ'),
    ('中小路', 'ナカコウジ'),
    ('長崎', 'ナガサキ'),
    ('ﾅｶﾞｻｷｵｰﾄ', 'ナガサキオート'),
    ('中島鈑金', 'ナカジマバンキン'),
    ('永田ﾓｰﾀｰｽ', 'ナガタモータース'),
    ('株式会社 中野鈑金塗装', 'ナカノバンキントソウ'),
    ('中村', 'ナカムラ'),
    ('株式会社ﾅｶﾜｵｰﾄ', 'ナカワオート'),
    ('ﾆｼｳﾗmotor vehicle', 'ニシウラモータービークル'),
    ('西川路 隆司', 'ニシカワジタカシ'),
    ('西澤自動車工業 株式会社', 'ニシザワジドウシャコウギョウ'),
    ('西山自動車', 'ニシヤマジドウシャ'),
    ('日工自動車 株式会社', 'ニッコウジドウシャ'),
    ('日産大阪販売㈱Zushi高槻店', 'ニッサンオオサカハンバイズシタカツキテン'),
    ('日産大阪販売㈱枚方ﾋﾙｽﾞ', 'ニッサンオオサカハンバイヒラカタヒルズ'),
    ('株式会社日産ｻﾃｨｵ奈良', 'ニッサンサティオナラ'),
    ('日産ﾌﾟﾘﾝｽ滋賀販売㈱近江八幡店', 'ニッサンプリンスシガハンバイオウミハチマンテン'),
    ('日産ﾌﾟﾘﾝｽ滋賀販売㈱大津店', 'ニッサンプリンスシガハンバイオオツテン'),
    ('日産ﾌﾟﾘﾝｽ滋賀販売㈱堅田店', 'ニッサンプリンスシガハンバイカタタテン'),
    ('日産ﾌﾟﾘﾝｽ滋賀販売㈱彦根店', 'ニッサンプリンスシガハンバイヒコネテン'),
    ('日産ﾌﾟﾘﾝｽ滋賀販売㈱ﾌﾟﾘﾝﾋﾟｱ栗東', 'ニッサンプリンスシガハンバイプリンピアリットウ'),
    ('日産ﾌﾟﾘﾝｽ滋賀販売㈱水口店', 'ニッサンプリンスシガハンバイミナクチテン'),
    ('日産ﾌﾟﾘﾝｽ滋賀販売㈱栗東店', 'ニッサンプリンスシガハンバイリットウテン'),
    ('日産ﾌﾟﾘﾝｽ奈良販売㈱佐保店', 'ニッサンプリンスナラハンバイサホテン'),
    ('日産ﾌﾟﾘﾝｽ奈良販売㈱三条大路店', 'ニッサンプリンスナラハンバイサンジョウオオジテン'),
    ('日産ﾌﾟﾘﾝｽ奈良販売㈱奈良店', 'ニッサンプリンスナラハンバイナラテン'),
    ('株式会社 日彰工業', 'ニッショウコウギョウ'),
    ('日星自動車 株式会社', 'ニッセイジドウシャ'),
    ('日本ﾘｽｸｺﾝｻﾙﾃｨﾝｸﾞ', 'ニホンリスクコンサルティング'),
    ('Neo Space Auto', 'ネオスペースオート'),
    ('NEXT WORKS', 'ネクストワークス'),
    ('ﾈｯﾂﾄﾖﾀﾔｻｶ㈱宇治店', 'ネッツトヨタヤサカウジテン'),
    ('ﾈｯﾂﾄﾖﾀﾔｻｶ㈱大久保店', 'ネッツトヨタヤサカオオクボテン'),
    ('ﾈｯﾂﾄﾖﾀﾔｻｶ㈱木津店', 'ネッツトヨタヤサカキヅテン'),
    ('ﾈｯﾂﾄﾖﾀﾔｻｶ㈱松井山手店', 'ネッツトヨタヤサカマツイヤマテテン'),
    ('ﾈｯﾂﾄﾖﾀﾔｻｶ㈱桃山店', 'ネッツトヨタヤサカモモヤマテン'),
    ('株式会社ﾉｰﾌﾞﾙ', 'ノーブル'),
    ('則本 拓哉', 'ノリモトタクヤ'),
    ('株式会社 白梅ﾓｰﾀｰｽ', 'ハクバイモータース'),
    ('一建設 株式会社', 'ハジメケンセツ'),
    ('橋本鈑金', 'ハシモトバンキン'),
    ('Back Stage', 'バックステージ'),
    ('Bahati', 'バハティ'),
    ('株式会社ﾊﾟﾌﾞﾘｯｸﾎｰﾑ', 'パブリックホーム'),
    ('株式会社ﾊﾔｼｵｰﾄ', 'ハヤシオート'),
    ('ﾊﾟﾜｰﾄﾞ', 'パワード'),
    ('株式会社 ぱわふる自動車', 'パワフルジドウシャ'),
    ('有限会社ﾊﾞﾝｶｰ保険事務所', 'バンカーホケンジムショ'),
    ('株式会社BRB', 'ビーアールビー'),
    ('株式会社ﾋﾞｰﾉｽ', 'ビーノス'),
    ('株式会社ビーノス', 'ビーノス'),
    ('東山自動車工業株式会社', 'ヒガシヤマジドウシャコウギョウ'),
    ('樋口鈑金塗装', 'ヒグチバンキントソウ'),
    ('Victoria', 'ビクトリア'),
    ('樋谷自動車株式会社', 'ヒダニジドウシャ'),
    ('ﾋﾟｯﾄﾊｳｽ', 'ピットハウス'),
    ('株式会社ひまわり自動車', 'ヒマワリジドウシャ'),
    ('平井', 'ヒライ'),
    ('株式会社 広岡装美', 'ヒロオカソウビ'),
    ('広中ｵｰﾄ', 'ヒロナカオート'),
    ('ﾌｧｲﾅﾝｼｬﾙｱﾄﾞﾊﾞﾝｽ ﾗｲﾌﾌﾟﾗｻﾞ一里山', 'ファイナンシャルアドバンスライフプラザイチリヤマ'),
    ('ﾌｧｲﾅﾝｼｬﾙｱﾗｲｱﾝｽ', 'ファイナンシャルアライアンス'),
    ('ﾌｧｲﾝﾓｰﾀｰｽ', 'ファインモータース'),
    ('ﾌｨｱｯﾄ/ｱﾊﾞﾙﾄ滋賀', 'フィアットアバルトシガ'),
    ('Feel Auto', 'フィールオート'),
    ('福間 玲奈', 'フクマレイナ'),
    ('ﾌｼﾞｲﾓｰﾀｰｽ', 'フジイモータース'),
    ('富士ｵｰﾄ', 'フジオート'),
    ('藤村', 'フジムラ'),
    ('ﾌﾞﾗｲﾄ鈑金', 'ブライトバンキン'),
    ('FREE', 'フリー'),
    ('ﾌﾞﾙｰﾜｰｸｽ', 'ブルーワークス'),
    ('株式会社ﾌﾙｶﾜｽﾎﾟｰﾂ', 'フルカワスポーツ'),
    ('Fulegel Auto House', 'フレーゲルオートハウス'),
    ('ﾌﾚｯﾂ', 'フレッツ'),
    ('ﾌﾟﾚﾐｱﾑｵｰﾄ', 'プレミアムオート'),
    ('株式会社 friends', 'フレンズ'),
    ('株式会社ﾌﾛｲﾄﾞｼｯﾌﾟ', 'フロイドシップ'),
    ('有限会社 ﾌﾟﾛｳﾞｧﾝｽ', 'プロヴァンス'),
    ('PROTODRIVE', 'プロトドライブ'),
    ('ﾍﾟｲﾝﾄｶﾞﾚｰｼﾞ･ｼﾞｭﾝ', 'ペイントガレージジュン'),
    ('BELL CREA', 'ベルクレア'),
    ('有限会社 北斗商事', 'ホクトショウジ'),
    ('有限会社 ﾎｹﾝ', 'ホケン'),
    ('保険ﾏｲｽﾀｰ', 'ホケンマイスター'),
    ('有限会社 ﾎﾞﾃﾞｨｰｼｮｯﾌﾟいまい', 'ボディーショップイマイ'),
    ('ﾎﾞﾃﾞｨｰｼｮｯﾌﾟ小田', 'ボディーショップオダ'),
    ('ﾎﾞﾃﾞｨｰｼｮｯﾌﾟｼｵﾐ', 'ボディーショップシオミ'),
    ('株式会社 ﾎﾞﾃﾞｨｰｼｮｯﾌﾟﾂﾀﾞ', 'ボディーショップツダ'),
    ('ﾎﾞﾃﾞｨ-ｼｮｯﾌﾟ岩前', 'ボディショップイワマエ'),
    ('有限会社 ﾎﾞﾃﾞｨｼｮｯﾌﾟｴﾑ', 'ボディショップエム'),
    ('ﾎﾞﾃﾞｨｼｮｯﾌﾟ大橋', 'ボディショップオオハシ'),
    ('ﾎﾞﾃﾞｨｼｮｯﾌﾟｵｶｲ', 'ボディショップオカイ'),
    ('ﾎﾞﾃﾞｨｼｮｯﾌﾟ新谷', 'ボディショップシンタニ'),
    ('ﾎﾞﾃﾞｨｼｮｯﾌﾟ美車門', 'ボディショップビシャモン'),
    ('BODY SHOP Y’s', 'ボディショップワイズ'),
    ('株式会社ﾎﾞﾃﾞｨﾌｧｲﾄｼﾞｬﾊﾟﾝ', 'ボディファイトジャパン'),
    ('ﾎﾝﾀﾞｶｰｽﾞ京都 上京店', 'ホンダカーズキョウトカミギョウテン'),
    ('株式会社マークリー', 'マークリー'),
    ('ﾏｰﾍﾞﾘｯｸ', 'マーベリック'),
    ('ﾏｲｶｰｾﾝﾀｰ宇治', 'マイカーセンターウジ'),
    ('株式会社 ﾏｲｶｰｾﾝﾀｰ八日市', 'マイカーセンターヨウカイチ'),
    ('槙本保険', 'マキモトホケン'),
    ('ﾏｻｷｵｰﾄ', 'マサキオート'),
    ('ﾏｽﾀｰﾋﾟｰｽ', 'マスターピース'),
    ('松井', 'マツイ'),
    ('株式会社 松井自動車工業', 'マツイジドウシャコウギョウ'),
    ('松岡', 'マツオカ'),
    ('松下自動車', 'マツシタジドウシャ'),
    ('Matt Myoungho Akitaka Schultz', 'マットミョンホアキタカシュルツ'),
    ('松本', 'マツモト'),
    ('丸栄商会', 'マルエイショウカイ'),
    ('丸岡自動車', 'マルオカジドウシャ'),
    ('丸福自動車', 'マルフクジドウシャ'),
    ('丸目', 'マルメ'),
    ('株式会社 三久保商会', 'ミクボショウカイ'),
    ('株式会社美咲総務部', 'ミサキソウムブ'),
    ('三角 貞之', 'ミスミサダユキ'),
    ('ﾐﾌﾞ自動車', 'ミブジドウシャ'),
    ('株式会社 雅紙管', 'ミヤビシカン'),
    ('宮村', 'ミヤムラ'),
    ('村井自動車商会', 'ムライジドウシャショウカイ'),
    ('村上自動車商会', 'ムラカミジドウシャショウカイ'),
    ('村田', 'ムラタ'),
    ('ﾑﾗﾀｵｰﾄｻｰﾋﾞｽ', 'ムラタオートサービス'),
    ('ﾒｲｸﾜﾝ', 'メイクワン'),
    ('ﾒｶﾄﾞｯｸｵｶﾀﾞ', 'メカドックオカダ'),
    ('株式会社 ﾒﾃﾞｨｹｱ･ﾘﾊﾋﾞﾘ', 'メディケアリハビリ'),
    ('物部ﾓｰﾀｰｽ', 'モノベモータース'),
    ('門谷自動車工業', 'モンタニジドウシャコウギョウ'),
    ('山形 憲司', 'ヤマガタケンジ'),
    ('ﾔﾏｼｹﾞ', 'ヤマシゲ'),
    ('ﾔﾏﾀﾞﾓｰﾀｰｽ', 'ヤマダモータース'),
    ('ﾔﾏﾅｶPAINT', 'ヤマナカペイント'),
    ('ﾕｰﾎﾟｽ8号栗東店', 'ユーポスハチゴウリットウテン'),
    ('Yutacar', 'ユタカー'),
    ('ﾕﾅｲﾃｯﾄﾞｼｽﾃﾑ', 'ユナイテッドシステム'),
    ('吉岡自動車', 'ヨシオカジドウシャ'),
    ('株式会社ヨシダ工務店', 'ヨシダコウムテン'),
    ('吉田自動車工業', 'ヨシダジドウシャコウギョウ'),
    ('吉仲', 'ヨシナカ'),
    ('株式会社 吉仲自動車販売', 'ヨシナカジドウシャハンバイ'),
    ('株式会社 RIZE', 'ライズ'),
    ('ﾗｲｽﾞｱｯﾌﾟ', 'ライズアップ'),
    ('ﾗｲﾄﾞｸﾗﾌﾄ', 'ライドクラフト'),
    ('株式会社 ﾗｲﾌﾌﾟﾗｻﾞﾊﾟｰﾄﾅｰ', 'ライフプラザパートナー'),
    ('LuxuryStyle', 'ラグジュアリースタイル'),
    ('ﾗｼﾞｯﾌﾟ', 'ラジップ'),
    ('有限会社ﾗﾝﾄﾞｵｰﾄ', 'ランドオート'),
    ('栗東ｵｰﾄｾﾝﾀｰ', 'リットウオートセンター'),
    ('ﾘﾆｱﾃｨ', 'リニアティ'),
    ('revival shop G-Flow', 'リバイバルショップジーフロー'),
    ('ﾘﾌﾚｯｸｽ', 'リフレックス'),
    ('流儀ﾃﾞｻﾞｲﾝ', 'リュウギデザイン'),
    ('株式会社ﾘﾕｰｼﾞｭ', 'リユージュ'),
    ('株式会社ﾘﾗｲｱﾝｽ', 'リライアンス'),
    ('ﾘﾝｸﾞﾌﾟﾛｻｰﾋﾞｽ株式会社', 'リングプロサービス'),
    ('RooM925', 'ルームキューニーゴ'),
    ('ﾙﾉｰ滋賀栗東', 'ルノーシガリットウ'),
    ('ﾚｲｸｵｰﾄ', 'レイクオート'),
    ('ﾚｸｻｽCPO彦根', 'レクサスシーピーオーヒコネ'),
    ('ﾚｼﾌﾟﾛｵｰﾄ', 'レシプロオート'),
    ('株式会社 RESO', 'レソ'),
    ('Reverent service', 'レバレントサービス'),
    ('ﾛﾝﾄﾞﾍﾞﾙ', 'ロンドベル'),
    ('YM garage', 'ワイエムガレージ'),
    ('YG auto', 'ワイジーオート'),
    ('Y’sAUTO', 'ワイズオート'),
    ('ONEPAGE', 'ワンページ'),
]
def _seed_clients(c):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.executemany('INSERT OR IGNORE INTO clients (name, reading, created_at) VALUES (?, ?, ?)',
                  [(name, reading, now) for name, reading in _CLIENT_SEED])
    # reading が空のレコードを更新
    for name, reading in _CLIENT_SEED:
        c.execute('UPDATE clients SET reading=? WHERE name=? AND (reading IS NULL OR reading="")',
                  (reading, name))

# ══════════════════════════════════════════════════════════
# ⚠️ セールス部門 仮稼働用ダミーデータ（本物の30台リスト受領後に削除する）
#   ・_SALES_DEMO_* と _seed_sales_demo() と init_db 内の呼び出し1行を消せば撤去できる
#   ・投入は一度きり（settings の sales_demo_seeded で管理）。画面から消しても復活しない
#   ・撤去用API: POST /api/admin/clear-sales
# ══════════════════════════════════════════════════════════
_SALES_DEMO_STAFF = ['岡田 涼太', '西村 綾', '森本 大輔', '中井 千夏',
                     '山下 拓也', '藤原 みなみ', '高橋 誠', '小川 由紀']

# (車番, 車種, 区分, 地域)
_SALES_DEMO_VEHICLES = [
    ('8001', 'ﾊｽﾗｰ',      '軽自動車', '京都'), ('8002', 'ﾀﾝﾄ',       '軽自動車', '京都'),
    ('8003', 'N-BOX',     '軽自動車', '京都'), ('8004', 'ﾜｺﾞﾝR',     '軽自動車', '京都'),
    ('8005', 'ﾐﾗｲｰｽ',    '軽自動車', '京都'), ('8006', 'ｱﾙﾄ',       '軽自動車', '京都'),
    ('8007', 'ﾑｰｳﾞ',      '軽自動車', '京都'), ('8008', 'ｽﾍﾟｰｼｱ',   '軽自動車', '京都'),
    ('8009', 'ﾌｨｯﾄ',      '普通車',   '京都'), ('8010', 'ﾔﾘｽ',       '普通車',   '京都'),
    ('8011', 'ﾉｰﾄ',       '普通車',   '京都'), ('8012', 'ｱｸｱ',       '普通車',   '京都'),
    ('8013', 'ﾙｰﾐｰ',      '普通車',   '京都'), ('8014', 'ｼｴﾝﾀ',      'ﾜﾝﾎﾞｯｸｽ', '京都'),
    ('8015', 'ADﾊﾞﾝ',     '商用車',   '京都'),
    ('8016', 'ﾊｽﾗｰ',      '軽自動車', '滋賀'), ('8017', 'ﾀﾝﾄ',       '軽自動車', '滋賀'),
    ('8018', 'N-BOX',     '軽自動車', '滋賀'), ('8019', 'ﾜｺﾞﾝR',     '軽自動車', '滋賀'),
    ('8020', 'ﾐﾗｲｰｽ',    '軽自動車', '滋賀'), ('8021', 'ｱﾙﾄ',       '軽自動車', '滋賀'),
    ('8022', 'ﾑｰｳﾞ',      '軽自動車', '滋賀'), ('8023', 'ﾃﾞｲｽﾞ',     '軽自動車', '滋賀'),
    ('8024', 'ﾌｨｯﾄ',      '普通車',   '滋賀'), ('8025', 'ﾔﾘｽ',       '普通車',   '滋賀'),
    ('8026', 'ﾉｰﾄ',       '普通車',   '滋賀'), ('8027', 'ｱｸｱ',       '普通車',   '滋賀'),
    ('8028', 'ﾀﾝｸ',       '普通車',   '滋賀'), ('8029', 'ﾌﾘｰﾄﾞ',     'ﾜﾝﾎﾞｯｸｽ', '滋賀'),
    ('8030', 'ﾊｲｾﾞｯﾄ',    '商用車',   '滋賀'),
]

# (車番, 状態, 開始日オフセット, 終了日オフセット or None, 担当, 顧客, 適用)
_SALES_DEMO_EVENTS = [
    ('8001', '貸出中', -4,  6,   '岡田 涼太',   'ｻﾝﾌﾟﾙ自動車',   '車検'),
    ('8003', '貸出中', -2,  12,  '西村 綾',     'ﾃｽﾄ工業',       '一般修理'),
    ('8005', '貸出中', -17, 3,   '森本 大輔',   'ﾃﾞﾓﾓｰﾀｰｽ',     '一般修理'),   # 15日超（赤字）
    ('8009', '貸出中', -1,  20,  '中井 千夏',   'ｻﾝﾌﾟﾙ商会',     '新規新車'),
    ('8016', '貸出中', -6,  8,   '山下 拓也',   'ﾃｽﾄ運輸',       '点検'),
    ('8018', '貸出中', -3,  4,   '藤原 みなみ', 'ｻﾝﾌﾟﾙ建設',     '車検'),
    ('8024', '貸出中', -23, 2,   '高橋 誠',     'ﾃﾞﾓ商事',       '乗り換え新車'),  # 15日超（赤字）
    ('8026', '貸出中', -5,  15,  '小川 由紀',   'ｻﾝﾌﾟﾙ電機',     '新規新車'),
    ('8002', '予約済', 2,   9,   '岡田 涼太',   'ﾃｽﾄ自動車販売', '車検'),
    ('8010', '予約済', 5,   11,  '中井 千夏',   'ｻﾝﾌﾟﾙ興業',     '乗り換え新車'),
    ('8019', '予約済', 1,   6,   '山下 拓也',   'ﾃﾞﾓ物産',       '点検'),
    ('8027', '予約済', 7,   14,  '高橋 誠',     'ﾃｽﾄ産業',       '一般修理'),
    ('8007', '修理中', -3,  4,   '森本 大輔',   '',              ''),
    ('8022', '修理中', -1,  6,   '藤原 みなみ', '',              ''),
    ('8013', '点検中', 0,   1,   '西村 綾',     '',              ''),
    ('8029', '車検中', -2,  2,   '小川 由紀',   '',              ''),
]

def _demo_insp_offset(i):
    """デモ車両の車検満了日を散らす（一部は2ヶ月以内で赤字警告になる）"""
    return 25 + (i * 11) % 320

def _refresh_sales_demo(c):
    """デモの日付を今日基準に振り直す。
    デプロイのたびに実行し、経過日数や予約がいつ見ても自然に見えるようにする。
    社員が入力した本物のイベント（notes が 'デモデータ' 以外）には触れない。"""
    row = c.execute("SELECT value FROM settings WHERE key='sales_demo_seeded'").fetchone()
    if not row or row[0] == 'skipped':
        return 0
    base = datetime.strptime(today_jst(), '%Y-%m-%d')
    off  = lambda n: (base + timedelta(days=n)).strftime('%Y-%m-%d')
    n = 0
    for num, status, s_off, e_off, staff, client, cat in _SALES_DEMO_EVENTS:
        n += c.execute(
            """UPDATE events SET start_date=?, end_date=?
               WHERE notes='デモデータ' AND vehicle_id IN
                 (SELECT id FROM vehicles WHERE number=? AND department='sales')""",
            (off(s_off), off(e_off) if e_off is not None else None, num)).rowcount
    for i, (num, _ctype, _cat, _region) in enumerate(_SALES_DEMO_VEHICLES):
        c.execute("UPDATE vehicles SET inspection_date=? WHERE number=? AND department='sales'",
                  (off(_demo_insp_offset(i)), num))
    return n

def _seed_sales_demo(c):
    """セールス部門の仮稼働用ダミーデータを一度だけ投入する"""
    if c.execute("SELECT value FROM settings WHERE key='sales_demo_seeded'").fetchone():
        return 0
    if c.execute("SELECT COUNT(*) FROM vehicles WHERE department='sales'").fetchone()[0] > 0:
        # 既に本物のデータが入っているなら何もしない
        c.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('sales_demo_seeded','skipped')")
        return 0

    now  = datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')
    base = datetime.strptime(today_jst(), '%Y-%m-%d')
    off  = lambda n: (base + timedelta(days=n)).strftime('%Y-%m-%d')

    for name in _SALES_DEMO_STAFF:
        c.execute("INSERT OR IGNORE INTO staff (name, department) VALUES (?, 'sales')", (name,))

    ids = {}
    for i, (num, ctype, cat, region) in enumerate(_SALES_DEMO_VEHICLES):
        max_id = c.execute('SELECT MAX(id) FROM vehicles').fetchone()[0] or 0
        vid = max_id + 1
        c.execute('''INSERT INTO vehicles
            (id,number,car_type,year,full_number,inspection_date,region,car_category,department)
            VALUES (?,?,?,?,?,?,?,?,'sales')''',
            (vid, num, ctype, '', f'{region}500ｻ {num}', off(_demo_insp_offset(i)), region, cat))
        ids[num] = vid

    # 全車にまず在庫イベント（「状態未登録」を出さないため）
    for num, vid in ids.items():
        c.execute('''INSERT INTO events
            (vehicle_id,status,start_date,end_date,staff,client,category,notes,created_at,location,washed,interior_cleaned)
            VALUES (?,'在庫',?,NULL,'','','','',?,?,1,?)''',
            (vid, off(-30), now, '京都本社' if ids[num] % 2 else '滋賀支店', 1 if vid % 3 == 0 else 0))

    # 稼働中・予約・整備を上書き（created_at が新しいほど優先される）
    for num, status, s_off, e_off, staff, client, cat in _SALES_DEMO_EVENTS:
        vid = ids.get(num)
        if not vid:
            continue
        c.execute('''INSERT INTO events
            (vehicle_id,status,start_date,end_date,staff,client,category,notes,created_at)
            VALUES (?,?,?,?,?,?,?,?,?)''',
            (vid, status, off(s_off), off(e_off) if e_off is not None else None,
             staff, client, cat, 'デモデータ', now))

    c.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('sales_demo_seeded',?)", (now,))
    return len(ids)

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
            inspection_date TEXT,
            region TEXT DEFAULT '',
            studless INTEGER DEFAULT 0,
            is_rental_other INTEGER DEFAULT 0,
            car_category TEXT DEFAULT '',
            department TEXT DEFAULT 'rental'
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
            created_at TEXT,
            location TEXT DEFAULT '',
            washed INTEGER DEFAULT 0,
            interior_cleaned INTEGER DEFAULT 0
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
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            reading TEXT DEFAULT '',
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS staff (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            department TEXT DEFAULT 'rental'
        );
    ''')
    conn.commit()

    # 既存DBへのカラム追加（既に存在する場合は無視）
    for sql in [
        "ALTER TABLE vehicles ADD COLUMN region TEXT DEFAULT ''",
        "ALTER TABLE vehicles ADD COLUMN studless INTEGER DEFAULT 0",
        "ALTER TABLE vehicles ADD COLUMN is_rental_other INTEGER DEFAULT 0",
        "ALTER TABLE vehicles ADD COLUMN car_category TEXT DEFAULT ''",
        "ALTER TABLE events ADD COLUMN location TEXT DEFAULT ''",
        "ALTER TABLE events ADD COLUMN washed INTEGER DEFAULT 0",
        "ALTER TABLE events ADD COLUMN interior_cleaned INTEGER DEFAULT 0",
        "ALTER TABLE events ADD COLUMN client_contact TEXT DEFAULT ''",
        "ALTER TABLE clients ADD COLUMN reading TEXT DEFAULT ''",
        # 部門（事業部）。既存データはすべてレンタカー事業部として扱う
        "ALTER TABLE vehicles ADD COLUMN department TEXT DEFAULT 'rental'",
        # 車両ごとの備考（管理表の備考欄をそのまま持たせる）
        "ALTER TABLE vehicles ADD COLUMN notes TEXT DEFAULT ''",
        "ALTER TABLE staff ADD COLUMN department TEXT DEFAULT 'rental'",
    ]:
        try:
            c.execute(sql)
        except Exception:
            pass
    conn.commit()

    # 部門未設定のレコードはレンタカー事業部に寄せる（既存336台・既存社員の保護）
    for sql in [
        "UPDATE vehicles SET department='rental' WHERE department IS NULL OR department=''",
        "UPDATE staff SET department='rental' WHERE department IS NULL OR department=''",
    ]:
        try:
            c.execute(sql)
        except Exception:
            pass
    conn.commit()

    # 取引先マスタ：常にINSERT OR IGNOREで差分追加（欠損補完）
    _seed_clients(c)
    conn.commit()

    # 車両所在地シード（Excelデータより）
    for num, loc in _VEHICLE_LOC.items():
        c.execute("UPDATE vehicles SET region=? WHERE number=? AND (region IS NULL OR region='')", (loc, num))
    conn.commit()

    # 従業員マスタ：初期データ
    for name in STAFF_NAMES:
        try:
            c.execute("INSERT OR IGNORE INTO staff (name, department) VALUES (?, 'rental')", (name,))
        except Exception:
            pass
    conn.commit()

    # ⚠️ セールス部門の仮稼働用ダミーデータ（本物のリスト受領後にこのブロックを削除）
    try:
        n = _seed_sales_demo(c)
        conn.commit()
        if n:
            app.logger.warning(f'[demo] セールス部門のダミーデータを投入しました: {n}台')
        else:
            # 投入済みならデプロイのたびに日付だけ今日基準へ振り直す
            m = _refresh_sales_demo(c)
            conn.commit()
            if m:
                app.logger.warning(f'[demo] ダミーの日付を更新しました: {m}件')
    except Exception as e:
        app.logger.warning(f'[demo] ダミーデータ処理に失敗: {e}')

    base = os.path.dirname(__file__)
    if c.execute('SELECT COUNT(*) FROM vehicles').fetchone()[0] == 0:
        vf = os.path.join(base, 'vehicles.json')
        if not os.path.exists(vf):
            vf = os.path.join(base, 'data', 'vehicles.json')
        if os.path.exists(vf):
            for v in json.load(open(vf, encoding='utf-8-sig')):
                c.execute('''INSERT OR IGNORE INTO vehicles
                    (id,number,car_type,year,full_number,inspection_date,department)
                    VALUES (?,?,?,?,?,?,?)''',
                    (v['id'], v['number'], v.get('car_type',''), v.get('year',''),
                     v.get('full_number',''), v.get('inspection_date',''),
                     norm_dept(v.get('department'))))
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
    today = today_jst()
    conn = get_db()
    row = conn.execute(
        '''SELECT * FROM events WHERE vehicle_id=? AND start_date<=? AND (end_date IS NULL OR end_date>=?)
           ORDER BY start_date DESC LIMIT 1''',
        (vehicle_id, today, today)).fetchone()
    conn.close()
    return dict(row) if row else None

def find_vehicle_by_number(number, dept=DEFAULT_DEPT):
    """車番から車両を引く。LINE経由の照合は既定でレンタカー事業部に限定する
    （両部門で4桁車番が重複しても、既存の動きが変わらないようにするため）"""
    conn = get_db()
    if dept:
        row = conn.execute('SELECT * FROM vehicles WHERE number=? AND department=?',
                           (number, dept)).fetchone()
    else:
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
STAFF_NAMES = ['平田弘子','内田麻鈴','山本圭太','吉岡佑真','藤田頼人','北川舞花','福田竜也','奥谷慎太郎','川上那歩','田中奈々実']
CATEGORIES  = ['損保','代車','マンスリー','通常']

# 車番 → 所在地（2025/07/15 エクセルより）
_VEHICLE_LOC = {
"6401":"京都","6671":"京都","6675":"滋賀","4032":"滋賀","4040":"京都","1496":"京都","3684":"滋賀",
"2958":"滋賀","4025":"京都","4024":"京都","5967":"滋賀","3219":"滋賀","3218":"京都","3814":"京都",
"4023":"滋賀","4026":"滋賀","4202":"京都","4427":"京都","4426":"滋賀","4879":"滋賀","4884":"京都",
"4886":"京都","4883":"滋賀","4882":"滋賀","4885":"京都","2140":"京都","5931":"滋賀","6402":"滋賀",
"6795":"京都","6839":"京都","6668":"滋賀","6308":"滋賀","5311":"京都","5758":"京都","7433":"滋賀",
"7462":"滋賀","7934":"京都","7985":"京都","1227":"滋賀","1657":"京都","1306":"京都","1795":"京都",
"3229":"滋賀","8418":"滋賀","6252":"京都","7956":"滋賀","9710":"滋賀","9715":"京都","9388":"滋賀",
"1868":"京都","2790":"京都","2801":"滋賀","2812":"滋賀","7578":"京都","2564":"京都","8805":"滋賀",
"8088":"滋賀","2548":"京都","7368":"京都","7579":"滋賀","7939":"滋賀","7941":"京都","9722":"京都",
"9809":"滋賀","1264":"京都","1308":"京都","1858":"滋賀","2562":"滋賀","2563":"京都","2782":"京都",
"2932":"滋賀","1243":"滋賀","1419":"京都","8264":"京都","4066":"滋賀","9048":"滋賀","7794":"京都",
"8222":"京都","8994":"滋賀","3660":"滋賀","3878":"京都","3885":"京都","9224":"滋賀","9209":"滋賀",
"7636":"京都","5420":"京都","5557":"滋賀","1265":"滋賀","1867":"京都","1970":"京都","2789":"滋賀",
"9072":"滋賀","9070":"京都","5920":"京都","8012":"滋賀","6610":"滋賀","7940":"京都","1926":"京都",
"7183":"滋賀","5641":"滋賀","6309":"京都","5571":"京都","8072":"滋賀","1094":"滋賀","3202":"京都",
"4044":"京都","4875":"滋賀","5314":"滋賀","5877":"京都","5865":"京都","5870":"滋賀","5868":"滋賀",
"5866":"京都","6393":"京都","6398":"滋賀","6392":"滋賀","6391":"京都","6390":"京都","6388":"滋賀",
"7267":"滋賀","7662":"京都","7991":"京都","8381":"滋賀","8396":"滋賀","8394":"京都","8397":"京都",
"8393":"滋賀","8377":"滋賀","8382":"京都","8380":"京都","8378":"滋賀","8395":"滋賀","8428":"京都",
"8512":"京都","8862":"滋賀","8861":"滋賀","9424":"京都","9425":"京都","1459":"京都","7946":"京都",
"7947":"滋賀","3379":"滋賀","3380":"京都","3381":"京都","3382":"滋賀","3383":"滋賀","3384":"京都",
"3385":"京都","3386":"滋賀","3387":"滋賀","3388":"京都","4127":"京都","4128":"滋賀","4262":"滋賀",
"4374":"京都","4398":"京都","4394":"滋賀","4395":"滋賀","4874":"京都","5315":"京都","5741":"滋賀",
"5867":"滋賀","6182":"滋賀","6183":"京都","6184":"滋賀","6394":"滋賀","6399":"京都","6395":"京都",
"6389":"滋賀","6396":"滋賀","8322":"京都","2933":"滋賀","3169":"滋賀","6452":"京都","6453":"京都",
"6454":"滋賀","7292":"滋賀","7680":"京都","7945":"京都","7944":"滋賀","9600":"滋賀","9553":"京都",
"9551":"京都","9548":"滋賀","9550":"滋賀","6826":"滋賀","7309":"京都","7930":"京都","8013":"滋賀",
"8603":"滋賀","8802":"京都","9433":"京都","9546":"滋賀","9545":"滋賀","1114":"京都","1113":"滋賀",
"1115":"滋賀","1117":"京都","1108":"京都","1909":"滋賀","1910":"滋賀","2432":"京都","2433":"京都",
"3256":"滋賀","3257":"滋賀","3258":"京都","3886":"京都","5418":"滋賀","5419":"滋賀","6529":"京都",
"7228":"京都","7229":"滋賀","7495":"滋賀","7639":"京都","8786":"京都","8784":"滋賀","8785":"滋賀",
"9068":"京都","9069":"京都","9071":"滋賀","2690":"滋賀","4448":"京都","8344":"京都","7670":"滋賀",
"5320":"京都","6762":"京都","4963":"滋賀","6344":"滋賀","7274":"京都","7277":"京都","7660":"滋賀",
"7678":"滋賀","1024":"京都","1602":"滋賀","4956":"滋賀","5331":"京都","5039":"京都","5566":"滋賀",
"5951":"滋賀","6223":"京都","6824":"京都","5734":"京都","1259":"滋賀","1428":"滋賀","1637":"京都",
"2166":"京都","2319":"滋賀","4117":"滋賀","9103":"京都","7351":"京都","7369":"滋賀","1226":"滋賀",
"9331":"京都","3030":"京都","4261":"滋賀","6872":"滋賀","6871":"京都","6868":"京都","6870":"滋賀",
"6869":"滋賀","6862":"京都","6867":"京都","6866":"滋賀","6863":"滋賀","6864":"京都","7099":"京都",
"7271":"滋賀","7270":"滋賀","7399":"京都","7532":"京都","7531":"滋賀","7666":"滋賀","7756":"京都",
"7996":"京都","7992":"滋賀","7993":"滋賀","7995":"京都","7984":"京都","7994":"滋賀","8324":"滋賀",
"8859":"京都","8860":"京都","2119":"京都","2120":"滋賀","2912":"滋賀","2913":"京都","3013":"京都",
"3015":"滋賀","3017":"滋賀","3107":"京都","3094":"京都","8161":"滋賀","2066":"滋賀","2100":"京都",
"2105":"京都","8529":"滋賀","8759":"滋賀","9102":"京都","9234":"京都","2509":"滋賀","1934":"滋賀",
"2133":"京都",
}

# 会話状態管理（未完了コマンドの継続）
CONV_STATE = {}   # source_id → state dict

def parse_date(s):
    s = s.strip().rstrip('〜~～')
    now = datetime.now(JST).date()
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
    """日付・期間を抽出。〜/から/〜/- いずれも対応"""
    start_d = end_d = None
    # トークンを結合して全体から検索（スペースで分かれているケースも対応）
    full = ' '.join(tokens)

    # 日付範囲パターン（7月1日から7月15日 / 7/1〜7/15 / 7月1日〜15日 等）
    dm = re.search(
        r'(\d{1,2}[月/]\d{1,2}日?)\s*[〜~～から\-～]+\s*(\d{1,2}[月/]\d{1,2}日?)',
        full)
    if dm:
        sd = parse_date(dm.group(1))
        ed = parse_date(dm.group(2))
        if sd: start_d = sd
        if ed: end_d   = ed
        return start_d, end_d

    # 単独の開始日（〜付き）
    dm2 = re.search(r'(\d{1,2}[月/]\d{1,2}日?)\s*[〜~～から]+', full)
    if dm2:
        sd = parse_date(dm2.group(1))
        if sd: start_d = sd
        return start_d, end_d

    # 単独の日付
    for t in tokens:
        sd = parse_date(t)
        if sd and not start_d:
            start_d = sd
    return start_d, end_d

def is_date_token(t):
    return bool(re.search(r'\d{1,2}[/月]\d{1,2}', t) or '〜' in t or '~' in t or '～' in t)

def register_event(v, status, state):
    """DBにイベントを登録し、確認メッセージを返す"""
    today   = today_jst()                          # C) JST対応
    start_d = state.get('start_date') or today
    end_d   = state.get('end_date')
    staff   = state.get('staff') or ''
    client  = state.get('client') or ''
    category= state.get('category') or ''
    mileage = state.get('mileage') or ''
    remarks = state.get('notes') or ''
    location= state.get('location') or ''
    # 洗車・室内清掃は在庫状態にのみ紐づく情報。
    # 貸出・予約に入った時点で古い実績は無効になるためクリアする。
    if status == '在庫':
        washed  = 1 if state.get('washed') else 0
        interior_cleaned = 1 if state.get('interior_cleaned') else 0
    else:
        washed = interior_cleaned = 0

    notes_parts = []
    if mileage:
        notes_parts.append(f"走行距離:{mileage}km")
    if location:
        notes_parts.append(f"所在地:{location}")
    if washed:
        notes_parts.append("洗車済")
    if interior_cleaned:
        notes_parts.append("室内清掃済")
    if remarks:
        notes_parts.append(remarks)
    notes_str = ' / '.join(notes_parts)

    conn = get_db()
    c    = conn.cursor()

    # A) B) 既存のオープンイベント（終了日なし or 将来終了）を自動クローズ
    # 新しいイベントの開始日以降は新しいイベントが有効になるため、前のイベントを締める
    if status == '在庫':
        # 返却・キャンセル: 現在アクティブなイベントをすべてクローズ
        c.execute('''UPDATE events SET end_date=?
                     WHERE vehicle_id=? AND (end_date IS NULL OR end_date >= ?)''',
                  (start_d, v['id'], start_d))
    else:
        # 貸出・予約等: 終了日のない既存イベントを新しい開始日の前日でクローズ
        prev_end = (datetime.strptime(start_d, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
        c.execute('''UPDATE events SET end_date=?
                     WHERE vehicle_id=? AND end_date IS NULL AND start_date < ?''',
                  (prev_end, v['id'], start_d))

    c.execute(
        'INSERT INTO events (vehicle_id,status,start_date,end_date,staff,client,category,notes,created_at,location,washed,interior_cleaned) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
        (v['id'], status, start_d, end_d, staff, client, category, notes_str,
         datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S'), location, washed, interior_cleaned))
    conn.commit()
    conn.close()

    msg = _build_line_msg(
        v['number'], v.get('car_type', ''), status, staff, client, start_d, end_d, category
    )
    extras = []
    if mileage: extras.append(f"{mileage}ｷﾛ")
    if remarks: extras.append(remarks)
    if extras:  msg += ' ' + ' '.join(extras)
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
    missing = list(state['missing'])

    if text.strip() in ['キャンセル', 'cancel', 'やめる', 'ヤメル']:
        del CONV_STATE[source_id]
        return "❌ 操作を中止しました"

    # ── キャンセル番号選択 ──
    if state.get('step') == 'select_cancel':
        evs = state['events']
        try:
            idx = int(text.strip()) - 1
            if idx < 0 or idx >= len(evs):
                raise ValueError
        except (ValueError, TypeError):
            return f"❓ 1〜{len(evs)}の番号で入力してください\n「キャンセル」で中止"
        ev      = evs[idx]
        t_today = today_jst()
        conn    = get_db()
        conn.execute('UPDATE events SET end_date=? WHERE id=?', (t_today, ev['id']))
        conn.commit(); conn.close()
        del CONV_STATE[source_id]
        v      = state['vehicle']
        period = f"{ev['start_date']}〜{ev.get('end_date') or '（返却未定）'}"
        msg    = (f"✅ キャンセルしました\n🚗 {v['number']} {v.get('car_type','')}\n"
                  f"状態: {ev['status']}\n顧客: {ev.get('client','')}\n期間: {period}")
        return msg

    # ─── テキスト全体からキーワードを検索（文章形式・スペース形式どちらも対応）───

    # ① カテゴリ：テキスト全体から検索
    cat_found = None
    for cat in CATEGORIES:
        if cat in text:
            cat_found = cat
            break

    # ② スタッフ名：「担当者は〜」パターン → 既知リスト の順で検索
    staff_found = None
    m_staff = re.search(r'担当[者はは：:\s]*([^\s、,，。　]+)', text)
    if m_staff:
        candidate = m_staff.group(1)
        if not any(cat in candidate for cat in CATEGORIES):
            staff_found = candidate
    if not staff_found:
        for s in STAFF_NAMES:
            if s in text:
                staff_found = s
                break

    # ③ 顧客名：「顧客名は〜」「顧客は〜」「取引先は〜」パターンで検索
    client_found = None
    m_client = re.search(r'(?:顧客名?|取引先)[はは：:\s]*([^\s、,，。　]+)', text)
    if m_client:
        candidate = m_client.group(1)
        if not any(cat in candidate for cat in CATEGORIES):
            client_found = candidate

    # ─── 取得できた情報をstateに反映 ───
    if 'category' in missing and cat_found:
        state['category'] = cat_found
        missing.remove('category')
    elif 'category' in missing and len(missing) == 1:
        return "❓「損保」「代車」「マンスリー」「通常」のどれかを教えてください\n※「キャンセル」で中止"

    if 'staff' in missing and staff_found:
        state['staff'] = staff_found
        missing.remove('staff')

    if 'client' in missing and client_found:
        state['client'] = client_found
        missing.remove('client')

    # ─── パターン未検出の残りはトークンから推測 ───
    if any(f in missing for f in ['staff', 'client']):
        tokens = re.split(r'[\s、,，。・　]+', text.strip())
        tokens = [t for t in tokens if t]
        # カテゴリ・担当者・顧客パターンのトーククンを除去
        remaining = [t for t in tokens
                     if not any(cat in t for cat in CATEGORIES)
                     and not re.match(r'担当|顧客|取引先', t)
                     and t != staff_found
                     and t != client_found]

        if 'staff' in missing and remaining:
            state['staff'] = remaining[0]
            remaining = remaining[1:]
            missing.remove('staff')

        if 'client' in missing and remaining:
            state['client'] = ' '.join(remaining)
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

    # ── フォームURL送信 ──
    if text in ['フォーム', '登録', '登録フォーム', 'form']:
        return '__FORM_BUTTON__'

    # ── 一覧・状況 ──
    if text in ['一覧', '状況', 'status']:
        today = today_jst()
        conn = get_db()
        # LINEからの照会はレンタカー事業部のみが対象（セールス部門は集計に含めない）
        total    = conn.execute("SELECT COUNT(*) FROM vehicles WHERE department='rental'").fetchone()[0]
        _cnt = lambda cond: conn.execute(
            f"""SELECT COUNT(DISTINCT e.vehicle_id) FROM events e
                JOIN vehicles v ON v.id = e.vehicle_id AND v.department='rental'
                WHERE {cond} AND e.start_date<=? AND (e.end_date IS NULL OR e.end_date>=?)""",
            (today, today)).fetchone()[0]
        rentals  = _cnt("e.status='貸出中'")
        reserved = _cnt("e.status='予約済'")
        repairs  = _cnt("e.status IN ('車検中','点検中','修理中')")
        conn.close()
        return (f"📊 {datetime.now(JST).strftime('%m/%d')} 車両状況\n"
                f"総台数: {total}台\n"
                f"🔴 貸出中: {rentals}台\n"
                f"🟡 予約済: {reserved}台\n"
                f"🔧 整備中: {repairs}台\n"
                f"🟢 在庫: {total - rentals - reserved - repairs}台")

    # ── 車番? ──
    m = re.match(r'^(\d{2,5})[?？]$', text)
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
    m = re.match(r'^(\d{2,5})\s+(.+)$', text)
    if m:
        number = m.group(1)
        rest   = m.group(2).strip()
        v = find_vehicle_by_number(number)
        if not v:
            return f"❌ 車番 {number} は見つかりません"

        tokens = rest.split()

        # ステータスキーワードを探す
        status   = None
        stat_key = None
        for i, t in enumerate(tokens):
            if t in STATUS_MAP:
                status   = STATUS_MAP[t]
                stat_key = t
                tokens.pop(i)
                break

        if not status:
            return (f"❓ 操作が分かりませんでした\n"
                    f"使い方: {number} [配車/予約/返却/キャンセル/車検/点検/修理] ...\n"
                    f"例: {number} 配車 田中 滋賀トヨタ 損保 28026")

        # ── 返却・キャンセル ──
        if status == '在庫':
            mileage  = extract_mileage(tokens)
            t_today  = today_jst()
            conn     = get_db()

            if stat_key in ('返却',):
                # 【返却】今日アクティブな1件だけ閉じる（将来の予約は残す）
                ev = conn.execute('''
                    SELECT * FROM events
                    WHERE vehicle_id=? AND start_date<=?
                      AND (end_date IS NULL OR end_date>=?)
                      AND status NOT IN ("在庫")
                    ORDER BY start_date DESC LIMIT 1
                ''', (v['id'], t_today, t_today)).fetchone()
                conn.close()
                if not ev:
                    return f"🚗 {v['number']} {v.get('car_type','')} は現在在庫中です"
                conn2 = get_db()
                notes_upd = f"走行距離:{mileage}km" if mileage else (ev['notes'] or '')
                conn2.execute('UPDATE events SET end_date=?, notes=? WHERE id=?',
                              (t_today, notes_upd, ev['id']))
                conn2.commit(); conn2.close()
                msg = (f"✅ 返却しました\n🚗 {v['number']} {v.get('car_type','')}\n"
                       f"状態: 在庫（返却済）\n顧客: {ev['client'] or ''}")
                if mileage: msg += f"\n走行距離: {mileage}km"
                return msg

            else:
                # 【キャンセル】アクティブ・将来のイベントを取得
                evs = conn.execute('''
                    SELECT * FROM events
                    WHERE vehicle_id=?
                      AND (end_date IS NULL OR end_date>=?)
                      AND status NOT IN ("在庫")
                    ORDER BY start_date ASC
                ''', (v['id'], t_today)).fetchall()
                conn.close()
                if not evs:
                    return f"🚗 {v['number']} {v.get('car_type','')} は現在在庫中です"
                if len(evs) == 1:
                    ev = evs[0]
                    conn2 = get_db()
                    conn2.execute('UPDATE events SET end_date=? WHERE id=?', (t_today, ev['id']))
                    conn2.commit(); conn2.close()
                    period = f"{ev['start_date']}〜{ev['end_date'] or '（返却未定）'}"
                    return (f"✅ キャンセルしました\n🚗 {v['number']} {v.get('car_type','')}\n"
                            f"状態: {ev['status']}\n顧客: {ev['client'] or ''}\n期間: {period}")
                # 複数件 → 番号選択
                lines = [f"🚗 {v['number']} {v.get('car_type','')}",
                         "どの予約をキャンセルしますか？"]
                for i, ev in enumerate(evs, 1):
                    period = f"{ev['start_date']}〜{ev['end_date'] or '返却未定'}"
                    lines.append(f"{i}. {ev['status']} {period} {ev['client'] or ''}")
                lines.append("\n番号で回答（「キャンセル」で中止）")
                CONV_STATE[source_id] = {
                    'step': 'select_cancel', 'vehicle': v,
                    'events': [dict(e) for e in evs],
                    'mileage': mileage, 'missing': [],
                }
                return '\n'.join(lines)

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

# LINEのリッチメニュー・フォームボタンから開くURL。
# 端末に前回の部門が残っていてもレンタカー事業部で開くよう dept を明示する。
LIFF_URL   = 'https://yoshioka-rental-1.onrender.com/liff?dept=rental'
LINE_API   = 'https://api.line.me/v2/bot'
LINE_DATA  = 'https://api-data.line.me/v2/bot'

def _make_richmenu_png():
    """PIL不要・Pythonのみで2500x843のリッチメニュー用PNG生成"""
    W, H = 2500, 843
    BORDER = 90
    # 行ごとのピクセルデータ（3バイト×W）
    DARK  = b'\x1a\x3a\x5c' * W   # #1a3a5c ダークブルー（上下帯）
    GREEN = b'\x4c\xaf\x50' * W   # #4caf50 グリーン（ボタン面）
    # フィルターバイト(0x00) + ピクセル列
    rows = [b'\x00' + (DARK if y < BORDER or y >= H - BORDER else GREEN)
            for y in range(H)]
    raw = b''.join(rows)

    def _chunk(tag, data):
        crc = zlib.crc32(tag + data) & 0xffffffff
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', crc)

    ihdr = _chunk(b'IHDR', struct.pack('>IIBBBBB', W, H, 8, 2, 0, 0, 0))
    idat = _chunk(b'IDAT', zlib.compress(raw, 6))
    iend = _chunk(b'IEND', b'')
    return b'\x89PNG\r\n\x1a\n' + ihdr + idat + iend

QUICK_REPLY_FORM = {
    'items': [
        {
            'type': 'action',
            'imageUrl': 'https://scdn.line-apps.com/n/channel_devcenter/img/fx/01_1_cafe.png',
            'action': {
                'type': 'uri',
                'label': '📱 入力フォーム',
                'uri': LIFF_URL,
            }
        }
    ]
}

def send_line_reply(reply_token, message):
    if not LINE_CHANNEL_TOKEN:
        return
    requests.post(
        'https://api.line.me/v2/bot/message/reply',
        headers={'Authorization': f'Bearer {LINE_CHANNEL_TOKEN}',
                 'Content-Type': 'application/json'},
        json={'replyToken': reply_token,
              'messages': [{'type': 'text', 'text': message,
                            'quickReply': QUICK_REPLY_FORM}]},
        timeout=5)

def send_form_button(reply_token):
    """入力フォームへのボタンメッセージを返信"""
    if not LINE_CHANNEL_TOKEN:
        return
    flex_msg = {
        'type': 'flex',
        'altText': '📱 車両状態登録フォーム',
        'contents': {
            'type': 'bubble',
            'size': 'kilo',
            'header': {
                'type': 'box',
                'layout': 'vertical',
                'contents': [
                    {'type': 'text', 'text': '🚗 吉岡商会 車両管理',
                     'weight': 'bold', 'color': '#ffffff', 'size': 'md'}
                ],
                'backgroundColor': '#1a3a5c',
                'paddingAll': '14px',
            },
            'body': {
                'type': 'box',
                'layout': 'vertical',
                'contents': [
                    {'type': 'text', 'text': '車両の状態を登録・更新する',
                     'size': 'sm', 'color': '#555555', 'margin': 'none'},
                ],
                'paddingAll': '14px',
            },
            'footer': {
                'type': 'box',
                'layout': 'vertical',
                'contents': [
                    {
                        'type': 'button',
                        'style': 'primary',
                        'color': '#4CAF50',
                        'action': {
                            'type': 'uri',
                            'label': '📱 入力フォームを開く',
                            'uri': LIFF_URL,
                        },
                        'height': 'sm',
                    }
                ],
                'paddingAll': '10px',
            },
        }
    }
    requests.post(
        'https://api.line.me/v2/bot/message/reply',
        headers={'Authorization': f'Bearer {LINE_CHANNEL_TOKEN}',
                 'Content-Type': 'application/json'},
        json={'replyToken': reply_token, 'messages': [flex_msg]},
        timeout=5)

def send_line_push(to, message):
    """LINE グループ・ユーザーへのプッシュ送信"""
    if not LINE_CHANNEL_TOKEN or not to:
        app.logger.warning(f'[LINE push] skipped: token={bool(LINE_CHANNEL_TOKEN)} to={bool(to)}')
        return
    try:
        r = requests.post(
            'https://api.line.me/v2/bot/message/push',
            headers={'Authorization': f'Bearer {LINE_CHANNEL_TOKEN}',
                     'Content-Type': 'application/json'},
            json={'to': to, 'messages': [{'type': 'text', 'text': message}]},
            timeout=5)
        if r.status_code != 200:
            app.logger.error(f'[LINE push] error {r.status_code}: {r.text}')
    except Exception as e:
        app.logger.error(f'[LINE push] exception: {e}')

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

def dept_group_id(dept):
    """部門ごとの通知先グループID。
    セールス部門は既定で通知しない（レンタカーのグループLINEを汚さないため）。
    将来セールス用のグループを使う場合は settings に line_group_id_sales を入れる。"""
    if norm_dept(dept) == 'sales':
        return get_setting('line_group_id_sales')
    return get_setting('line_group_id')

def push_to_dept_group(dept, message):
    """部門のグループLINEへ通知。送信したら True"""
    gid = dept_group_id(dept)
    if gid and LINE_CHANNEL_TOKEN:
        send_line_push(gid, message)
        return True
    return False

def vehicle_dept(conn, vehicle_id):
    """車両IDから部門を引く（見つからなければレンタカー扱い）"""
    row = conn.execute('SELECT department FROM vehicles WHERE id=?', (vehicle_id,)).fetchone()
    return norm_dept(row['department'] if row else None)

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
    """車両一覧。?dept=rental|sales で部門を絞る（未指定は従来どおり全件）"""
    dept = req_dept()
    conn = get_db()
    if dept:
        rows = [dict(r) for r in conn.execute(
            'SELECT * FROM vehicles WHERE department=? ORDER BY CAST(number AS INTEGER)',
            (dept,)).fetchall()]
    else:
        rows = [dict(r) for r in conn.execute(
            'SELECT * FROM vehicles ORDER BY CAST(number AS INTEGER)').fetchall()]
    conn.close()
    return jsonify(rows)

@app.route('/api/clients', methods=['GET'])
@login_required
def api_clients_get():
    conn = get_db()
    rows = [{'name': r[0], 'reading': r[1] or ''} for r in conn.execute('SELECT name, reading FROM clients ORDER BY name').fetchall()]
    conn.close()
    return jsonify(rows)

@app.route('/api/clients', methods=['POST'])
@login_required
def api_clients_post():
    d = request.get_json() or {}
    name = d.get('name', '').strip()
    if not name:
        return jsonify({'error': 'name required'}), 400
    reading = d.get('reading', '').strip()
    conn = get_db()
    conn.execute('INSERT OR IGNORE INTO clients (name, reading, created_at) VALUES (?, ?, ?)',
                 (name, reading, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

@app.route('/api/events', methods=['GET'])
@login_required
def api_events_get():
    conn = get_db()
    rows = [dict(r) for r in conn.execute('SELECT * FROM events ORDER BY id').fetchall()]
    conn.close()
    return jsonify(rows)

_HANKAKU_MAP = {
    'ア':'ｱ','イ':'ｲ','ウ':'ｳ','エ':'ｴ','オ':'ｵ',
    'カ':'ｶ','キ':'ｷ','ク':'ｸ','ケ':'ｹ','コ':'ｺ',
    'サ':'ｻ','シ':'ｼ','ス':'ｽ','セ':'ｾ','ソ':'ｿ',
    'タ':'ﾀ','チ':'ﾁ','ツ':'ﾂ','テ':'ﾃ','ト':'ﾄ',
    'ナ':'ﾅ','ニ':'ﾆ','ヌ':'ﾇ','ネ':'ﾈ','ノ':'ﾉ',
    'ハ':'ﾊ','ヒ':'ﾋ','フ':'ﾌ','ヘ':'ﾍ','ホ':'ﾎ',
    'マ':'ﾏ','ミ':'ﾐ','ム':'ﾑ','メ':'ﾒ','モ':'ﾓ',
    'ヤ':'ﾔ','ユ':'ﾕ','ヨ':'ﾖ',
    'ラ':'ﾗ','リ':'ﾘ','ル':'ﾙ','レ':'ﾚ','ロ':'ﾛ',
    'ワ':'ﾜ','ヲ':'ｦ','ン':'ﾝ',
    'ァ':'ｧ','ィ':'ｨ','ゥ':'ｩ','ェ':'ｪ','ォ':'ｫ',
    'ッ':'ｯ','ャ':'ｬ','ュ':'ｭ','ョ':'ｮ','ー':'ｰ',
    'ガ':'ｶﾞ','ギ':'ｷﾞ','グ':'ｸﾞ','ゲ':'ｹﾞ','ゴ':'ｺﾞ',
    'ザ':'ｻﾞ','ジ':'ｼﾞ','ズ':'ｽﾞ','ゼ':'ｾﾞ','ゾ':'ｿﾞ',
    'ダ':'ﾀﾞ','ヂ':'ﾁﾞ','ヅ':'ﾂﾞ','デ':'ﾃﾞ','ド':'ﾄﾞ',
    'バ':'ﾊﾞ','ビ':'ﾋﾞ','ブ':'ﾌﾞ','ベ':'ﾍﾞ','ボ':'ﾎﾞ',
    'パ':'ﾊﾟ','ピ':'ﾋﾟ','プ':'ﾌﾟ','ペ':'ﾍﾟ','ポ':'ﾎﾟ',
}

def _to_hankaku(s):
    return ''.join(_HANKAKU_MAP.get(c, c) for c in s)

def _fmt_date(ds):
    if not ds: return ''
    try:
        dt = datetime.strptime(ds, '%Y-%m-%d')
        return f"{dt.month}/{dt.day}"
    except:
        return ds

def _build_line_msg(num, car_type, status, staff, client, start_d, end_d, category):
    line1_parts = [p for p in [num, car_type, status, staff] if p]
    line1 = '🚗 ' + ' '.join(line1_parts)
    period = _fmt_date(start_d)
    if end_d:
        period += '〜' + _fmt_date(end_d)
    elif period:
        period += '〜'
    line3_parts = [p for p in [period, _to_hankaku(category) if category else ''] if p]
    lines = [line1]
    if client: lines.append(client)
    if line3_parts: lines.append(' '.join(line3_parts))
    return '\n'.join(lines)

def _event_summary_line(d, conn):
    """イベントのサマリ文字列を生成してグループLINEに送信"""
    v = conn.execute('SELECT number, car_type, department FROM vehicles WHERE id=?',
                     (d['vehicle_id'],)).fetchone()
    num      = v['number'] if v else str(d['vehicle_id'])
    car_type = v['car_type'] if v else ''
    dept     = norm_dept(v['department'] if v else None)
    msg = _build_line_msg(
        num, car_type,
        d.get('status', ''), d.get('staff', ''), d.get('client', ''),
        d.get('start_date') or '', d.get('end_date') or '', d.get('category', '')
    )
    push_to_dept_group(dept, msg)

@app.route('/api/events', methods=['POST'])
@login_required
def api_events_post():
    d = request.get_json()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    conn = get_db()
    cur = conn.execute(
        'INSERT INTO events (vehicle_id,status,start_date,end_date,staff,client,client_contact,category,notes,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
        (d['vehicle_id'], d['status'], d.get('start_date'), d.get('end_date'),
         d.get('staff',''), d.get('client',''), d.get('client_contact',''), d.get('category',''), d.get('notes',''), now))
    conn.commit()
    row = dict(conn.execute('SELECT * FROM events WHERE id=?', (cur.lastrowid,)).fetchone())
    _event_summary_line(d, conn)
    conn.close()
    return jsonify(row)

@app.route('/api/events/<int:eid>', methods=['PUT'])
@login_required
def api_events_put(eid):
    d = request.get_json()
    conn = get_db()
    conn.execute(
        'UPDATE events SET vehicle_id=?,status=?,start_date=?,end_date=?,staff=?,client=?,client_contact=?,category=?,notes=? WHERE id=?',
        (d['vehicle_id'], d['status'], d.get('start_date'), d.get('end_date'),
         d.get('staff',''), d.get('client',''), d.get('client_contact',''), d.get('category',''), d.get('notes',''), eid))
    conn.commit()
    row = conn.execute('SELECT * FROM events WHERE id=?', (eid,)).fetchone()
    _event_summary_line(d, conn)
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

# ── 朝の一斉報告 ────────────────────────────────────────────
MORNING_STAFF_KYOTO = ['平田弘子','内田麻鈴','山本圭太','吉岡佑真','藤田頼人','北川舞花']
MORNING_STAFF_SHIGA = ['福田竜也','奥谷慎太郎','川上那歩','田中奈々実']
MORNING_STAFF = MORNING_STAFF_KYOTO + MORNING_STAFF_SHIGA
_STAFF_REGION = ({s: '京都' for s in MORNING_STAFF_KYOTO} |
                 {s: '滋賀' for s in MORNING_STAFF_SHIGA})

# 終了日なしの非在庫イベントをいつまで有効とみなすか
_STALE_DAYS = 60

def match_staff(ev_staff):
    """イベントの担当者フィールドをスタッフマスタにマッチング"""
    if not ev_staff: return 'その他'
    for s in MORNING_STAFF:
        if ev_staff in s or s in ev_staff:
            return s
    return ev_staff

def _period_label(ev):
    """'7/30〜' 形式。単日なら '7/30'"""
    s = _fmt_date(ev.get('start_date') or '')
    if not s: return ''
    e = ev.get('end_date') or ''
    if e and e == ev.get('start_date'):
        return s
    return s + '～'

def _clean_note(ev):
    """営業が入力した備考のみ返す（システム由来の文言は除外）"""
    note = ((ev or {}).get('notes') or '').strip()
    if note.startswith('所在地:') or note.startswith('エクセル取込'):
        return ''
    return note

def _stock_marks(v, ev):
    """在庫車両につく状態マーク（★洗車済 ☆中清掃済 ●冬タイヤ）"""
    m = ''
    if (ev or {}).get('washed'):           m += '★'
    if (ev or {}).get('interior_cleaned'): m += '☆'
    if v.get('studless'):                  m += '●'
    return m

def resolve_vehicle_states(date=None, dept=DEFAULT_DEPT):
    """指定日時点の全車両の状態を確定する。

    各車両につき「その日にかかっているイベント」のうち登録が最も新しい1件を採用
    （＝新しい予約・修正が常に優先）。該当なしなら在庫扱い。
    dept を指定するとその部門の車両だけを対象にする（既定はレンタカー事業部）。
    """
    d = date or today_jst()
    conn = get_db()
    if dept:
        vehicles = [dict(r) for r in conn.execute(
            'SELECT * FROM vehicles WHERE department=? ORDER BY CAST(number AS INTEGER)',
            (dept,)).fetchall()]
    else:
        vehicles = [dict(r) for r in conn.execute(
            'SELECT * FROM vehicles ORDER BY CAST(number AS INTEGER)').fetchall()]
    rows = conn.execute(
        '''SELECT * FROM events
           WHERE start_date<=? AND (end_date IS NULL OR end_date>=?)
           ORDER BY created_at DESC, id DESC''', (d, d)).fetchall()
    conn.close()

    stale_before = (datetime.strptime(d, '%Y-%m-%d') - timedelta(days=_STALE_DAYS)).strftime('%Y-%m-%d')
    latest = {}
    for r in rows:
        r = dict(r)
        # 終了日なしのまま放置された古い貸出/予約は無視する
        if (not r.get('end_date')) and r.get('status') != '在庫' \
           and (r.get('start_date') or '') < stale_before:
            continue
        latest.setdefault(r['vehicle_id'], r)

    out = []
    for v in vehicles:
        ev = latest.get(v['id'])
        status = (ev or {}).get('status') or '在庫'
        out.append({'vehicle': v, 'event': ev, 'status': status})
    return out

def blocks_to_text(blocks):
    """プレビュー用の構造化ブロックから一斉ライン本文を組み立てる"""
    out = []
    for b in blocks:
        if b['type'] == 'blank':
            out.append('')
            continue
        out.append(b['text'])
        if b.get('sub'):
            out.append(b['sub'])
    return '\n'.join(out)

def build_morning_blocks(date=None):
    """朝一の一斉ラインを構造化ブロックで返す。

    プレビュー画面から車両を直接選んで編集・削除できるよう、
    明細行には vehicle_id / event_id を持たせる。本文はここから生成するので、
    画面表示と実際に送られる文面が食い違うことはない。
    """
    d = date or today_jst()
    states = resolve_vehicle_states(d, dept='rental')

    stock = {'京都': [], '滋賀': []}
    resv  = {'京都': {}, '滋賀': {}}
    maint = {'京都': [], '滋賀': []}
    unknown = []
    for st in states:
        v, ev, status = st['vehicle'], st['event'], st['status']
        vregion = v.get('region') if v.get('region') in ('京都', '滋賀') else '京都'
        if ev is None:
            # 一度も状態登録がない車両は在庫と断定できないため別枠
            unknown.append(v)
        elif status == '在庫':
            loc = (ev or {}).get('location') or ''
            region = '滋賀' if '滋賀' in loc else ('京都' if '京都' in loc else vregion)
            stock[region].append((v, ev))
        elif status == '予約済':
            staff  = match_staff((ev or {}).get('staff', ''))
            region = _STAFF_REGION.get(staff, vregion)
            resv[region].setdefault(staff, []).append((v, ev))
        elif status in ('修理中', '点検中', '車検中'):
            maint[vregion].append((v, ev, status))

    dt = datetime.strptime(d, '%Y-%m-%d')
    blocks = []
    def head(text):  blocks.append({'type': 'head',    'text': text})
    def section(t):  blocks.append({'type': 'section', 'text': t})
    def plain(text): blocks.append({'type': 'plain',   'text': text})
    def blank():     blocks.append({'type': 'blank'})
    def item(text, v, ev, kind, sub=None):
        b = {'type': 'item', 'text': text, 'kind': kind,
             'vehicle_id': v['id'], 'number': v['number'],
             'event_id': (ev or {}).get('id')}
        if sub: b['sub'] = sub
        blocks.append(b)

    head(f'【{dt.month}/{dt.day} 朝一 在庫・予約】')
    blank()
    plain('★洗車済　☆中清掃済　●冬タイヤ')
    blank()

    for region, flag, order in (('京都', '🔵', MORNING_STAFF_KYOTO),
                                ('滋賀', '🟢', MORNING_STAFF_SHIGA)):
        items = stock[region]
        section(f'{flag}{region} 在庫 {len(items)}台')
        if items:
            for v, ev in items:
                item(f"・{v['car_type']} {v['number']}{_stock_marks(v, ev)}".rstrip(),
                     v, ev, '在庫')
        else:
            plain('（在庫なし）')

        blank()
        section(f'{flag}{region} 予約')
        names = order + [s for s in resv[region] if s not in order]
        first = True
        for s in names:
            if s not in resv[region]:
                continue
            if not first: blank()
            plain(f'【{s}】')
            for v, ev in resv[region][s]:
                parts = [f"・{v['car_type']}", v['number'], _period_label(ev)]
                if ev.get('client'): parts.append(ev['client'])
                note = _clean_note(ev)
                item(' '.join(p for p in parts if p), v, ev, '予約済',
                     sub=f'　（{note}）' if note else None)
            first = False
        if first:
            plain('（予約なし）')

        for label, wanted in (('修理', ('修理中',)), ('点検・車検', ('点検中', '車検中'))):
            group = [x for x in maint[region] if x[2] in wanted]
            if not group:
                continue
            blank()
            section(f'▼{label}')
            for v, ev, status in group:
                item(f"・{v['car_type']} {v['number']}", v, ev, status)

        if region == '京都':
            blank()
            blank()

    if unknown:
        nums = ' '.join(v['number'] for v in unknown)
        blank()
        plain(f'※状態未登録 {len(unknown)}台（{nums}）')

    return blocks

def build_morning_report(date=None):
    """朝一の一斉ライン本文（テキスト）"""
    return blocks_to_text(build_morning_blocks(date))

@app.route('/api/morning-report', methods=['GET', 'POST'])
def api_morning_report():
    key = request.headers.get('X-Admin-Key','') or request.args.get('key','')
    if key != ADMIN_PASS:
        return jsonify({'error': 'Unauthorized'}), 401
    msg      = build_morning_report(request.args.get('date') or None)
    group_id = get_setting('line_group_id')
    if group_id and LINE_CHANNEL_TOKEN:
        send_line_push(group_id, msg)
        return jsonify({'sent': True, 'message': msg})
    return jsonify({'sent': False, 'reason': 'group_id or token not set', 'message': msg})

@app.route('/api/morning-report/preview', methods=['GET'])
@login_required
def api_morning_report_preview():
    """送信せずに本文と明細を返す（前夜の事前確認・修正用）"""
    d = request.args.get('date') or today_jst()
    blocks = build_morning_blocks(d)
    return jsonify({'date': d, 'message': blocks_to_text(blocks), 'blocks': blocks})

@app.route('/api/morning-report/entry/<int:eid>/remove', methods=['POST'])
@login_required
def api_morning_entry_remove(eid):
    """プレビューから登録を取り消す。

    予約・修理などを取り消した車両は、他に予定がなければ本来「在庫」に戻る。
    単にイベントを消すだけだと状態未登録に落ちてしまうため、
    restock 指定時は在庫として登録し直す。グループLINEへの通知は行わない。
    """
    body    = request.get_json() or {}
    restock = bool(body.get('restock'))
    d       = body.get('date') or today_jst()

    conn = get_db()
    row  = conn.execute('SELECT * FROM events WHERE id=?', (eid,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'not found'}), 404
    vehicle_id = row['vehicle_id']
    conn.execute('DELETE FROM events WHERE id=?', (eid,))

    restocked = False
    if restock:
        v = conn.execute('SELECT region FROM vehicles WHERE id=?', (vehicle_id,)).fetchone()
        loc = (v['region'] or '') if v else ''
        conn.execute(
            '''INSERT INTO events
               (vehicle_id,status,start_date,end_date,staff,client,category,notes,created_at,location)
               VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (vehicle_id, '在庫', d, None, '', '', '', '',
             datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S'), loc))
        restocked = True

    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'restocked': restocked, 'vehicle_id': vehicle_id})

@app.route('/api/morning-report/send', methods=['POST'])
@login_required
def api_morning_report_send():
    """プレビュー画面からの手動送信"""
    body = request.get_json() or {}
    msg  = body.get('message') or build_morning_report(body.get('date') or None)
    group_id = get_setting('line_group_id')
    if not group_id or not LINE_CHANNEL_TOKEN:
        return jsonify({'error': 'LINE not configured'}), 500
    send_line_push(group_id, msg)
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

# ── 取引先マスタ強制シード（管理者専用） ─────────────────────
@app.route('/api/admin/seed-clients', methods=['POST'])
def admin_seed_clients():
    key = request.headers.get('X-Admin-Key','') or request.args.get('key','')
    if key != ADMIN_PASS:
        return jsonify({'error': 'unauthorized'}), 401
    conn = get_db()
    c = conn.cursor()
    _seed_clients(c)
    conn.commit()
    count = c.execute('SELECT COUNT(*) FROM clients').fetchone()[0]
    conn.close()
    return jsonify({'ok': True, 'count': count})

# ── フォームリンクをグループへプッシュ（管理者専用） ───────────
@app.route('/api/admin/send-form-link', methods=['POST'])
def admin_send_form_link():
    key = request.headers.get('X-Admin-Key','') or request.args.get('key','')
    if key != ADMIN_PASS:
        return jsonify({'error': 'Unauthorized'}), 401
    group_id = get_setting('line_group_id')
    if not group_id or not LINE_CHANNEL_TOKEN:
        return jsonify({'error': 'グループIDまたはトークン未設定'}), 400
    pin_text = '📌 このメッセージをピン留めしてください\n長押し → ピン留め → グループ画面上部に常時表示されます'
    flex_msg = {
        'type': 'flex',
        'altText': '📱 車両状態登録フォーム',
        'contents': {
            'type': 'bubble',
            'size': 'mega',
            'header': {
                'type': 'box',
                'layout': 'vertical',
                'contents': [
                    {'type': 'text', 'text': '🚗 吉岡商会 車両管理システム',
                     'weight': 'bold', 'color': '#ffffff', 'size': 'lg'},
                    {'type': 'text', 'text': '車両の状態登録・更新はこちら',
                     'color': '#bbddff', 'size': 'sm', 'margin': 'sm'},
                ],
                'backgroundColor': '#1a3a5c',
                'paddingAll': '16px',
            },
            'footer': {
                'type': 'box',
                'layout': 'vertical',
                'spacing': 'sm',
                'contents': [
                    {
                        'type': 'button',
                        'style': 'primary',
                        'color': '#4CAF50',
                        'action': {
                            'type': 'uri',
                            'label': '📱 入力フォームを開く',
                            'uri': LIFF_URL,
                        },
                        'height': 'md',
                    },
                    {
                        'type': 'button',
                        'style': 'secondary',
                        'action': {
                            'type': 'uri',
                            'label': '📲 ホーム画面に追加する方法',
                            'uri': 'https://yoshioka-rental-1.onrender.com/qr',
                        },
                        'height': 'sm',
                    },
                    {
                        'type': 'text',
                        'text': 'ホーム画面に追加すると次回からLINEを開かずワンタップで使えます',
                        'size': 'xs',
                        'color': '#aaaaaa',
                        'align': 'center',
                        'wrap': True,
                        'margin': 'sm',
                    }
                ],
                'paddingAll': '12px',
            },
        }
    }
    requests.post(
        'https://api.line.me/v2/bot/message/push',
        headers={'Authorization': f'Bearer {LINE_CHANNEL_TOKEN}',
                 'Content-Type': 'application/json'},
        json={'to': group_id, 'messages': [flex_msg]},
        timeout=5)
    return jsonify({'sent': True, 'note': 'LINEグループでメッセージを長押し→ピン留めしてください'})

# ── LINEリッチメニュー設定（管理者専用） ─────────────────────
@app.route('/api/admin/setup-richmenu', methods=['POST'])
def admin_setup_richmenu():
    key = request.headers.get('X-Admin-Key','') or request.args.get('key','')
    if key != ADMIN_PASS:
        return jsonify({'error': 'Unauthorized'}), 401
    if not LINE_CHANNEL_TOKEN:
        return jsonify({'error': 'LINE_CHANNEL_TOKEN未設定'}), 400

    hdrs = {'Authorization': f'Bearer {LINE_CHANNEL_TOKEN}'}

    # 1. リッチメニュー作成
    menu_def = {
        'size': {'width': 2500, 'height': 843},
        'selected': True,
        'name': '入力フォームメニュー',
        'chatBarText': '📱 入力フォームを開く',
        'areas': [{
            'bounds': {'x': 0, 'y': 0, 'width': 2500, 'height': 843},
            'action': {'type': 'uri', 'uri': LIFF_URL}
        }]
    }
    r1 = requests.post(f'{LINE_API}/richmenu',
                       headers={**hdrs, 'Content-Type': 'application/json'},
                       json=menu_def, timeout=15)
    if r1.status_code != 200:
        return jsonify({'error': f'作成失敗: {r1.text}'}), 500
    rich_menu_id = r1.json()['richMenuId']

    # 2. 画像アップロード
    png = _make_richmenu_png()
    r2 = requests.post(f'{LINE_DATA}/richmenu/{rich_menu_id}/content',
                       headers={**hdrs, 'Content-Type': 'image/png'},
                       data=png, timeout=30)
    if r2.status_code != 200:
        return jsonify({'error': f'画像アップロード失敗: {r2.text}'}), 500

    # 3. デフォルトリッチメニューに設定（全ユーザー対象）
    requests.post(f'{LINE_API}/user/all/richmenu/{rich_menu_id}',
                  headers=hdrs, timeout=10)

    return jsonify({'ok': True, 'richMenuId': rich_menu_id,
                    'note': 'LINEアプリでボットに話しかけると画面下部にボタンが表示されます'})

# ── QRコード・ホーム画面追加ガイドページ ─────────────────────
QR_PAGE = '''<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>フォームをホーム画面に追加</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Meiryo',sans-serif;background:#f0f2f5;min-height:100vh;padding:20px}
.card{background:white;border-radius:16px;padding:24px;max-width:420px;margin:0 auto;box-shadow:0 4px 20px rgba(0,0,0,0.12)}
h1{font-size:18px;color:#1a3a5c;text-align:center;margin-bottom:6px}
.sub{font-size:13px;color:#888;text-align:center;margin-bottom:20px}
#qr{text-align:center;margin:16px 0}
.btn{display:block;width:100%;padding:14px;background:#4CAF50;color:white;border:none;border-radius:10px;font-size:16px;font-weight:bold;text-align:center;text-decoration:none;margin-bottom:12px;cursor:pointer}
.steps{background:#f8f9fa;border-radius:10px;padding:16px;margin-top:16px}
.steps h2{font-size:13px;font-weight:bold;color:#1a3a5c;margin-bottom:10px}
.step{display:flex;align-items:flex-start;gap:10px;margin-bottom:10px;font-size:13px;line-height:1.5}
.step-num{background:#1a3a5c;color:white;border-radius:50%;width:22px;height:22px;min-width:22px;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:bold}
.os-tabs{display:flex;gap:8px;margin-bottom:10px}
.os-tab{flex:1;padding:6px;border:2px solid #ddd;border-radius:8px;text-align:center;font-size:13px;cursor:pointer;background:white}
.os-tab.active{border-color:#1a3a5c;background:#1a3a5c;color:white}
.os-steps{display:none}.os-steps.show{display:block}
</style>
</head>
<body>
<div class="card">
  <h1>🚗 車両登録フォーム</h1>
  <p class="sub">ホーム画面に追加すると次回からワンタップで開けます</p>

  <div id="qr"></div>

  <a class="btn" href="/liff?dept=rental">📱 今すぐフォームを開く</a>

  <div class="steps">
    <h2>📲 ホーム画面への追加方法</h2>
    <div class="os-tabs">
      <div class="os-tab active" onclick="showOS('ios')">iPhone</div>
      <div class="os-tab" onclick="showOS('android')">Android</div>
    </div>
    <div class="os-steps show" id="ios-steps">
      <div class="step"><div class="step-num">1</div><div>「今すぐフォームを開く」をタップ</div></div>
      <div class="step"><div class="step-num">2</div><div>画面下部の <strong>共有ボタン（□↑）</strong> をタップ</div></div>
      <div class="step"><div class="step-num">3</div><div><strong>「ホーム画面に追加」</strong> を選択</div></div>
      <div class="step"><div class="step-num">4</div><div>名前を「車両登録」などにして <strong>「追加」</strong></div></div>
    </div>
    <div class="os-steps" id="android-steps">
      <div class="step"><div class="step-num">1</div><div>「今すぐフォームを開く」をタップ</div></div>
      <div class="step"><div class="step-num">2</div><div>右上の <strong>メニュー（⋮）</strong> をタップ</div></div>
      <div class="step"><div class="step-num">3</div><div><strong>「ホーム画面に追加」</strong> を選択</div></div>
      <div class="step"><div class="step-num">4</div><div>名前を確認して <strong>「追加」</strong></div></div>
    </div>
  </div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
<script>
new QRCode(document.getElementById('qr'), {
  text: window.location.origin + '/liff?dept=rental',
  width: 180, height: 180,
  colorDark: '#1a3a5c', colorLight: '#ffffff',
  correctLevel: QRCode.CorrectLevel.M
});
function showOS(os) {
  document.querySelectorAll('.os-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.os-steps').forEach(s => s.classList.remove('show'));
  event.target.classList.add('active');
  document.getElementById(os + '-steps').classList.add('show');
}
</script>
</body>
</html>'''

@app.route('/qr')
def qr_page():
    return QR_PAGE

# ── LIFF フォーム ────────────────────────────────────────────
@app.route('/liff')
def liff_form():
    return send_from_directory('www', 'liff.html')

@app.route('/api/liff/clients')
def api_liff_clients():
    """取引先マスタ（LIFF用・認証不要）"""
    conn = get_db()
    rows = [{'name': r[0], 'reading': r[1] or ''} for r in conn.execute('SELECT name, reading FROM clients ORDER BY name').fetchall()]
    conn.close()
    return jsonify(rows)

@app.route('/api/liff/vehicles')
def api_liff_vehicles():
    """車番検索（LIFF用・認証不要）。?dept= で部門内に限定する"""
    number = request.args.get('number', '').strip()
    if not number:
        return jsonify([])
    dept = req_dept()
    conn = get_db()
    if dept:
        rows = [dict(r) for r in conn.execute(
            'SELECT * FROM vehicles WHERE number=? AND department=? ORDER BY id',
            (number, dept)).fetchall()]
    else:
        rows = [dict(r) for r in conn.execute(
            'SELECT * FROM vehicles WHERE number=? ORDER BY id', (number,)).fetchall()]
    conn.close()
    return jsonify(rows)

@app.route('/api/liff/staff')
def api_liff_staff():
    """社員マスタ（LIFF用・認証不要）。?dept= で部門を絞る"""
    dept = req_dept()
    conn = get_db()
    try:
        if dept:
            rows = [dict(r) for r in conn.execute(
                'SELECT id, name, department FROM staff WHERE department=? ORDER BY id',
                (dept,)).fetchall()]
        else:
            rows = [dict(r) for r in conn.execute(
                'SELECT id, name, department FROM staff ORDER BY id').fetchall()]
    except Exception:
        rows = []
    conn.close()
    return jsonify(rows)

@app.route('/api/liff/submit', methods=['POST'])
def api_liff_submit():
    """フォーム送信（LIFF用）"""
    d   = request.get_json() or {}
    key = request.headers.get('X-Admin-Key','') or d.get('key','')
    if key != ADMIN_PASS:
        return jsonify({'error': 'Unauthorized'}), 401

    action_map = {
        '配車':'貸出中','予約':'予約済','修理':'修理中',
        '返却':'在庫','車検':'車検中','点検':'点検中',
    }
    status = action_map.get(d.get('action',''))
    if not status:
        return jsonify({'error': 'Invalid action'}), 400

    conn = get_db()
    v    = conn.execute('SELECT * FROM vehicles WHERE id=?', (d.get('vehicle_id'),)).fetchone()
    conn.close()
    if not v:
        return jsonify({'error': '車両が見つかりません'}), 404
    v = dict(v)

    state = {
        'start_date':      d.get('start_date') or today_jst(),
        'end_date':        d.get('end_date') or None,
        'staff':           d.get('staff',''),
        'client':          d.get('client',''),
        'category':        d.get('category',''),
        'mileage':         d.get('mileage',''),
        'notes':           d.get('notes',''),
        'location':        d.get('location',''),
        'washed':          d.get('washed', False),
        'interior_cleaned':d.get('interior_cleaned', False),
    }

    # 車両マスタの更新（他社借り・スタッドレス・地域）
    update_fields = []
    update_vals   = []
    if 'studless' in d:
        update_fields.append('studless=?')
        update_vals.append(1 if d['studless'] else 0)
    if 'is_rental_other' in d:
        update_fields.append('is_rental_other=?')
        update_vals.append(1 if d['is_rental_other'] else 0)
    if 'region' in d and d['region']:
        update_fields.append('region=?')
        update_vals.append(d['region'])
    # 所在地に「京都」「滋賀」が含まれれば region を更新（「京都本社」「滋賀支店」なども対応）
    elif state.get('location'):
        loc = state['location']
        if '京都' in loc:
            update_fields.append('region=?')
            update_vals.append('京都')
        elif '滋賀' in loc:
            update_fields.append('region=?')
            update_vals.append('滋賀')
    if update_fields:
        conn2 = get_db()
        conn2.execute(f"UPDATE vehicles SET {','.join(update_fields)} WHERE id=?",
                      update_vals + [v['id']])
        conn2.commit()
        conn2.close()

    msg = register_event(v, status, state)

    # グループLINEに通知（部門ごとの通知先。セールス部門は既定で送信しない）
    dept      = norm_dept(v.get('department'))
    line_sent = push_to_dept_group(dept, msg)
    if not line_sent:
        app.logger.warning(f'[liff/submit] LINE not sent: dept={dept} '
                           f'group_id={dept_group_id(dept)!r} token={bool(LINE_CHANNEL_TOKEN)}')

    return jsonify({'ok': True, 'message': msg, 'line_sent': line_sent})

@app.route('/api/liff/events')
def api_liff_events():
    """車両の将来イベント一覧（LIFF用・キャンセル選択）"""
    vehicle_id = request.args.get('vehicle_id', '')
    if not vehicle_id:
        return jsonify([])
    today = today_jst()
    conn = get_db()
    rows = conn.execute(
        '''SELECT id, status, start_date, end_date, client, staff FROM events
           WHERE vehicle_id=? AND status IN ('予約済','貸出中')
             AND (end_date IS NULL OR end_date >= ?)
           ORDER BY start_date''',
        (vehicle_id, today)
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/liff/cancel', methods=['POST'])
def api_liff_cancel():
    """予約キャンセル（LIFF用）"""
    d   = request.get_json() or {}
    key = d.get('key', '')
    if key != ADMIN_PASS:
        return jsonify({'error': 'Unauthorized'}), 401
    event_id = d.get('event_id')
    if not event_id:
        return jsonify({'error': 'event_id required'}), 400
    conn = get_db()
    ev = conn.execute('SELECT * FROM events WHERE id=?', (event_id,)).fetchone()
    if not ev:
        conn.close()
        return jsonify({'error': 'Event not found'}), 404
    v = conn.execute('SELECT * FROM vehicles WHERE id=?', (ev['vehicle_id'],)).fetchone()
    conn.execute('DELETE FROM events WHERE id=?', (event_id,))
    conn.commit()
    conn.close()
    # LINE通知
    num = v['number'] if v else ''
    ctype = v['car_type'] if v else ''
    client = ev['client'] or ''
    start_d = ev['start_date'] or ''
    msg = f'🚗 {num} {ctype} ❌キャンセル\n{client}\n{_fmt_date(start_d)}〜 取り消し'
    dept = norm_dept(v['department'] if v else None)
    if dept == 'rental':
        # レンタカー事業部は従来どおり環境変数の宛先（挙動を変えない）
        group_id = os.environ.get('LINE_GROUP_ID', '')
        if LINE_CHANNEL_TOKEN and group_id:
            send_line_push(group_id, msg)
    else:
        push_to_dept_group(dept, msg)
    return jsonify({'ok': True})

# ── 車両マスタ一括追加（管理者専用） ────────────────────────
@app.route('/api/admin/add-vehicles', methods=['POST'])
def admin_add_vehicles():
    key = request.headers.get('X-Admin-Key', '')
    if key != ADMIN_PASS:
        return jsonify({'error': 'Unauthorized'}), 401
    payload = request.get_json() or []
    # [{...}] でも {"dept":"sales","items":[...]} でも受け付ける
    if isinstance(payload, dict):
        items = payload.get('items') or []
        base_dept = norm_dept(payload.get('dept') or payload.get('department'))
    else:
        items = payload
        base_dept = norm_dept(request.args.get('dept'))
    conn = get_db()
    c = conn.cursor()
    added, skipped = 0, 0
    for item in items:
        dept = norm_dept(item.get('department'), base_dept)
        # 重複判定は同一部門内のみ（部門が違えば同じ車番でも別車両）
        exists = c.execute('SELECT id FROM vehicles WHERE number=? AND department=?',
                           (item['number'], dept)).fetchone()
        if exists:
            skipped += 1
            continue
        # 新規IDを最大値+1で割り当て
        max_id = c.execute('SELECT MAX(id) FROM vehicles').fetchone()[0] or 0
        c.execute('INSERT INTO vehicles (id,number,car_type,year,full_number,inspection_date,region,car_category,department) VALUES (?,?,?,?,?,?,?,?,?)',
            (max_id + 1, item['number'], item.get('car_type',''),
             item.get('year',''), item.get('full_number',''), item.get('inspection_date',''),
             item.get('region',''), item.get('car_category',''), dept))
        added += 1
    conn.commit()
    conn.close()
    return jsonify({'added': added, 'skipped': skipped})

# ── セールス部門データの全削除（管理者専用・ダミー撤去用） ──
@app.route('/api/admin/clear-sales', methods=['POST'])
def admin_clear_sales():
    """セールス部門の車両・イベント・社員をすべて削除する。
    仮稼働のダミーを消して本物の30台を入れ直すときに使う。
    レンタカー事業部のデータには一切触れない。"""
    key = request.headers.get('X-Admin-Key','') or (request.get_json(silent=True) or {}).get('key','')
    if key != ADMIN_PASS:
        return jsonify({'error': 'Unauthorized'}), 401
    conn = get_db()
    c = conn.cursor()
    n_ev = c.execute('''DELETE FROM events WHERE vehicle_id IN
                        (SELECT id FROM vehicles WHERE department='sales')''').rowcount
    n_v  = c.execute("DELETE FROM vehicles WHERE department='sales'").rowcount
    n_s  = c.execute("DELETE FROM staff WHERE department='sales'").rowcount
    # 再デプロイでダミーが復活しないようフラグは残したまま
    conn.commit()
    conn.close()
    return jsonify({'deleted_vehicles': n_v, 'deleted_events': n_ev, 'deleted_staff': n_s})

# ── 社員マスタ一括追加（管理者専用） ────────────────────────
@app.route('/api/admin/add-staff', methods=['POST'])
def admin_add_staff():
    """社員をまとめて登録する。
    payload: {"dept":"sales","names":["山田太郎", ...]} または ["山田太郎", ...]"""
    key = request.headers.get('X-Admin-Key', '')
    if key != ADMIN_PASS:
        return jsonify({'error': 'Unauthorized'}), 401
    payload = request.get_json() or {}
    if isinstance(payload, list):
        names, dept = payload, norm_dept(request.args.get('dept'))
    else:
        names = payload.get('names') or []
        dept  = norm_dept(payload.get('dept') or payload.get('department'))
    conn = get_db()
    c = conn.cursor()
    added, skipped = 0, []
    for n in names:
        name = (n or '').strip()
        if not name:
            continue
        dup = c.execute('SELECT department FROM staff WHERE name=?', (name,)).fetchone()
        if dup:
            skipped.append(name)
            continue
        c.execute('INSERT INTO staff (name, department) VALUES (?,?)', (name, dept))
        added += 1
    conn.commit()
    conn.close()
    return jsonify({'added': added, 'dept': dept, 'skipped': skipped})

# ── 車両メタデータ一括更新（管理者専用） ────────────────────
@app.route('/api/admin/update-vehicle-meta', methods=['POST'])
def admin_update_vehicle_meta():
    key = request.headers.get('X-Admin-Key', '')
    if key != ADMIN_PASS:
        return jsonify({'error': 'Unauthorized'}), 401
    items = request.get_json() or []
    conn = get_db()
    c = conn.cursor()
    updated, not_found = 0, []
    dept = norm_dept(request.args.get('dept'))   # 既定はレンタカー事業部
    for item in items:
        row = c.execute('SELECT id FROM vehicles WHERE number=? AND department=?',
                        (item['number'], dept)).fetchone()
        if not row:
            not_found.append(item['number'])
            continue
        c.execute('UPDATE vehicles SET car_category=?, inspection_date=? WHERE number=? AND department=?',
                  (item.get('car_category',''), item.get('inspection_date',''), item['number'], dept))
        updated += 1
    conn.commit()
    conn.close()
    return jsonify({'updated': updated, 'not_found_count': len(not_found), 'not_found': not_found[:20]})

# ── 稼働実績一括インポート（管理者専用） ────────────────────
@app.route('/api/admin/import-status', methods=['POST'])
def admin_import_status():
    key = request.headers.get('X-Admin-Key', '')
    if key != ADMIN_PASS:
        return jsonify({'error': 'Unauthorized'}), 401
    items = request.get_json() or []
    conn = get_db()
    c = conn.cursor()
    dept = norm_dept(request.args.get('dept'))   # 既定はレンタカー事業部
    # 対象部門の車両のイベントだけを入れ替える（他部門のデータは残す）
    c.execute('''DELETE FROM events WHERE vehicle_id IN
                 (SELECT id FROM vehicles WHERE department=?)''', (dept,))
    inserted, not_found = 0, []
    today = '2026-05-27'
    for item in items:
        v = c.execute('SELECT id FROM vehicles WHERE number=? AND department=?',
                      (item['number'], dept)).fetchone()
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

# ── 管理表との同期（管理者専用） ────────────────────────────
@app.route('/api/admin/sync-master', methods=['POST'])
def admin_sync_master():
    """管理表を正として車両マスタを合わせる。

    payload: {dept, items:[{match_id|full_number, number, car_type, year,
                            full_number, car_category, inspection_date,
                            notes, studless, region}], delete_ids:[...]}
    match_id があれば更新、なければ新規登録。delete_ids は車両ごと削除する
    （紐づく状態履歴も消える）。region は空欄なら既存値を残す。
    """
    key = request.headers.get('X-Admin-Key','') or (request.get_json(silent=True) or {}).get('key','')
    if key != ADMIN_PASS:
        return jsonify({'error': 'Unauthorized'}), 401
    body  = request.get_json() or {}
    dept  = norm_dept(body.get('dept'))
    items = body.get('items') or []
    dels  = body.get('delete_ids') or []

    conn = get_db(); c = conn.cursor()
    updated = inserted = 0
    for it in items:
        vals = (it.get('number',''), it.get('car_type',''), it.get('year',''),
                it.get('full_number',''), it.get('inspection_date') or '',
                it.get('car_category',''), it.get('notes',''),
                1 if it.get('studless') else 0)
        mid = it.get('match_id')
        if mid:
            c.execute('''UPDATE vehicles SET number=?,car_type=?,year=?,full_number=?,
                         inspection_date=?,car_category=?,notes=?,studless=? WHERE id=?''',
                      vals + (mid,))
            if it.get('region'):
                c.execute('UPDATE vehicles SET region=? WHERE id=?', (it['region'], mid))
            updated += 1
        else:
            c.execute('''INSERT INTO vehicles
                         (number,car_type,year,full_number,inspection_date,car_category,
                          notes,studless,region,is_rental_other,department)
                         VALUES (?,?,?,?,?,?,?,?,?,0,?)''',
                      vals + (it.get('region',''), dept))
            inserted += 1

    deleted = 0
    for vid in dels:
        c.execute('DELETE FROM events WHERE vehicle_id=?', (vid,))
        c.execute('DELETE FROM vehicles WHERE id=?', (vid,))
        deleted += 1

    conn.commit()
    total = c.execute('SELECT COUNT(*) FROM vehicles WHERE department=?', (dept,)).fetchone()[0]
    conn.close()
    return jsonify({'updated': updated, 'inserted': inserted,
                    'deleted': deleted, 'total_now': total})

@app.route('/api/admin/sync-status', methods=['POST'])
def admin_sync_status():
    """指定日の状態を管理表どおりに置き換える。

    payload: {dept, date, reset, items:[{number|full_number, status, staff}]}
    reset=True でその部門の既存イベントを全消去してから入れ直す。
    """
    key = request.headers.get('X-Admin-Key','') or (request.get_json(silent=True) or {}).get('key','')
    if key != ADMIN_PASS:
        return jsonify({'error': 'Unauthorized'}), 401
    body  = request.get_json() or {}
    dept  = norm_dept(body.get('dept'))
    d     = body.get('date') or today_jst()
    items = body.get('items') or []

    conn = get_db(); c = conn.cursor()
    if body.get('reset'):
        c.execute('''DELETE FROM events WHERE vehicle_id IN
                     (SELECT id FROM vehicles WHERE department=?)''', (dept,))
    now = datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')
    inserted, not_found = 0, []
    for it in items:
        row = None
        if it.get('full_number'):
            row = c.execute(
                '''SELECT id FROM vehicles
                   WHERE REPLACE(REPLACE(full_number," ",""),"　","")=? AND department=?''',
                (str(it['full_number']).replace(' ',''), dept)).fetchone()
        if not row and it.get('number'):
            row = c.execute('SELECT id FROM vehicles WHERE number=? AND department=?',
                            (str(it['number']), dept)).fetchone()
        if not row:
            not_found.append(it.get('full_number') or it.get('number')); continue
        c.execute('''INSERT INTO events
                     (vehicle_id,status,start_date,end_date,staff,client,category,notes,created_at,location)
                     VALUES (?,?,?,?,?,?,?,?,?,?)''',
                  (row[0], it['status'], d, None, it.get('staff',''), '', '',
                   it.get('notes',''), now, ''))
        inserted += 1
    conn.commit(); conn.close()
    return jsonify({'inserted': inserted, 'not_found_count': len(not_found),
                    'not_found': not_found[:20]})

# ── エクセル現況の一括取込（管理者専用） ────────────────────
@app.route('/api/admin/import-grid', methods=['POST'])
def admin_import_grid():
    """エクセルの日付グリッドから期間付きイベントを一括登録する。

    既存の 'Excel取込' イベントのみ入替え、社員が登録した実データは残す。
    payload: [{number, status, start_date, end_date, staff, client, notes}, ...]
    """
    key = request.headers.get('X-Admin-Key','') or (request.get_json(silent=True) or {}).get('key','')
    if key != ADMIN_PASS:
        return jsonify({'error': 'Unauthorized'}), 401
    body  = request.get_json() or {}
    items = body.get('items') or []
    conn = get_db()
    c = conn.cursor()
    dept = norm_dept(body.get('dept') or body.get('department'))   # 既定はレンタカー事業部
    reset = body.get('reset', True)
    if reset:
        c.execute('''DELETE FROM events WHERE category='Excel取込' AND vehicle_id IN
                     (SELECT id FROM vehicles WHERE department=?)''', (dept,))
    now = datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S')
    inserted, not_found = 0, []
    for it in items:
        v = None
        # 同じ車番が複数存在する（分類番号違い）ため、まず登録番号全体で照合
        if it.get('full_number'):
            v = c.execute('SELECT id FROM vehicles WHERE REPLACE(full_number," ","")=? AND department=?',
                          (str(it['full_number']).replace(' ', ''), dept)).fetchone()
        if not v:
            v = c.execute('SELECT id FROM vehicles WHERE number=? AND department=?',
                          (str(it['number']), dept)).fetchone()
        if not v:
            not_found.append(it['number']); continue
        c.execute('''INSERT INTO events
            (vehicle_id,status,start_date,end_date,staff,client,category,notes,created_at,location)
            VALUES (?,?,?,?,?,?,?,?,?,?)''',
            (v[0], it['status'], it.get('start_date'), it.get('end_date'),
             it.get('staff',''), it.get('client',''), 'Excel取込',
             it.get('notes',''), now, it.get('location','')))
        inserted += 1
    conn.commit()
    conn.close()
    return jsonify({'inserted': inserted, 'reset': bool(reset),
                    'not_found_count': len(not_found), 'not_found': not_found[:20]})

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
            if reply == '__FORM_BUTTON__':
                send_form_button(reply_token)
            elif reply is not None:
                send_line_reply(reply_token, reply)
            # 解析できないメッセージは無視（グループの通常会話に干渉しない）
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

# ── マスタ管理画面 ──────────────────────────────────────────
@app.route('/master')
@login_required
def master_page():
    return send_from_directory('www', 'master.html')

# 車両マスタ CRUD
@app.route('/api/master/vehicles', methods=['GET'])
@login_required
def master_vehicles_get():
    dept = req_dept()
    conn = get_db()
    if dept:
        rows = [dict(r) for r in conn.execute(
            'SELECT * FROM vehicles WHERE department=? ORDER BY CAST(number AS INTEGER)',
            (dept,)).fetchall()]
    else:
        rows = [dict(r) for r in conn.execute(
            'SELECT * FROM vehicles ORDER BY CAST(number AS INTEGER)').fetchall()]
    conn.close()
    return jsonify(rows)

@app.route('/api/master/vehicles', methods=['POST'])
@master_edit_required
def master_vehicles_post():
    d = request.get_json() or {}
    number = (d.get('number') or '').strip()
    if not number:
        return jsonify({'error': 'number required'}), 400
    # 同じ4桁車番でも分類番号違いの車両が存在しうるため、重複は許容する
    dept = norm_dept(d.get('department'))
    conn = get_db()
    cur = conn.execute('INSERT INTO vehicles (number, car_type, region, studless, is_rental_other, car_category, department, notes) VALUES (?,?,?,?,?,?,?,?)',
                       (number, d.get('car_type',''), d.get('region',''), 0, 0,
                        d.get('car_category',''), dept, d.get('notes','')))
    conn.commit()
    row = dict(conn.execute('SELECT * FROM vehicles WHERE id=?', (cur.lastrowid,)).fetchone())
    conn.close()
    return jsonify(row)

@app.route('/api/master/vehicles/<int:vid>', methods=['PUT'])
@master_edit_required
def master_vehicles_put(vid):
    d = request.get_json() or {}
    conn = get_db()
    cur_row = conn.execute('SELECT department FROM vehicles WHERE id=?', (vid,)).fetchone()
    dept = norm_dept(d.get('department'), norm_dept(cur_row['department'] if cur_row else None))
    conn.execute('UPDATE vehicles SET number=?,car_type=?,region=?,car_category=?,studless=?,is_rental_other=?,department=?,notes=? WHERE id=?',
                 (d.get('number',''), d.get('car_type',''), d.get('region',''), d.get('car_category',''),
                  1 if d.get('studless') else 0, 1 if d.get('is_rental_other') else 0, dept,
                  d.get('notes',''), vid))
    conn.commit()
    row = dict(conn.execute('SELECT * FROM vehicles WHERE id=?', (vid,)).fetchone())
    conn.close()
    return jsonify(row)

@app.route('/api/master/vehicles/<int:vid>', methods=['DELETE'])
@master_edit_required
def master_vehicles_delete(vid):
    conn = get_db()
    conn.execute('DELETE FROM events WHERE vehicle_id=?', (vid,))
    conn.execute('DELETE FROM vehicles WHERE id=?', (vid,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# 取引先マスタ CRUD
@app.route('/api/master/clients', methods=['GET'])
@login_required
def master_clients_get():
    conn = get_db()
    rows = [dict(r) for r in conn.execute('SELECT * FROM clients ORDER BY reading, name').fetchall()]
    conn.close()
    return jsonify(rows)

@app.route('/api/master/clients', methods=['POST'])
@master_edit_required
def master_clients_post():
    d = request.get_json() or {}
    name = (d.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name required'}), 400
    reading = (d.get('reading') or '').strip()
    conn = get_db()
    conn.execute('INSERT OR IGNORE INTO clients (name, reading, created_at) VALUES (?,?,?)',
                 (name, reading, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    row = dict(conn.execute('SELECT * FROM clients WHERE name=?', (name,)).fetchone())
    conn.close()
    return jsonify(row)

@app.route('/api/master/clients/<int:cid>', methods=['PUT'])
@master_edit_required
def master_clients_put(cid):
    d = request.get_json() or {}
    conn = get_db()
    conn.execute('UPDATE clients SET name=?, reading=? WHERE id=?',
                 (d.get('name',''), d.get('reading',''), cid))
    conn.commit()
    row = dict(conn.execute('SELECT * FROM clients WHERE id=?', (cid,)).fetchone())
    conn.close()
    return jsonify(row)

@app.route('/api/master/clients/<int:cid>', methods=['DELETE'])
@master_edit_required
def master_clients_delete(cid):
    conn = get_db()
    conn.execute('DELETE FROM clients WHERE id=?', (cid,))
    conn.commit()
    conn.close()
    return jsonify({'ok': True})

# 従業員マスタ CRUD
@app.route('/api/master/staff', methods=['GET'])
@login_required
def master_staff_get():
    dept = req_dept()
    conn = get_db()
    try:
        if dept:
            rows = [dict(r) for r in conn.execute(
                'SELECT * FROM staff WHERE department=? ORDER BY id', (dept,)).fetchall()]
        else:
            rows = [dict(r) for r in conn.execute('SELECT * FROM staff ORDER BY id').fetchall()]
    except Exception:
        rows = [{'id': i+1, 'name': n, 'department': 'rental'} for i, n in enumerate(STAFF_NAMES)]
    conn.close()
    return jsonify(rows)

@app.route('/api/master/staff', methods=['POST'])
@master_edit_required
def master_staff_post():
    d = request.get_json() or {}
    name = (d.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name required'}), 400
    dept = norm_dept(d.get('department'))
    conn = get_db()
    try:
        # 氏名はUNIQUE。他部門に同名が居る場合は取り違えを防ぐため明示的に弾く
        dup = conn.execute('SELECT department FROM staff WHERE name=?', (name,)).fetchone()
        if dup and norm_dept(dup['department']) != dept:
            conn.close()
            return jsonify({'error': f'「{name}」は{DEPARTMENTS[norm_dept(dup["department"])]}に登録済みです'}), 400
        conn.execute('INSERT OR IGNORE INTO staff (name, department) VALUES (?,?)', (name, dept))
        conn.commit()
        row = dict(conn.execute('SELECT * FROM staff WHERE name=?', (name,)).fetchone())
    except Exception:
        row = {'name': name, 'department': dept}
    conn.close()
    return jsonify(row)

@app.route('/api/master/staff/<int:sid>', methods=['PUT'])
@master_edit_required
def master_staff_put(sid):
    d = request.get_json() or {}
    conn = get_db()
    try:
        cur_row = conn.execute('SELECT department FROM staff WHERE id=?', (sid,)).fetchone()
        dept = norm_dept(d.get('department'), norm_dept(cur_row['department'] if cur_row else None))
        conn.execute('UPDATE staff SET name=?, department=? WHERE id=?',
                     (d.get('name',''), dept, sid))
        conn.commit()
        row = dict(conn.execute('SELECT * FROM staff WHERE id=?', (sid,)).fetchone())
    except Exception:
        row = {'id': sid, 'name': d.get('name','')}
    conn.close()
    return jsonify(row)

@app.route('/api/master/staff/<int:sid>', methods=['DELETE'])
@master_edit_required
def master_staff_delete(sid):
    conn = get_db()
    try:
        conn.execute('DELETE FROM staff WHERE id=?', (sid,))
        conn.commit()
    except Exception:
        pass
    conn.close()
    return jsonify({'ok': True})

# ── LINE任意メッセージ送信（管理者専用） ───────────────────────
@app.route('/api/admin/line-push', methods=['POST'])
def admin_line_push():
    key = request.headers.get('X-Admin-Key','') or (request.get_json() or {}).get('key','')
    if key != ADMIN_PASS:
        return jsonify({'error': 'Unauthorized'}), 401
    msg = (request.get_json() or {}).get('message','')
    if not msg:
        return jsonify({'error': 'message required'}), 400
    group_id = get_setting('line_group_id')
    if not group_id or not LINE_CHANNEL_TOKEN:
        return jsonify({'error': 'LINE not configured'}), 500
    send_line_push(group_id, msg)
    return jsonify({'ok': True})

# ── 起動 ─────────────────────────────────────────────────
init_db()

# ── タイヤ管理モジュール（既存機能には影響しない追加分） ──────────
from tires import init_tires
init_tires(app, get_db, login_required, today_jst)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
