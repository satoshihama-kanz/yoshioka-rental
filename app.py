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

# ── 取引先マスタ初期データ ──────────────────────────────────
_CLIENT_SEED = [
    "ABOUT11","ATELIER","BELL CREA","BODY SHOP Y's","Back Stage","Bahati",
    "C4cars","CARBOX","CARPAC","Car Shop REXT","DOUBLE　FOUR","EAST　LAKE",
    "Eternal　SKY","FREE","Feel Auto","Fulegel Auto House","G-STYLE",
    "GARAGE89","Garage88","GraceFare","JA共済連　滋賀","JUST","K Produce nice",
    "K'SMART","K.G.S.","K.U. STYLE","K2ｵｰﾄﾜｰｸｽ","KAGAYAKI GARAGE","KEN FACTORY",
    "KLASSE","KNｵｰﾄ","KTM","K＆R style","LRmotor","LuxuryStyle","MRT","MY商会",
    "Matt Myoungho Akitaka Schultz","N-BASE","NEXT WORKS","Neo Space Auto",
    "ONEPAGE","PROTODRIVE","R's AUTO","RSﾕﾆﾃｨｰ","Reverent service","RooM925",
    "S&S MARKET","SP Auto Service","SR factory","ST CREATE 株式会社","Victoria",
    "WEST","Y'sAUTO","YG auto","YM　garage","Yutacar","car office Rev'O",
    "customer　ORIENTED　T.S","garage Toys","juice factory","office Zen",
    "revival shop G-Flow","start","あんしん保険","くるま工房","さすてな京都",
    "たむら屋ﾓｰﾀｰｽ","カワイデンキ株式会社","㈱A.Y","㈱奥村ﾓｰﾀｰｽ","㈱武村商会",
    "㈱滋賀ﾄﾖﾀ ﾓﾋﾞﾘﾃｨﾌﾟﾗｻﾞ守山East","㈱滋賀ﾄﾖﾀ　国道8号栗東店",
    "㈱滋賀ﾄﾖﾀ　大津瀬田店","㈱滋賀ﾄﾖﾀ　水口泉店","㈱滋賀ﾄﾖﾀ　瀬田・草津店",
    "㈱滋賀ﾄﾖﾀ　ﾈｯﾂ栗東店","㈱滋賀ﾄﾖﾀ　ﾓﾋﾞﾘﾃｨﾌﾟﾗｻﾞ守山West","㈲雨森ｵｰﾄｻｰﾋﾞｽ",
    "一建設　株式会社","三角　貞之","三軌工業株式会社","三陽自動車","中央自工",
    "中小路","中島鈑金","中村","丸岡自動車","丸栄商会","丸目","丸福自動車",
    "京央自動車　株式会社","京都日産自動車㈱亀岡大井北店","京都日産自動車㈱亀岡大井南店",
    "京都日産自動車㈱京田辺店","京都日産自動車㈱伏見店","京都日産自動車㈱十条店",
    "京都日産自動車㈱南店","京都日産自動車㈱右京店","京都日産自動車㈱向日店",
    "京都日産自動車㈱宇治店","京都日産自動車㈱宇治東店","京都日産自動車㈱宇治西店",
    "京都日産自動車㈱山科店","京都日産自動車㈱山科音羽店","京都日産自動車㈱峰山店",
    "京都日産自動車㈱木津川台店","京都日産自動車㈱本社店","京都日産自動車㈱東舞鶴店",
    "京都日産自動車㈱桂店","京都日産自動車㈱法人営業部","京都日産自動車㈱洛北店",
    "京都日産自動車㈱西大路店","京都日産自動車㈱西舞鶴店","京都日産自動車㈱長岡京店",
    "京都日産自動車㈱高野店","京都日産自動車㈱ｶｰﾒｯｾ久御山","京都日産自動車㈱ﾙﾉｰ京都",
    "京都ｻｰﾋﾞｽ 吉祥院","京都ｻｰﾋﾞｽ久御山","京都ｻｰﾋﾞｽ株式会社",
    "京都ﾄﾖﾀ自動車㈱乙訓店","京都ﾄﾖﾀ自動車㈱宇治店","京都ﾄﾖﾀ自動車㈱桂川洛西店",
    "京都ﾄﾖﾍﾟｯﾄ㈱宇治店","京都ﾄﾖﾍﾟｯﾄ㈱山科店","京都ﾗｲﾌﾊﾟｰﾄﾅｰ","京鈑金",
    "今井自動車工業","今林 幸海","伊吹","保険ﾏｲｽﾀｰ","公成建設株式会社",
    "共栄火災海上保険株式会社","則本　拓哉","加藤石油","勝美自動車株式会社",
    "北川ﾓｰﾀｰｽ","北田自動車","古一商会","合同会社H'2","合同会社SUWANC",
    "合同会社　LIF","吉仲","吉岡自動車","吉田自動車工業","善ｵｰﾄ",
    "土田企画ｵｰﾄｻｰﾋﾞｽ","塚本","大島自動車工作所","大谷自動車","大阪ﾄﾖﾍﾟｯﾄ㈱吹田店",
    "契約ｾﾝﾀｰ","安心ｱｼｽﾄ株式会社","宮村","家具町ﾓｰﾀｰｽ","富士ｵｰﾄ",
    "小船井　達哉","小西","山形　憲司","岡田","岡田合同会社","岩井紙器店",
    "岩本自動車工作所","島中","川本","川正自動車株式会社","川端 芹奈","平井",
    "広中ｵｰﾄ","戸塚","戸梶","戸梶 蒼菜","指定場所","新宮自動車","新谷",
    "日工自動車　株式会社","日星自動車 株式会社","日本ﾘｽｸｺﾝｻﾙﾃｨﾝｸﾞ",
    "日産大阪販売㈱Zushi高槻店","日産大阪販売㈱枚方ﾋﾙｽﾞ",
    "日産ﾌﾟﾘﾝｽ奈良販売㈱三条大路店","日産ﾌﾟﾘﾝｽ奈良販売㈱佐保店",
    "日産ﾌﾟﾘﾝｽ奈良販売㈱奈良店","日産ﾌﾟﾘﾝｽ滋賀販売㈱堅田店",
    "日産ﾌﾟﾘﾝｽ滋賀販売㈱大津店","日産ﾌﾟﾘﾝｽ滋賀販売㈱彦根店",
    "日産ﾌﾟﾘﾝｽ滋賀販売㈱栗東店","日産ﾌﾟﾘﾝｽ滋賀販売㈱水口店",
    "日産ﾌﾟﾘﾝｽ滋賀販売㈱近江八幡店","日産ﾌﾟﾘﾝｽ滋賀販売㈱ﾌﾟﾘﾝﾋﾟｱ栗東",
    "有限会社 契約ｾﾝﾀｰ　浅井 真","有限会社 徳本ﾚﾝﾀｶｰ","有限会社 ｺﾄﾌﾞｷ",
    "有限会社 ﾎﾞﾃﾞｨｰｼｮｯﾌﾟいまい","有限会社　J-Breath","有限会社　伸",
    "有限会社　内山車体","有限会社　北斗商事","有限会社　園部自動車工業",
    "有限会社　寒梅社","有限会社　岡﨑自販","有限会社　市村自動車整備工場",
    "有限会社　新陽","有限会社　武田自動車","有限会社　蒲生自動車",
    "有限会社　ｲｸﾏ自動車","有限会社　ｴﾙｸﾞｽﾎﾟｰﾂ","有限会社　ｶｰｼｮｯﾌﾟJUN",
    "有限会社　ｶｰﾃｯｸｳｶｲ","有限会社　ﾀﾅｶﾄﾚｰﾃﾞｨﾝｸﾞ","有限会社　ﾌﾟﾛｳﾞｧﾝｽ",
    "有限会社　ﾎｹﾝ","有限会社　ﾎﾞﾃﾞｨｼｮｯﾌﾟｴﾑ","有限会社今村製作所",
    "有限会社ｵｰﾄﾘﾍﾟｱ","有限会社ｵｸﾞﾗｵｰﾄｻｰﾋﾞｽ","有限会社ｻｶﾓﾄ自動車",
    "有限会社ﾊﾞﾝｶｰ保険事務所","有限会社ﾗﾝﾄﾞｵｰﾄ","杉山　翔大","杉本自動車",
    "杉林ｵｰﾄｻｰﾋﾞｽ","村上自動車商会","村井自動車商会","村田","東京海上日動火災保険株式会社",
    "東山自動車工業株式会社","松下自動車","松井","松岡","松本","栄建設工業 株式会社",
    "栗東ｵｰﾄｾﾝﾀｰ","株式会社 RESO","株式会社 friends","株式会社 久保板金",
    "株式会社 京滋ﾊｳｼﾞﾝｸﾞ","株式会社 共進自動車","株式会社 ｽﾘｰｴｽｺｰﾎﾟﾚｰｼｮﾝ",
    "株式会社BRB","株式会社C-ONE","株式会社FJﾈｯﾄﾜｰｸ","株式会社OS　BODY　WORK",
    "株式会社SKY　planning","株式会社　Area ace","株式会社　Auto Works",
    "株式会社　D-PROJECT","株式会社　GOODSUN","株式会社　KIEFER",
    "株式会社　RIZE","株式会社　SG-Auto","株式会社　UNO","株式会社　smile",
    "株式会社　tsuji","株式会社　ぱわふる自動車","株式会社　三久保商会",
    "株式会社　中井鈑金自動車","株式会社　中野鈑金塗装","株式会社　久保鈑金",
    "株式会社　吉仲自動車販売","株式会社　城南自動車","株式会社　広岡装美",
    "株式会社　日彰工業","株式会社　松井自動車工業","株式会社　松樹",
    "株式会社　湖南鈑金","株式会社　湖南ｵｰﾄｾﾝﾀｰ","株式会社　白梅ﾓｰﾀｰｽ",
    "株式会社　白野","株式会社　笠井工務店","株式会社　第一総合企画",
    "株式会社　葛城ﾓｰﾀｰｽ","株式会社　雅紙管","株式会社　高島代理店",
    "株式会社　ＨＹ－システム","株式会社　ｴﾌｹｲ","株式会社　ｵｸﾀﾞｵｰﾄ",
    "株式会社　ｶｰｵｰｸ","株式会社　ｶｰﾐﾙｽﾞ","株式会社　ｶﾜｶﾂ",
    "株式会社　ｾﾞﾆｽ","株式会社　ﾎﾞﾃﾞｨｰｼｮｯﾌﾟﾂﾀﾞ","株式会社　ﾏｲｶｰｾﾝﾀｰ八日市",
    "株式会社　ﾒﾃﾞｨｹｱ・ﾘﾊﾋﾞﾘ","株式会社　ﾗｲﾌﾌﾟﾗｻﾞﾊﾟｰﾄﾅｰ","株式会社ひまわり自動車",
    "株式会社ビーノス","株式会社マークリー","株式会社ヨシダ工務店","株式会社三共自動車",
    "株式会社中央保険ｾﾝﾀｰ橋本","株式会社忍自動車","株式会社日産ｻﾃｨｵ奈良",
    "株式会社美咲総務部","株式会社ｳｲﾝﾗｲﾌ 伏見営業所","株式会社ｳｲﾝﾗｲﾌ滋賀北営業所",
    "株式会社ｵｰﾄﾃｯｸ","株式会社ｶｰｼｪｱｶﾙﾁｬｰ","株式会社ｶｰﾌｨｰﾙﾄﾞﾖｼｵｶ",
    "株式会社ｸﾗﾌﾄｳﾞｧｰｹﾞﾝ","株式会社ｼﾞｰｴﾙ","株式会社ﾅｶﾜｵｰﾄ","株式会社ﾉｰﾌﾞﾙ",
    "株式会社ﾊﾔｼｵｰﾄ","株式会社ﾊﾟﾌﾞﾘｯｸﾎｰﾑ","株式会社ﾋﾞｰﾉｽ","株式会社ﾌﾙｶﾜｽﾎﾟｰﾂ",
    "株式会社ﾌﾛｲﾄﾞｼｯﾌﾟ","株式会社ﾎﾞﾃﾞｨﾌｧｲﾄｼﾞｬﾊﾟﾝ","株式会社ﾘﾕｰｼﾞｭ","株式会社ﾘﾗｲｱﾝｽ",
    "梶本　陸人","槙本保険","樋口鈑金塗装","樋谷自動車株式会社","橋本鈑金",
    "武田ｵｰﾄｻｰﾋﾞｽ株式会社","永田ﾓｰﾀｰｽ","汐先","江州自動車","江本","江本　武",
    "池本ﾓｰﾀｰｽ","池田","池田自動車","活力ある京都をつくる会","流儀ﾃﾞｻﾞｲﾝ","浅野",
    "滋賀日産自動車㈱八日市店","滋賀日産自動車㈱南彦根店","滋賀日産自動車㈱堅田店",
    "滋賀日産自動車㈱大津店","滋賀日産自動車㈱守山店","滋賀日産自動車㈱安曇川店",
    "滋賀日産自動車㈱彦根店","滋賀日産自動車㈱栗東店","滋賀日産自動車㈱水口店",
    "滋賀日産自動車㈱水口西店","滋賀日産自動車㈱瀬田店","滋賀日産自動車㈱近江八幡店",
    "滋賀日産自動車㈱ｸﾞﾗﾝ南草津","滋賀県共済協同組合","滋賀ﾀﾞｲﾊﾂ販売㈱Newﾓﾋﾞﾘﾃｨ",
    "滋賀ﾀﾞｲﾊﾂ販売㈱八幡店","滋賀ﾀﾞｲﾊﾂ販売㈱堅田店","滋賀ﾀﾞｲﾊﾂ販売㈱大津店",
    "滋賀ﾀﾞｲﾊﾂ販売㈱愛知川店","滋賀ﾀﾞｲﾊﾂ販売㈱栗東店","滋賀ﾀﾞｲﾊﾂ販売㈱水口店",
    "滋賀ﾀﾞｲﾊﾂ販売㈱草津店","滋賀ﾀﾞｲﾊﾂ販売㈱長浜店","物部ﾓｰﾀｰｽ","田中","田中電工",
    "田中ｵｰﾄ","田畑","甲南ﾓｰﾀｰｽ","白岩自工","相根　美樹","石井","福間　玲奈",
    "竹内ﾓｰﾀｰｽ","竹谷教材株式会社","笠井","藤村","西山自動車","西川路　隆司",
    "西澤自動車工業　株式会社","貴生川車体工業","車房N","辻内自動車",
    "近江電機自動車整備工場","近藤　淳一","鈴木","長崎","門谷自動車工業","雄琴自工",
    "青竜ｵｰﾄｻｰﾋﾞｽ","高岡　大丈","高田","黒瀬","齋藤自動車",
    "ｱｯﾌﾟﾙﾜｰﾙﾄﾞ城陽店","ｱｲﾌﾟﾗﾝ24","ｱﾍﾟｯｸｽｸﾞﾛｳ","ｲﾁﾐﾔ自動車合同会社",
    "ｲﾝｼｭｱﾗﾝｽﾌﾞﾚｰﾝ","ｲﾝｼｭｱﾗﾝｽﾌﾞﾚｰﾝ亀岡","ｲﾝｼｭｱﾗﾝｽﾌﾞﾚｰﾝ京都中央支店",
    "ｳｲﾝﾗｲﾌ","ｳｴｽｷﾞ自工","ｳｴﾝｽﾞｵｰﾄ","ｳｶｲﾓｰﾀｰｽ","ｳﾁﾀﾞ自動車工業","ｳﾞｪﾙｸｵｰﾄ",
    "ｴﾙﾌｧｲﾝ","ｵｰﾄｳﾁﾀﾞ","ｵｰﾄｸﾗﾌﾄ","ｵｰﾄｺﾚｸｼｮﾝ ｷﾞｱ","ｵｰﾄｻｰﾋﾞｽ嶋田",
    "ｵｰﾄｼｮｯﾌﾟﾀｹｵｶ","ｵｰﾄｼｮｯﾌﾟﾓﾘ","ｵｰﾄｼﾞｬﾊﾟﾝ","ｵｰﾄｽﾃｰｼｮﾝ","ｵｰﾄｾﾚｸﾄ･ﾀﾅｶ",
    "ｵｰﾄﾂｰﾙ","ｵｰﾄﾃｯｸ車検整備ｾﾝﾀｰ","ｵｰﾄﾊﾞｯｸｽ八日市店","ｵｰﾄﾔｰﾄﾞ","ｵｰﾄﾘｯﾁ古谷",
    "ｵｳｼﾞﾌｧｸﾄﾘｰ","ｵﾘｯｸｽ自動車㈱大阪港営業所","ｶｰ・ﾋﾞｭｰﾃｨ京都","ｶｰｱｯﾌﾟﾗｲﾄ",
    "ｶｰｵﾌｨｽｶﾅﾓ","ｶｰｵﾌｨｽｼﾞｰｻﾞｰ","ｶｰｻｰﾋﾞｽﾊｼﾓﾄ","ｶｰｼｮｯﾌﾟwing","ｶｰｼｮｯﾌﾟ道楽車",
    "ｶｰｼｮｯﾌﾟｳﾉ","ｶｰｽﾃｰｼｮﾝﾅｶﾑﾗ","ｶｰﾊｳｽﾌﾘｰｾﾚｸｼｮﾝ","ｶｰﾊﾟｰﾄﾅｰ","ｶｰﾊﾟﾚｽ伏見",
    "ｶｰﾌｧｸﾄﾘｰJUN","ｶｰﾎﾞﾃﾞｰｲﾅﾊﾞ","ｶｰﾗｲﾌ長岡","ｶﾞﾘﾊﾞｰ槙島店","ｶﾞﾚｰｼﾞM",
    "ｶﾞﾚｰｼﾞOKUMURA","ｶﾞﾚｰｼﾞShine","ｶﾞﾚｰｼﾞｱｽﾄﾚｱ","ｶﾞﾚｰｼﾞｷｯｽﾞ","ｶﾞﾚｰｼﾞｻｻﾞﾝ",
    "ｶﾞﾚｰｼﾞﾀｶﾑﾗ","ｷｮｰｼﾝｵｰﾄ","ｷｰｽｵｰﾄﾌﾟﾛｼﾞｪｸﾄ","ｷﾞｬﾗﾘｰｵｵｻﾜ",
    "ｸﾛｽｵﾌｨｽ(ｱﾍﾟｯｸｽｸﾞﾛｳ㈱)","ｸﾞﾛｰﾊﾞﾙｱｲﾃﾞｨｱﾙ株式会社","ｹﾋｵｰﾄ","ｺﾝｾﾌﾟﾄ",
    "ｺﾞﾘﾗﾊｳｽ","ｻﾝﾌｫｰｽ","ｼｰｻﾏｰｶｽﾀﾑｽﾞ","ｼｰﾎﾞｰｲ彦根店","ｼﾙｴｯﾄｼｮｯﾌﾟ中島",
    "ｼﾞｬﾍﾟｯｸｽ","ｼﾞｮｲｶﾙ八幡","ｾﾌﾃｨﾛｰﾄﾞ","ﾀｷｶﾞﾜｵｰﾄ","ﾀｽｸｶﾞﾚｰｼﾞ","ﾂｶｻｵｰﾄ",
    "ﾃｲﾗｰｽﾞｵｰﾄ","ﾃﾞｭｱﾙﾌﾞﾚｽﾄ","ﾃﾞﾝｷのいまいﾔﾍﾞ","ﾄｯﾌﾟ京滋",
    "ﾄﾖﾀｶﾛｰﾗ京都㈱吉祥院店","ﾄﾖﾀｶﾛｰﾗ京都㈱宇治店","ﾄﾖﾀｶﾛｰﾗ滋賀㈱八日市店",
    "ﾄﾖﾀｶﾛｰﾗ滋賀㈱大津店","ﾄﾖﾀｶﾛｰﾗ滋賀㈱彦根店","ﾄﾖﾀｶﾛｰﾗ滋賀㈱日野店",
    "ﾄﾖﾀｶﾛｰﾗ滋賀㈱栗東店","ﾄﾖﾀｶﾛｰﾗ滋賀㈱湖南店","ﾄﾖﾀｶﾛｰﾗ滋賀㈱甲賀店",
    "ﾄﾖﾀｶﾛｰﾗ滋賀㈱草津店","ﾄﾖﾀﾓﾋﾞﾘﾃｨ滋賀㈱Sat彦根","ﾄﾖﾀﾓﾋﾞﾘﾃｨ滋賀㈱栗東店",
    "ﾄﾖﾍﾟｯﾄｶﾄﾞﾉ八条店","ﾅｶﾞｻｷｵｰﾄ","ﾆｼｳﾗmotor vehicle",
    "ﾈｯﾂﾄﾖﾀﾔｻｶ㈱大久保店","ﾈｯﾂﾄﾖﾀﾔｻｶ㈱宇治店","ﾈｯﾂﾄﾖﾀﾔｻｶ㈱木津店",
    "ﾈｯﾂﾄﾖﾀﾔｻｶ㈱松井山手店","ﾈｯﾂﾄﾖﾀﾔｻｶ㈱桃山店","ﾊﾟﾜｰﾄﾞ","ﾋﾟｯﾄﾊｳｽ",
    "ﾌｧｲﾅﾝｼｬﾙｱﾄﾞﾊﾞﾝｽ ﾗｲﾌﾌﾟﾗｻﾞ一里山","ﾌｧｲﾅﾝｼｬﾙｱﾗｲｱﾝｽ","ﾌｧｲﾝﾓｰﾀｰｽ",
    "ﾌｨｱｯﾄ/ｱﾊﾞﾙﾄ滋賀","ﾌｼﾞｲﾓｰﾀｰｽ","ﾌﾚｯﾂ","ﾌﾞﾗｲﾄ鈑金","ﾌﾞﾙｰﾜｰｸｽ","ﾌﾟﾚﾐｱﾑｵｰﾄ",
    "ﾍﾟｲﾝﾄｶﾞﾚｰｼﾞ･ｼﾞｭﾝ","ﾎﾝﾀﾞｶｰｽﾞ京都　上京店","ﾎﾞﾃﾞｨ-ｼｮｯﾌﾟ岩前",
    "ﾎﾞﾃﾞｨｰｼｮｯﾌﾟ小田","ﾎﾞﾃﾞｨｰｼｮｯﾌﾟｼｵﾐ","ﾎﾞﾃﾞｨｼｮｯﾌﾟ大橋","ﾎﾞﾃﾞｨｼｮｯﾌﾟ新谷",
    "ﾎﾞﾃﾞｨｼｮｯﾌﾟ美車門","ﾎﾞﾃﾞｨｼｮｯﾌﾟｵｶｲ","ﾏｰﾍﾞﾘｯｸ","ﾏｲｶｰｾﾝﾀｰ宇治","ﾏｻｷｵｰﾄ",
    "ﾏｽﾀｰﾋﾟｰｽ","ﾐﾌﾞ自動車","ﾑﾗﾀｵｰﾄｻｰﾋﾞｽ","ﾒｲｸﾜﾝ","ﾒｶﾄﾞｯｸｵｶﾀﾞ","ﾔﾏｼｹﾞ",
    "ﾔﾏﾀﾞﾓｰﾀｰｽ","ﾔﾏﾅｶPAINT","ﾕｰﾎﾟｽ8号栗東店","ﾕﾅｲﾃｯﾄﾞｼｽﾃﾑ","ﾗｲｽﾞｱｯﾌﾟ",
    "ﾗｲﾄﾞｸﾗﾌﾄ","ﾗｼﾞｯﾌﾟ","ﾘﾆｱﾃｨ","ﾘﾌﾚｯｸｽ","ﾘﾝｸﾞﾌﾟﾛｻｰﾋﾞｽ株式会社","ﾙﾉｰ滋賀栗東",
    "ﾚｲｸｵｰﾄ","ﾚｸｻｽCPO彦根","ﾚｼﾌﾟﾛｵｰﾄ","ﾛﾝﾄﾞﾍﾞﾙ",
]

