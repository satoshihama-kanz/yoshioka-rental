// 更新時刻の表記（更新-8/3-9:40:19）
function fmtUpdated() {
    const d = new Date();
    const p = n => String(n).padStart(2, '0');
    return `更新-${d.getMonth() + 1}/${d.getDate()}-${d.getHours()}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

const API = '';
let vehicles = [];        // 全部門の車両（APIから取得したまま）
let events = [];
let filteredVehicles = [];
let currentDetailVehicleId = null;
let calendarOffset = 0;
let editingEventId = null;

// ====== 部門（事業部） ======
const DEPT_LABELS = { rental: 'レンタカー事業部', sales: 'セールス部門' };
const DEPT_DEFAULT = 'rental';

function initialDept() {
    const q = new URLSearchParams(location.search).get('dept');
    if (q && DEPT_LABELS[q]) return q;
    const saved = localStorage.getItem('fleetDept');
    return (saved && DEPT_LABELS[saved]) ? saved : DEPT_DEFAULT;
}
let currentDept = initialDept();

// 現在の部門に属する車両だけを返す（department 未設定はレンタカー扱い）
function deptVehicles() {
    return vehicles.filter(v => (v.department || DEPT_DEFAULT) === currentDept);
}

function applyDeptChrome() {
    document.body.classList.toggle('dept-sales', currentDept === 'sales');
    Object.keys(DEPT_LABELS).forEach(d => {
        const t = document.getElementById('deptTab-' + d);
        if (t) t.classList.toggle('active', d === currentDept);
    });
    const ml = document.getElementById('masterLink');
    if (ml) ml.href = '/master?dept=' + currentDept;
}

function switchDept(dept) {
    if (!DEPT_LABELS[dept] || dept === currentDept) return;
    currentDept = dept;
    localStorage.setItem('fleetDept', dept);
    applyDeptChrome();
    closeDetail();
    rebuildTypeFilter();
    document.getElementById('totalBadge').textContent = deptVehicles().length;
    loadStaffOptions();
    applyFilters();
}

// 車種フィルターの選択肢を現在の部門で作り直す
function rebuildTypeFilter() {
    const sel = document.getElementById('filterType');
    if (!sel) return;
    const prev = sel.value;
    const types = [...new Set(deptVehicles().map(v => v.car_type).filter(Boolean))].sort();
    sel.innerHTML = '<option value="">車種：すべて</option>';
    types.forEach(t => {
        const o = document.createElement('option');
        o.value = t; o.textContent = t;
        sel.appendChild(o);
    });
    sel.value = types.includes(prev) ? prev : '';
}

// 担当営業プルダウン：レンタカーは従来の固定リスト、セールスは社員マスタから
let _rentalStaffOptionsHTML = null;
async function loadStaffOptions() {
    const sel = document.getElementById('formStaff');
    if (!sel) return;
    if (_rentalStaffOptionsHTML === null) _rentalStaffOptionsHTML = sel.innerHTML;
    if (currentDept === 'rental') { sel.innerHTML = _rentalStaffOptionsHTML; return; }
    sel.innerHTML = '<option value="">（選択）</option>';
    try {
        const list = await fetch(`${API}/api/master/staff?dept=${currentDept}`).then(r => r.json());
        (Array.isArray(list) ? list : []).forEach(s => {
            const o = document.createElement('option');
            o.value = s.name; o.textContent = s.name;
            sel.appendChild(o);
        });
    } catch (e) { console.error('[staff] load error:', e); }
}

// 初期化
async function init() {
    applyDeptChrome();
    const [v, e] = await Promise.all([
        fetch(API + '/api/vehicles').then(r => r.json()),
        fetch(API + '/api/events').then(r => r.json()),
        loadClients()
    ]);
    vehicles = Array.isArray(v) ? v : [];
    events = Array.isArray(e) ? e : [];

    // 車種フィルター選択肢を生成（現在の部門のみ）
    rebuildTypeFilter();

    document.getElementById('totalBadge').textContent = deptVehicles().length;
    document.getElementById('lastUpdated').textContent = fmtUpdated();

    loadStaffOptions();
    applyFilters();
    refreshPendingBadge();
    setInterval(refreshEvents, 30000);
    setInterval(refreshPendingBadge, 60000);
}

async function refreshEvents() {
    events = await fetch(API + '/api/events').then(r => r.json()).catch(() => events);
    renderCurrentPage();
}

function renderCurrentPage() {
    const active = document.querySelector('.page.active');
    if (!active) return;
    const id = active.id;
    if (id === 'page-dashboard') renderDashboard();
    else if (id === 'page-calendar') renderCalendar();
    else if (id === 'page-list') renderList();
    else if (id === 'page-pending') renderPending();
    if (currentDetailVehicleId) renderDetail(currentDetailVehicleId);
}

// 今日の日付文字列 yyyy-mm-dd
function today() { return new Date().toISOString().slice(0, 10); }
function fmtDate(d) {
    if (!d) return '';
    const dt = new Date(d);
    return `${dt.getMonth()+1}/${dt.getDate()}`;
}
function fmtDateFull(d) {
    if (!d) return '';
    const dt = new Date(d);
    return `${dt.getFullYear()}/${String(dt.getMonth()+1).padStart(2,'0')}/${String(dt.getDate()).padStart(2,'0')}`;
}

// 指定日における車両の状態を返す
// 終了日なしの非在庫イベントをいつまで有効とみなすか（サーバ側 _STALE_DAYS と一致させる）
const STALE_DAYS = 60;

function getVehicleStatusOnDate(vehicleId, dateStr) {
    const vEvents = events.filter(e => String(e.vehicle_id) === String(vehicleId));
    if (vEvents.length === 0) return { status: '在庫', event: null };

    const staleBefore = new Date(new Date(dateStr).getTime() - STALE_DAYS * 86400000)
        .toISOString().slice(0, 10);

    // その日にかかっているイベントのうち、登録が最も新しいものを採用する。
    // 朝一ラインの resolve_vehicle_states と同じ規則（新しい予約・修正が常に優先）。
    const candidates = vEvents.filter(e => {
        const s   = e.start_date || '0000-00-00';
        const end = e.end_date || '9999-12-31';
        if (dateStr < s || dateStr > end) return false;
        // 終了日のないまま放置された古い貸出・予約は無視する
        if (!e.end_date && e.status !== '在庫' && s < staleBefore) return false;
        return true;
    }).sort((a, b) =>
        (b.created_at || '').localeCompare(a.created_at || '') || (b.id - a.id));

    if (candidates.length === 0) return { status: '在庫', event: null };
    return { status: candidates[0].status, event: candidates[0] };
}

// 現在（今日）の状態を返す
function getVehicleCurrentStatus(vehicleId) {
    return getVehicleStatusOnDate(vehicleId, today());
}

// フィルター適用
function applyFilters() {
    const typeFilter = document.getElementById('filterType').value;
    const statusFilter = document.getElementById('filterStatus').value;
    const search = document.getElementById('filterSearch').value.trim().toLowerCase();

    filteredVehicles = deptVehicles().filter(v => {
        if (typeFilter && v.car_type !== typeFilter) return false;
        if (search && !v.number.toLowerCase().includes(search) && !v.car_type.toLowerCase().includes(search)) return false;
        if (statusFilter) {
            const { status } = getVehicleCurrentStatus(v.id);
            if (status !== statusFilter) return false;
        }
        return true;
    });

    const cnt = filteredVehicles.length;
    document.getElementById('filterCount').textContent = cnt < deptVehicles().length ? `${cnt}件表示` : '';
    renderCurrentPage();
}

function clearFilters() {
    document.getElementById('filterType').value = '';
    document.getElementById('filterStatus').value = '';
    document.getElementById('filterSearch').value = '';
    applyFilters();
}

// ダッシュボードカードクリック→状態フィルター
function filterByStatus(status) {
    // 在庫は特殊処理（イベントなし車両）
    if (status === '') {
        // 全表示に戻す
        document.getElementById('filterStatus').value = '';
        applyFilters();
        renderDashboard();
        return;
    }
    document.getElementById('filterStatus').value = status;
    applyFilters();
    renderDashboard();
    // 車検中クリック時は点検中も含めて表示
    if (status === '車検中') {
        const todayStr = today();
        filteredVehicles = deptVehicles().filter(v => {
            const { status: s } = getVehicleCurrentStatus(v.id);
            return s === '車検中' || s === '点検中';
        });
        renderDashboard();
    }
}

// タブ切り替え
function showPage(name) {
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.getElementById('page-' + name).classList.add('active');
    const tabs = document.querySelectorAll('.tab');
    const idx = { dashboard: 0, calendar: 1, list: 2, pending: 3 }[name];
    tabs[idx].classList.add('active');
    renderCurrentPage();
}

// 車検警告（2ヶ月以内なら警告）
function inspectionWarning(dateStr) {
    if (!dateStr) return '';
    const insp = new Date(dateStr);
    const now  = new Date();
    const diff = (insp - now) / (1000 * 60 * 60 * 24);
    const m = insp.getMonth() + 1, d = insp.getDate();
    if (diff <= 60) return `<span class="insp-warn">${m}/${d}</span>`;
    return '';
}

// 直近イベントの状態アイコン（洗車・清掃・スタッドレス）
function statusIcons(ev, v) {
    // 朝一ラインの ★洗車済 ☆中清掃済 ●冬タイヤ と同じ並び
    let icons = '';
    if (ev && ev.washed)           icons += '🚿';
    if (ev && ev.interior_cleaned) icons += '✨';
    if (v.studless)                icons += '❄️';
    return icons;
}

// ====== ダッシュボード ======
function renderDashboard() {
    const statusCounts = { 在庫: 0, 貸出中: 0, 予約済: 0, 車検中: 0, 点検中: 0, 修理中: 0 };
    filteredVehicles.forEach(v => {
        const { status } = getVehicleCurrentStatus(v.id);
        if (statusCounts[status] !== undefined) statusCounts[status]++;
        else statusCounts['在庫']++;
    });

    const cards = document.getElementById('summaryCards');
    cards.innerHTML = `
        <div class="card available" style="cursor:pointer" onclick="filterByStatus('')">
          <div class="card-num">${statusCounts['在庫']}</div><div class="card-label">在庫</div></div>
        <div class="card rented" style="cursor:pointer" onclick="filterByStatus('貸出中')">
          <div class="card-num">${statusCounts['貸出中']}</div><div class="card-label">貸出中</div></div>
        <div class="card reserved" style="cursor:pointer" onclick="filterByStatus('予約済')">
          <div class="card-num">${statusCounts['予約済']}</div><div class="card-label">予約済</div></div>
        <div class="card inspection" style="cursor:pointer" onclick="filterByStatus('車検中')">
          <div class="card-num">${statusCounts['車検中'] + statusCounts['点検中']}</div><div class="card-label">車検・点検</div></div>
        <div class="card repair" style="cursor:pointer" onclick="filterByStatus('修理中')">
          <div class="card-num">${statusCounts['修理中']}</div><div class="card-label">修理中</div></div>
    `;

    const grid = document.getElementById('vehicleGrid');
    if (filteredVehicles.length === 0) { grid.innerHTML = '<div class="loading">該当する車両がありません</div>'; return; }

    const statusColor = { '在庫':'#4CAF50','貸出中':'#2196F3','予約済':'#FF9800','車検中':'#9C27B0','点検中':'#795548','修理中':'#f44336' };

    let html = `<table class="vlist-table">
    <thead><tr>
      <th>車番</th><th>車種</th><th>状態</th><th>担当/顧客</th><th>返却日</th><th>アイコン</th><th>車検</th>
    </tr></thead><tbody>`;

    filteredVehicles.forEach(v => {
        const { status, event: ev } = getVehicleCurrentStatus(v.id);
        const color  = statusColor[status] || '#4CAF50';
        const badge  = `<span style="background:${color};color:white;padding:2px 5px;border-radius:8px;font-size:11px;font-weight:bold;">${status.charAt(0)}</span>`;
        const client = ev ? [ev.staff, ev.client].filter(Boolean).join(' / ') : '';
        const endDt  = ev && ev.end_date ? fmtDate(ev.end_date) : '';
        const icons  = statusIcons(ev, v);
        const insp   = inspectionWarning(v.inspection_date);
        const cat    = v.car_category ? `<span class="vlist-cat">${v.car_category}</span>` : '';
        html += `<tr onclick="openDetail(${v.id})">
          <td class="vlist-num">${v.number}</td>
          <td><span class="vlist-type">${v.car_type}</span> ${cat}</td>
          <td>${badge}</td>
          <td class="vlist-client">${client}</td>
          <td style="font-size:12px;color:#888;">${endDt}</td>
          <td class="vlist-icons">${icons}</td>
          <td class="vlist-insp">${insp}</td>
        </tr>`;
    });
    html += '</tbody></table>';
    grid.innerHTML = html;
}

// ====== カレンダー ======
function renderCalendar() {
    const startDate = new Date();
    startDate.setDate(startDate.getDate() + calendarOffset);
    startDate.setHours(0,0,0,0);
    const days = 28;
    const dates = [];
    for (let i = 0; i < days; i++) {
        const d = new Date(startDate);
        d.setDate(d.getDate() + i);
        dates.push(d);
    }

    const endDate = dates[dates.length - 1];
    const sy = String(startDate.getFullYear()).slice(2), sm = startDate.getMonth()+1, sd = startDate.getDate();
    const ey = String(endDate.getFullYear()).slice(2),   em = endDate.getMonth()+1,   ed = endDate.getDate();
    const rangeLabel = sy === ey
        ? `${sy}/${sm}/${sd} - ${em}/${ed}`
        : `${sy}/${sm}/${sd} - ${ey}/${em}/${ed}`;
    document.getElementById('calendarRangeLabel').textContent = rangeLabel;

    const todayStr = today();
    const statusClass = { '在庫': 'available', '貸出中': 'rented', '予約済': 'reserved', '車検中': 'inspection', '点検中': 'inspection', '修理中': 'repair' };
    const dayNames = ['日','月','火','水','木','金','土'];

    let html = '<table class="calendar-table"><thead><tr>';
    html += '<th class="cal-vehicle-col" style="font-size:9px;">カテゴリ／車両</th>';
    dates.forEach(d => {
        const ds = d.toISOString().slice(0,10);
        const isToday = ds === todayStr;
        const dow = d.getDay();
        let cls = 'cal-date-header';
        if (isToday) cls += ' today';
        else if (dow === 0) cls += ' sunday';
        else if (dow === 6) cls += ' saturday';
        html += `<th class="${cls}" style="font-size:8px;padding:2px 1px;">${d.getMonth()+1}/${d.getDate()}<br>${dayNames[dow]}</th>`;
    });
    html += '</tr></thead><tbody>';

    // 並び替え / 在庫フィルター
    const sortKey = (document.getElementById('calendarSort') || {value:'number'}).value;
    const statusOrder = {'貸出中':0,'予約済':1,'車検中':2,'点検中':3,'修理中':4,'在庫':5,'':6};

    // 在庫のみ（地域絞り込み含む）→ テーブル表示に切り替え
    if (sortKey === 'stock' || sortKey === 'stock_kyoto' || sortKey === 'stock_shiga') {
        const regionFilter = sortKey === 'stock_kyoto' ? '京都' : sortKey === 'stock_shiga' ? '滋賀' : null;
        renderStockTable(todayStr, regionFilter);
        return;
    }

    const regionOrder = r => r === '京都' ? 0 : r === '滋賀' ? 1 : 2;
    const sortedVehicles = [...filteredVehicles].sort((a, b) => {
        const ra = regionOrder(a.region), rb = regionOrder(b.region);
        if (sortKey === 'status') {
            const sa = getVehicleStatusOnDate(a.id, todayStr).status;
            const sb = getVehicleStatusOnDate(b.id, todayStr).status;
            const diff = (statusOrder[sa] ?? 9) - (statusOrder[sb] ?? 9);
            if (diff !== 0) return diff;
            if (ra !== rb) return ra - rb;
            return a.number.localeCompare(b.number);
        } else if (sortKey === 'type') {
            if (ra !== rb) return ra - rb;
            const diff = (a.car_type || '').localeCompare(b.car_type || '');
            return diff !== 0 ? diff : a.number.localeCompare(b.number);
        } else {
            if (ra !== rb) return ra - rb;
            return a.number.localeCompare(b.number);
        }
    });

    sortedVehicles.forEach(v => {
        html += `<tr>`;
        const regionChar = v.region === '京都' ? '<span style="font-size:9px;color:#1565C0;font-weight:bold;">京</span>' :
                           v.region === '滋賀' ? '<span style="font-size:9px;color:#2E7D32;font-weight:bold;">滋</span>' : '';
        html += `<td class="cal-vehicle-col" onclick="openDetail(${v.id})" style="cursor:pointer;">
            ${regionChar}${v.car_category ? `<span class="cal-cat">${v.car_category}</span>` : ''}
            <span class="cal-vehicle-num">${v.number}</span>
            <span class="cal-vehicle-type">${v.car_type}</span>
        </td>`;
        dates.forEach(d => {
            const ds = d.toISOString().slice(0,10);
            const { status, event: ev } = getVehicleStatusOnDate(v.id, ds);
            const sc = statusClass[status] || 'unknown';
            let tip = status;
            if (ev && ev.staff)  tip += ` ${ev.staff}`;
            if (ev && ev.client) tip += ` / ${ev.client}`;
            // セル内テキスト：貸出中・予約済のとき顧客名の先頭4文字を表示
            let cellText = '';
            if (ev && ev.client && (status === '貸出中' || status === '予約済')) {
                cellText = `<span class="cal-cell-text">${ev.client.slice(0,5)}</span>`;
            }
            html += `<td class="cal-cell ${sc}" onclick="openDetail(${v.id})"
                onmouseenter="showTip(event,'${(v.number+' '+v.car_type+': '+tip).replace(/'/g,"")}')"
                onmouseleave="hideTip()">${cellText}</td>`;
        });
        html += '</tr>';
    });
    html += '</tbody></table>';

    document.getElementById('calendarView').innerHTML = html;
    document.getElementById('calendarView').className = 'calendar-container';
}

// ====== 在庫テーブル（在庫のみ選択時） ======
function renderStockTable(todayStr, regionFilter) {
    const _ro = r => r === '京都' ? 0 : r === '滋賀' ? 1 : 2;
    const stockVehicles = filteredVehicles
        .filter(v => getVehicleStatusOnDate(v.id, todayStr).status === '在庫'
                  && (!regionFilter || v.region === regionFilter))
        .sort((a, b) => {
            const rd = _ro(a.region) - _ro(b.region);
            if (rd !== 0) return rd;
            const ca = a.car_type || '', cb = b.car_type || '';
            return ca !== cb ? ca.localeCompare(cb) : a.number.localeCompare(b.number);
        });

    if (stockVehicles.length === 0) {
        document.getElementById('calendarView').innerHTML =
            '<div class="loading">在庫車両はありません</div>';
        return;
    }

    let html = `<table class="vlist-table">
    <thead><tr>
      <th>車種カテゴリ</th>
      <th>車番・車種</th>
      <th>ステータス</th>
      <th>次の予定日</th>
    </tr></thead><tbody>`;

    stockVehicles.forEach(v => {
        // 現在有効なイベントから洗車・清掃を取得（朝一ラインと同じ判定）
        const latestEv = getVehicleCurrentStatus(v.id).event;

        // アイコン
        let icons = '';
        if (latestEv && latestEv.washed)            icons += '<span title="洗車済み">🚿</span>';
        if (latestEv && latestEv.interior_cleaned)  icons += '<span title="室内清掃済み">✨</span>';
        if (v.studless)                             icons += '<span title="スタッドレス">❄️</span>';

        // 車検警告
        const inspHtml = inspectionWarning(v.inspection_date);

        const statusCell = `<span style="background:#4CAF50;color:white;padding:1px 5px;border-radius:8px;font-size:10px;font-weight:bold;">在</span>
            ${icons ? `<span style="margin-left:2px;font-size:10px;">${icons}</span>` : ''}
            ${inspHtml ? `<span style="margin-left:3px;">${inspHtml}</span>` : ''}`;

        // 次の予約・貸出予定
        const nextEv = events
            .filter(e => String(e.vehicle_id) === String(v.id) && (e.start_date || '') > todayStr)
            .sort((a, b) => (a.start_date || '').localeCompare(b.start_date || ''))[0] || null;
        const nextCell = nextEv
            ? `<span style="color:#FF9800;font-size:10px;">▶${fmtDate(nextEv.start_date)} ${nextEv.status}</span>
               ${nextEv.client ? `<br><span style="font-size:9px;color:#888;">${nextEv.client}</span>` : ''}`
            : '<span style="color:#ccc;font-size:10px;">—</span>';

        html += `<tr onclick="openDetail(${v.id})" style="cursor:pointer;">
          <td><span class="vlist-cat" style="font-size:12px;">${v.car_category || '—'}</span></td>
          <td>
            <span class="vlist-num">${v.number}</span>
            <span class="vlist-type" style="margin-left:4px;">${v.car_type}</span>
          </td>
          <td>${statusCell}</td>
          <td>${nextCell}</td>
        </tr>`;
    });

    html += `</tbody></table>
    <div style="text-align:right;font-size:12px;color:#888;margin-top:6px;">在庫 ${stockVehicles.length}台</div>`;

    document.getElementById('calendarView').innerHTML = html;
    document.getElementById('calendarView').className = 'calendar-container';
}

// ====== 取引先マスタ ======
// yomi: ひらがな読み, romaji: ローマ字略称（アルファベット社名用）
// 取引先マスタ（APIから取得）
let CLIENT_LIST = []; // [{name, reading}]
async function loadClients() {
    try {
        const data = await fetch(API + '/api/clients').then(r => r.json());
        CLIENT_LIST = Array.isArray(data) ? data : [];
        console.log('[clients] loaded:', CLIENT_LIST.length);
    } catch(e) {
        console.error('[clients] load error:', e);
        CLIENT_LIST = [];
    }
}

// 半角カナ・全角カナ→ひらがな（NFKC正規化 + 全角カナ→ひらがな）
function toSearchKey(s) {
    const normalized = (s || '').normalize('NFKC');
    return normalized.replace(/[ァ-ヶ]/g, c => String.fromCharCode(c.charCodeAt(0) - 0x60)).toLowerCase();
}

let _clientTimer;
function onClientInputDebounce(el) {
    clearTimeout(_clientTimer);
    _clientTimer = setTimeout(() => onClientInput(el.value), 200);
}

function onClientInput(val) {
    const input = document.getElementById('formClient');
    const box = document.getElementById('clientSuggest');
    if (!val || !val.trim()) { box.style.display = 'none'; return; }
    const q = toSearchKey(val.trim());
    const raw = val.trim().toLowerCase();
    const filtered = CLIENT_LIST.filter(item => {
        const name = item.name || item;
        const reading = item.reading || '';
        const nameKey = toSearchKey(name);
        const readingKey = toSearchKey(reading);
        return nameKey.includes(q) || readingKey.includes(q) ||
               name.toLowerCase().includes(raw) || reading.toLowerCase().includes(raw);
    });
    if (filtered.length === 0) { box.style.display = 'none'; return; }
    box.innerHTML = filtered.slice(0, 30).map(item => {
        const name = item.name || item;
        return `<div onclick="selectClient(this)" data-name="${name.replace(/"/g,'&quot;')}"` +
               ` style="padding:8px 12px;cursor:pointer;font-size:13px;border-bottom:1px solid #f0f0f0;"` +
               ` onmouseover="this.style.background='#e8f4ff'" onmouseout="this.style.background=''">${name}</div>`;
    }).join('');
    // position: fixed でモーダルのoverflow-y:autoを回避
    const rect = input.getBoundingClientRect();
    box.style.position = 'fixed';
    box.style.left = rect.left + 'px';
    box.style.width = rect.width + 'px';
    box.style.top = (rect.bottom + 2) + 'px';
    box.style.maxHeight = Math.min(220, window.innerHeight - rect.bottom - 10) + 'px';
    box.style.display = 'block';
}

