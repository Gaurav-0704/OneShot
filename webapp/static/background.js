/* background.js — Animated particle constellation background
   Self-contained canvas 2D. No dependencies.
   Pauses on tab hidden. Respects prefers-reduced-motion. */

(function () {
  'use strict';

  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  const canvas = document.getElementById('bg-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');

  /* ── Config ─────────────────────────────────────────────── */
  const N        = 72;     // particle count
  const LINK_D   = 160;    // max link distance
  const SPD      = 0.35;   // base speed
  const C_PT     = '79,142,247';   // electric blue (R,G,B)
  const C_ACCENT = '157,122,255';  // purple accent — used for ~15% of particles

  let W, H, pts, raf, stopped = false;

  /* ── Particle factory ───────────────────────────────────── */
  function mkPt() {
    const purple = Math.random() < 0.15;
    const col    = purple ? C_ACCENT : C_PT;
    return {
      x: Math.random() * W, y: Math.random() * H,
      vx: (Math.random() - 0.5) * SPD,
      vy: (Math.random() - 0.5) * SPD,
      r: Math.random() * 1.6 + 0.4,
      a: Math.random() * 0.55 + 0.2,
      col,
    };
  }

  /* ── Init ───────────────────────────────────────────────── */
  function resize() {
    W = canvas.width  = window.innerWidth;
    H = canvas.height = window.innerHeight;
  }

  function init() {
    resize();
    pts = Array.from({ length: N }, mkPt);
  }

  /* ── Draw loop ──────────────────────────────────────────── */
  function frame() {
    ctx.clearRect(0, 0, W, H);

    for (let i = 0; i < N; i++) {
      const p = pts[i];

      /* Move + wrap */
      p.x += p.vx; p.y += p.vy;
      if (p.x < -12) p.x = W + 12;
      if (p.x > W + 12) p.x = -12;
      if (p.y < -12) p.y = H + 12;
      if (p.y > H + 12) p.y = -12;

      /* Draw dot */
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, 6.283);
      ctx.fillStyle = `rgba(${p.col},${p.a})`;
      ctx.fill();

      /* Draw links to nearby particles */
      for (let j = i + 1; j < N; j++) {
        const q  = pts[j];
        const dx = p.x - q.x, dy = p.y - q.y;
        const d2 = dx * dx + dy * dy;
        if (d2 < LINK_D * LINK_D) {
          const alpha = (1 - Math.sqrt(d2) / LINK_D) * 0.22;
          ctx.beginPath();
          ctx.moveTo(p.x, p.y);
          ctx.lineTo(q.x, q.y);
          ctx.strokeStyle = `rgba(${p.col},${alpha})`;
          ctx.lineWidth   = 0.5;
          ctx.stroke();
        }
      }
    }

    if (!stopped) raf = requestAnimationFrame(frame);
  }

  /* ── Lifecycle ──────────────────────────────────────────── */
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      stopped = true;
      if (raf) { cancelAnimationFrame(raf); raf = null; }
    } else {
      stopped = false;
      raf = requestAnimationFrame(frame);
    }
  });

  window.addEventListener('resize', () => {
    resize();
    pts.forEach(p => {
      if (p.x > W) p.x = Math.random() * W;
      if (p.y > H) p.y = Math.random() * H;
    });
  });

  init();
  raf = requestAnimationFrame(frame);
})();
