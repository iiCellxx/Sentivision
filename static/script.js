let isAnalyzing   = false;
let sentimentChart = null;
let emotionCounts  = {Happy:0, Surprise:0, Sad:0, Fear:0, Angry:0, Disgust:0};

let reportOverallChart     = null;
let reportTrendsChart      = null;
let reportTopEmotionsChart = null;
let selectedSessionId      = null;

// Polling controller – cancelled on stop to prevent stale callbacks
let _pollController = null;

// Session timer
let _sessionStartTime = null;
let _sessionTimerInterval = null;

// Connection status tracking
let _connectionState = 'disconnected';
let _connectionRetryCount = 0;

// Default RTSP URL shown only as placeholder (real value fetched from server)
const DEFAULT_RTSP_PLACEHOLDER = "rtsp://user:password@ip:port/profile0";

// ── Track-ID → 3-letter alias (mirrors app.py logic) ─────────────────────────
const _ID_LETTERS = 'ABCDEFGHJKLMNPQRSTUVWXYZ';   // 23 chars, no I/O/W
function trackIdToAlias(tid) {
    const base = _ID_LETTERS.length;
    const i    = Math.max(0, tid - 1);
    return _ID_LETTERS[Math.floor(i / (base * base)) % base]
         + _ID_LETTERS[Math.floor(i / base) % base]
         + _ID_LETTERS[i % base];
}

const emotionToSentiment = {
    Happy:'positive', Surprise:'positive',
    Sad:'negative', Fear:'negative', Angry:'negative', Disgust:'negative'
};
const emotionColors = {
    Angry:'#FF0000', Disgust:'#008000', Fear:'#800080',
    Happy:'#FFFF00', Sad:'#0000FF',    Surprise:'#FFA500'
};

// ── Sanitization helper ─────────────────────────────────────────────────────────
function sanitizeHTML(str) {
    if (str == null) return '';
    const div = document.createElement('div');
    div.textContent = String(str);
    return div.innerHTML;
}