function selectClient(el) {
    const name = el.dataset.name;
    document.getElementById('formClient').value = name;
    document.getElementById('clientSuggest').style.display = 'none';
    document.getElementById('formClientContact').focus();
}

document.addEventListener('click', e => {
    if (!e.target.closest('#formClient') && !e.target.closest('#clientSuggest')) {
        const box = document.getElementById('clientSuggest');
        if (box) box.style.display = 'none';
    }
});

// ====== 未処理メッセージ ======
async function renderPending() {
    const el = document.getElementById('pendingList');
    el.innerHTML = '<div class="loading">読み込み中…</div>';
    try {
        const res = await fetch('/api/pending');
        const items = await res.json();
        updatePendingBadge(items.length);
        if (items.length === 0) {
            el.innerHTML = '<div style="text-align:center;padding:40px;color:#888;">✅ 未処理メッセージはありません</div>';
            return;
        }
        el.innerHTML = items.map(item => {
            const dt = (item.created_at || '').slice(0, 16);
            const msg = (item.message || '').replace(/</g,'&lt;').replace(/\n/g,'<br>');
            return `<div style="background:white;border-radius:8px;padding:14px 16px;margin-bottom:8px;box-shadow:0 1px 4px rgba(0,0,0,0.1);border-left:4px solid #FF9800;">
                <div style="font-size:11px;color:#888;margin-bottom:6px;">📅 ${dt}</div>
                <div style="font-size:13px;line-height:1.7;margin-bottom:10px;white-space:pre-wrap;">${msg}</div>
                <button class="btn btn-success btn-sm" onclick="resolvePending(${item.id}, this)">✅ 処理済みにする</button>
            </div>`;
        }).join('');
    } catch(e) {
        el.innerHTML = '<div style="color:red;">読み込みエラー</div>';
    }
}

