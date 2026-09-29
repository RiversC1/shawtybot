// Live weather for the battle scene: a canvas particle layer drawn over the
// arena (under the HP labels and panels). Rain falls in depth layers and
// splashes on the floor with the odd lightning flash; harsh sunlight sweeps
// light rays and drifting motes; a sandstorm gusts grains and dust clouds
// across the field; hail pelts down and bounces or shatters on the floor.
//
// Honors the battle page's "Motion effects" switch (BattleFX.kit), not the
// OS reduce-motion setting: with motion off it paints one still frame.
window.BattleWeather = (function () {
  const FLOOR = 0.44; // where the arena floor starts, as a fraction of height
  const rand = (a, b) => a + Math.random() * (b - a);

  function motionOff() {
    try {
      return !!(window.BattleFX && window.BattleFX.kit && window.BattleFX.kit.reduceMotion);
    } catch (e) {
      return false;
    }
  }

  function attach(scene) {
    const canvas = document.createElement("canvas");
    canvas.className = "battle-weather-canvas";
    canvas.setAttribute("aria-hidden", "true");
    scene.appendChild(canvas);
    const ctx = canvas.getContext("2d");

    let W = 0, H = 0, dpr = 1;
    let target = null; // weather the scene should show
    let current = null; // weather currently drawn (fades out before switching)
    let intensity = 0;
    let parts = [];
    let fx = []; // short-lived splashes, bounces, shards
    let t = 0;
    let flash = 0;
    let nextFlash = rand(5, 10);
    let raf = 0;
    let last = 0;
    let stillTimer = 0;
    let stillDirty = true; // the still frame (motion off) needs repainting

    function resize() {
      const r = scene.getBoundingClientRect();
      dpr = Math.min(window.devicePixelRatio || 1, 1.5);
      W = Math.max(1, r.width);
      H = Math.max(1, r.height);
      canvas.width = Math.round(W * dpr);
      canvas.height = Math.round(H * dpr);
      if (current) seed(current);
      stillDirty = true;
      if (current && motionOff()) paintStill();
    }

    // Motion off: one representative frame, repainted only when the weather
    // or the scene size changes.
    function paintStill() {
      if (current !== target) {
        current = target;
        if (current) seed(current);
        stillDirty = true;
      }
      if (!stillDirty) return;
      stillDirty = false;
      intensity = current ? 1 : 0;
      if (current) {
        const keep = t;
        step(0.3);
        t = keep;
      }
      draw();
    }

    // Particle counts scale with scene area so phones aren't overcrowded.
    const density = () => Math.max(0.35, (W * H) / (1000 * 480));
    const groundY = () => H * FLOOR + Math.random() * H * (1 - FLOOR) * 0.96;

    function seed(kind) {
      parts = [];
      fx = [];
      const n = (base) => Math.round(base * density());
      if (kind === "rain") {
        for (let i = 0; i < n(230); i++) parts.push(newDrop(true));
      } else if (kind === "sun") {
        for (let i = 0; i < 7; i++) {
          parts.push({ ray: true, a: 1.72 + i * 0.2 + rand(-0.05, 0.05), w: rand(0.05, 0.11), ph: rand(0, 6.28), sp: rand(0.25, 0.6) });
        }
        for (let i = 0; i < n(46); i++) parts.push(newMote(true));
      } else if (kind === "sand") {
        for (let i = 0; i < 7; i++) {
          parts.push({ cloud: true, x: rand(0, W), y: rand(H * 0.1, H), rx: rand(120, 260), ry: rand(40, 90), sp: rand(50, 110), a: rand(0.1, 0.2) });
        }
        for (let i = 0; i < n(300); i++) parts.push(newGrain(true));
      } else if (kind === "hail") {
        for (let i = 0; i < n(70); i++) parts.push(newStone(true));
        for (let i = 0; i < n(60); i++) parts.push(newFlake(true));
      }
    }

    function newDrop(anywhere) {
      const z = rand(0.25, 1);
      return { z, x: rand(-40, W + 160), y: anywhere ? rand(-H, H) : rand(-120, -10), gy: groundY(), sp: 820 + 700 * z, len: 10 + 24 * z };
    }
    function newMote(anywhere) {
      return { mote: true, x: rand(0, W), y: anywhere ? rand(0, H) : H + 10, r: rand(0.8, 2.6), sp: rand(8, 26), ph: rand(0, 6.28), dx: rand(-10, 6) };
    }
    function newGrain(anywhere) {
      const z = rand(0.2, 1);
      return { z, x: anywhere ? rand(0, W) : W + rand(0, 80), y: rand(-10, H + 10), ph: rand(0, 6.28), sp: 260 + 560 * z };
    }
    function newStone(anywhere) {
      const z = rand(0.35, 1);
      return { stone: true, z, x: rand(0, W + 120), y: anywhere ? rand(-H, H) : rand(-80, -10), gy: groundY(), sp: 480 + 420 * z, r: 1.6 + 3.2 * z };
    }
    function newFlake(anywhere) {
      return { flake: true, x: rand(0, W), y: anywhere ? rand(0, H) : -5, r: rand(0.8, 2), sp: rand(50, 110), ph: rand(0, 6.28) };
    }

    // ---------- Simulation ----------

    function gust() {
      return 0.7 + 0.35 * Math.sin(t * 0.8) + 0.2 * Math.sin(t * 2.3 + 1);
    }

    function step(dt) {
      t += dt;
      if (current === "rain") {
        const vx = -210;
        for (const p of parts) {
          p.y += p.sp * dt;
          p.x += vx * dt * (p.sp / 1300);
          if (p.y >= p.gy && p.z > 0.5 && p.y - p.sp * dt < p.gy) {
            fx.push({ ripple: true, x: p.x, y: p.gy, age: 0, life: 0.4, s: p.z });
            if (Math.random() < 0.5) {
              for (let k = 0; k < 2; k++) fx.push({ drop: true, x: p.x, y: p.gy, vx: rand(-60, 60), vy: rand(-140, -70), age: 0, life: 0.35 });
            }
          }
          if (p.y > p.gy + (p.z > 0.5 ? 0 : 40) || p.x < -60) Object.assign(p, newDrop(false));
        }
        nextFlash -= dt;
        if (nextFlash <= 0) {
          flash = 1;
          nextFlash = rand(7, 15);
          setTimeout(() => { flash = Math.max(flash, 0.7); }, 160);
        }
        flash = Math.max(0, flash - dt * 3.2);
      } else if (current === "sun") {
        for (const p of parts) {
          if (!p.mote) continue;
          p.y -= p.sp * dt;
          p.x += (p.dx + Math.sin(t + p.ph) * 8) * dt;
          if (p.y < -10) Object.assign(p, newMote(false));
        }
      } else if (current === "sand") {
        const g = gust();
        for (const p of parts) {
          if (p.cloud) {
            p.x -= p.sp * g * dt;
            if (p.x < -p.rx * 1.2) { p.x = W + p.rx; p.y = rand(H * 0.1, H); }
            continue;
          }
          p.x -= p.sp * g * dt;
          p.y += Math.sin(t * 3 + p.ph) * 40 * dt + 18 * dt;
          if (p.x < -20 || p.y > H + 20) Object.assign(p, newGrain(false));
        }
      } else if (current === "hail") {
        for (const p of parts) {
          if (p.flake) {
            p.y += p.sp * dt;
            p.x += (Math.sin(t * 1.5 + p.ph) * 22 - 30) * dt;
            if (p.y > H + 5) Object.assign(p, newFlake(false));
            continue;
          }
          p.y += p.sp * dt;
          p.x -= 110 * dt;
          if (p.y >= p.gy) {
            if (p.z > 0.55 && Math.random() < 0.55) {
              for (let k = 0; k < 3; k++) fx.push({ shard: true, x: p.x, y: p.gy, vx: rand(-90, 90), vy: rand(-120, -40), age: 0, life: 0.35 });
            } else {
              fx.push({ bounce: true, x: p.x, y: p.gy, vx: rand(-50, 30), vy: -p.sp * rand(0.18, 0.3), r: p.r, gy: p.gy, age: 0, life: 0.6 });
            }
            Object.assign(p, newStone(false));
          }
        }
      }
      for (const e of fx) {
        e.age += dt;
        if (e.drop || e.shard) {
          e.vy += 600 * dt;
          e.x += e.vx * dt;
          e.y += e.vy * dt;
        } else if (e.bounce) {
          e.vy += 900 * dt;
          e.x += e.vx * dt;
          e.y = Math.min(e.gy, e.y + e.vy * dt);
        }
      }
      fx = fx.filter((e) => e.age < e.life);
    }

    // ---------- Drawing ----------

    function draw() {
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      if (!current || intensity <= 0) return;
      ctx.globalAlpha = intensity;
      if (current === "rain") drawRain();
      else if (current === "sun") drawSun();
      else if (current === "sand") drawSand();
      else if (current === "hail") drawHail();
      ctx.globalAlpha = 1;
    }

    function drawRain() {
      ctx.lineCap = "round";
      for (const p of parts) {
        // Streak trails back along the drop's velocity (wind slants it).
        ctx.strokeStyle = `rgba(195, 220, 255, ${0.18 + 0.5 * p.z})`;
        ctx.lineWidth = 0.7 + p.z * 1.1;
        ctx.beginPath();
        ctx.moveTo(p.x, p.y);
        ctx.lineTo(p.x + (210 / 1300) * p.len, p.y - p.len);
        ctx.stroke();
      }
      for (const e of fx) {
        const k = e.age / e.life;
        if (e.ripple) {
          ctx.strokeStyle = `rgba(200, 225, 255, ${0.55 * (1 - k)})`;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.ellipse(e.x, e.y, 3 + 13 * k * e.s, (3 + 13 * k * e.s) * 0.32, 0, 0, Math.PI * 2);
          ctx.stroke();
        } else {
          ctx.fillStyle = `rgba(210, 230, 255, ${0.7 * (1 - k)})`;
          ctx.fillRect(e.x, e.y, 1.6, 1.6);
        }
      }
      if (flash > 0) {
        ctx.fillStyle = `rgba(225, 235, 255, ${0.32 * flash})`;
        ctx.fillRect(0, 0, W, H);
      }
    }

    function drawSun() {
      const ox = W * 0.9, oy = -H * 0.12;
      const len = Math.hypot(W, H) * 1.15;
      ctx.globalCompositeOperation = "lighter";
      const pulse = 0.85 + 0.15 * Math.sin(t * 1.3);
      const core = ctx.createRadialGradient(ox, oy, 0, ox, oy, W * 0.42 * pulse);
      core.addColorStop(0, "rgba(255, 244, 190, 0.55)");
      core.addColorStop(0.35, "rgba(255, 200, 90, 0.22)");
      core.addColorStop(1, "rgba(255, 170, 60, 0)");
      ctx.fillStyle = core;
      ctx.fillRect(0, 0, W, H);
      for (const p of parts) {
        if (!p.ray) continue;
        const a = p.a + Math.sin(t * p.sp + p.ph) * 0.05;
        const w = p.w * (0.8 + 0.3 * Math.sin(t * p.sp * 1.7 + p.ph));
        const alpha = 0.11 + 0.08 * Math.sin(t * p.sp * 1.3 + p.ph * 2);
        const g = ctx.createLinearGradient(ox, oy, ox + Math.cos(a) * len, oy + Math.sin(a) * len);
        g.addColorStop(0, `rgba(255, 236, 170, ${alpha + 0.1})`);
        g.addColorStop(0.6, `rgba(255, 215, 120, ${alpha * 0.5})`);
        g.addColorStop(1, "rgba(255, 200, 100, 0)");
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.moveTo(ox, oy);
        ctx.lineTo(ox + Math.cos(a - w) * len, oy + Math.sin(a - w) * len);
        ctx.lineTo(ox + Math.cos(a + w) * len, oy + Math.sin(a + w) * len);
        ctx.closePath();
        ctx.fill();
      }
      // Lens flare: a few soft discs on the line from the sun through the middle.
      const cx = W * 0.5, cy = H * 0.55;
      [[0.55, 26, 0.12], [0.85, 12, 0.18], [1.25, 40, 0.07]].forEach(([k, r, a]) => {
        const x = ox + (cx - ox) * k + Math.sin(t * 0.5) * 6, y = oy + (cy - oy) * k;
        const g = ctx.createRadialGradient(x, y, 0, x, y, r);
        g.addColorStop(0, `rgba(255, 230, 160, ${a})`);
        g.addColorStop(1, "rgba(255, 230, 160, 0)");
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.arc(x, y, r, 0, Math.PI * 2);
        ctx.fill();
      });
      for (const p of parts) {
        if (!p.mote) continue;
        const tw = 0.45 + 0.55 * Math.abs(Math.sin(t * 2 + p.ph));
        ctx.fillStyle = `rgba(255, 225, 140, ${0.55 * tw})`;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r * 2.2, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = `rgba(255, 250, 220, ${0.8 * tw})`;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r * 0.8, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalCompositeOperation = "source-over";
    }

    function drawSand() {
      const g = gust();
      for (const p of parts) {
        if (!p.cloud) continue;
        const grad = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.rx);
        grad.addColorStop(0, `rgba(205, 165, 100, ${p.a * (0.7 + 0.5 * g)})`);
        grad.addColorStop(1, "rgba(205, 165, 100, 0)");
        ctx.fillStyle = grad;
        ctx.save();
        ctx.translate(p.x, p.y);
        ctx.scale(1, p.ry / p.rx);
        ctx.translate(-p.x, -p.y);
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.rx, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
      }
      ctx.lineCap = "round";
      for (const p of parts) {
        if (p.cloud) continue;
        const streak = p.sp * g * 0.018;
        ctx.strokeStyle = `rgba(236, 206, 150, ${0.3 + 0.55 * p.z})`;
        ctx.lineWidth = 0.8 + 1.8 * p.z;
        ctx.beginPath();
        ctx.moveTo(p.x, p.y);
        ctx.lineTo(p.x + streak, p.y - streak * 0.08);
        ctx.stroke();
      }
    }

    function drawHail() {
      for (const p of parts) {
        if (!p.flake) continue;
        ctx.fillStyle = "rgba(235, 245, 255, 0.7)";
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
        ctx.fill();
      }
      for (const p of parts) {
        if (!p.stone) continue;
        drawStone(p.x, p.y, p.r, 0.55 + 0.45 * p.z);
        ctx.strokeStyle = `rgba(220, 240, 255, ${0.25 * p.z})`;
        ctx.lineWidth = p.r * 0.9;
        ctx.beginPath();
        ctx.moveTo(p.x, p.y);
        ctx.lineTo(p.x + 110 * 0.035, p.y - p.sp * 0.035);
        ctx.stroke();
      }
      for (const e of fx) {
        const k = e.age / e.life;
        if (e.bounce) drawStone(e.x, e.y, e.r, 1 - k);
        else {
          ctx.fillStyle = `rgba(230, 245, 255, ${0.9 * (1 - k)})`;
          ctx.fillRect(e.x, e.y, 2, 2);
        }
      }
    }

    function drawStone(x, y, r, a) {
      ctx.fillStyle = `rgba(225, 240, 255, ${a})`;
      ctx.strokeStyle = `rgba(140, 190, 235, ${a})`;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      ctx.fillStyle = `rgba(255, 255, 255, ${a})`;
      ctx.beginPath();
      ctx.arc(x - r * 0.35, y - r * 0.35, r * 0.35, 0, Math.PI * 2);
      ctx.fill();
    }

    // ---------- Loop ----------

    function frame(now) {
      raf = 0;
      const dt = Math.min(0.05, last ? (now - last) / 1000 : 0.016);
      last = now;
      if (current !== target) {
        intensity -= dt * 2.5;
        if (intensity <= 0 || !current) {
          current = target;
          intensity = 0;
          if (current) seed(current);
        }
      } else if (current) {
        intensity = Math.min(1, intensity + dt * 1.2);
      }
      if (current) step(dt);
      draw();
      schedule();
    }

    function schedule() {
      if (raf || stillTimer) return;
      if (!current && !target) {
        last = 0;
        return;
      }
      if (motionOff()) {
        // Check back in case the viewer turns motion effects on.
        paintStill();
        last = 0;
        stillTimer = setTimeout(() => { stillTimer = 0; schedule(); }, 800);
        return;
      }
      stillDirty = true;
      if (document.hidden) {
        last = 0;
        return;
      }
      raf = requestAnimationFrame(frame);
    }

    document.addEventListener("visibilitychange", schedule);
    if (window.ResizeObserver) new ResizeObserver(resize).observe(scene);
    else window.addEventListener("resize", resize);
    resize();

    return {
      set(kind) {
        const next = kind || null;
        if (next === target) return;
        target = next;
        schedule();
      },
    };
  }

  return { attach };
})();