// ── Chart init ────────────────────────────────────────────────────────────────
function initChart() {
    const ctx = document.getElementById('sentimentChart');
    if (!ctx) return;
    sentimentChart = new Chart(ctx.getContext('2d'), {
        type: 'doughnut',
        data: {
            labels: ['Positive', 'Negative'],
            datasets: [{
                data: [0, 0],
                backgroundColor: ['#2ecc71', '#e74c3c'],
                borderWidth: 3,
                borderColor: '#fff'
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: true,
            animation: { animateRotate: true, animateScale: true, duration: 600, easing: 'easeInOutQuart' },
            transitions: { active: { animation: { duration: 400, easing: 'easeOutQuart' } } },
            plugins: {
                legend: { position: 'bottom', labels: { padding: 20, font: { size: 14, weight: 'bold' } } },
                tooltip: {
                    callbacks: {
                        label: ctx => {
                            const v = ctx.parsed || 0;
                            const total = ctx.dataset.data.reduce((a,b)=>a+b, 0);
                            const pct = total ? (v/total*100).toFixed(1) : 0;
                            return `${ctx.label}: ${v} person${v!==1?'s':''} (${pct}%)`;
                        }
                    }
                }
            }
        }
    });
}

function updateSentimentAnalysis() {
    let pos = 0, neg = 0;
    for (const [e, c] of Object.entries(emotionCounts)) {
        if (emotionToSentiment[e] === 'positive') pos += c;
        else if (emotionToSentiment[e] === 'negative') neg += c;
    }
    if (sentimentChart) {
        sentimentChart.data.datasets[0].data = [pos, neg];
        sentimentChart.update('active');
    }
    const total = pos + neg;
    document.getElementById('positivePercent').textContent = total ? `${(pos/total*100).toFixed(1)}%` : '0%';
    document.getElementById('negativePercent').textContent = total ? `${(neg/total*100).toFixed(1)}%` : '0%';
}

// ── Analysis start / stop ─────────────────────────────────────────────────────
function startAnalysis() {
    // Show connecting state
    setConnectionStatus('connecting');
    
    fetch('/start')
        .then(r => r.json())
        .then(() => {
            isAnalyzing = true;
            _sessionStartTime = Date.now();
            startSessionTimer();
            document.getElementById('videoFeed').src = '/video?' + Date.now();
            document.getElementById('startBtn').disabled = true;
            document.getElementById('stopBtn').disabled  = false;
            updateStatus(true);
            Object.keys(emotionCounts).forEach(k => emotionCounts[k] = 0);
            if (sentimentChart) {
                sentimentChart.data.datasets[0].data = [0, 0];
                sentimentChart.update('none');
            }
            // After a short delay, check if video is loading
            setTimeout(() => {
                setConnectionStatus('connected');
            }, 2000);
            startPolling();
        })
        .catch(() => {
            setConnectionStatus('disconnected');
            showToast('Failed to start analysis', 'error');
        });
}

function stopAnalysis() {
    // Cancel any in-flight poll immediately
    if (_pollController) { _pollController.abort(); _pollController = null; }
    if (_sessionTimerInterval) { clearInterval(_sessionTimerInterval); _sessionTimerInterval = null; }
    isAnalyzing = false;
    _sessionStartTime = null;

    fetch('/stop')
        .then(r => r.json())
        .then(() => {
            document.getElementById('videoFeed').src = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='800' height='600'%3E%3Crect width='800' height='600' fill='%23000'/%3E%3Ctext x='50%25' y='50%25' fill='%23666' text-anchor='middle' dy='.3em' font-family='Arial' font-size='24'%3EAnalysis stopped%3C/text%3E%3C/svg%3E";
            document.getElementById('startBtn').disabled = false;
            document.getElementById('stopBtn').disabled  = true;
            updateStatus(false);
            setConnectionStatus('disconnected');
            document.getElementById('sessionTimer').classList.remove('active');
            document.getElementById('connectionStatus').classList.remove('visible');
            showToast('Analysis completed. Check Reports.', 'success');
        })
        .catch(() => showToast('Failed to stop analysis', 'error'));
}

function updateStatus(active) {
    const ind = document.getElementById('statusIndicator');
    const txt = document.getElementById('statusText');
    if (active) { ind.classList.add('active');    txt.textContent = 'Analyzing'; }
    else        { ind.classList.remove('active'); txt.textContent = 'Stopped';   }
}

// ── Session Timer ─────────────────────────────────────────────────────────────
function startSessionTimer() {
    const timerEl = document.getElementById('sessionTimer');
    const timerValue = document.getElementById('timerValue');
    timerEl.classList.add('active');
    
    _sessionTimerInterval = setInterval(() => {
        if (!_sessionStartTime) return;
        const elapsed = Math.floor((Date.now() - _sessionStartTime) / 1000);
        const h = Math.floor(elapsed / 3600).toString().padStart(2, '0');
        const m = Math.floor((elapsed % 3600) / 60).toString().padStart(2, '0');
        const s = (elapsed % 60).toString().padStart(2, '0');
        timerValue.textContent = `${h}:${m}:${s}`;
    }, 1000);
}

// ── Connection Status ─────────────────────────────────────────────────────────
function setConnectionStatus(state) {
    _connectionState = state;
    const el = document.getElementById('connectionStatus');
    const text = document.getElementById('connectionText');
    const dot = el.querySelector('.connection-dot');
    
    el.classList.remove('connected', 'disconnected', 'connecting');
    el.classList.add(state);
    el.classList.add('visible');
    
    const statusText = {
        'connected': 'Connected',
        'disconnected': 'Disconnected',
        'connecting': 'Connecting...'
    };
    text.textContent = statusText[state] || state;
    
    if (state === 'disconnected' && isAnalyzing) {
        _connectionRetryCount++;
        if (_connectionRetryCount < 3) {
            setTimeout(() => setConnectionStatus('connecting'), 2000);
        }
    } else if (state === 'connected') {
        _connectionRetryCount = 0;
    }
}

// ── Polling ───────────────────────────────────────────────────────────────────
function startPolling() {
    // Cancel any previous poll loop
    if (_pollController) { _pollController.abort(); }
    _pollController = new AbortController();
    const signal = _pollController.signal;

    const poll = () => {
        if (!isAnalyzing) return;
        fetch('/emotion_data', { signal })
            .then(r => r.json())
            .then(d => {
                if (d.emotions) {
                    emotionCounts = d.emotions;
                    updateSentimentAnalysis();
                }
            })
            .catch(err => {
                if (err.name === 'AbortError') return; // expected on stop
                console.warn('[poll]', err);
            })
            .finally(() => {
                if (isAnalyzing) setTimeout(poll, 500);
            });
    };
    setTimeout(poll, 500);
}

// ── Camera Settings ───────────────────────────────────────────────────────────
function loadCameraSettings() {
    fetch('/api/settings/camera')
        .then(r => r.json())
        .then(data => {
            document.getElementById('rtspUrl').value = data.rtsp_url || DEFAULT_RTSP_PLACEHOLDER;
        })
        .catch(() => {
            document.getElementById('rtspUrl').value = DEFAULT_RTSP_PLACEHOLDER;
        });
}

function saveCameraSettings() {
    const rtspInput = document.getElementById('rtspUrl');
    const rtspError = document.getElementById('rtspError');
    const rtspUrl = rtspInput.value.trim();
    
    // Clear previous errors
    rtspInput.classList.remove('error');
    rtspError.textContent = '';
    rtspError.style.display = 'none';
    
    if (!rtspUrl) {
        rtspInput.classList.add('error');
        rtspError.textContent = 'RTSP URL cannot be empty';
        rtspError.style.display = 'block';
        showSettingsStatus('RTSP URL cannot be empty', 'error');
        return;
    }
    if (!rtspUrl.startsWith('rtsp://')) {
        rtspInput.classList.add('error');
        rtspError.textContent = 'URL must start with rtsp://';
        rtspError.style.display = 'block';
        showSettingsStatus('Must start with rtsp://', 'error');
        return;
    }
    if (isAnalyzing) {
        showSettingsStatus('Stop analysis before changing settings', 'error');
        return;
    }

    showSettingsStatus('Saving…', 'info');
    fetch('/api/settings/camera', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rtsp_url: rtspUrl })
    })
    .then(r => r.json())
    .then(data => {
        if (data.success) { 
            showSettingsStatus('✓ Settings saved!', 'success'); 
            showToast('Camera settings updated', 'success'); 
        }
        else {
            rtspInput.classList.add('error');
            rtspError.textContent = data.error || 'Failed to save';
            rtspError.style.display = 'block';
            showSettingsStatus('✗ ' + (data.error || 'Failed'), 'error');
        }
    })
    .catch(() => {
        showSettingsStatus('✗ Network error. Try again.', 'error');
    });
}