async function resolvePending(id, btn) {
    btn.disabled = true;
    btn.textContent = '処理中…';
    try {
        await fetch(`/api/pending/${id}/resolve`, {method:'POST'});
        btn.closest('div[style]').style.opacity = '0.4';
        btn.textContent = '✅ 処理済み';
        // バッジ更新
        const res = await fetch('/api/pending');
        const items = await res.json();
        updatePendingBadge(items.length);
        setTimeout(() => renderPending(), 800);
    } catch(e) {
        btn.textContent = 'エラー';
    }
}

function updatePendingBadge(count) {
    const badge = document.getElementById('pendingBadge');
    if (!badge) return;
    if (count > 0) {
        badge.textContent = count;
        badge.style.display = 'inline';
    } else {
        badge.style.display = 'none';
    }
}

// ページ読み込み時にバッジを更新
async function refreshPendingBadge() {
    try {
        const res = await fetch('/api/pending');
        const items = await res.json();
        updatePendingBadge(items.length);
    } catch(e) {}
}

// ====== リスト ======
function renderList() {
    if (filteredVehicles.length === 0) {
        document.getElementById('vehicleList').innerHTML = '<div class="loading">該当する車両がありません</div>';
        return;
    }
    const statusClass = { '在庫': '#4CAF50', '貸出中': '#2196F3', '予約済': '#FF9800', '車検中': '#9C27B0', '点検中': '#795548', '修理中': '#f44336' };
    let html = `<table style="width:100%;border-collapse:collapse;background:white;border-radius:8px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,0.1);">
    <thead><tr style="background:#1a3a5c;color:white;">
        <th style="padding:10px 12px;text-align:left;">番号</th>
        <th style="padding:10px 12px;text-align:left;">車種</th>
        <th style="padding:10px 12px;text-align:left;">年式</th>
        <th style="padding:10px 12px;text-align:left;">現在状態</th>
        <th style="padding:10px 12px;text-align:left;">取引先</th>
        <th style="padding:10px 12px;text-align:left;">返却/終了日</th>
        <th style="padding:10px 12px;text-align:left;">次の予定</th>
        <th style="padding:10px 12px;text-align:left;">車検満了</th>
        <th style="padding:10px 12px;"></th>
    </tr></thead><tbody>`;

    filteredVehicles.forEach((v, i) => {
        const { status, event: ev } = getVehicleCurrentStatus(v.id);
        const color = statusClass[status] || '#4CAF50';
        const future = events.filter(e => String(e.vehicle_id) === String(v.id) && (e.start_date || '') > today())
            .sort((a,b) => (a.start_date||'').localeCompare(b.start_date||''));
        const next = future.length > 0 ? `${fmtDate(future[0].start_date)} ${future[0].status}` : '';
        const bg = i % 2 === 0 ? '#fff' : '#f9f9f9';
        html += `<tr style="background:${bg};border-bottom:1px solid #f0f0f0;" onclick="openDetail(${v.id})" style="cursor:pointer;">
            <td style="padding:9px 12px;font-weight:bold;font-size:15px;color:#1a3a5c;">${v.number}</td>
            <td style="padding:9px 12px;">${v.car_type}</td>
            <td style="padding:9px 12px;font-size:12px;color:#888;">${v.year || ''}</td>
            <td style="padding:9px 12px;"><span style="background:${color};color:white;padding:2px 8px;border-radius:12px;font-size:12px;font-weight:bold;">${status}</span></td>
            <td style="padding:9px 12px;font-size:13px;">${ev && ev.client ? (ev.staff ? ev.staff+' / ' : '') + ev.client : ''}</td>
            <td style="padding:9px 12px;font-size:13px;">${ev && ev.end_date ? fmtDateFull(ev.end_date) : ''}</td>
            <td style="padding:9px 12px;font-size:12px;color:#FF9800;">${next}</td>
            <td style="padding:9px 12px;font-size:12px;color:#888;">${v.inspection_date ? fmtDateFull(v.inspection_date) : ''}</td>
            <td style="padding:9px 12px;"><button class="btn btn-primary btn-sm" onclick="event.stopPropagation();openLiffForm(${v.id})">📝 登録</button></td>
        </tr>`;
    });
    html += '</tbody></table>';
    document.getElementById('vehicleList').innerHTML = html;
}