def _seed_clients(c):
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    c.executemany('INSERT OR IGNORE INTO clients (name, created_at) VALUES (?, ?)',
                  [(n, now) for n in _CLIENT_SEED])

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
            car_category TEXT DEFAULT ''
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
            created_at TEXT
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
    ]:
        try:
            c.execute(sql)
        except Exception:
            pass
    conn.commit()

    # 取引先マスタ初期データ投入（INSERT OR IGNORE で重複スキップ）
    if c.execute('SELECT COUNT(*) FROM clients').fetchone()[0] == 0:
        _seed_clients(c)
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
    today = today_jst()
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
    washed  = 1 if state.get('washed') else 0
    interior_cleaned = 1 if state.get('interior_cleaned') else 0

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

LIFF_URL   = 'https://yoshioka-rental-1.onrender.com/liff'
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

@app.route('/api/clients', methods=['GET'])
@login_required
def api_clients_get():
    conn = get_db()
    rows = [r[0] for r in conn.execute('SELECT name FROM clients ORDER BY name').fetchall()]
    conn.close()
    return jsonify(rows)

@app.route('/api/clients', methods=['POST'])
@login_required
def api_clients_post():
    name = (request.get_json() or {}).get('name', '').strip()
    if not name:
        return jsonify({'error': 'name required'}), 400
    conn = get_db()
    conn.execute('INSERT OR IGNORE INTO clients (name, created_at) VALUES (?, ?)',
                 (name, datetime.now().strftime('%Y-%m-%d %H:%M:%S')))
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