function resetCameraSettings() {
    if (isAnalyzing) { showSettingsStatus('Stop analysis before resetting', 'error'); return; }
    if (confirm('Reset camera URL to server default?')) {
        fetch('/api/settings/camera')
            .then(r => r.json())
            .then(d => { document.getElementById('rtspUrl').value = d.rtsp_url || DEFAULT_RTSP_PLACEHOLDER; })
            .catch(() => {});
    }
}

function showSettingsStatus(message, type) {
    const el = document.getElementById('settingsStatus');
    el.textContent = message;
    el.className   = 'settings-status ' + type;
    el.style.display = 'block';
    if (type === 'success') setTimeout(() => { el.style.display = 'none'; }, 3000);
}

// ── Modal openers (fixed: openSettingsModal now loads current URL) ────────────
function openSettingsModal() {
    openModal('settingsModal');
    loadCameraSettings();   // ← this was missing before
}

function openReportsModal() {
    openModal('reportsModal');
    loadSessions();
}

// ── Sessions list ─────────────────────────────────────────────────────────────
function loadSessions() {
    const list = document.getElementById('sessionsList');
    list.innerHTML = '<div class="loading">Loading sessions…</div>';
    fetch('/api/sessions')
        .then(r => r.json())
        .then(d => {
            if (d.success && d.sessions.length) displaySessions(d.sessions);
            else list.innerHTML = '<div class="empty-state">No sessions found.</div>';
        })
        .catch(() => list.innerHTML = '<div class="error-state">Failed to load sessions.</div>');
}