// ====== 詳細パネル ======
function openDetail(vehicleId) {
    currentDetailVehicleId = vehicleId;
    renderDetail(vehicleId);
    document.getElementById('detailPanel').classList.add('open');
}

function closeDetail() {
    document.getElementById('detailPanel').classList.remove('open');
    currentDetailVehicleId = null;
}

function renderDetail(vehicleId) {
    const v = vehicles.find(x => x.id == vehicleId);
    if (!v) return;
    document.getElementById('detailNumber').textContent = v.number;
    document.getElementById('detailType').textContent = v.car_type;
    document.getElementById('detailVehicleInfo').innerHTML = `
        <div>部門: <strong>${DEPT_LABELS[v.department || DEPT_DEFAULT]}</strong></div>
        <div>ナンバー: <strong>${v.full_number || v.number}</strong></div>
        <div>年式: ${v.year || '不明'}</div>
        <div>車検満了: ${v.inspection_date ? fmtDateFull(v.inspection_date) : '未登録'}</div>
    `;

    const vEvents = events.filter(e => String(e.vehicle_id) === String(vehicleId))
        .sort((a, b) => (b.start_date || '').localeCompare(a.start_date || ''));

    if (vEvents.length === 0) {
        document.getElementById('detailEvents').innerHTML = '<div style="color:#aaa;font-size:13px;padding:8px 0;">登録された状態はありません</div>';
        return;
    }

    const todayStr = today();
    document.getElementById('detailEvents').innerHTML = vEvents.map(e => {
        const isPast = e.end_date && e.end_date < todayStr;
        const opacity = isPast ? 'opacity:0.5;' : '';
        const dateStr = e.start_date ? `${fmtDateFull(e.start_date)} ～ ${e.end_date ? fmtDateFull(e.end_date) : '未定'}` : '日付未設定';
        return `<div class="event-item status-${e.status}" style="${opacity}">
            <button class="event-delete" onclick="deleteEvent(${e.id})" title="削除">✕</button>
            <div class="event-type">${e.status}</div>
            <div class="event-dates">${dateStr}</div>
            ${e.staff || e.client ? `<div class="event-client">👤 ${e.staff ? e.staff+' / ' : ''}${e.client || ''}</div>` : ''}
            ${e.category ? `<div class="event-client" style="font-size:11px;">区分: ${e.category}</div>` : ''}
            ${e.notes ? `<div class="event-notes">📝 ${e.notes}</div>` : ''}
        </div>`;
    }).join('');
}