def _event_summary_line(d, conn):
    """イベントのサマリ文字列を生成してグループLINEに送信"""
    v = conn.execute('SELECT number, car_type FROM vehicles WHERE id=?', (d['vehicle_id'],)).fetchone()
    num      = v['number'] if v else str(d['vehicle_id'])
    car_type = v['car_type'] if v else ''
    status   = d.get('status', '')
    staff    = d.get('staff', '')
    client   = d.get('client', '')
    category = d.get('category', '')
    start_d  = (d.get('start_date') or '')
    end_d    = (d.get('end_date') or '')
    # 日付を M/D 形式に変換
    def fmt(ds):
        if not ds: return ''
        try:
            dt = datetime.strptime(ds, '%Y-%m-%d')
            return f"{dt.month}/{dt.day}"
        except:
            return ds
    period = fmt(start_d) + '〜' + (fmt(end_d) if end_d else '')
    parts = [p for p in [num, car_type, status, staff, client, period, category] if p]
    msg = '📋 ' + '、'.join(parts)
    group_id = get_setting('line_group_id')
    if group_id and LINE_CHANNEL_TOKEN:
        send_line_push(group_id, msg)

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
MORNING_STAFF = [
    '平田弘子','内田麻鈴','山本圭太','吉岡佑真','市川久登',
    '奥谷慎太郎','福田竜也','川上那歩','田中杏果','田中奈々実',
]