function displaySessions(sessions) {
    const list = document.getElementById('sessionsList');
    list.innerHTML = '';
    sessions.forEach(s => {
        const card  = document.createElement('div');
        card.className = 'session-card';
        card.dataset.sessionId = s.id;
        if (selectedSessionId === s.id) card.classList.add('active');

        const start = new Date(s.start_time);
        const end   = s.end_time ? new Date(s.end_time) : null;
        const dur   = end ? Math.round((end - start) / 60000) : 0;
        const badge = s.status === 'active'
            ? '<span class="badge badge-active">Active</span>'
            : '<span class="badge badge-completed">Completed</span>';
        const domIcon = getEmotionIcon(s.dominant_emotion || '');

        card.innerHTML = `
            <div class="session-header">
                <div class="session-title"><strong>Session #${sanitizeHTML(String(s.id))}</strong>${badge}</div>
                <button class="btn-delete" onclick="deleteSessionPrompt(${s.id},event)" title="Delete">🗑️</button>
            </div>
            <div class="session-info">
                <div class="info-row"><span class="info-label">Started:</span><span>${formatDateTime(start)}</span></div>
                ${end ? `<div class="info-row"><span class="info-label">Duration:</span><span>${dur} min</span></div>` : ''}
                <div class="info-row"><span class="info-label">Frames:</span><span>${s.total_frames || 0}</span></div>
                ${s.dominant_emotion ? `<div class="info-row"><span class="info-label">Dominant:</span><span>${domIcon} ${sanitizeHTML(s.dominant_emotion)}</span></div>` : ''}
            </div>`;
        card.onclick = e => { if (!e.target.closest('.btn-delete')) selectSession(s.id); };
        list.appendChild(card);
    });
}

function selectSession(id) {
    selectedSessionId = id;
    document.querySelectorAll('.session-card').forEach(c =>
        c.classList.toggle('active', parseInt(c.dataset.sessionId) === id)
    );
    document.getElementById('exportBtn').disabled = false;
    loadSessionReport(id);
}

function loadSessionReport(id) {
    ['overallContent','realtimeContent','trendsContent','topContent','perPersonContent']
        .forEach(el => { document.getElementById(el).innerHTML = '<div class="loading">Loading…</div>'; });

    fetch(`/api/session/${id}`)
        .then(r => r.json())
        .then(data => {
            displayOverallReport(data);
            displayRealtimeData(data.realtime_data || []);
            displayTrendsReport(data.minute_summaries || []);
            displayTopEmotions(data.top_emotions || []);
        })
        .catch(() => {
            ['overallContent','realtimeContent','trendsContent','topContent']
                .forEach(el => { document.getElementById(el).innerHTML = '<div class="error-state">Failed to load report.</div>'; });
        });

    fetch(`/api/session/${id}/per-person`)
        .then(r => r.json())
        .then(data => displayPerPersonReport(data.per_person || []))
        .catch(() => {
            document.getElementById('perPersonContent').innerHTML =
                '<div class="error-state">Failed to load per-person data.</div>';
        });
}

// ── Report panels ─────────────────────────────────────────────────────────────
function displayOverallReport(data) {
    const c     = document.getElementById('overallContent');
    const stats = data.overall_stats;
    const sess  = data.session;
    if (!stats) { c.innerHTML = '<div class="empty-state">No stats yet.</div>'; return; }

    const start = new Date(sess.start_time);
    const end   = sess.end_time ? new Date(sess.end_time) : new Date();
    const dur   = Math.round((end - start) / 60000);

    c.innerHTML = `
        <div class="report-header">
            <h3>📊 Overall Statistics</h3>
            <div class="report-meta">Session #${sess.id} • ${formatDateTime(start)} • ${dur} min</div>
        </div>
        <div class="stats-grid">
            <div class="stat-box"><div class="stat-icon">${getEmotionIcon(stats.dominant_emotion)}</div>
                <div class="stat-info"><div class="stat-value">${stats.dominant_emotion}</div><div class="stat-label">Dominant</div></div></div>
            <div class="stat-box"><div class="stat-icon">🎬</div>
                <div class="stat-info"><div class="stat-value">${sess.total_frames}</div><div class="stat-label">Frames</div></div></div>
        </div>
        <div class="chart-section"><h4>Sentiment Distribution</h4><canvas id="reportOverallChart"></canvas></div>
        <div class="sentiment-breakdown">
            <div class="sentiment-item positive">
                <div class="sentiment-label">😊 Positive</div>
                <div class="sentiment-bar"><div class="sentiment-fill" style="width:${stats.positive_percentage}%"></div></div>
                <div class="sentiment-value">${stats.positive_percentage.toFixed(1)}%</div>
            </div>
            <div class="sentiment-item negative">
                <div class="sentiment-label">😢 Negative</div>
                <div class="sentiment-bar"><div class="sentiment-fill" style="width:${stats.negative_percentage}%"></div></div>
                <div class="sentiment-value">${stats.negative_percentage.toFixed(1)}%</div>
            </div>
        </div>`;

    setTimeout(() => {
        const ctx = document.getElementById('reportOverallChart');
        if (!ctx) return;
        if (reportOverallChart) { reportOverallChart.destroy(); reportOverallChart = null; }
        reportOverallChart = new Chart(ctx.getContext('2d'), {
            type: 'doughnut',
            data: {
                labels: ['Positive', 'Negative'],
                datasets: [{ data: [stats.positive_percentage, stats.negative_percentage],
                             backgroundColor: ['#2ecc71','#e74c3c'], borderWidth: 3, borderColor: '#fff' }]
            },
            options: {
                responsive: true,
                animation: { animateRotate: true, duration: 1200, easing: 'easeOutQuart' },
                plugins: { legend: { position: 'bottom' } }
            }
        });
    }, 100);
}