// ====== LIFFフォームを開く ======
function openLiffForm(vehicleId) {
    const v = vehicleId ? vehicles.find(x => x.id == vehicleId) : null;
    const dept = v ? (v.department || DEPT_DEFAULT) : currentDept;
    const url = v
        ? `/liff?dept=${dept}&vehicle=${encodeURIComponent(v.number)}`
        : `/liff?dept=${dept}`;
    window.open(url, '_blank');
}

// ====== 状態登録モーダル ======
function openAddModal(vehicleId) {
    editingEventId = null;
    document.getElementById('modalTitle').textContent = `車両状態を登録（${DEPT_LABELS[currentDept]}）`;
    const numInput = document.getElementById('formVehicleNumber');

    if (vehicleId) {
        const v = vehicles.find(x => x.id == vehicleId);
        if (v) {
            numInput.value = v.number;
            numInput.readOnly = true;
            document.getElementById('vehicleSuggest').textContent = `${v.car_type} (${v.full_number || v.number})`;
        }
    } else {
        numInput.value = '';
        numInput.readOnly = false;
        document.getElementById('vehicleSuggest').textContent = '';
    }

    document.getElementById('formStatus').value = '貸出中';
    document.getElementById('formStartDate').value = today();
    document.getElementById('formEndDate').value = '';
    document.getElementById('formStaff').value = '';
    document.getElementById('formClient').value = '';
    document.getElementById('formClientContact').value = '';
    document.getElementById('formCategory').value = '';
    document.getElementById('formNotes').value = '';
    onStatusChange();

    document.getElementById('addModal').classList.add('open');
}