def match_staff(ev_staff):
    """イベントの担当者フィールドをスタッフリストにマッチング"""
    if not ev_staff: return 'その他'
    for s in MORNING_STAFF:
        if ev_staff in s or s in ev_staff:
            return s
    return ev_staff

def build_morning_report():
    today = today_jst()
    conn  = get_db()

    vehicles = conn.execute(
        'SELECT * FROM vehicles ORDER BY CAST(number AS INTEGER)'
    ).fetchall()

    # 今日アクティブなイベント（最新1件/車両）
    active = {}
    for r in conn.execute(
        '''SELECT e.*, v.number as vnum, v.car_type as vtype
           FROM events e JOIN vehicles v ON e.vehicle_id=v.id
           WHERE e.start_date<=? AND (e.end_date IS NULL OR e.end_date>=?)
             AND e.status != "在庫"
           ORDER BY e.start_date DESC''', (today, today)
    ).fetchall():
        if r['vehicle_id'] not in active:
            active[r['vehicle_id']] = dict(r)
    conn.close()

    stock, reserves, maint = [], {}, []
    for v in vehicles:
        v = dict(v)
        ev = active.get(v['id'])
        if ev is None:
            stock.append(v)
        elif ev['status'] == '予約済':
            staff = match_staff(ev.get('staff',''))
            reserves.setdefault(staff, []).append((v, ev))
        elif ev['status'] in ('車検中','点検中','修理中'):
            maint.append((v, ev))

    today_dt = datetime.strptime(today, '%Y-%m-%d')
    lines = [f"おはようございます🚗\n{today_dt.month}月{today_dt.day}日 車両稼働状況\n"]

    # 在庫
    lines.append("■■■　在庫　■■■")
    if stock:
        for v in stock:
            lines.append(f"{v['number']} {v['car_type']}")
    else:
        lines.append("（在庫なし）")

    # 予約
    lines.append("\n■■■　予約　■■■")
    shown = set()
    for staff in MORNING_STAFF:
        lines.append(f"\n【{staff}】")
        shown.add(staff)
        if staff in reserves:
            for v, ev in reserves[staff]:
                lines.append(f"{v['number']} {v['car_type']}")
                parts = []
                if ev.get('start_date'):
                    d = datetime.strptime(ev['start_date'], '%Y-%m-%d')
                    parts.append(f"{d.month}月{d.day}日〜")
                if ev.get('client'):  parts.append(ev['client'])
                if ev.get('category'): parts.append(ev['category'])
                if parts: lines.append(f"({' '.join(parts)})")
    # リスト外の担当者
    for staff, items in reserves.items():
        if staff not in shown:
            lines.append(f"\n【{staff}】")
            for v, ev in items:
                lines.append(f"{v['number']} {v['car_type']}")

    # 車検・修理・点検
    lines.append("\n■■■　車検・修理・点検　■■■")
    if maint:
        for v, ev in maint:
            note = ev.get('notes') or ev.get('status','')
            lines.append(f"{v['number']} {v['car_type']}（{note}）")
    else:
        lines.append("（なし）")

    return '\n'.join(lines)