function displayRealtimeData(data) {
    const c = document.getElementById('realtimeContent');
    if (!data.length) { c.innerHTML = '<div class="empty-state">No recent frame data.</div>'; return; }
    const recent = data.slice(0, 10);
    // Label correctly: "Last 60 seconds of frame data (up to 10 rows)"
    c.innerHTML = `
        <div class="report-header">
            <h3>⚡ Recent Frame Data</h3>
            <p class="report-desc">Last 60 seconds of recorded frames (up to 10 shown)</p>
        </div>
        <div class="realtime-table">
            <table>
                <thead><tr><th>Time</th><th>Happy</th><th>Surprise</th><th>Sad</th><th>Fear</th><th>Angry</th><th>Disgust</th><th>Total</th></tr></thead>
                <tbody>
                    ${recent.map(f => `<tr>
                        <td>${formatTime(new Date(f.timestamp))}</td>
                        <td>${f.happy}</td><td>${f.surprise}</td><td>${f.sad}</td>
                        <td>${f.fear}</td><td>${f.angry}</td><td>${f.disgust}</td>
                        <td><strong>${f.total_persons}</strong></td>
                    </tr>`).join('')}
                </tbody>
            </table>
        </div>`;
}

function displayTrendsReport(data) {
    const c = document.getElementById('trendsContent');
    if (!data.length) { c.innerHTML = '<div class="empty-state">No trend data.</div>'; return; }
    const avgPersons = (data.reduce((s, m) => s + m.avg_total_persons, 0) / data.length).toFixed(1);
    c.innerHTML = `
        <div class="report-header"><h3>📈 Per-Minute Trends</h3><p class="report-desc">Averages per minute</p></div>
        <div class="chart-section"><canvas id="reportTrendsChart"></canvas></div>
        <div class="trends-summary"><h4>Summary</h4>
            <p>Minutes recorded: <strong>${data.length}</strong></p>
            <p>Avg persons/min: <strong>${avgPersons}</strong></p>
        </div>`;

    setTimeout(() => {
        const ctx = document.getElementById('reportTrendsChart');
        if (!ctx) return;
        if (reportTrendsChart) { reportTrendsChart.destroy(); reportTrendsChart = null; }
        reportTrendsChart = new Chart(ctx.getContext('2d'), {
            type: 'line',
            data: {
                labels: data.map(m => formatTime(new Date(m.minute_mark))),
                datasets: [
                    { label:'Happy',    data: data.map(m => m.avg_happy),    borderColor: emotionColors.Happy,    backgroundColor: emotionColors.Happy    + '40', tension: 0.4 },
                    { label:'Surprise', data: data.map(m => m.avg_surprise), borderColor: emotionColors.Surprise, backgroundColor: emotionColors.Surprise + '40', tension: 0.4 },
                    { label:'Sad',      data: data.map(m => m.avg_sad),      borderColor: emotionColors.Sad,      backgroundColor: emotionColors.Sad      + '40', tension: 0.4 },
                    { label:'Angry',    data: data.map(m => m.avg_angry),    borderColor: emotionColors.Angry,    backgroundColor: emotionColors.Angry    + '40', tension: 0.4 },
                ]
            },
            options: {
                responsive: true,
                plugins: { legend: { position: 'bottom' } },
                scales: { y: { beginAtZero: true, title: { display: true, text: 'Avg Count' } } }
            }
        });
    }, 100);
}