// 既存の登録を読み込んで編集する（朝一プレビューからの修正用）
function openEditModal(eventId, vehicleId) {
    const ev = events.find(e => e.id == eventId);
    if (!ev) { alert('登録が見つかりません（画面を再読込してください）'); return; }
    editingEventId = ev.id;

    const v = vehicles.find(x => x.id == (vehicleId || ev.vehicle_id));
    const numInput = document.getElementById('formVehicleNumber');
    numInput.value = v ? v.number : '';
    numInput.readOnly = true;
    document.getElementById('vehicleSuggest').textContent =
        v ? `${v.car_type} (${v.full_number || v.number})` : '';

    document.getElementById('modalTitle').textContent = '登録内容を編集';
    document.getElementById('formStatus').value      = ev.status || '在庫';
    document.getElementById('formStartDate').value   = ev.start_date || '';
    document.getElementById('formEndDate').value     = ev.end_date || '';
    document.getElementById('formStaff').value       = ev.staff || '';
    document.getElementById('formClient').value      = ev.client || '';
    document.getElementById('formClientContact').value = ev.client_contact || '';
    document.getElementById('formCategory').value    = ev.category || '';
    document.getElementById('formNotes').value       = _clientNote(ev);
    onStatusChange();

    document.getElementById('addModal').classList.add('open');
}