@app.route('/api/morning-report', methods=['GET', 'POST'])
def api_morning_report():
    key = request.headers.get('X-Admin-Key','') or request.args.get('key','')
    if key != ADMIN_PASS:
        return jsonify({'error': 'Unauthorized'}), 401
    msg      = build_morning_report()
    group_id = get_setting('line_group_id')
    if group_id and LINE_CHANNEL_TOKEN:
        send_line_push(group_id, msg)
        return jsonify({'sent': True})
    return jsonify({'sent': False, 'reason': 'group_id or token not set'})

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

  <a class="btn" href="/liff">📱 今すぐフォームを開く</a>

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
  text: window.location.origin + '/liff',
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

@app.route('/api/liff/vehicles')
def api_liff_vehicles():
    """車番検索（LIFF用・認証不要）"""
    number = request.args.get('number', '').strip()
    if not number:
        return jsonify([])
    conn = get_db()
    rows = [dict(r) for r in conn.execute(
        'SELECT * FROM vehicles WHERE number=? ORDER BY id', (number,)).fetchall()]
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
    if update_fields:
        conn2 = get_db()
        conn2.execute(f"UPDATE vehicles SET {','.join(update_fields)} WHERE id=?",
                      update_vals + [v['id']])
        conn2.commit()
        conn2.close()

    msg = register_event(v, status, state)

    # グループLINEに通知
    group_id = get_setting('line_group_id')
    if group_id:
        send_line_push(group_id, f"📱 フォームより登録\n{msg}")

    return jsonify({'ok': True, 'message': msg})

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
    for item in items:
        row = c.execute('SELECT id FROM vehicles WHERE number=?', (item['number'],)).fetchone()
        if not row:
            not_found.append(item['number'])
            continue
        c.execute('UPDATE vehicles SET car_category=?, inspection_date=? WHERE number=?',
                  (item.get('car_category',''), item.get('inspection_date',''), item['number']))
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

# ── 起動 ─────────────────────────────────────────────────
init_db()
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
