/* charts.js — Chart.js wrappers for OneShot dashboard widgets.
   Requires Chart.js 4 (loaded via CDN in index.html).
   All charts use the dark glass palette. */

(function (G) {
  'use strict';

  /* ── Dark theme defaults ─────────────────────────────────── */
  const ACCENT   = '#4f8ef7';
  const PURPLE   = '#9d7aff';
  const GREEN    = '#22d3a0';
  const AMBER    = '#f5a623';
  const RED      = '#ff5c5c';
  const GRID     = 'rgba(255,255,255,0.06)';
  const TEXT_MUT = 'rgba(230,234,244,0.5)';

  /* Registry so we can destroy before re-rendering */
  const _charts = {};

  function destroy(id) {
    if (_charts[id]) { _charts[id].destroy(); delete _charts[id]; }
  }

  function base(canvas) {
    if (!canvas) return null;
    const ctx = canvas.getContext('2d');
    return ctx;
  }

  const defScales = {
    x: { grid: { color: GRID }, ticks: { color: TEXT_MUT, font: { size: 11 } } },
    y: { grid: { color: GRID }, ticks: { color: TEXT_MUT, font: { size: 11 } } },
  };

  /* ── Applied-per-day line chart ─────────────────────────── */
  G.renderAppliedChart = function (canvasId, rows) {
    destroy(canvasId);
    const el = document.getElementById(canvasId);
    if (!el || typeof Chart === 'undefined') return;

    /* bucket applied_at into last 7 days */
    const days = 7;
    const labels = [], counts = [];
    for (let i = days - 1; i >= 0; i--) {
      const d = new Date(); d.setDate(d.getDate() - i);
      labels.push(d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' }));
      const key = d.toISOString().slice(0, 10);
      counts.push(rows.filter(r => (r.applied_at || '').startsWith(key)).length);
    }

    _charts[canvasId] = new Chart(el, {
      type: 'line',
      data: {
        labels,
        datasets: [{
          label: 'Applied',
          data: counts,
          borderColor: ACCENT,
          backgroundColor: 'rgba(79,142,247,0.12)',
          pointBackgroundColor: ACCENT,
          pointRadius: 4,
          tension: 0.35,
          fill: true,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: defScales,
      },
    });
  };

  /* ── ATS score distribution bar chart ───────────────────── */
  G.renderATSChart = function (canvasId, rows) {
    destroy(canvasId);
    const el = document.getElementById(canvasId);
    if (!el || typeof Chart === 'undefined') return;

    const buckets = ['0–20', '20–40', '40–60', '60–80', '80–100'];
    const counts  = [0, 0, 0, 0, 0];
    rows.forEach(r => {
      const s = Number(r.ats_score);
      if (!isNaN(s)) counts[Math.min(4, Math.floor(s / 20))]++;
    });

    _charts[canvasId] = new Chart(el, {
      type: 'bar',
      data: {
        labels: buckets,
        datasets: [{
          label: 'Jobs',
          data: counts,
          backgroundColor: [RED, AMBER, AMBER, GREEN, GREEN],
          borderRadius: 6,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: defScales,
      },
    });
  };

  /* ── Fit score distribution bar chart ───────────────────── */
  G.renderFitChart = function (canvasId, rows) {
    destroy(canvasId);
    const el = document.getElementById(canvasId);
    if (!el || typeof Chart === 'undefined') return;

    const labels = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10'];
    const counts = Array(10).fill(0);
    rows.forEach(r => {
      const s = Math.round(Number(r.fit_score));
      if (s >= 1 && s <= 10) counts[s - 1]++;
    });

    _charts[canvasId] = new Chart(el, {
      type: 'bar',
      data: {
        labels,
        datasets: [{
          label: 'Jobs',
          data: counts,
          backgroundColor: labels.map((_, i) =>
            i < 4 ? 'rgba(255,92,92,0.65)' :
            i < 6 ? 'rgba(245,166,35,0.65)' :
            'rgba(34,211,160,0.65)'
          ),
          borderRadius: 5,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: defScales,
      },
    });
  };

  /* ── Source doughnut chart ─────────────────────────────── */
  G.renderSourceChart = function (canvasId, rows) {
    destroy(canvasId);
    const el = document.getElementById(canvasId);
    if (!el || typeof Chart === 'undefined') return;

    const tally = {};
    rows.forEach(r => {
      const s = r.site || r.applier || 'other';
      tally[s] = (tally[s] || 0) + 1;
    });
    const labels = Object.keys(tally);
    const data   = Object.values(tally);
    const colors = [ACCENT, PURPLE, GREEN, AMBER, RED, '#60a5fa', '#a78bfa'];

    _charts[canvasId] = new Chart(el, {
      type: 'doughnut',
      data: {
        labels,
        datasets: [{
          data,
          backgroundColor: colors.slice(0, labels.length),
          borderWidth: 0,
          hoverOffset: 6,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: {
            position: 'right',
            labels: { color: TEXT_MUT, font: { size: 11 }, padding: 14, boxWidth: 12 },
          },
        },
        cutout: '68%',
      },
    });
  };

  /* ── Destroy all (call before full page navigation) ─────── */
  G.destroyAllCharts = function () {
    Object.keys(_charts).forEach(destroy);
  };

})(window);