// システムが付けた文言（所在地/取込メモ）は編集欄に出さない
function _clientNote(ev) {
    const n = (ev.notes || '').trim();
    if (n.startsWith('所在地:') || n.startsWith('エクセル取込')) return '';
    return n;
}

function closeAddModal() {
    document.getElementById('addModal').classList.remove('open');
    document.getElementById('formVehicleNumber').readOnly = false;
    editingEventId = null;
}

function onVehicleNumberInput() {
    const num = document.getElementById('formVehicleNumber').value.trim();
    // 車番の照合は表示中の部門内だけで行う（両部門で同じ4桁があっても取り違えない）
    const v = deptVehicles().find(x => x.number === num);
    const sug = document.getElementById('vehicleSuggest');
    if (v) { sug.textContent = `✓ ${v.car_type} (${v.full_number || v.number})`; sug.style.color = '#4CAF50'; }
    else if (num.length >= 3) { sug.textContent = '該当車両なし'; sug.style.color = '#f44336'; }
    else { sug.textContent = ''; }
}

function onStatusChange() {
    const s = document.getElementById('formStatus').value;
    const showClient = ['貸出中', '予約済'].includes(s);
    document.getElementById('clientFields').style.display = showClient ? '' : 'none';
}

async function saveEvent() {
    const numInput = document.getElementById('formVehicleNumber').value.trim();
    const v = deptVehicles().find(x => x.number === numInput);
    if (!v) { alert(`車両番号が見つかりません（${DEPT_LABELS[currentDept]}）`); return; }

    const payload = {
        vehicle_id: v.id,
        status: document.getElementById('formStatus').value,
        start_date: document.getElementById('formStartDate').value || null,
        end_date: document.getElementById('formEndDate').value || null,
        staff: document.getElementById('formStaff').value || null,
        client: document.getElementById('formClient').value.trim() || null,
        client_contact: document.getElementById('formClientContact').value.trim() || null,
        category: document.getElementById('formCategory').value || null,
        notes: document.getElementById('formNotes').value.trim() || null
    };

    const editing = editingEventId;
    await fetch(API + '/api/events' + (editing ? '/' + editing : ''), {
        method: editing ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });

    events = await fetch(API + '/api/events').then(r => r.json());
    closeAddModal();
    renderCurrentPage();
    if (currentDetailVehicleId) renderDetail(currentDetailVehicleId);
    // 朝一プレビューを開いたまま編集した場合はその場で作り直す
    if (document.getElementById('morningModal').classList.contains('open')) loadMorningPreview();

    document.getElementById('lastUpdated').textContent = fmtUpdated();
}

async function deleteEvent(eventId) {
    if (!confirm('この状態記録を削除しますか？')) return;
    await fetch(API + '/api/events/' + eventId, { method: 'DELETE' });
    events = await fetch(API + '/api/events').then(r => r.json());
    renderCurrentPage();
    if (currentDetailVehicleId) renderDetail(currentDetailVehicleId);
}

// ツールチップ
function showTip(e, text) {
    const t = document.getElementById('tooltip');
    t.textContent = text;
    t.style.display = 'block';
    t.style.left = (e.pageX + 12) + 'px';
    t.style.top = (e.pageY - 8) + 'px';
}
function hideTip() {
    document.getElementById('tooltip').style.display = 'none';
}

