const API = '';
let vehicles = [];
let events = [];
let filteredVehicles = [];
let currentDetailVehicleId = null;
let calendarOffset = 0;
let editingEventId = null;

// 初期化
async function init() {
    const [v, e] = await Promise.all([
        fetch(API + '/api/vehicles').then(r => r.json()),
        fetch(API + '/api/events').then(r => r.json())
    ]);
    vehicles = Array.isArray(v) ? v : [];
    events = Array.isArray(e) ? e : [];

    // 車種フィルター選択肢を生成
    const types = [...new Set(vehicles.map(v => v.car_type))].sort();
    const sel = document.getElementById('filterType');
    types.forEach(t => {
        const o = document.createElement('option');
        o.value = t; o.textContent = t;
        sel.appendChild(o);
    });

    document.getElementById('totalBadge').textContent = `全 ${vehicles.length} 台`;
    document.getElementById('lastUpdated').textContent = `最終更新: ${new Date().toLocaleString('ja-JP')}`;

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
function getVehicleStatusOnDate(vehicleId, dateStr) {
    const vEvents = events.filter(e => String(e.vehicle_id) === String(vehicleId));
    if (vEvents.length === 0) return { status: '在庫', event: null };

    // その日にかかっているイベントを探す（終了日なしは当日以降ずっと有効）
    for (const e of vEvents.sort((a, b) => (b.start_date || '').localeCompare(a.start_date || ''))) {
        const s = e.start_date || '0000-00-00';
        const end = e.end_date || '9999-12-31';
        if (dateStr >= s && dateStr <= end) return { status: e.status, event: e };
    }
    return { status: '在庫', event: null };
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

    filteredVehicles = vehicles.filter(v => {
        if (typeFilter && v.car_type !== typeFilter) return false;
        if (search && !v.number.toLowerCase().includes(search) && !v.car_type.toLowerCase().includes(search)) return false;
        if (statusFilter) {
            const { status } = getVehicleCurrentStatus(v.id);
            if (status !== statusFilter) return false;
        }
        return true;
    });

    const cnt = filteredVehicles.length;
    document.getElementById('filterCount').textContent = cnt < vehicles.length ? `${cnt}件表示` : '';
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
        filteredVehicles = vehicles.filter(v => {
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
        <div class="card available" style="cursor:pointer" onclick="filterByStatus('')" title="クリックで全表示">
          <div class="card-num">${statusCounts['在庫']}</div><div class="card-label">在庫（空き）</div></div>
        <div class="card rented" style="cursor:pointer" onclick="filterByStatus('貸出中')" title="クリックで貸出中を表示">
          <div class="card-num">${statusCounts['貸出中']}</div><div class="card-label">貸出中</div></div>
        <div class="card reserved" style="cursor:pointer" onclick="filterByStatus('予約済')" title="クリックで予約済を表示">
          <div class="card-num">${statusCounts['予約済']}</div><div class="card-label">予約済</div></div>
        <div class="card inspection" style="cursor:pointer" onclick="filterByStatus('車検中')" title="クリックで車検・点検中を表示">
          <div class="card-num">${statusCounts['車検中'] + statusCounts['点検中']}</div><div class="card-label">車検・点検中</div></div>
        <div class="card repair" style="cursor:pointer" onclick="filterByStatus('修理中')" title="クリックで修理中を表示">
          <div class="card-num">${statusCounts['修理中']}</div><div class="card-label">修理中</div></div>
    `;

    const grid = document.getElementById('vehicleGrid');
    if (filteredVehicles.length === 0) { grid.innerHTML = '<div class="loading">該当する車両がありません</div>'; return; }

    grid.innerHTML = filteredVehicles.map(v => {
        const { status, event: ev } = getVehicleCurrentStatus(v.id);
        const badge = `<span class="status-badge badge-${status}">${status}</span>`;

        let infoLines = '';
        if (ev) {
            // 担当者
            if (ev.staff) infoLines += `<div class="vc-info-row"><span class="vc-info-label">担当</span><span class="vc-info-val">${ev.staff}</span></div>`;
            // 取引先
            if (ev.client) infoLines += `<div class="vc-info-row"><span class="vc-info-label">先</span><span class="vc-info-val vc-client-text">${ev.client}</span></div>`;
            // 適用区分
            if (ev.category) infoLines += `<div class="vc-info-row"><span class="vc-info-label">区分</span><span class="vc-info-val vc-cat-${ev.category}">${ev.category}</span></div>`;
            // 期間
            const sDate = ev.start_date ? fmtDate(ev.start_date) : '';
            const eDate = ev.end_date ? fmtDate(ev.end_date) : '未定';
            if (sDate) infoLines += `<div class="vc-info-row"><span class="vc-info-label">期間</span><span class="vc-info-val">${sDate}〜${ev.end_date ? eDate : ''}</span></div>`;
            // 備考
            if (ev.notes) infoLines += `<div class="vc-notes">${ev.notes}</div>`;
        }

        // 次の予定
        const todayStr = today();
        const future = events.filter(e => String(e.vehicle_id) === String(v.id) && (e.start_date || '') > todayStr)
            .sort((a,b) => (a.start_date||'').localeCompare(b.start_date||''));
        let nextLine = '';
        if (future.length > 0) {
            const n = future[0];
            const label = n.client ? `${n.client}` : n.notes ? n.notes : n.status;
            nextLine = `<div class="vc-next">▶ ${fmtDate(n.start_date)} <span class="vc-next-label">${n.status}</span> ${n.staff ? n.staff+' ' : ''}${label}</div>`;
        }

        return `<div class="vehicle-card status-${status}" onclick="openDetail(${v.id})">
            <div class="vc-header">
                <span class="vc-number">${v.number}</span>
                ${badge}
            </div>
            <div class="vc-type">${v.car_type}</div>
            <div class="vc-info">${infoLines}</div>
            ${nextLine}
        </div>`;
    }).join('');
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
    document.getElementById('calendarRangeLabel').textContent =
        `${startDate.getFullYear()}/${startDate.getMonth()+1}/${startDate.getDate()} ～ ${endDate.getFullYear()}/${endDate.getMonth()+1}/${endDate.getDate()}`;

    const todayStr = today();
    const statusClass = { '在庫': 'available', '貸出中': 'rented', '予約済': 'reserved', '車検中': 'inspection', '点検中': 'inspection', '修理中': 'repair' };
    const dayNames = ['日','月','火','水','木','金','土'];

    let html = '<table class="calendar-table"><thead><tr>';
    html += '<th class="cal-vehicle-col">車両</th>';
    dates.forEach(d => {
        const ds = d.toISOString().slice(0,10);
        const isToday = ds === todayStr;
        const dow = d.getDay();
        let cls = 'cal-date-header';
        if (isToday) cls += ' today';
        else if (dow === 0) cls += ' sunday';
        else if (dow === 6) cls += ' saturday';
        html += `<th class="${cls}">${d.getMonth()+1}/${d.getDate()}<br><small>${dayNames[dow]}</small></th>`;
    });
    html += '</tr></thead><tbody>';

    // 並び替え
    const sortKey = (document.getElementById('calendarSort') || {value:'number'}).value;
    const statusOrder = {'貸出中':0,'予約済':1,'車検中':2,'点検中':3,'修理中':4,'在庫':5,'':6};
    const sortedVehicles = [...filteredVehicles].sort((a, b) => {
        if (sortKey === 'status') {
            const sa = getVehicleStatusOnDate(a.id, todayStr).status;
            const sb = getVehicleStatusOnDate(b.id, todayStr).status;
            const diff = (statusOrder[sa] ?? 9) - (statusOrder[sb] ?? 9);
            return diff !== 0 ? diff : a.number.localeCompare(b.number);
        } else if (sortKey === 'type') {
            const diff = (a.car_type || '').localeCompare(b.car_type || '');
            return diff !== 0 ? diff : a.number.localeCompare(b.number);
        } else {
            return a.number.localeCompare(b.number);
        }
    });

    sortedVehicles.forEach(v => {
        html += `<tr>`;
        html += `<td class="cal-vehicle-col" onclick="openDetail(${v.id})" style="cursor:pointer;">
            <div class="cal-vehicle-num">${v.number}</div>
            <div class="cal-vehicle-type">${v.car_type}</div>
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
            <td style="padding:9px 12px;"><button class="btn btn-primary btn-sm" onclick="event.stopPropagation();openAddModal(${v.id})">＋登録</button></td>
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

// ====== 状態登録モーダル ======
function openAddModal(vehicleId) {
    editingEventId = null;
    document.getElementById('modalTitle').textContent = '車両状態を登録';
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
    document.getElementById('formCategory').value = '';
    document.getElementById('formNotes').value = '';
    onStatusChange();

    document.getElementById('addModal').classList.add('open');
}

function closeAddModal() {
    document.getElementById('addModal').classList.remove('open');
    document.getElementById('formVehicleNumber').readOnly = false;
}

function onVehicleNumberInput() {
    const num = document.getElementById('formVehicleNumber').value.trim();
    const v = vehicles.find(x => x.number === num);
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
    const v = vehicles.find(x => x.number === numInput);
    if (!v) { alert('車両番号が見つかりません'); return; }

    const payload = {
        vehicle_id: v.id,
        status: document.getElementById('formStatus').value,
        start_date: document.getElementById('formStartDate').value || null,
        end_date: document.getElementById('formEndDate').value || null,
        staff: document.getElementById('formStaff').value || null,
        client: document.getElementById('formClient').value.trim() || null,
        category: document.getElementById('formCategory').value || null,
        notes: document.getElementById('formNotes').value.trim() || null
    };

    await fetch(API + '/api/events', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    });

    events = await fetch(API + '/api/events').then(r => r.json());
    closeAddModal();
    renderCurrentPage();
    if (currentDetailVehicleId) renderDetail(currentDetailVehicleId);

    document.getElementById('lastUpdated').textContent = `最終更新: ${new Date().toLocaleString('ja-JP')}`;
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

init();
