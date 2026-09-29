// Live weather for the battle scene: a canvas particle layer drawn over the
// arena (under the HP labels and panels). Rain falls in depth layers and
// splashes on the floor with the odd lightning flash; harsh sunlight sweeps
// light rays and drifting motes; a sandstorm gusts grains and dust clouds
// across the field; hail pelts down and bounces or shatters on the floor.
//
// Built to stay smooth on modest machines: the big slow shapes (sun rays,
// dust clouds) are CSS layers the GPU moves without repainting; the canvas
// only draws small particles, batched (one path per depth layer, not one per
// particle), with glows pre-rendered once and stamped. It renders at 1x,
// pauses while the scene is off-screen or the tab hidden, and if frames still
// run long it quietly thins the particles out.
//
// Honors the battle page's "Motion effects" switch (BattleFX.kit), not the
// OS reduce-motion setting: with motion off it paints one still frame.
window.BattleWeather = (function () {
  const FLOOR = 0.44; // where the arena floor starts, as a fraction of height
  const LAYERS = [0.35, 0.65, 1]; // particle depth layers (far -> near)
  const RAIN_WIND = 210 / 1300; // horizontal drift per unit of fall
  const rand = (a, b) => a + Math.random() * (b - a);
  const STONE_R = [2, 3, 4.5]; // hailstone radius per depth layer
  const pickLayer = () => (Math.random() < 0.45 ? 0 : Math.random() < 0.6 ? 1 : 2);

  function motionOff() {
    try {
      return !!(window.BattleFX && window.BattleFX.kit && window.BattleFX.kit.reduceMotion);
    } catch (e) {
      return false;
    }
  }

  function offscreen(w, h, paint) {
    const c = document.createElement("canvas");
    c.width = Math.max(1, Math.round(w));
    c.height = Math.max(1, Math.round(h));
    paint(c.getContext("2d"), c.width, c.height);
    return c;
  }

  // Soft round glow, reused for sun motes, lens flares and dust clouds.
  function glowSprite(rgb, size = 64) {
    return offscreen(size, size, (g, w) => {
      const grad = g.createRadialGradient(w / 2, w / 2, 0, w / 2, w / 2, w / 2);
      grad.addColorStop(0, `rgba(${rgb}, 1)`);
      grad.addColorStop(0.35, `rgba(${rgb}, 0.45)`);
      grad.addColorStop(1, `rgba(${rgb}, 0)`);
      g.fillStyle = grad;
      g.fillRect(0, 0, w, w);
    });
  }

  function attach(scene) {
    const canvas = document.createElement("canvas");
    canvas.className = "battle-weather-canvas";
    canvas.setAttribute("aria-hidden", "true");
    scene.appendChild(canvas);
    const ctx = canvas.getContext("2d");

    const sprites = {
      mote: glowSprite("255, 226, 150"),
      flare: glowSprite("255, 230, 160"),
    };
    // Sun rays and dust clouds: CSS, animated on the compositor (style.css),
    // inside the tint layer so they fade in and out with it.
    const tint = scene.querySelector(".battle-weather-layer") || scene;
    for (const cls of ["battle-weather-rays", "battle-weather-dust"]) {
      const el = document.createElement("div");
      el.className = cls;
      tint.appendChild(el);
    }

    let W = 0, H = 0;
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
    let onScreen = true;
    // Adaptive quality: the share of particles simulated and drawn.
    let quality = 1;
    let slowTime = 0;

    function resize() {
      const r = scene.getBoundingClientRect();
      W = Math.max(1, Math.round(r.width));
      H = Math.max(1, Math.round(r.height));
      canvas.width = W;
      canvas.height = H;
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
    const live = () => Math.ceil(parts.length * quality);

    function seed(kind) {
      parts = [];
      fx = [];
      const n = (base) => Math.round(base * density());
      if (kind === "rain") {
        for (let i = 0; i < n(200); i++) parts.push(newDrop(true));
      } else if (kind === "sun") {
        for (let i = 0; i < n(40); i++) parts.push(newMote(true));
      } else if (kind === "sand") {
        for (let i = 0; i < n(240); i++) parts.push(newGrain(true));
      } else if (kind === "hail") {
        for (let i = 0; i < n(26); i++) parts.push(newFlake(true));
        for (let i = 0; i < n(60); i++) parts.push(newStone(true));
      }
    }

    function newDrop(anywhere) {
      const l = pickLayer();
      const z = LAYERS[l];
      return { l, z, x: rand(-40, W + 160), y: anywhere ? rand(-H, H) : rand(-120, -10), gy: groundY(), sp: 820 + 700 * z, len: 10 + 24 * z };
    }
    function newMote(anywhere) {
      return { x: rand(0, W), y: anywhere ? rand(0, H) : H + 10, s: rand(6, 16), sp: rand(8, 26), ph: rand(0, 6.28), dx: rand(-10, 6) };
    }
    function newGrain(anywhere) {
      const l = pickLayer();
      return { l, z: LAYERS[l], x: anywhere ? rand(0, W) : W + rand(0, 80), y: rand(-10, H + 10), ph: rand(0, 6.28), sp: 260 + 560 * LAYERS[l] };
    }
    function newStone(anywhere) {
      const l = pickLayer();
      return { stone: true, l, z: LAYERS[l], x: rand(0, W + 120), y: anywhere ? rand(-H, H) : rand(-80, -10), gy: groundY(), sp: 480 + 420 * LAYERS[l] };
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
      const n = live();
      if (current === "rain") {
        for (let i = 0; i < n; i++) {
          const p = parts[i];
          const prevY = p.y;
          p.y += p.sp * dt;
          p.x -= RAIN_WIND * p.sp * dt;
          if (p.l === 2 && prevY < p.gy && p.y >= p.gy && fx.length < 60) {
            fx.push({ ripple: true, x: p.x, y: p.gy, age: 0, life: 0.4 });
            if (Math.random() < 0.4) fx.push({ drop: true, x: p.x, y: p.gy, vx: rand(-60, 60), vy: rand(-140, -70), age: 0, life: 0.35 });
          }
          if (p.y > p.gy + (p.l === 2 ? 0 : 40) || p.x < -60) Object.assign(p, newDrop(false));
        }
        nextFlash -= dt;
        if (nextFlash <= 0) {
          flash = 1;
          nextFlash = rand(7, 15);
          setTimeout(() => { flash = Math.max(flash, 0.7); }, 160);
        }
        flash = Math.max(0, flash - dt * 3.2);
      } else if (current === "sun") {
        for (let i = 0; i < n; i++) {
          const p = parts[i];
          p.y -= p.sp * dt;
          p.x += (p.dx + Math.sin(t + p.ph) * 8) * dt;
          if (p.y < -10) Object.assign(p, newMote(false));
        }
      } else if (current === "sand") {
        const g = gust();
        for (let i = 0; i < n; i++) {
          const p = parts[i];
          p.x -= p.sp * g * dt;
          p.y += Math.sin(t * 3 + p.ph) * 40 * dt + 18 * dt;
          if (p.x < -20 || p.y > H + 20) Object.assign(p, newGrain(false));
        }
      } else if (current === "hail") {
        for (let i = 0; i < n; i++) {
          const p = parts[i];
          if (p.flake) {
            p.y += p.sp * dt;
            p.x += (Math.sin(t * 1.5 + p.ph) * 22 - 30) * dt;
            if (p.y > H + 5) Object.assign(p, newFlake(false));
            continue;
          }
          p.y += p.sp * dt;
          p.x -= 110 * dt;
          if (p.y >= p.gy) {
            if (fx.length < 50) {
              if (p.l === 2 && Math.random() < 0.55) {
                for (let k = 0; k < 3; k++) fx.push({ shard: true, x: p.x, y: p.gy, vx: rand(-90, 90), vy: rand(-120, -40), age: 0, life: 0.35 });
              } else {
                fx.push({ bounce: true, x: p.x, y: p.gy, vx: rand(-50, 30), vy: -p.sp * rand(0.18, 0.3), l: p.l, gy: p.gy, age: 0, life: 0.6 });
              }
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
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.globalAlpha = 1;
      ctx.clearRect(0, 0, W, H);
      if (!current || intensity <= 0) return;
      if (current === "rain") drawRain();
      else if (current === "sun") drawSun();
      else if (current === "sand") drawSand();
      else if (current === "hail") drawHail();
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = "source-over";
    }

    // One stroke per depth layer instead of one per particle.
    function strokeLayers(n, color, width, segment) {
      for (let l = 0; l < LAYERS.length; l++) {
        ctx.beginPath();
        for (let i = 0; i < n; i++) {
          const p = parts[i];
          if (p.l === l) segment(p);
        }
        ctx.globalAlpha = intensity * color(LAYERS[l]);
        ctx.lineWidth = width(LAYERS[l]);
        ctx.stroke();
      }
    }

    function drawRain() {
      const n = live();
      ctx.lineCap = "round";
      ctx.strokeStyle = "rgb(195, 220, 255)";
      strokeLayers(n, (z) => 0.18 + 0.5 * z, (z) => 0.7 + z * 1.1, (p) => {
        ctx.moveTo(p.x, p.y);
        ctx.lineTo(p.x + RAIN_WIND * p.len, p.y - p.len);
      });
      ctx.strokeStyle = "rgb(200, 225, 255)";
      ctx.fillStyle = "rgb(210, 230, 255)";
      ctx.lineWidth = 1;
      for (const e of fx) {
        const k = e.age / e.life;
        ctx.globalAlpha = intensity * 0.6 * (1 - k);
        if (e.ripple) {
          const r = 3 + 13 * k;
          ctx.beginPath();
          ctx.ellipse(e.x, e.y, r, r * 0.32, 0, 0, Math.PI * 2);
          ctx.stroke();
        } else {
          ctx.fillRect(e.x, e.y, 1.6, 1.6);
        }
      }
      if (flash > 0) {
        ctx.globalAlpha = intensity * 0.32 * flash;
        ctx.fillStyle = "rgb(225, 235, 255)";
        ctx.fillRect(0, 0, W, H);
      }
    }

    function drawSun() {
      // Rays and glow are the CSS layer; here: lens flare + drifting motes.
      const ox = W * 0.9, oy = -H * 0.12;
      ctx.globalCompositeOperation = "lighter";
      const cx = W * 0.5, cy = H * 0.55;
      [[0.55, 52, 0.12], [0.85, 24, 0.18], [1.25, 80, 0.07]].forEach(([k, d, a]) => {
        const x = ox + (cx - ox) * k + Math.sin(t * 0.5) * 6, y = oy + (cy - oy) * k;
        ctx.globalAlpha = intensity * a;
        ctx.drawImage(sprites.flare, x - d / 2, y - d / 2, d, d);
      });
      const n = live();
      for (let i = 0; i < n; i++) {
        const p = parts[i];
        ctx.globalAlpha = intensity * (0.35 + 0.65 * Math.abs(Math.sin(t * 2 + p.ph)));
        ctx.drawImage(sprites.mote, p.x - p.s / 2, p.y - p.s / 2, p.s, p.s);
      }
    }

    function drawSand() {
      const g = gust();
      const n = live();
      ctx.lineCap = "round";
      ctx.strokeStyle = "rgb(236, 206, 150)";
      strokeLayers(n, (z) => 0.3 + 0.55 * z, (z) => 0.8 + 1.8 * z, (p) => {
        const streak = p.sp * g * 0.018;
        ctx.moveTo(p.x, p.y);
        ctx.lineTo(p.x + streak, p.y - streak * 0.08);
      });
    }

    function drawHail() {
      const n = live();
      ctx.fillStyle = "rgb(235, 245, 255)";
      ctx.globalAlpha = intensity * 0.7;
      ctx.beginPath();
      for (let i = 0; i < n; i++) {
        const p = parts[i];
        if (!p.flake) continue;
        ctx.moveTo(p.x + p.r, p.y);
        ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      }
      ctx.fill();
      ctx.lineCap = "round";
      ctx.strokeStyle = "rgb(220, 240, 255)";
      strokeLayers(n, (z) => 0.25 * z, (z) => 1.5 + 3 * z, (p) => {
        if (!p.stone) return;
        ctx.moveTo(p.x, p.y);
        ctx.lineTo(p.x + 110 * 0.035, p.y - p.sp * 0.035);
      });
      // Stones: one fill + one outline + one highlight pass per depth layer.
      const bounces = fx.filter((e) => e.bounce);
      for (let l = 0; l < LAYERS.length; l++) {
        const r = STONE_R[l];
        ctx.beginPath();
        for (let i = 0; i < n; i++) {
          const p = parts[i];
          if (p.stone && p.l === l) { ctx.moveTo(p.x + r, p.y); ctx.arc(p.x, p.y, r, 0, Math.PI * 2); }
        }
        for (const e of bounces) {
          if (e.l === l) { ctx.moveTo(e.x + r, e.y); ctx.arc(e.x, e.y, r, 0, Math.PI * 2); }
        }
        ctx.globalAlpha = intensity * (0.55 + 0.45 * LAYERS[l]);
        ctx.fillStyle = "rgb(228, 242, 255)";
        ctx.fill();
        ctx.strokeStyle = "rgb(140, 190, 235)";
        ctx.lineWidth = 1;
        ctx.stroke();
      }
      ctx.globalAlpha = intensity * 0.8;
      ctx.fillStyle = "rgb(230, 245, 255)";
      ctx.beginPath();
      for (const e of fx) {
        if (e.shard) ctx.rect(e.x, e.y, 2, 2);
      }
      ctx.fill();
    }

    // ---------- Loop ----------

    function frame(now) {
      raf = 0;
      const raw = last ? (now - last) / 1000 : 0.016;
      const dt = Math.min(0.05, raw);
      last = now;
      // Frames running long (under ~40fps) for a while: thin the particles.
      if (raw > 0.025 && raw < 0.5) {
        slowTime += raw;
        if (slowTime > 1.2 && quality > 0.4) {
          quality = Math.max(0.4, quality - 0.2);
          slowTime = 0;
        }
      } else {
        slowTime = Math.max(0, slowTime - raw * 0.5);
      }
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
      if (document.hidden || !onScreen) {
        last = 0;
        return;
      }
      raf = requestAnimationFrame(frame);
    }

    document.addEventListener("visibilitychange", schedule);
    if (window.IntersectionObserver) {
      new IntersectionObserver((entries) => {
        onScreen = entries[entries.length - 1].isIntersecting;
        schedule();
      }).observe(scene);
    }
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