async function setupRichMenu() {
    if (!confirm('LINEボットの画面下部に「入力フォームを開く」常駐ボタンを設定します。\nよろしいですか？')) return;
    try {
        const res = await fetch('/api/admin/setup-richmenu', {
            method: 'POST',
            headers: { 'X-Admin-Key': '3155' }
        });
        const data = await res.json();
        if (data.ok) {
            alert('✅ リッチメニューを設定しました！\n\nLINEでボット（吉岡商会bot）に「こんにちは」などと送ると、画面下部に緑のボタンが常時表示されます。\n\nグループチャットでも、ボットに話しかけることでボタンが現れます。');
        } else {
            alert('❌ 設定失敗: ' + (data.error || '不明なエラー'));
        }
    } catch(e) {
        alert('❌ 通信エラー');
    }
}

async function sendFormLinkToLine() {
    if (!confirm('グループLINEにフォームボタンを送信します。\n送信後、そのメッセージを長押し→「ピン留め」すると\nグループ画面の上部に常時表示されます。')) return;
    try {
        const res = await fetch('/api/admin/send-form-link', {
            method: 'POST',
            headers: { 'X-Admin-Key': '3155' }
        });
        const data = await res.json();
        if (data.sent) {
            alert('✅ LINEグループに送信しました！\n\n【次の手順】\n①LINEグループを開く\n②届いたメッセージを長押し\n③「ピン留め」を選択\n\nこれでグループ画面の上部にボタンが常時表示されます 📌');
        } else {
            alert('❌ 送信失敗: ' + (data.error || '不明なエラー'));
        }
    } catch(e) {
        alert('❌ 通信エラー');
    }
}

init();

// ── 朝一ﾗｲﾝﾌﾟﾚﾋﾞｭｰ ──────────────────────────────────────────
let morningBlocks = [];

function openMorningPreview() {
    const el = document.getElementById('morningDate');
    if (!el.value) {
        const now = new Date(Date.now() + 9 * 3600 * 1000);
        el.value = now.toISOString().slice(0, 10);
    }
    document.getElementById('morningModal').classList.add('open');
    loadMorningPreview();
}

function closeMorningPreview() {
    document.getElementById('morningModal').classList.remove('open');
}

function esc(t) {
    return String(t).replace(/[&<>"]/g, c => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;' }[c]));
}

async function loadMorningPreview() {
    const box  = document.getElementById('morningText');
    const info = document.getElementById('morningInfo');
    box.innerHTML = '読み込み中…';
    info.textContent = '';
    try {
        const d = document.getElementById('morningDate').value;
        const r = await fetch('/api/morning-report/preview?date=' + encodeURIComponent(d));
        if (!r.ok) throw new Error(r.status);
        const j = await r.json();
        morningBlocks = j.blocks || [];
        renderMorningBlocks();
        info.textContent = j.message.length + '文字';
    } catch (e) {
        box.textContent = '❌ 取得に失敗しました';
    }
}

// 明細行はタップで編集・削除できるようにする。
// 見出しや空行はそのまま表示するだけ。
function renderMorningBlocks() {
    const box = document.getElementById('morningText');
    box.innerHTML = morningBlocks.map((b, i) => {
        if (b.type === 'blank') return '<div style="height:8px;"></div>';
        if (b.type === 'item') {
            const sub = b.sub ? `<div style="font-size:11px;color:#666;">${esc(b.sub)}</div>` : '';
            return `<div class="mp-item" onclick="openMorningRowMenu(${i})">`
                 + `<span>${esc(b.text)}</span><span class="mp-edit">✎</span>${sub}</div>`;
        }
        const weight = (b.type === 'head' || b.type === 'section') ? 'font-weight:bold;' : '';
        return `<div style="${weight}">${esc(b.text)}</div>`;
    }).join('');
}

// 選択した行に対する操作
function openMorningRowMenu(idx) {
    const b = morningBlocks[idx];
    if (!b || b.type !== 'item') return;
    document.getElementById('mpRowLabel').textContent = b.text.replace(/^・/, '');
    document.getElementById('mpRowIdx').value = idx;
    document.getElementById('mpRowMenu').classList.add('open');
}

function closeMorningRowMenu() {
    document.getElementById('mpRowMenu').classList.remove('open');
}

function morningRowEdit() {
    const b = morningBlocks[document.getElementById('mpRowIdx').value];
    closeMorningRowMenu();
    if (!b) return;
    if (b.event_id) openEditModal(b.event_id, b.vehicle_id);
    else openAddModal(b.vehicle_id);
}

async function morningRowDelete() {
    const b = morningBlocks[document.getElementById('mpRowIdx').value];
    if (!b) return;
    if (!b.event_id) { alert('この行には削除できる登録がありません'); return; }
    if (!confirm(`${b.text.replace(/^・/, '')}
この登録を削除しますか？
（一覧から外れ、状態未登録になります）`)) return;
    closeMorningRowMenu();
    await fetch(API + '/api/events/' + b.event_id, { method: 'DELETE' });
    events = await fetch(API + '/api/events').then(r => r.json());
    renderCurrentPage();
    loadMorningPreview();
}

// プレビューから車両を追加登録する（在庫・車検などの追加）
function morningAddVehicle() {
    openAddModal();
}

async function sendMorningNow() {
    if (!confirm('この内容でグループLINEに送信します。よろしいですか？')) return;
    try {
        const r = await fetch('/api/morning-report/send', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ date: document.getElementById('morningDate').value })
        });
        alert(r.ok ? '✅ 送信しました' : '❌ 送信に失敗しました');
        if (r.ok) closeMorningPreview();
    } catch (e) {
        alert('❌ 通信エラー');
    }
}