function displayTopEmotions(data) {
    const c = document.getElementById('topContent');
    if (!data.length) { c.innerHTML = '<div class="empty-state">No data.</div>'; return; }
    const max = data[0].count;
    c.innerHTML = `
        <div class="report-header"><h3>🏆 Top Emotions</h3><p class="report-desc">By detection count</p></div>
        <div class="top-emotions-list">
            ${data.map((e, i) => {
                const pct  = (e.count / max * 100);
                const icon  = getEmotionIcon(e.emotion);
                const medal = i === 0 ? '🥇' : i === 1 ? '🥈' : i === 2 ? '🥉' : '';
                return `<div class="top-emotion-item">
                    <div class="emotion-rank">${medal} #${i+1}</div>
                    <div class="emotion-details">
                        <div class="emotion-name">${icon} ${e.emotion}</div>
                        <div class="emotion-bar"><div class="emotion-fill" style="width:${pct}%;background:${emotionColors[e.emotion]}"></div></div>
                        <div class="emotion-stats"><span class="emotion-count">${e.count} detections</span></div>
                    </div>
                </div>`;
            }).join('')}
        </div>
        <div class="chart-section"><h4>Distribution</h4><canvas id="reportTopEmotionsChart"></canvas></div>`;

    setTimeout(() => {
        const ctx = document.getElementById('reportTopEmotionsChart');
        if (!ctx) return;
        if (reportTopEmotionsChart) { reportTopEmotionsChart.destroy(); reportTopEmotionsChart = null; }
        reportTopEmotionsChart = new Chart(ctx.getContext('2d'), {
            type: 'bar',
            data: {
                labels: data.map(e => e.emotion),
                datasets: [{ label: 'Detections', data: data.map(e => e.count),
                             backgroundColor: data.map(e => emotionColors[e.emotion]),
                             borderWidth: 2, borderColor: '#fff' }]
            },
            options: {
                responsive: true,
                plugins: { legend: { display: false } },
                scales: { y: { beginAtZero: true, title: { display: true, text: 'Count' } } }
            }
        });
    }, 100);
}

function buildEmotionBarSVG(emotionCounts, totalDetections, dominantEmotion) {
    const allEmotions = ['Happy','Surprise','Sad','Fear','Angry','Disgust'];
    const W = 240, H = 72, barW = 28, gap = 8, padL = 6, padB = 20;
    const chartH = H - padB;
    const emotionColorMap = { Happy:'#f59e0b', Surprise:'#f97316', Sad:'#3b82f6', Fear:'#8b5cf6', Angry:'#ef4444', Disgust:'#10b981' };
    const emotionShortLabel = { Happy:'HAP', Surprise:'SUR', Sad:'SAD', Fear:'FEA', Angry:'ANG', Disgust:'DIS' };
    const maxCount = Math.max(...allEmotions.map(e => emotionCounts[e] || 0), 1);

    const bars = allEmotions.map((e, i) => {
        const count = emotionCounts[e] || 0;
        const x     = padL + i * (barW + gap);
        const barH  = count === 0 ? 2 : Math.max(4, Math.round((count / maxCount) * chartH));
        const y     = chartH - barH;
        const color = emotionColorMap[e];
        const isDom = e === dominantEmotion;
        const pct   = totalDetections ? ((count / totalDetections) * 100).toFixed(1) : '0.0';
        const rx = 4;
        const path = `M${x},${y+barH} L${x},${y+rx} Q${x},${y} ${x+rx},${y} L${x+barW-rx},${y} Q${x+barW},${y} ${x+barW},${y+rx} L${x+barW},${y+barH} Z`;
        return `
          <g class="pp-bar-group" data-emotion="${e}" data-count="${count}" data-pct="${pct}%">
            ${isDom ? `<rect x="${x-2}" y="${y-4}" width="${barW+4}" height="${barH+4}" rx="6" fill="${color}" opacity="0.15"/>` : ''}
            <path d="${path}" fill="${color}" opacity="${count===0?'0.18':'0.92'}" class="pp-bar-rect">
              <animate attributeName="opacity" from="0" to="${count===0?'0.18':'0.92'}" dur="0.5s" fill="freeze" begin="${i*0.07}s"/>
            </path>
            ${count > 0 ? `<text x="${x+barW/2}" y="${y-3}" text-anchor="middle" font-size="8" font-weight="700" fill="${color}" opacity="0.9">${count}</text>` : ''}
            <text x="${x+barW/2}" y="${H-4}" text-anchor="middle" font-size="7.5" font-weight="600" fill="#94a3b8">${emotionShortLabel[e]}</text>
            ${isDom ? `<circle cx="${x+barW/2}" cy="${y-12}" r="3" fill="${color}"/>` : ''}
          </g>`;
    }).join('');

    const gridLines = [0.25,0.5,0.75,1].map(f => {
        const gy = Math.round(chartH - f * chartH);
        return `<line x1="${padL}" y1="${gy}" x2="${W-6}" y2="${gy}" stroke="#e2e8f0" stroke-width="1" stroke-dasharray="3,3"/>`;
    }).join('');

    return `<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg" class="pp-emotion-svg">
      ${gridLines}${bars}
    </svg>`;
}

function displayPerPersonReport(data) {
    const c = document.getElementById('perPersonContent');
    if (!data.length) { c.innerHTML = '<div class="empty-state">No per-person data for this session.</div>'; return; }

    const allEmotions   = ['Happy','Surprise','Sad','Fear','Angry','Disgust'];
    const emotionColorMap = { Happy:'#f59e0b', Surprise:'#f97316', Sad:'#3b82f6', Fear:'#8b5cf6', Angry:'#ef4444', Disgust:'#10b981' };

    const rows = data.map((p, idx) => {
        const dominantIcon = getEmotionIcon(p.dominant_emotion);
        const svgChart     = buildEmotionBarSVG(p.emotion_counts, p.total_detections, p.dominant_emotion);
        const alias = trackIdToAlias(p.person_number);
        return `<tr class="pp-row" style="animation-delay:${idx*0.06}s">
            <td class="pp-person-num">
                <div class="pp-avatar" style="font-size:0.65em;letter-spacing:-0.5px">${alias}</div>
                <span class="pp-cust-label">Customer</span>
            </td>
            <td><div style="font-weight:700;color:#1e293b;font-size:.85em">${p.duration}</div></td>
            <td><div style="color:#64748b;font-size:.82em">${formatTime(new Date(p.first_seen))}</div></td>
            <td><div style="color:#64748b;font-size:.82em">${formatTime(new Date(p.last_seen))}</div></td>
            <td class="pp-chart-cell">
                <div class="pp-chart-wrap">${svgChart}<div class="pp-chart-tooltip" id="pp-tip-${idx}"></div></div>
            </td>
            <td>
                <div class="pp-dominant-pill" style="--dom-color:${emotionColorMap[p.dominant_emotion]||'#27ae60'}">
                    <span class="pp-dom-icon">${dominantIcon}</span>
                    <span class="pp-dom-label">${p.dominant_emotion}</span>
                </div>
            </td>
            <td>
                <div class="pp-sentiment-cell">
                    <div class="pp-sent-ring pp-sent-pos">
                        <svg viewBox="0 0 36 36" class="pp-donut">
                            <circle cx="18" cy="18" r="15" fill="none" stroke="#e2e8f0" stroke-width="3.5"/>
                            <circle cx="18" cy="18" r="15" fill="none" stroke="#22c55e" stroke-width="3.5"
                                stroke-dasharray="${(p.positive_percentage/100)*94.2} 94.2"
                                stroke-linecap="round" transform="rotate(-90 18 18)" class="pp-donut-arc"/>
                        </svg>
                        <span class="pp-donut-val">${p.positive_percentage}%</span>
                    </div>
                </div>
            </td>
            <td>
                <div class="pp-sentiment-cell">
                    <div class="pp-sent-ring pp-sent-neg">
                        <svg viewBox="0 0 36 36" class="pp-donut">
                            <circle cx="18" cy="18" r="15" fill="none" stroke="#e2e8f0" stroke-width="3.5"/>
                            <circle cx="18" cy="18" r="15" fill="none" stroke="#ef4444" stroke-width="3.5"
                                stroke-dasharray="${(p.negative_percentage/100)*94.2} 94.2"
                                stroke-linecap="round" transform="rotate(-90 18 18)" class="pp-donut-arc"/>
                        </svg>
                        <span class="pp-donut-val">${p.negative_percentage}%</span>
                    </div>
                </div>
            </td>
        </tr>`;
    }).join('');

    c.innerHTML = `
        <div class="pp-section-header">
            <div>
                <h3 class="pp-title">Per Person Sentiment</h3>
                <p class="pp-subtitle">${data.length} customer${data.length!==1?'s':''} detected</p>
            </div>
            <div class="pp-global-legend">
                ${allEmotions.map(e => `
                    <span class="pp-leg-item">
                        <span class="pp-leg-swatch" style="background:${emotionColorMap[e]}"></span>
                        <span class="pp-leg-name">${e}</span>
                    </span>`).join('')}
            </div>
        </div>
        <div class="pp-table-wrap">
            <table class="pp-table">
                <thead><tr>
                    <th class="pp-th-customer">Customer</th>
                    <th>Stay Duration</th><th>Arrival</th><th>Departure</th>
                    <th class="pp-th-graph">Emotion Breakdown</th>
                    <th>Dominant</th><th>Positive</th><th>Negative</th>
                </tr></thead>
                <tbody>${rows}</tbody>
            </table>
        </div>`;

    c.querySelectorAll('.pp-bar-group').forEach(g => {
        g.addEventListener('mouseenter', () => {
            const tip = g.closest('tr')?.querySelector('.pp-chart-tooltip');
            if (tip) { tip.textContent = `${g.dataset.emotion}: ${g.dataset.count} (${g.dataset.pct})`; tip.style.opacity = '1'; }
        });
        g.addEventListener('mouseleave', () => {
            const tip = g.closest('tr')?.querySelector('.pp-chart-tooltip');
            if (tip) tip.style.opacity = '0';
        });
    });
}

// ── Tab switching ─────────────────────────────────────────────────────────────
function switchReportTab(tab) {
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
    document.querySelectorAll('.tab-content').forEach(ct => ct.classList.toggle('active', ct.id === `${tab}-tab`));
}

// ── Delete session ────────────────────────────────────────────────────────────
function deleteSessionPrompt(id, e) {
    e.stopPropagation();
    if (confirm('Delete this session and all its data?')) deleteSession(id);
}

function deleteSession(id) {
    fetch(`/api/session/${id}/delete`, { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            if (d.success) {
                showToast('Session deleted', 'success');
                if (selectedSessionId === id) {
                    selectedSessionId = null;
                    document.getElementById('exportBtn').disabled = true;
                }
                loadSessions();
            } else showToast('Delete failed', 'error');
        })
        .catch(() => showToast('Delete failed', 'error'));
}

// ── PDF export ────────────────────────────────────────────────────────────────
function exportReport() {
    if (!selectedSessionId) { showToast('Select a session first', 'error'); return; }
    const btn = document.getElementById('exportBtn');
    const txt = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = '⏳ Generating…';
    fetch(`/api/session/${selectedSessionId}/export-pdf`)
        .then(r => { if (!r.ok) throw new Error('Server error'); return r.blob(); })
        .then(b => {
            const u = URL.createObjectURL(b);
            const a = document.createElement('a');
            a.href = u;
            a.download = `sentivision_report_${selectedSessionId}.pdf`;
            a.click();
            URL.revokeObjectURL(u);
            showToast('PDF downloaded!', 'success');
        })
        .catch(() => showToast('PDF generation failed', 'error'))
        .finally(() => { btn.disabled = false; btn.innerHTML = txt; });
}

// ── Utilities ─────────────────────────────────────────────────────────────────
function formatDateTime(d) {
    return d.toLocaleString('en-US', { month:'short', day:'numeric', year:'numeric', hour:'2-digit', minute:'2-digit' });
}
function formatTime(d) {
    return d.toLocaleTimeString('en-US', { hour:'2-digit', minute:'2-digit', second:'2-digit' });
}
function getEmotionIcon(e) {
    return { Happy:'😊', Sad:'😢', Angry:'😠', Fear:'😨', Surprise:'😲', Disgust:'🤢', Neutral:'😐' }[e] || '😐';
}
function showToast(msg, type = 'info') {
    const t = document.createElement('div');
    t.className = `toast toast-${type}`;
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.classList.add('show'), 100);
    setTimeout(() => { t.classList.remove('show'); setTimeout(() => t.remove(), 300); }, 3000);
}
function openModal(id)  { document.getElementById(id)?.classList.add('active'); }
function closeModal(id) { document.getElementById(id)?.classList.remove('active'); }

// ── Init ──────────────────────────────────────────────────────────────────────
window.addEventListener('click', e => {
    if (e.target.classList.contains('modal')) e.target.classList.remove('active');
});

window.addEventListener('load', () => {
    initChart();
    fetch('/status')
        .then(r => r.json())
        .then(d => {
            if (d.capturing) {
                isAnalyzing = true;
                document.getElementById('videoFeed').src = '/video?' + Date.now();
                document.getElementById('startBtn').disabled = true;
                document.getElementById('stopBtn').disabled  = false;
                updateStatus(true);
                startPolling();
            }
        });
});