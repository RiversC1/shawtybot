// Game-style animations for individual moves, loosely modeled on how each
// move looks in the main-series games: Earthquake shakes the whole screen,
// Hydro Pump fires a water jet, Surf sends a wave across the field, Thunder
// drops a bolt from the sky, and so on.
//
// Lookup order for a move: an explicit entry in MOVES, then a name-pattern
// rule in PATTERNS (every "... Punch", "... Fang", "... Beam"...), else null,
// in which case BattleFX falls back to its generic type/category animation.
// Each recipe schedules its effects and returns how long the whole thing
// takes in ms; BattleMoves.play turns that into a promise.
window.BattleMoves = (function () {
  const K = BattleFX.kit;
  const { rand, particle, run, centerOf } = K;
  const at = (ms, fn) => setTimeout(fn, ms);

  function el(cls, x, y, css) {
    const d = document.createElement("div");
    d.className = cls;
    d.style.left = `${x}px`;
    d.style.top = `${y}px`;
    if (css) Object.assign(d.style, css);
    K.layer().appendChild(d);
    return d;
  }

  const C = {
    water: { color: "#3f9bff", glow: "#cfeaff" },
    fire: { color: "#ff6a1f", glow: "#ffd54a" },
    electric: { color: "#ffd83a", glow: "#fffbd0" },
    ice: { color: "#8ee6ff", glow: "#ffffff" },
    grass: { color: "#4fcf4f", glow: "#d0ffb8" },
    psychic: { color: "#ff5fa8", glow: "#ffc6e0" },
    ghost: { color: "#6b4fc9", glow: "#c4b2ff" },
    dark: { color: "#3a2f3a", glow: "#8b6f86" },
    dragon: { color: "#6a55ff", glow: "#c0b6ff" },
    poison: { color: "#a64fe0", glow: "#e2b6ff" },
    fighting: { color: "#e8603c", glow: "#ffd1b8" },
    steel: { color: "#b9c6d6", glow: "#ffffff" },
    rock: { color: "#a98b54", glow: "#e8d6a4" },
    ground: { color: "#b98f4c", glow: "#ecd3a0" },
    flying: { color: "#d8e2ff", glow: "#ffffff" },
    bug: { color: "#9fc332", glow: "#e6f6a0" },
    fairy: { color: "#ff9fe0", glow: "#fff0fa" },
    normal: { color: "#f2ede0", glow: "#ffffff" },
    mud: { color: "#7a5a32", glow: "#b89868" },
    sludge: { color: "#8a3cc8", glow: "#c890f0" },
    heal: { color: "#56e08a", glow: "#d4ffe2" },
    gold: { color: "#ffcf3a", glow: "#fff6c8" },
    white: { color: "#ffffff", glow: "#ffffff" },
    red: { color: "#ff3b3b", glow: "#ffb0b0" },
  };

  // ---------------------------------------------------------------- screen

  function shake(amp = 9, dur = 520, vertical = false) {
    if (K.reduceMotion) return;
    const frames = [];
    const n = 12;
    for (let i = 0; i <= n; i++) {
      const k = 1 - i / n;
      const a = i === n ? 0 : amp * k * (i % 2 ? 1 : -1);
      frames.push({ transform: vertical ? `translate(${a * 0.25}px, ${a}px)` : `translate(${a}px, ${a * 0.3}px)` });
    }
    K.scene().animate(frames, { duration: dur, easing: "linear" });
  }

  function screenFlash(color = "#ffffff", dur = 380, peak = 0.75) {
    const f = el("fx-screen", 0, 0, { background: color });
    run(f, [{ opacity: 0 }, { opacity: peak, offset: 0.2 }, { opacity: 0 }], { duration: dur, easing: "ease-out" });
  }

  function screenTint(color, dur = 900, peak = 0.35) {
    const f = el("fx-screen", 0, 0, { background: color, mixBlendMode: "screen" });
    run(f, [{ opacity: 0 }, { opacity: peak, offset: 0.25 }, { opacity: peak, offset: 0.75 }, { opacity: 0 }], { duration: dur });
  }

  function distort(sprite, dur = 800) {
    if (!sprite || K.reduceMotion) return;
    sprite.animate([
      { transform: "none" },
      { transform: "skewX(12deg) scaleY(0.92)" },
      { transform: "skewX(-12deg) scaleY(1.06)" },
      { transform: "skewX(8deg)" },
      { transform: "skewX(-6deg)" },
      { transform: "none" },
    ], { duration: dur, easing: "ease-in-out" });
  }

  // ------------------------------------------------------------ projectiles

  // Big dust clouds billowing along the ground around a point.
  function dust(target, c = { color: "#a88a5c", glow: "#e6d2a8" }, o = {}) {
    const count = o.count || 9;
    for (let i = 0; i < count; i++) {
      const x = target.x + rand(-(o.spread || 90), o.spread || 90);
      const d = particle("wisp", x, target.y + target.h * 0.35, rand(30, 48), c);
      run(d, [
        { transform: "translate(-50%,-50%) scale(0.4)", opacity: 0 },
        { transform: `translate(calc(-50% + ${rand(-20, 20)}px), calc(-50% - ${rand(10, 30)}px)) scale(1.3)`, opacity: 0.75, offset: 0.35 },
        { transform: `translate(calc(-50% + ${rand(-40, 40)}px), calc(-50% - ${rand(30, 50)}px)) scale(1.8)`, opacity: 0 },
      ], { duration: rand(700, 1000), delay: rand(0, 250), easing: "ease-out" });
    }
  }

  // The target jolts up and down with the ground.
  function bounce(sprite, dur = 700) {
    if (!sprite || K.reduceMotion) return;
    sprite.animate([
      { transform: "translateY(0)" }, { transform: "translateY(-14px)" }, { transform: "translateY(2px)" },
      { transform: "translateY(-9px)" }, { transform: "translateY(1px)" }, { transform: "translateY(-4px)" }, { transform: "translateY(0)" },
    ], { duration: dur, easing: "ease-out" });
  }

  function beam(from, to, c, o = {}) {
    const width = o.width || 16;
    const dur = o.dur || 650;
    const dx = to.x - from.x, dy = to.y - from.y;
    const len = Math.hypot(dx, dy), ang = (Math.atan2(dy, dx) * 180) / Math.PI;
    const b = el(`fx-beam fx-beam-${o.style || "solid"}`, from.x, from.y, { width: `${len}px`, height: `${width}px` });
    b.style.setProperty("--c", c.color);
    b.style.setProperty("--g", c.glow);
    const t = (sx, sy = 1) => `translate(0, -50%) rotate(${ang}deg) scale(${sx}, ${sy})`;
    run(b, [
      { transform: t(0, 0.6), opacity: 1 },
      { transform: t(1, 1), opacity: 1, offset: 0.25 },
      { transform: t(1, 1.15), opacity: 1, offset: 0.55 },
      { transform: t(1, 0.4), opacity: 0 },
    ], { duration: dur, easing: "ease-out" });
    return dur;
  }

  // A jagged lightning bolt between two points, redrawn a few times so it
  // crackles.
  function lightning(from, to, c, o = {}) {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("class", "fx-svg");
    K.layer().appendChild(svg);
    const path = document.createElementNS(ns, "polyline");
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", c.glow);
    path.setAttribute("stroke-width", o.width || 5);
    path.setAttribute("stroke-linejoin", "round");
    path.style.filter = `drop-shadow(0 0 6px ${c.color}) drop-shadow(0 0 12px ${c.color})`;
    svg.appendChild(path);
    const segs = o.segments || 9;
    const redraw = () => {
      const pts = [];
      for (let i = 0; i <= segs; i++) {
        const k = i / segs;
        const j = i === 0 || i === segs ? 0 : rand(-18, 18);
        const nx = -(to.y - from.y), ny = to.x - from.x, l = Math.hypot(nx, ny) || 1;
        pts.push(`${from.x + (to.x - from.x) * k + (nx / l) * j},${from.y + (to.y - from.y) * k + (ny / l) * j}`);
      }
      path.setAttribute("points", pts.join(" "));
    };
    redraw();
    const dur = o.dur || 520;
    const iv = setInterval(redraw, 70);
    run(svg, [{ opacity: 0 }, { opacity: 1, offset: 0.1 }, { opacity: 0.6, offset: 0.4 }, { opacity: 1, offset: 0.6 }, { opacity: 0 }], { duration: dur })
      .finished.then(() => clearInterval(iv), () => clearInterval(iv));
    return dur;
  }

  // A dense jet of particles (Hydro Pump, Flamethrower, Water Gun...).
  function stream(from, to, c, o = {}) {
    const count = o.count || 28, dur = o.dur || 750, travel = o.travel || 360;
    for (let i = 0; i < count; i++) {
      const delay = (i / count) * (dur - travel);
      const p = particle(o.shape || "orb", from.x, from.y, rand(o.min || 9, o.max || 16), c);
      const jx = rand(-(o.spread || 10), o.spread || 10), jy = rand(-(o.spread || 10), o.spread || 10);
      run(p, [
        { transform: "translate(-50%,-50%) scale(0.5)", opacity: 0.2 },
        { transform: `translate(calc(-50% + ${(to.x - from.x) * 0.5 + jx}px), calc(-50% + ${(to.y - from.y) * 0.5 + jy}px)) scale(1)`, opacity: 1, offset: 0.5 },
        { transform: `translate(calc(-50% + ${to.x - from.x + jx * 2}px), calc(-50% + ${to.y - from.y + jy * 2}px)) scale(${o.endScale || 1.3})`, opacity: 0 },
      ], { duration: travel, delay, easing: "linear" });
    }
    return dur;
  }

  // One large orb that flies (optionally lobbed in an arc) to the target.
  function orb(from, to, c, o = {}) {
    const size = o.size || 38, dur = o.dur || 560, arc = o.arc || 0;
    const p = particle(o.shape || "orb", from.x, from.y, size / 1.4, c);
    p.classList.add("fx-big-orb");
    const frames = [];
    for (let i = 0; i <= 10; i++) {
      const k = i / 10;
      const lift = arc * 4 * k * (1 - k);
      frames.push({
        transform: `translate(calc(-50% + ${(to.x - from.x) * k}px), calc(-50% + ${(to.y - from.y) * k - lift}px)) rotate(${k * (o.spin || 0)}deg) scale(${0.6 + 0.4 * Math.min(1, k * 3)})`,
        opacity: i === 10 ? 0.9 : 1,
      });
    }
    run(p, frames, { duration: dur, easing: "ease-in" });
    if (o.trail) {
      for (let i = 1; i <= 6; i++) at(i * (dur / 8), () => {
        const k = (i * (dur / 8)) / dur;
        const lift = arc * 4 * k * (1 - k);
        const t = particle("orb", from.x + (to.x - from.x) * k, from.y + (to.y - from.y) * k - lift, size / 3, c);
        run(t, [{ transform: "translate(-50%,-50%) scale(1)", opacity: 0.7 }, { transform: "translate(-50%,-50%) scale(0.2)", opacity: 0 }], { duration: 380 });
      });
    }
    return dur;
  }

  // A tidal wave that sweeps across the whole field from the user's side.
  function wave(side, c, o = {}) {
    const s = K.scene().getBoundingClientRect();
    const w = el("fx-wave", 0, 0, { height: `${s.height * (o.height || 0.75)}px`, width: `${s.width * 0.7}px` });
    w.style.setProperty("--c", c.color);
    w.style.setProperty("--g", c.glow);
    const dur = o.dur || 1100;
    const startX = side === "A" ? -s.width * 0.75 : s.width * 1.05;
    const endX = side === "A" ? s.width * 1.05 : -s.width * 0.75;
    const flip = side === "A" ? 1 : -1;
    run(w, [
      { transform: `translate(${startX}px, ${s.height * 0.3}px) scaleX(${flip})`, opacity: 0.2 },
      { transform: `translate(${startX + (endX - startX) * 0.45}px, ${s.height * 0.22}px) scaleX(${flip})`, opacity: 0.95, offset: 0.45 },
      { transform: `translate(${endX}px, ${s.height * 0.28}px) scaleX(${flip})`, opacity: 0 },
    ], { duration: dur, easing: "ease-in-out" });
    for (let i = 0; i < 16; i++) at(rand(150, dur - 250), () => {
      const x = rand(0, s.width), y = rand(s.height * 0.3, s.height * 0.8);
      const d = particle("bubble", x, y, rand(6, 12), c);
      run(d, [{ transform: "translate(-50%,-50%)", opacity: 1 }, { transform: `translate(-50%, calc(-50% - ${rand(20, 50)}px))`, opacity: 0 }], { duration: 500 });
    });
    return dur;
  }

  // Things raining down from above: onto the target, or across the whole
  // field when `wide`.
  function skyFall(target, c, o = {}) {
    const s = K.scene().getBoundingClientRect();
    const count = o.count || 10, dur = o.dur || 900;
    for (let i = 0; i < count; i++) {
      const x = o.wide ? rand(0, s.width) : target.x + rand(-45, 45);
      const endY = o.wide ? rand(s.height * 0.35, s.height) : target.y + rand(-10, 20);
      const drift = o.drift || -40;
      const p = particle(o.shape || "rock", x - drift, -30, rand(o.min || 12, o.max || 20), c);
      run(p, [
        { transform: "translate(-50%,-50%) rotate(0deg)", opacity: 0 },
        { transform: `translate(calc(-50% + ${drift * 0.2}px), calc(-50% + ${(endY + 30) * 0.2}px)) rotate(60deg)`, opacity: 1, offset: 0.2 },
        { transform: `translate(calc(-50% + ${drift}px), calc(-50% + ${endY + 30}px)) rotate(${rand(180, 320)}deg)`, opacity: 1, offset: 0.9 },
        { transform: `translate(calc(-50% + ${drift}px), calc(-50% + ${endY + 30}px)) scale(0.3)`, opacity: 0 },
      ], { duration: o.fallDur || 520, delay: (i / count) * (dur - (o.fallDur || 520)), easing: "ease-in" });
    }
    return dur;
  }

  // Particles swirling up around the target (Fire Spin, Leaf Storm, Whirlpool).
  function vortex(target, c, o = {}) {
    const count = o.count || 16, dur = o.dur || 950, r = o.radius || 42;
    for (let i = 0; i < count; i++) {
      const p = particle(o.shape || "orb", target.x, target.y, rand(o.min || 9, o.max || 15), c);
      const a0 = (i / count) * Math.PI * 2;
      const frames = [];
      for (let k = 0; k <= 8; k++) {
        const f = k / 8;
        const a = a0 + f * Math.PI * 3;
        frames.push({
          transform: `translate(calc(-50% + ${Math.cos(a) * r * (0.6 + f * 0.5)}px), calc(-50% + ${Math.sin(a) * r * 0.35 + (0.4 - f) * 60}px)) rotate(${f * 540}deg)`,
          opacity: k === 0 || k === 8 ? 0 : 1,
        });
      }
      run(p, frames, { duration: dur - 150, delay: rand(0, 150), easing: "linear" });
    }
    return dur;
  }

  // Spikes/pillars bursting up from the ground under the target.
  function erupt(target, c, o = {}) {
    const count = o.count || 5, dur = o.dur || 700;
    for (let i = 0; i < count; i++) {
      const x = target.x + (i - (count - 1) / 2) * (o.gap || 20) + rand(-5, 5);
      const h = rand(o.minH || 40, o.maxH || 80);
      const s = el(`fx-spike`, x, target.y + target.h * 0.45, { height: `${h}px`, width: `${o.width || 18}px` });
      s.style.setProperty("--c", c.color);
      s.style.setProperty("--g", c.glow);
      run(s, [
        { transform: "translate(-50%, 0) scaleY(0)", opacity: 1 },
        { transform: "translate(-50%, -100%) scaleY(1)", opacity: 1, offset: 0.35 },
        { transform: "translate(-50%, -100%) scaleY(1)", opacity: 1, offset: 0.7 },
        { transform: "translate(-50%, -100%) scaleY(0.2)", opacity: 0 },
      ], { duration: dur, delay: i * 50, easing: "ease-out" });
    }
    return dur + count * 50;
  }

  // Claw / blade marks across the target.
  function slashes(target, c, o = {}) {
    const count = o.count || 3, gap = o.interval || 110;
    for (let i = 0; i < count; i++) at(i * gap, () => {
      const ang = (o.angle ?? -35) + (o.cross && i % 2 ? 70 : 0) + rand(-6, 6);
      const off = (i - (count - 1) / 2) * (o.spacing ?? 14);
      const s = el("fx-slash-mark", target.x + off, target.y + off * 0.3, { width: `${o.length || 110}px` });
      s.style.setProperty("--c", c.color);
      s.style.setProperty("--g", c.glow);
      run(s, [
        { transform: `translate(-50%,-50%) rotate(${ang}deg) scaleX(0)`, opacity: 1 },
        { transform: `translate(-50%,-50%) rotate(${ang}deg) scaleX(1)`, opacity: 1, offset: 0.35 },
        { transform: `translate(-50%,-50%) rotate(${ang}deg) scaleX(1)`, opacity: 0 },
      ], { duration: 360, easing: "ease-out" });
    });
    return count * gap + 360;
  }

  // Upper and lower fangs snapping shut on the target.
  function jaws(target, c) {
    for (const dir of [-1, 1]) {
      const j = el(`fx-jaw ${dir < 0 ? "fx-jaw-top" : "fx-jaw-bottom"}`, target.x, target.y + dir * 46);
      j.style.setProperty("--c", c.color);
      j.style.setProperty("--g", c.glow);
      run(j, [
        { transform: "translate(-50%,-50%)", opacity: 0 },
        { transform: "translate(-50%,-50%)", opacity: 1, offset: 0.25 },
        { transform: `translate(-50%, calc(-50% + ${-dir * 36}px))`, opacity: 1, offset: 0.55, easing: "ease-in" },
        { transform: `translate(-50%, calc(-50% + ${-dir * 32}px))`, opacity: 0 },
      ], { duration: 560 });
    }
    at(300, () => K.burst(target, { ...c, shape: "star" }, 6));
    return 600;
  }

  // A strike landing: big star flash + sparks (punches, kicks, headbutts).
  function hit(target, c, o = {}) {
    const size = o.size || 70;
    const s = particle("star", target.x + rand(-8, 8), target.y + rand(-8, 8), size / 1.4, { color: c.color, glow: "#ffffff" });
    run(s, [
      { transform: "translate(-50%,-50%) scale(0.2) rotate(0deg)", opacity: 1 },
      { transform: "translate(-50%,-50%) scale(1.1) rotate(20deg)", opacity: 1, offset: 0.35 },
      { transform: "translate(-50%,-50%) scale(1.3) rotate(30deg)", opacity: 0 },
    ], { duration: 340, easing: "ease-out" });
    K.flash(target, c, size);
    if (!K.reduceMotion) K.burst(target, c, o.sparks ?? 7);
    if (o.shake) shake(o.shake, 300);
    return 340;
  }

  function hits(target, c, n, interval = 170, o = {}) {
    for (let i = 0; i < n; i++) at(i * interval, () => hit({ ...target, x: target.x + rand(-18, 18), y: target.y + rand(-14, 14) }, c, o));
    return n * interval + 340;
  }

  // Fast dash with afterimages (Quick Attack, Extreme Speed, Aerial Ace).
  function dash(ctx, c, o = {}) {
    const { atk, atkSprite, from, to } = ctx;
    const dur = o.dur || 420;
    if (!K.reduceMotion) {
      atk.animate([
        { transform: "translate(0,0)" },
        { transform: `translate(${(to.x - from.x) * 0.75}px, ${(to.y - from.y) * 0.75}px)`, offset: 0.4 },
        { transform: `translate(${(to.x - from.x) * 0.75}px, ${(to.y - from.y) * 0.75}px)`, offset: 0.55 },
        { transform: "translate(0,0)" },
      ], { duration: dur, easing: "ease-in-out" });
      if (atkSprite && atkSprite.src) {
        for (let i = 1; i <= 4; i++) at(i * 35, () => {
          const r = atkSprite.getBoundingClientRect(), s = K.scene().getBoundingClientRect();
          const g = document.createElement("img");
          g.src = atkSprite.src;
          g.className = "fx-afterimage";
          g.style.left = `${r.left - s.left}px`;
          g.style.top = `${r.top - s.top}px`;
          g.style.width = `${r.width}px`;
          g.style.height = `${r.height}px`;
          K.layer().appendChild(g);
          run(g, [{ opacity: 0.45 }, { opacity: 0 }], { duration: 300 });
        });
      }
      speedLines(from, to, c);
    }
    at(dur * 0.4, () => hit(to, c, { size: o.size || 60 }));
    return dur + 100;
  }

  function speedLines(from, to, c) {
    const dx = to.x - from.x, dy = to.y - from.y, ang = (Math.atan2(dy, dx) * 180) / Math.PI;
    for (let i = 0; i < 7; i++) {
      const k = rand(0.1, 0.8);
      const s = el("fx-speed-line", from.x + dx * k + rand(-25, 25), from.y + dy * k + rand(-25, 25));
      s.style.setProperty("--g", c.glow);
      run(s, [
        { transform: `translate(-50%,-50%) rotate(${ang}deg) scaleX(0.2)`, opacity: 0 },
        { transform: `translate(-50%,-50%) rotate(${ang}deg) scaleX(1)`, opacity: 0.9, offset: 0.4 },
        { transform: `translate(-50%,-50%) rotate(${ang}deg) scaleX(0.3)`, opacity: 0 },
      ], { duration: 300, delay: i * 25 });
    }
  }

  // A body-slam style charge: big lunge, screen shake on contact.
  function heavy(ctx, c, o = {}) {
    const { atk, from, to } = ctx;
    if (o.aura) K.glowSprite(ctx.atkSprite, c.glow, 500);
    K.lunge(atk, from, to);
    at(250, () => hit(to, c, { size: o.size || 90, shake: o.shake ?? 8, sparks: 10 }));
    return 760;
  }

  // Sound waves: arcs traveling from the user to the target.
  function soundWaves(from, to, c, o = {}) {
    const count = o.count || 5, dur = o.dur || 800;
    const ang = (Math.atan2(to.y - from.y, to.x - from.x) * 180) / Math.PI;
    for (let i = 0; i < count; i++) {
      const a = el("fx-sound-arc", from.x, from.y);
      a.style.setProperty("--c", c.color);
      run(a, [
        { transform: `translate(-50%,-50%) rotate(${ang}deg) scale(0.4)`, opacity: 0 },
        { transform: `translate(calc(-50% + ${(to.x - from.x) * 0.5}px), calc(-50% + ${(to.y - from.y) * 0.5}px)) rotate(${ang}deg) scale(1)`, opacity: 1, offset: 0.5 },
        { transform: `translate(calc(-50% + ${to.x - from.x}px), calc(-50% + ${to.y - from.y}px)) rotate(${ang}deg) scale(1.6)`, opacity: 0 },
      ], { duration: 520, delay: i * ((dur - 520) / count), easing: "linear" });
    }
    return dur;
  }

  // Rings bursting outward from a point (Dark Pulse, Discharge, Lava Plume).
  function pulse(center, c, o = {}) {
    const count = o.count || 4;
    for (let i = 0; i < count; i++) {
      const r = particle("ring", center.x, center.y, 40, c);
      if (o.thick) r.style.borderWidth = "6px";
      run(r, [
        { transform: "translate(-50%,-50%) scale(0.4)", opacity: 0.95 },
        { transform: `translate(-50%,-50%) scale(${o.scale || 6})`, opacity: 0 },
      ], { duration: o.dur || 700, delay: i * 140, easing: "ease-out" });
    }
    return (o.dur || 700) + count * 140;
  }

  // Full-screen weather flurry for a moment (Rain Dance, Hail, Sandstorm...).
  function weather(kind, o = {}) {
    const s = K.scene().getBoundingClientRect();
    const dur = o.dur || 1300;
    const spec = {
      rain: { c: C.water, shape: "drop", count: 55, fall: 420, drift: -30 },
      snow: { c: C.ice, shape: "orb", count: 45, fall: 900, drift: -60, min: 4, max: 8 },
      hail: { c: C.ice, shape: "shard", count: 40, fall: 600, drift: -50 },
      sand: { c: C.ground, shape: "orb", count: 60, fall: 700, drift: -220, min: 3, max: 6, horizontal: true },
    }[kind];
    if (kind === "sun") {
      screenTint("#ffcc55", dur, 0.4);
      const g = particle("flash", s.width * 0.85, 10, 200, C.gold);
      run(g, [{ transform: "translate(-50%,-50%) scale(0.5)", opacity: 0 }, { transform: "translate(-50%,-50%) scale(1.3)", opacity: 0.9, offset: 0.4 }, { transform: "translate(-50%,-50%) scale(1.5)", opacity: 0 }], { duration: dur });
      return dur;
    }
    if (kind === "rain") screenTint("#1b3b6a", dur, 0.35);
    if (kind === "sand") screenTint("#b8904a", dur, 0.3);
    for (let i = 0; i < spec.count; i++) {
      const x = spec.horizontal ? s.width + 20 : rand(-20, s.width + 60);
      const y = spec.horizontal ? rand(0, s.height) : -20;
      const p = particle(spec.shape, x, y, rand(spec.min || 6, spec.max || 12), spec.c);
      const dx = spec.horizontal ? -(s.width + 60) : spec.drift;
      const dy = spec.horizontal ? rand(-20, 20) : s.height + 40;
      run(p, [
        { transform: "translate(-50%,-50%)", opacity: 0 },
        { transform: `translate(calc(-50% + ${dx * 0.1}px), calc(-50% + ${dy * 0.1}px))`, opacity: 0.9, offset: 0.1 },
        { transform: `translate(calc(-50% + ${dx}px), calc(-50% + ${dy}px))`, opacity: 0.8 },
      ], { duration: spec.fall, delay: rand(0, dur - spec.fall), easing: "linear" });
    }
    return dur;
  }

  // Energy pulled from the target back into the user (Giga Drain, Absorb).
  function drain(target, user, c = C.heal) {
    for (let i = 0; i < 12; i++) {
      const p = particle("orb", target.x + rand(-20, 20), target.y + rand(-20, 20), rand(7, 11), c);
      const mx = (user.x - target.x) / 2 + rand(-60, 60), my = (user.y - target.y) / 2 + rand(-60, 60);
      run(p, [
        { transform: "translate(-50%,-50%) scale(0.4)", opacity: 0 },
        { transform: `translate(calc(-50% + ${mx}px), calc(-50% + ${my}px)) scale(1)`, opacity: 1, offset: 0.5 },
        { transform: `translate(calc(-50% + ${user.x - target.x}px), calc(-50% + ${user.y - target.y}px)) scale(0.6)`, opacity: 0 },
      ], { duration: 650, delay: i * 45, easing: "ease-in-out" });
    }
    at(700, () => K.aura(user, { ...c, shape: "star" }, 8, true));
    return 1100;
  }

  function shield(slot, c) {
    const t = centerOf(slot);
    const b = el("fx-shield", t.x, t.y, { width: `${t.w * 1.2}px`, height: `${t.h * 1.25}px` });
    b.style.setProperty("--c", c.color);
    b.style.setProperty("--g", c.glow);
    run(b, [
      { transform: "translate(-50%,-50%) scale(0.3)", opacity: 0 },
      { transform: "translate(-50%,-50%) scale(1.05)", opacity: 1, offset: 0.3 },
      { transform: "translate(-50%,-50%) scale(1)", opacity: 0.9, offset: 0.7 },
      { transform: "translate(-50%,-50%) scale(1.1)", opacity: 0 },
    ], { duration: 900 });
    return 900;
  }

  function swords(slot) {
    const t = centerOf(slot);
    for (let i = 0; i < 4; i++) {
      const s = el("fx-sword", t.x, t.y);
      const a0 = i * 90;
      run(s, [0, 0.25, 0.5, 0.75, 1].map((k) => {
        const a = ((a0 + k * 360) * Math.PI) / 180;
        return {
          transform: `translate(calc(-50% + ${Math.cos(a) * 42}px), calc(-50% + ${Math.sin(a) * 14 - 40 - k * 20}px)) rotate(${k === 1 ? 180 : 0}deg)`,
          opacity: k === 1 ? 0 : 1,
        };
      }), { duration: 1000, easing: "linear" });
    }
    K.glowSprite(slot.querySelector(".battle-sprite"), "#ff9a3c", 900);
    return 1000;
  }

  function powder(target, c) {
    for (let i = 0; i < 26; i++) {
      const p = particle("orb", target.x + rand(-45, 45), target.y - 70, rand(4, 7), c);
      run(p, [
        { transform: "translate(-50%,-50%)", opacity: 0 },
        { transform: `translate(calc(-50% + ${rand(-10, 10)}px), calc(-50% + 30px))`, opacity: 1, offset: 0.3 },
        { transform: `translate(calc(-50% + ${rand(-20, 20)}px), calc(-50% + ${rand(80, 110)}px))`, opacity: 0 },
      ], { duration: 900, delay: rand(0, 350), easing: "ease-in" });
    }
    return 1100;
  }

  function explosion(target) {
    screenFlash("#ffffff", 450, 0.9);
    shake(16, 750);
    K.flash(target, C.fire, 180);
    K.burst(target, { ...C.fire, shape: "flame" }, 22);
    at(120, () => pulse(target, C.fire, { count: 2, scale: 8, dur: 600 }));
    return 1100;
  }

  function glare(from, to, c = C.red) {
    const eye = particle("flash", from.x, from.y - 20, 34, c);
    run(eye, [{ transform: "translate(-50%,-50%) scale(0.3)", opacity: 0 }, { transform: "translate(-50%,-50%) scale(1)", opacity: 1, offset: 0.4 }, { transform: "translate(-50%,-50%) scale(0.6)", opacity: 0 }], { duration: 500 });
    at(250, () => soundWaves(from, to, c, { count: 2, dur: 700 }));
    return 950;
  }

  function selfAura(ctx, c, shape, o = {}) {
    K.glowSprite(ctx.atkSprite, c.glow, o.dur || 900);
    if (!K.reduceMotion) K.aura(ctx.from, { ...c, shape: shape || "orb" }, o.count || 12, o.up !== false);
    return o.dur || 900;
  }

  function heal(ctx) {
    K.glowSprite(ctx.atkSprite, C.heal.glow, 900);
    K.aura(ctx.from, { ...C.heal, shape: "star" }, 14, true);
    pulse(ctx.from, C.heal, { count: 2, scale: 3, dur: 600 });
    return 1000;
  }

  // ----------------------------------------------------------- recipes

  const T = (type) => C[type] || C.normal;

  const MOVES = {
    // Ground
    "Earthquake": (x) => { shake(15, 1000); dust(x.to); dust(x.from, undefined, { count: 5, spread: 60 }); bounce(x.tgtSprite, 800); K.rise(x.to, { ...T("ground"), shape: "rock" }); at(250, () => K.rise(x.to, { ...T("ground"), shape: "rock" })); return 1050; },
    "Magnitude": (x) => { shake(12, 850); dust(x.to, undefined, { count: 7 }); bounce(x.tgtSprite, 700); K.rise(x.to, { ...T("ground"), shape: "rock" }); return 900; },
    "Bulldoze": (x) => { shake(8, 650); dust(x.to, undefined, { count: 6, spread: 60 }); bounce(x.tgtSprite, 600); K.rise(x.to, { ...T("ground"), shape: "rock" }); return 750; },
    "Fissure": (x) => { shake(16, 1000); dust(x.to, T("dark"), { count: 8 }); erupt(x.to, T("dark"), { count: 7, gap: 14, width: 10, maxH: 110 }); return 1100; },
    "Earth Power": (x) => { shake(6, 500); return erupt(x.to, { color: "#ff7a2f", glow: "#ffd24a" }, { count: 4, gap: 26, width: 22, maxH: 100 }); },
    "Dig": (x) => { shake(8, 500); K.rise(x.to, { ...T("ground"), shape: "rock" }); return hit(x.to, T("ground"), { shake: 6 }) + 300; },
    "High Horsepower": (x) => heavy(x, T("ground"), { shake: 12 }),
    "Headlong Rush": (x) => heavy(x, T("ground"), { shake: 12, aura: true }),
    "Mud Slap": (x) => stream(x.from, x.to, C.mud, { count: 12, dur: 500, travel: 300 }),
    "Mud Bomb": (x) => { orb(x.from, x.to, C.mud, { arc: 60, size: 30 }); at(560, () => K.splash(x.to, { ...C.mud, shape: "orb" })); return 900; },
    "Sand Tomb": (x) => vortex(x.to, T("ground"), { shape: "orb", count: 22, min: 4, max: 8 }),

    // Water
    "Hydro Pump": (x) => { K.glowSprite(x.atkSprite, C.water.glow, 400); stream(x.from, x.to, C.water, { count: 42, dur: 900, travel: 320, spread: 14, max: 19 }); at(500, () => { K.splash(x.to, { ...C.water, shape: "bubble" }); shake(6, 350); }); return 1000; },
    "Water Gun": (x) => { stream(x.from, x.to, C.water, { count: 18, dur: 600, travel: 330, spread: 6 }); at(420, () => K.splash(x.to, { ...C.water, shape: "bubble" })); return 750; },
    "Surf": (x) => { wave(x.side, C.water); at(500, () => shake(6, 400)); return 1150; },
    "Muddy Water": (x) => { wave(x.side, C.mud); at(500, () => shake(6, 400)); return 1150; },
    "Wave Crash": (x) => { selfAura(x, C.water, "bubble", { dur: 300 }); at(250, () => heavy(x, C.water, { shake: 10 })); return 1000; },
    "Waterfall": (x) => { skyFall(x.to, C.water, { shape: "drop", count: 18, dur: 600, fallDur: 350, drift: 0 }); at(350, () => hit(x.to, C.water, { shake: 6 })); return 800; },
    "Aqua Tail": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => { slashes(x.to, C.water, { count: 1, length: 140, angle: -15 }); K.splash(x.to, { ...C.water, shape: "bubble" }); }); return 760; },
    "Liquidation": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => { hit(x.to, C.water, { shake: 5 }); K.splash(x.to, { ...C.water, shape: "bubble" }); }); return 760; },
    "Crabhammer": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => { hit(x.to, C.water, { size: 100, shake: 9 }); K.splash(x.to, { ...C.water, shape: "bubble" }); }); return 800; },
    "Razor Shell": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => slashes(x.to, C.water, { count: 2, cross: true })); return 800; },
    "Bubble Beam": (x) => stream(x.from, x.to, C.water, { shape: "bubble", count: 24, dur: 800, travel: 450, spread: 16 }),
    "Bubble": (x) => stream(x.from, x.to, C.water, { shape: "bubble", count: 12, dur: 700, travel: 500, spread: 18 }),
    "Brine": (x) => { stream(x.from, x.to, C.water, { count: 22, dur: 700, travel: 330 }); return 800; },
    "Whirlpool": (x) => vortex(x.to, C.water, { shape: "bubble", count: 20, radius: 50 }),
    "Water Pulse": (x) => { orb(x.from, x.to, C.water, { size: 26 }); at(560, () => pulse(x.to, C.water, { count: 3, scale: 3 })); return 1000; },
    "Aqua Ring": (x) => selfAura(x, C.water, "bubble"),
    "Soak": (x) => { stream(x.from, x.to, C.water, { count: 16, dur: 600, travel: 360 }); return 750; },
    "Rain Dance": () => weather("rain"),

    // Fire
    "Flamethrower": (x) => { stream(x.from, x.to, C.fire, { shape: "flame", count: 36, dur: 900, travel: 360, spread: 12, max: 18 }); at(500, () => K.burst(x.to, { ...C.fire, shape: "flame" }, 10)); return 1000; },
    "Ember": (x) => { stream(x.from, x.to, C.fire, { shape: "flame", count: 8, dur: 550, travel: 380, spread: 14 }); return 650; },
    "Fire Blast": (x) => { orb(x.from, x.to, C.fire, { size: 34, dur: 450 }); at(460, () => { for (let i = 0; i < 5; i++) { const a = (i / 5) * Math.PI * 2 - Math.PI / 2; beam(x.to, { x: x.to.x + Math.cos(a) * 70, y: x.to.y + Math.sin(a) * 70 }, C.fire, { width: 20, dur: 600 }); } shake(8, 450); }); return 1100; },
    "Fire Spin": (x) => vortex(x.to, C.fire, { shape: "flame", count: 20 }),
    "Inferno": (x) => { orb(x.from, x.to, C.fire, { size: 30 }); at(520, () => vortex(x.to, C.fire, { shape: "flame", count: 22, radius: 50 })); return 1450; },
    "Heat Wave": (x) => { screenTint("#ff6a1f", 1000, 0.35); for (let i = 0; i < 3; i++) at(i * 180, () => soundWaves(x.from, x.to, C.fire, { count: 2, dur: 650 })); at(600, () => K.burst(x.to, { ...C.fire, shape: "flame" }, 10)); return 1100; },
    "Eruption": (x) => { shake(10, 600); pulse(x.from, C.fire, { count: 1, scale: 3 }); at(250, () => skyFall(x.to, C.fire, { shape: "rock", count: 12, dur: 900 })); return 1200; },
    "Lava Plume": (x) => { pulse(x.from, C.fire, { count: 3, scale: 9, thick: true }); at(300, () => K.burst(x.to, { ...C.fire, shape: "flame" }, 12)); return 1100; },
    "Overheat": (x) => { screenTint("#ff3b00", 900, 0.4); K.glowSprite(x.atkSprite, C.fire.glow, 500); at(200, () => beam(x.from, x.to, C.fire, { width: 34, dur: 700 })); at(500, () => shake(10, 450)); return 1000; },
    "Flare Blitz": (x) => { selfAura(x, C.fire, "flame", { dur: 350, count: 10 }); at(300, () => heavy(x, C.fire, { shake: 12 })); at(560, () => K.burst(x.to, { ...C.fire, shape: "flame" }, 16)); return 1100; },
    "Burn Up": (x) => { selfAura(x, C.fire, "flame", { dur: 400 }); at(300, () => beam(x.from, x.to, C.fire, { width: 30 })); return 1000; },
    "Fire Punch": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => { hit(x.to, C.fire); K.burst(x.to, { ...C.fire, shape: "flame" }, 10); }); return 760; },
    "Blaze Kick": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => { hit(x.to, C.fire, { size: 80 }); K.burst(x.to, { ...C.fire, shape: "flame" }, 12); }); return 760; },
    "Fire Fang": (x) => { jaws(x.to, C.fire); at(300, () => K.burst(x.to, { ...C.fire, shape: "flame" }, 8)); return 700; },
    "Sunny Day": () => weather("sun"),
    "Will O Wisp": (x) => { for (let i = 0; i < 3; i++) at(i * 120, () => orb(x.from, { ...x.to, y: x.to.y + rand(-20, 20) }, { color: "#5a7bff", glow: "#c8d4ff" }, { size: 22, arc: 40, dur: 700 })); return 1100; },

    // Electric
    "Thunderbolt": (x) => { K.glowSprite(x.atkSprite, C.electric.glow, 400); lightning(x.from, x.to, C.electric, { dur: 620 }); at(200, () => lightning(x.from, x.to, C.electric, { dur: 420, width: 3 })); at(350, () => K.burst(x.to, { ...C.electric, shape: "spark" }, 12)); return 850; },
    "Thunder": (x) => { screenFlash("#fffbd0", 300, 0.8); lightning({ x: x.to.x + 30, y: -20 }, x.to, C.electric, { dur: 650, width: 9, segments: 7 }); at(200, () => { shake(10, 450); K.burst(x.to, { ...C.electric, shape: "spark" }, 16); }); return 950; },
    "Thunder Shock": (x) => { lightning(x.from, x.to, C.electric, { dur: 420, width: 3 }); at(250, () => K.burst(x.to, { ...C.electric, shape: "spark" }, 8)); return 600; },
    "Discharge": (x) => { pulse(x.from, C.electric, { count: 3, scale: 8 }); for (let i = 0; i < 3; i++) at(i * 120, () => lightning(x.from, { x: x.to.x + rand(-40, 40), y: x.to.y + rand(-30, 30) }, C.electric, { dur: 380, width: 3 })); return 1000; },
    "Zap Cannon": (x) => { orb(x.from, x.to, C.electric, { size: 44, dur: 700, trail: true }); at(700, () => { lightning({ x: x.to.x - 50, y: x.to.y - 40 }, { x: x.to.x + 50, y: x.to.y + 40 }, C.electric, { dur: 400 }); shake(8, 400); }); return 1150; },
    "Thunder Punch": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => { hit(x.to, C.electric); lightning({ x: x.to.x - 40, y: x.to.y - 50 }, { x: x.to.x + 30, y: x.to.y + 30 }, C.electric, { dur: 350 }); }); return 800; },
    "Thunder Fang": (x) => { jaws(x.to, C.electric); at(300, () => lightning({ x: x.to.x, y: x.to.y - 50 }, x.to, C.electric, { dur: 350 })); return 700; },
    "Wild Charge": (x) => { selfAura(x, C.electric, "spark", { dur: 350 }); at(300, () => heavy(x, C.electric, { shake: 10 })); at(550, () => lightning({ x: x.to.x - 40, y: x.to.y - 40 }, { x: x.to.x + 40, y: x.to.y + 40 }, C.electric, { dur: 350 })); return 1100; },
    "Spark": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => K.burst(x.to, { ...C.electric, shape: "spark" }, 10)); return 760; },
    "Thunder Wave": (x) => { lightning(x.from, x.to, C.electric, { dur: 450, width: 2 }); at(350, () => pulse(x.to, C.electric, { count: 3, scale: 2.5 })); return 900; },
    "Charge": (x) => selfAura(x, C.electric, "spark"),
    "Electric Terrain": () => { screenTint("#ffe14a", 1100, 0.35); return 1100; },

    // Ice
    "Ice Beam": (x) => { beam(x.from, x.to, C.ice, { width: 14, dur: 700, style: "rings" }); at(450, () => K.burst(x.to, { ...C.ice, shape: "shard" }, 12)); return 950; },
    "Blizzard": (x) => { weather("snow", { dur: 1100 }); stream(x.from, x.to, C.ice, { shape: "shard", count: 30, dur: 1000, travel: 420, spread: 40 }); return 1150; },
    "Freeze Dry": (x) => { beam(x.from, x.to, C.ice, { width: 10, dur: 600 }); at(400, () => K.burst(x.to, { ...C.ice, shape: "shard" }, 14)); return 900; },
    "Icicle Crash": (x) => { skyFall(x.to, C.ice, { shape: "shard", count: 8, dur: 700, fallDur: 380, drift: 0, min: 16, max: 24 }); at(500, () => shake(8, 350)); return 900; },
    "Icicle Spear": (x) => { for (let i = 0; i < 4; i++) at(i * 140, () => orb(x.from, x.to, { ...C.ice }, { shape: "shard", size: 22, dur: 300 })); return 900; },
    "Ice Shard": (x) => { orb(x.from, x.to, C.ice, { shape: "shard", size: 26, dur: 280 }); at(280, () => K.burst(x.to, { ...C.ice, shape: "shard" }, 8)); return 500; },
    "Ice Punch": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => { hit(x.to, C.ice); K.burst(x.to, { ...C.ice, shape: "shard" }, 10); }); return 760; },
    "Ice Fang": (x) => { jaws(x.to, C.ice); at(300, () => K.burst(x.to, { ...C.ice, shape: "shard" }, 8)); return 700; },
    "Icy Wind": (x) => { stream(x.from, x.to, C.ice, { shape: "orb", count: 26, dur: 800, travel: 450, spread: 30, min: 4, max: 8 }); return 900; },
    "Aurora Beam": (x) => { beam(x.from, x.to, { color: "#9f8cff", glow: "#c8f6ff" }, { style: "rainbow", width: 16, dur: 700 }); return 850; },
    "Sheer Cold": (x) => { screenTint("#9ae6ff", 1000, 0.45); at(300, () => K.burst(x.to, { ...C.ice, shape: "shard" }, 20)); return 1050; },
    "Hail": () => weather("hail"),
    "Mist": (x) => { K.aura(x.from, { ...C.white, shape: "wisp" }, 14, true); return 900; },

    // Grass
    "Solar Beam": (x) => { K.glowSprite(x.atkSprite, "#fff6b0", 500); K.chargeGlow(x.from, C.gold); at(250, () => beam(x.from, x.to, { color: "#fff27a", glow: "#ffffff" }, { width: 32, dur: 750 })); at(550, () => shake(7, 400)); return 1100; },
    "Energy Ball": (x) => { orb(x.from, x.to, C.grass, { size: 40, dur: 560, trail: true }); at(560, () => K.burst(x.to, { ...C.grass, shape: "orb" }, 10)); return 900; },
    "Leaf Blade": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => slashes(x.to, C.grass, { count: 1, length: 150, angle: -40 })); return 800; },
    "Leaf Storm": (x) => { stream(x.from, x.to, C.grass, { shape: "leaf", count: 30, dur: 700, travel: 400, spread: 30 }); at(450, () => vortex(x.to, C.grass, { shape: "leaf", count: 18 })); return 1400; },
    "Leaf Tornado": (x) => vortex(x.to, C.grass, { shape: "leaf", count: 20, radius: 46 }),
    "Petal Blizzard": (x) => { weather("snow", { dur: 900 }); vortex(x.to, C.fairy, { shape: "leaf", count: 22, radius: 60 }); return 1000; },
    "Petal Dance": (x) => { vortex(x.from, C.fairy, { shape: "leaf", count: 14, dur: 600 }); at(500, () => { K.lunge(x.atk, x.from, x.to); stream(x.from, x.to, C.fairy, { shape: "leaf", count: 20, dur: 500, travel: 350, spread: 20 }); }); return 1200; },
    "Razor Leaf": (x) => { for (let i = 0; i < 6; i++) at(i * 70, () => orb(x.from, { ...x.to, x: x.to.x + rand(-20, 20) }, C.grass, { shape: "leaf", size: 20, dur: 380, spin: 720 })); return 850; },
    "Magical Leaf": (x) => stream(x.from, x.to, C.grass, { shape: "leaf", count: 14, dur: 700, travel: 450, spread: 24 }),
    "Seed Bomb": (x) => { for (let i = 0; i < 4; i++) at(i * 90, () => orb(x.from, { ...x.to, x: x.to.x + rand(-15, 15) }, { color: "#7a5a2a", glow: "#c8a060" }, { size: 16, arc: 70, dur: 500 })); at(650, () => K.burst(x.to, C.grass, 12)); return 1000; },
    "Bullet Seed": (x) => { for (let i = 0; i < 6; i++) at(i * 70, () => orb(x.from, x.to, { color: "#b89a3a", glow: "#f0e0a0" }, { size: 12, dur: 300 })); return 800; },
    "Power Whip": (x) => slashes(x.to, C.grass, { count: 2, length: 170, angle: -20, interval: 160 }) + 100,
    "Vine Whip": (x) => slashes(x.to, C.grass, { count: 2, length: 120, angle: -30, interval: 140 }),
    "Wood Hammer": (x) => heavy(x, { color: "#9a6a30", glow: "#e0b070" }, { shake: 12, size: 100 }),
    "Giga Drain": (x) => drain(x.to, x.from),
    "Mega Drain": (x) => drain(x.to, x.from),
    "Absorb": (x) => drain(x.to, x.from),
    "Leech Life": (x) => { hit(x.to, C.bug, { size: 50 }); at(200, () => drain(x.to, x.from, { color: "#d04040", glow: "#ffb0b0" })); return 1300; },
    "Dream Eater": (x) => { distort(x.tgtSprite, 600); at(300, () => drain(x.to, x.from, C.psychic)); return 1400; },
    "Drain Punch": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => hit(x.to, C.fighting)); at(450, () => drain(x.to, x.from)); return 1500; },
    "Horn Leech": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => hit(x.to, C.grass)); at(450, () => drain(x.to, x.from)); return 1500; },
    "Leech Seed": (x) => { for (let i = 0; i < 3; i++) at(i * 110, () => orb(x.from, { ...x.to, x: x.to.x + rand(-20, 20) }, { color: "#8a6a2a", glow: "#e0c070" }, { size: 12, arc: 80, dur: 550 })); at(750, () => K.aura(x.to, { ...C.grass, shape: "leaf" }, 8, true)); return 1200; },
    "Sleep Powder": (x) => powder(x.to, { color: "#6fd06f", glow: "#d8ffd0" }),
    "Stun Spore": (x) => powder(x.to, { color: "#f0d040", glow: "#fff4b0" }),
    "Poison Powder": (x) => powder(x.to, { color: "#b060e0", glow: "#e8c0ff" }),
    "Spore": (x) => powder(x.to, { color: "#c09060", glow: "#f0dcc0" }),
    "Cotton Spore": (x) => powder(x.to, C.white),
    "Worry Seed": (x) => { orb(x.from, x.to, { color: "#8a6a2a", glow: "#e0c070" }, { size: 14, arc: 70 }); return 700; },
    "Synthesis": heal, "Morning Sun": heal, "Moonlight": heal,
    "Ingrain": (x) => { erupt({ ...x.from, y: x.from.y + 10 }, C.grass, { count: 4, width: 6, minH: 20, maxH: 40 }); return selfAura(x, C.grass, "leaf"); },
    "Aromatherapy": (x) => { K.aura(x.from, { ...C.fairy, shape: "leaf" }, 16, true); return 1000; },
    "Growth": (x) => selfAura(x, C.grass, "leaf"),

    // Psychic
    "Psychic": (x) => { screenTint("#ff4fa0", 1000, 0.4); distort(x.tgtSprite, 900); pulse(x.to, C.psychic, { count: 3, scale: 2.5 }); return 1000; },
    "Psybeam": (x) => { beam(x.from, x.to, C.psychic, { style: "rainbow", width: 18, dur: 750 }); at(450, () => distort(x.tgtSprite, 500)); return 900; },
    "Psycho Cut": (x) => { orb(x.from, x.to, C.psychic, { shape: "slash", size: 40, dur: 350 }); at(350, () => slashes(x.to, C.psychic, { count: 1, length: 130 })); return 750; },
    "Extrasensory": (x) => { screenFlash("#ff9fd0", 400, 0.5); at(150, () => pulse(x.to, C.psychic, { count: 2, scale: 3 })); at(200, () => distort(x.tgtSprite, 500)); return 800; },
    "Zen Headbutt": (x) => { selfAura(x, C.psychic, "orb", { dur: 300, count: 6 }); at(250, () => heavy(x, C.psychic, { shake: 7 })); return 1000; },
    "Future Sight": (x) => { orb(x.from, { x: x.from.x, y: -40 }, C.psychic, { size: 30, dur: 500 }); at(500, () => screenTint("#ff5fa8", 500, 0.3)); return 950; },
    "Synchronoise": (x) => { pulse(x.from, C.psychic, { count: 4, scale: 9 }); return 1100; },
    "Stored Power": (x) => { for (let i = 0; i < 5; i++) at(i * 80, () => orb({ x: x.from.x + rand(-40, 40), y: x.from.y - 40 }, x.to, C.psychic, { size: 18, dur: 400 })); return 900; },
    "Hypnosis": (x) => { pulse(x.from, C.psychic, { count: 5, scale: 4, dur: 900 }); at(500, () => distort(x.tgtSprite, 500)); return 1300; },
    "Calm Mind": (x) => { pulse(x.from, C.psychic, { count: 2, scale: 2 }); return selfAura(x, C.psychic, "orb"); },
    "Amnesia": (x) => { K.floatText(x.atk, "?", "info"); return selfAura(x, C.psychic, "orb"); },
    "Barrier": (x) => shield(x.atk, C.psychic),
    "Light Screen": (x) => shield(x.atk, { color: "#ffe25a", glow: "#fff7c0" }),
    "Reflect": (x) => shield(x.atk, { color: "#6fb8ff", glow: "#d8ecff" }),
    "Heal Pulse": (x) => { pulse(x.to, C.heal, { count: 3, scale: 3 }); return 900; },
    "Trick": (x) => { orb(x.from, x.to, C.white, { size: 16, arc: 60, dur: 500 }); at(250, () => orb(x.to, x.from, C.gold, { size: 16, arc: 60, dur: 500 })); return 900; },
    "Psych Up": (x) => selfAura(x, C.psychic, "star"),
    "Power Swap": (x) => { orb(x.from, x.to, C.red, { size: 18, arc: 50 }); at(250, () => orb(x.to, x.from, C.red, { size: 18, arc: 50 })); return 900; },
    "Guard Swap": (x) => { orb(x.from, x.to, C.water, { size: 18, arc: 50 }); at(250, () => orb(x.to, x.from, C.water, { size: 18, arc: 50 })); return 900; },
    "Rest": (x) => { K.floatText(x.atk, "z z z", "info"); return heal(x); },
    "Teeter Dance": (x) => { distort(x.atkSprite, 800); distort(x.tgtSprite, 800); return 900; },

    // Ghost
    "Shadow Ball": (x) => { K.chargeGlow(x.from, C.ghost); orb(x.from, x.to, C.ghost, { shape: "wisp", size: 46, dur: 600, trail: true }); at(600, () => { K.flash(x.to, C.ghost, 90); pulse(x.to, C.ghost, { count: 2, scale: 2.5 }); }); return 1000; },
    "Hex": (x) => { screenTint("#3b1f6a", 800, 0.5); at(200, () => pulse(x.to, C.ghost, { count: 3, scale: 2.5 })); return 900; },
    "Phantom Force": (x) => { screenTint("#1f1236", 600, 0.5); at(250, () => hit(x.to, C.ghost, { size: 90, shake: 8 })); return 800; },
    "Ominous Wind": (x) => stream(x.from, x.to, C.ghost, { shape: "wisp", count: 20, dur: 800, travel: 450, spread: 26 }),
    "Shadow Claw": (x) => slashes(x.to, C.ghost, { count: 3 }),
    "Shadow Punch": (x) => { orb(x.from, x.to, C.ghost, { shape: "wisp", size: 30, dur: 350 }); at(350, () => hit(x.to, C.ghost)); return 750; },
    "Shadow Sneak": (x) => { orb({ x: x.from.x, y: x.from.y + 30 }, { x: x.to.x, y: x.to.y + 30 }, C.ghost, { shape: "wisp", size: 34, dur: 380 }); at(380, () => hit(x.to, C.ghost)); return 750; },
    "Night Shade": (x) => { screenTint("#240f40", 700, 0.5); at(200, () => beam(x.from, x.to, C.ghost, { width: 18 })); return 900; },
    "Confuse Ray": (x) => { orb(x.from, x.to, { color: "#ffe05a", glow: "#fff8c0" }, { size: 24, arc: 50, dur: 700 }); at(700, () => distort(x.tgtSprite, 400)); return 1100; },
    "Curse": (x) => { screenTint("#1a0f24", 800, 0.5); return selfAura(x, C.ghost, "wisp"); },
    "Destiny Bond": (x) => { screenTint("#2a1040", 900, 0.5); pulse(x.from, C.ghost, { count: 3, scale: 3 }); return 1000; },
    "Spite": (x) => { K.glowSprite(x.tgtSprite, C.ghost.glow, 700); return 800; },
    "Lick": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => hit(x.to, { color: "#ff7ab0", glow: "#ffd0e4" }, { size: 50 })); return 700; },

    // Dark
    "Dark Pulse": (x) => { pulse(x.from, C.dark, { count: 4, scale: 10, thick: true, dur: 800 }); return 1200; },
    "Crunch": (x) => jaws(x.to, C.white),
    "Bite": (x) => jaws(x.to, C.white),
    "Poison Fang": (x) => { jaws(x.to, C.white); at(300, () => K.aura(x.to, { ...C.poison, shape: "bubble" }, 6, true)); return 700; },
    "Night Slash": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => slashes(x.to, { color: "#4a2a6a", glow: "#c090ff" }, { count: 1, length: 160, angle: -30 })); return 800; },
    "Sucker Punch": (x) => dash(x, C.dark),
    "Feint Attack": (x) => { K.glowSprite(x.atkSprite, "#000", 300); at(200, () => dash(x, C.dark)); return 750; },
    "Assurance": (x) => dash(x, C.dark),
    "Foul Play": (x) => heavy(x, C.dark, { shake: 8 }),
    "Throat Chop": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => slashes(x.to, C.dark, { count: 1, length: 120, angle: 0 })); return 760; },
    "Payback": (x) => heavy(x, C.dark, { shake: 7 }),
    "Knock Off": (x) => heavy(x, C.dark, { shake: 6 }),
    "Nasty Plot": (x) => { screenTint("#1a0f24", 700, 0.4); return selfAura(x, C.dark, "wisp"); },
    "Taunt": (x) => { K.floatText(x.atk, "!!", "info"); return 700; },
    "Embargo": (x) => { pulse(x.to, C.dark, { count: 2, scale: 2 }); return 700; },
    "Quash": (x) => { pulse(x.to, C.dark, { count: 2, scale: 2 }); return 700; },

    // Dragon
    "Dragon Pulse": (x) => { beam(x.from, x.to, C.dragon, { width: 22, dur: 700, style: "rings" }); at(450, () => pulse(x.to, C.dragon, { count: 2, scale: 2.5 })); return 950; },
    "Draco Meteor": (x) => { orb(x.from, { x: x.from.x, y: -60 }, C.fire, { size: 34, dur: 400 }); at(400, () => { skyFall(x.to, { color: "#ff7a2f", glow: "#ffe0a0" }, { shape: "orb", count: 8, dur: 800, min: 18, max: 26, drift: -60 }); }); at(800, () => shake(12, 600)); return 1400; },
    "Outrage": (x) => { selfAura(x, C.red, "flame", { dur: 400, count: 10 }); at(300, () => { K.lunge(x.atk, x.from, x.to); hits(x.to, C.dragon, 3, 140, { shake: 5 }); }); return 1200; },
    "Thrash": (x) => { selfAura(x, C.red, "orb", { dur: 300 }); at(250, () => { K.lunge(x.atk, x.from, x.to); hits(x.to, C.normal, 3, 140, { shake: 4 }); }); return 1100; },
    "Raging Fury": (x) => { selfAura(x, C.fire, "flame", { dur: 400 }); at(300, () => { K.lunge(x.atk, x.from, x.to); hits(x.to, C.fire, 3, 140, { shake: 5 }); }); return 1200; },
    "Dragon Rush": (x) => { selfAura(x, C.dragon, "orb", { dur: 350 }); at(300, () => heavy(x, C.dragon, { shake: 12, size: 100 })); return 1100; },
    "Dragon Claw": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => slashes(x.to, C.dragon, { count: 3 })); return 900; },
    "Dragon Tail": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => slashes(x.to, C.dragon, { count: 1, length: 160, angle: -10 })); return 800; },
    "Dragon Breath": (x) => stream(x.from, x.to, C.dragon, { count: 26, dur: 800, travel: 380, spread: 16 }),
    "Dragon Rage": (x) => { orb(x.from, x.to, C.fire, { size: 30, dur: 450 }); at(450, () => vortex(x.to, C.fire, { shape: "flame", count: 14 })); return 1300; },
    "Twister": (x) => vortex(x.to, C.dragon, { shape: "slash", count: 16 }),
    "Dragon Dance": (x) => { selfAura(x, C.dragon, "flame", { dur: 1000, count: 14 }); return 1000; },

    // Poison
    "Sludge Bomb": (x) => { orb(x.from, x.to, C.sludge, { size: 38, arc: 90, dur: 620 }); at(620, () => K.splash(x.to, { ...C.sludge, shape: "bubble" })); return 1000; },
    "Sludge Wave": (x) => { wave(x.side, C.sludge); return 1150; },
    "Sludge": (x) => { orb(x.from, x.to, C.sludge, { size: 26, arc: 60 }); at(560, () => K.splash(x.to, { ...C.sludge, shape: "bubble" })); return 900; },
    "Gunk Shot": (x) => { stream(x.from, x.to, C.sludge, { count: 30, dur: 700, travel: 320, spread: 12, max: 20 }); at(450, () => { K.splash(x.to, { ...C.sludge, shape: "bubble" }); shake(7, 350); }); return 950; },
    "Poison Jab": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => { hit(x.to, C.poison, { size: 50 }); K.aura(x.to, { ...C.poison, shape: "bubble" }, 6, true); }); return 800; },
    "Cross Poison": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => slashes(x.to, C.poison, { count: 2, cross: true, spacing: 0 })); return 800; },
    "Poison Sting": (x) => { orb(x.from, x.to, C.poison, { shape: "shard", size: 16, dur: 300 }); return 500; },
    "Acid": (x) => stream(x.from, x.to, C.sludge, { shape: "bubble", count: 12, dur: 650, travel: 420, spread: 16 }),
    "Venoshock": (x) => { stream(x.from, x.to, C.sludge, { count: 20, dur: 650, travel: 330 }); at(450, () => pulse(x.to, C.poison, { count: 2, scale: 2 })); return 900; },
    "Toxic": (x) => { orb(x.from, x.to, C.sludge, { size: 22, arc: 70 }); at(560, () => K.aura(x.to, { ...C.sludge, shape: "bubble" }, 12, true)); return 1100; },
    "Belch": (x) => { soundWaves(x.from, x.to, C.sludge, { count: 4, dur: 700 }); at(500, () => shake(6, 300)); return 800; },
    "Toxic Thread": (x) => { beam(x.from, x.to, C.poison, { width: 4, dur: 700 }); return 800; },
    "Acid Armor": (x) => selfAura(x, C.sludge, "bubble"),

    // Fighting
    "Close Combat": (x) => { K.lunge(x.atk, x.from, x.to); hits(x.to, C.fighting, 5, 110, { size: 55, sparks: 4 }); at(500, () => shake(6, 300)); return 1000; },
    "Cross Chop": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => slashes(x.to, C.white, { count: 2, cross: true, spacing: 0, length: 140 })); at(350, () => shake(6, 300)); return 850; },
    "Dynamic Punch": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => { hit(x.to, C.fighting, { size: 120, shake: 12, sparks: 14 }); pulse(x.to, C.fighting, { count: 2, scale: 3 }); }); return 1000; },
    "Focus Punch": (x) => { selfAura(x, C.fighting, "orb", { dur: 400 }); at(350, () => heavy(x, C.fighting, { shake: 12, size: 110 })); return 1150; },
    "Focus Blast": (x) => { K.chargeGlow(x.from, C.fighting); orb(x.from, x.to, { color: "#ffb040", glow: "#fff2c0" }, { size: 50, dur: 600, trail: true }); at(600, () => { hit(x.to, C.fighting, { size: 110, shake: 10 }); }); return 1050; },
    "Aura Sphere": (x) => { K.chargeGlow(x.from, { color: "#3f8cff", glow: "#cfe4ff" }); orb(x.from, x.to, { color: "#3f8cff", glow: "#cfe4ff" }, { size: 42, dur: 520, trail: true }); at(520, () => pulse(x.to, { color: "#3f8cff", glow: "#cfe4ff" }, { count: 2, scale: 3 })); return 950; },
    "Hammer Arm": (x) => heavy(x, C.fighting, { shake: 12, size: 100 }),
    "High Jump Kick": (x) => { x.atk.animate([{ transform: "translate(0,0)" }, { transform: "translate(0,-70px)", offset: 0.4 }, { transform: `translate(${(x.to.x - x.from.x) * 0.7}px, ${(x.to.y - x.from.y) * 0.7}px)`, offset: 0.7 }, { transform: "translate(0,0)" }], { duration: 750 }); at(520, () => hit(x.to, C.fighting, { size: 100, shake: 10 })); return 950; },
    "Low Kick": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => hit({ ...x.to, y: x.to.y + 30 }, C.fighting, { size: 70 })); return 760; },
    "Triple Kick": (x) => { K.lunge(x.atk, x.from, x.to); return hits(x.to, C.fighting, 3, 170); },
    "Sky Uppercut": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => { hit(x.to, C.fighting, { size: 90 }); beam({ x: x.to.x, y: x.to.y + 40 }, { x: x.to.x + 10, y: x.to.y - 80 }, C.white, { width: 8, dur: 350 }); }); return 850; },
    "Superpower": (x) => { selfAura(x, C.fighting, "orb", { dur: 350 }); at(300, () => heavy(x, C.fighting, { shake: 12, size: 110 })); return 1100; },
    "Bulk Up": (x) => selfAura(x, C.fighting, "orb"),
    "Reversal": (x) => { selfAura(x, C.red, "orb", { dur: 300 }); at(250, () => heavy(x, C.fighting, { shake: 9 })); return 1000; },
    "Counter": (x) => { shield(x.atk, C.fighting); at(400, () => heavy(x, C.fighting, { shake: 9 })); return 1200; },
    "Axe Kick": (x) => { x.atk.animate([{ transform: "translate(0,0)" }, { transform: "translate(0,-50px)", offset: 0.35 }, { transform: `translate(${(x.to.x - x.from.x) * 0.7}px, ${(x.to.y - x.from.y) * 0.7}px)`, offset: 0.65 }, { transform: "translate(0,0)" }], { duration: 700 }); at(470, () => hit(x.to, C.fighting, { size: 90, shake: 9 })); return 900; },
    "Brick Break": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => { slashes(x.to, C.white, { count: 1, angle: 90, length: 90 }); hit(x.to, C.fighting); }); return 800; },
    "Wake Up Slap": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => hits(x.to, C.normal, 2, 120, { size: 50 })); return 800; },
    "Mach Punch": (x) => dash(x, C.fighting),
    "Vacuum Wave": (x) => { orb(x.from, x.to, { color: "#b8e6ff", glow: "#ffffff" }, { size: 30, dur: 300 }); at(300, () => pulse(x.to, C.white, { count: 2, scale: 2 })); return 700; },
    "Detect": (x) => shield(x.atk, C.fighting),

    // Steel
    "Flash Cannon": (x) => { K.chargeGlow(x.from, C.steel); at(150, () => beam(x.from, x.to, C.steel, { width: 26, dur: 700 })); at(450, () => { screenFlash("#ffffff", 250, 0.4); shake(6, 300); }); return 1000; },
    "Iron Head": (x) => { K.glowSprite(x.atkSprite, "#ffffff", 400); at(150, () => heavy(x, C.steel, { shake: 9 })); return 950; },
    "Iron Tail": (x) => { K.glowSprite(x.atkSprite, "#ffffff", 400); K.lunge(x.atk, x.from, x.to); at(250, () => { slashes(x.to, C.steel, { count: 1, length: 150, angle: -20 }); shake(6, 300); }); return 850; },
    "Heavy Slam": (x) => { x.atk.animate([{ transform: "translate(0,0)" }, { transform: "translate(0,-80px)", offset: 0.4 }, { transform: `translate(${(x.to.x - x.from.x) * 0.75}px, ${(x.to.y - x.from.y) * 0.75}px)`, offset: 0.7 }, { transform: "translate(0,0)" }], { duration: 800 }); at(560, () => hit(x.to, C.steel, { size: 120, shake: 16, sparks: 12 })); return 1000; },
    "Gyro Ball": (x) => { x.atkSprite && x.atkSprite.animate([{ transform: "rotate(0)" }, { transform: "rotate(720deg)" }], { duration: 600 }); at(300, () => heavy(x, C.steel, { shake: 8 })); return 1050; },
    "Meteor Mash": (x) => { orb(x.from, x.to, C.gold, { shape: "star", size: 40, dur: 450 }); at(450, () => hit(x.to, C.steel, { size: 110, shake: 10 })); return 900; },
    "Mirror Shot": (x) => { screenFlash("#e8f4ff", 300, 0.5); at(150, () => beam(x.from, x.to, C.steel, { width: 14 })); return 850; },
    "Metal Claw": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => slashes(x.to, C.steel, { count: 3 })); return 900; },
    "Steel Wing": (x) => { K.glowSprite(x.atkSprite, "#ffffff", 400); at(150, () => { K.lunge(x.atk, x.from, x.to); at(250, () => slashes(x.to, C.steel, { count: 2, length: 140 })); }); return 1000; },
    "Bullet Punch": (x) => dash(x, C.steel),
    "Metal Burst": (x) => { shield(x.atk, C.steel); at(400, () => beam(x.from, x.to, C.steel, { width: 20 })); return 1150; },
    "Iron Defense": (x) => { K.glowSprite(x.atkSprite, "#ffffff", 900); for (let i = 0; i < 3; i++) at(i * 200, () => K.flash(x.from, C.steel, 110)); return 1000; },
    "Autotomize": (x) => { K.aura(x.from, { ...C.steel, shape: "shard" }, 10, false); return 900; },
    "Metal Sound": (x) => soundWaves(x.from, x.to, C.steel, { count: 5 }),
    "Magnet Rise": (x) => { x.atk.animate([{ transform: "translateY(0)" }, { transform: "translateY(-16px)" }, { transform: "translateY(0)" }], { duration: 900 }); return selfAura(x, C.electric, "spark"); },

    // Rock
    "Rock Slide": (x) => { skyFall(x.to, T("rock"), { count: 10, dur: 850, min: 16, max: 26 }); at(600, () => shake(8, 350)); return 1000; },
    "Stone Edge": (x) => { erupt(x.to, { color: "#8f8a80", glow: "#e8e4dc" }, { count: 5, gap: 18, width: 20, maxH: 100 }); at(250, () => shake(8, 350)); return 950; },
    "Rock Wrecker": (x) => { orb(x.from, x.to, T("rock"), { shape: "rock", size: 56, dur: 600, spin: 360 }); at(600, () => { hit(x.to, T("rock"), { size: 130, shake: 16 }); }); return 1150; },
    "Rock Blast": (x) => { for (let i = 0; i < 3; i++) at(i * 180, () => orb(x.from, { ...x.to, x: x.to.x + rand(-15, 15) }, T("rock"), { shape: "rock", size: 28, dur: 320, spin: 360 })); at(700, () => shake(5, 250)); return 950; },
    "Rock Throw": (x) => { orb(x.from, x.to, T("rock"), { shape: "rock", size: 30, arc: 60, dur: 500 }); at(500, () => shake(5, 250)); return 800; },
    "Rock Tomb": (x) => { skyFall(x.to, T("rock"), { count: 5, dur: 600, drift: 0, min: 20, max: 28 }); return 800; },
    "Power Gem": (x) => { beam(x.from, x.to, { color: "#ff5f9f", glow: "#ffe0f0" }, { width: 16, dur: 650 }); at(400, () => K.burst(x.to, { color: "#ffd24a", glow: "#fff6c0", shape: "shard" }, 12)); return 900; },
    "Ancient Power": (x) => { K.aura(x.from, { ...T("rock"), shape: "rock" }, 6, true); at(450, () => { for (let i = 0; i < 4; i++) at(i * 70, () => orb({ x: x.from.x + rand(-40, 40), y: x.from.y - 40 }, x.to, T("rock"), { shape: "rock", size: 20, dur: 380 })); }); return 1150; },
    "Head Smash": (x) => { heavy(x, T("rock"), { shake: 16, size: 130 }); at(350, () => K.burst(x.to, { ...T("rock"), shape: "rock" }, 14)); return 1000; },
    "Stealth Rock": (x) => { for (let i = 0; i < 5; i++) at(i * 90, () => orb(x.from, { x: x.to.x + rand(-60, 60), y: x.to.y + rand(10, 40) }, T("rock"), { shape: "shard", size: 16, arc: 60, dur: 500 })); return 1000; },
    "Sandstorm": () => weather("sand"),

    // Flying
    "Air Slash": (x) => { for (let i = 0; i < 3; i++) at(i * 110, () => orb(x.from, { ...x.to, y: x.to.y + (i - 1) * 16 }, C.flying, { shape: "slash", size: 44, dur: 330 })); at(420, () => slashes(x.to, C.white, { count: 2 })); return 900; },
    "Aerial Ace": (x) => { dash(x, C.flying); at(250, () => slashes(x.to, C.white, { count: 1, length: 150 })); return 750; },
    "Brave Bird": (x) => { selfAura(x, { color: "#6fa0ff", glow: "#dce8ff" }, "orb", { dur: 350 }); at(300, () => heavy(x, { color: "#6fa0ff", glow: "#ffffff" }, { shake: 13, size: 110 })); return 1100; },
    "Sky Attack": (x) => { K.glowSprite(x.atkSprite, "#ffd56a", 500); at(300, () => dash(x, C.gold, { size: 110 })); at(450, () => shake(10, 400)); return 1000; },
    "Fly": (x) => { orb({ x: x.to.x, y: -30 }, x.to, C.flying, { size: 50, dur: 350 }); at(350, () => hit(x.to, C.flying, { size: 90, shake: 8 })); return 800; },
    "Bounce": (x) => { orb({ x: x.to.x, y: -30 }, x.to, C.flying, { size: 50, dur: 350 }); at(350, () => hit(x.to, C.flying, { size: 90, shake: 8 })); return 800; },
    "Hurricane": (x) => { vortex(x.to, C.flying, { shape: "slash", count: 26, radius: 60, dur: 1100 }); at(300, () => shake(6, 700)); return 1150; },
    "Razor Wind": (x) => { for (let i = 0; i < 4; i++) at(i * 90, () => orb(x.from, { ...x.to, y: x.to.y + rand(-20, 20) }, C.flying, { shape: "slash", size: 40, dur: 350 })); return 850; },
    "Gust": (x) => vortex(x.to, C.flying, { shape: "slash", count: 12 }),
    "Whirlwind": (x) => { vortex(x.to, C.flying, { shape: "slash", count: 18 }); return 950; },
    "Drill Peck": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => { for (let i = 0; i < 3; i++) at(i * 80, () => K.flash(x.to, C.white, 50)); vortex(x.to, C.white, { shape: "slash", count: 8, dur: 450, radius: 20 }); }); return 850; },
    "Peck": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => hit(x.to, C.white, { size: 40 })); return 700; },
    "Tailwind": (x) => { stream({ x: x.from.x - 80, y: x.from.y }, { x: x.from.x + 200, y: x.from.y - 40 }, C.flying, { shape: "slash", count: 16, dur: 800, travel: 450, spread: 40 }); return 900; },
    "Roost": heal,
    "Feather Dance": (x) => { skyFall(x.to, C.white, { shape: "leaf", count: 12, dur: 900, drift: -20 }); return 1000; },

    // Bug
    "Bug Buzz": (x) => { soundWaves(x.from, x.to, C.bug, { count: 6, dur: 900 }); at(500, () => distort(x.tgtSprite, 400)); return 1000; },
    "X Scissor": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => slashes(x.to, C.bug, { count: 2, cross: true, spacing: 0, length: 140 })); return 850; },
    "Megahorn": (x) => { K.glowSprite(x.atkSprite, C.bug.glow, 300); at(200, () => heavy(x, C.bug, { shake: 11, size: 100 })); return 1000; },
    "Bug Bite": (x) => jaws(x.to, C.bug),
    "Pin Missile": (x) => { for (let i = 0; i < 5; i++) at(i * 90, () => orb(x.from, { ...x.to, x: x.to.x + rand(-15, 15) }, C.bug, { shape: "shard", size: 18, dur: 280 })); return 800; },
    "Signal Beam": (x) => beam(x.from, x.to, { color: "#ff5fa8", glow: "#b8ffcf" }, { style: "rainbow", width: 16 }) + 150,
    "Silver Wind": (x) => stream(x.from, x.to, C.white, { shape: "orb", count: 24, dur: 800, travel: 450, spread: 26, min: 4, max: 8 }),
    "U Turn": (x) => { dash(x, C.bug); return 700; },
    "Fury Cutter": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => slashes(x.to, C.bug, { count: 2, cross: true, spacing: 0 })); return 800; },
    "Attack Order": (x) => { for (let i = 0; i < 8; i++) at(i * 60, () => orb({ x: x.from.x + rand(-40, 40), y: x.from.y - 30 }, { ...x.to, x: x.to.x + rand(-25, 25) }, { color: "#e0b020", glow: "#fff0a0" }, { size: 12, dur: 350 })); return 900; },
    "Fell Stinger": (x) => { orb(x.from, x.to, C.bug, { shape: "shard", size: 26, dur: 300 }); at(300, () => hit(x.to, C.bug, { size: 60 })); return 700; },
    "Quiver Dance": (x) => { K.aura(x.from, { ...C.fairy, shape: "leaf" }, 16, true); return selfAura(x, C.bug, "star"); },
    "Sticky Web": (x) => { for (let i = 0; i < 4; i++) at(i * 90, () => beam(x.from, { x: x.to.x + rand(-60, 60), y: x.to.y + rand(0, 40) }, C.white, { width: 2, dur: 700 })); return 1000; },
    "String Shot": (x) => { for (let i = 0; i < 3; i++) at(i * 80, () => beam(x.from, { x: x.to.x + rand(-20, 20), y: x.to.y + rand(-20, 20) }, C.white, { width: 2, dur: 600 })); return 800; },

    // Fairy
    "Moonblast": (x) => { orb({ x: x.from.x, y: 10 }, x.to, { color: "#ffc6f0", glow: "#ffffff" }, { size: 54, dur: 700, trail: true }); screenTint("#2a1a4a", 900, 0.4); at(700, () => { K.flash(x.to, C.fairy, 120); K.burst(x.to, { ...C.fairy, shape: "star" }, 12); }); return 1100; },
    "Dazzling Gleam": (x) => { screenFlash("#ffd0f0", 500, 0.85); at(150, () => K.burst(x.to, { ...C.fairy, shape: "star" }, 16)); return 800; },
    "Play Rough": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => { hits(x.to, C.fairy, 4, 110, { size: 50 }); vortex(x.to, C.fairy, { shape: "star", count: 10, dur: 600, radius: 30 }); }); return 1100; },
    "Draining Kiss": (x) => { orb(x.from, x.to, C.fairy, { shape: "star", size: 28, dur: 450 }); at(450, () => drain(x.to, x.from, C.fairy)); return 1400; },
    "Charm": (x) => { for (let i = 0; i < 4; i++) at(i * 120, () => orb(x.from, x.to, C.fairy, { shape: "star", size: 18, arc: 30, dur: 500 })); return 900; },
    "Baby Doll Eyes": (x) => { K.glowSprite(x.atkSprite, C.fairy.glow, 500); at(250, () => orb(x.from, x.to, C.fairy, { shape: "star", size: 18, dur: 450 })); return 900; },
    "Sweet Kiss": (x) => { orb(x.from, x.to, C.fairy, { shape: "star", size: 22, arc: 40, dur: 500 }); return 800; },
    "Fairy Wind": (x) => stream(x.from, x.to, C.fairy, { shape: "star", count: 18, dur: 700, travel: 420, spread: 20 }),

    // Normal
    "Hyper Beam": (x) => { K.chargeGlow(x.from, C.gold); K.glowSprite(x.atkSprite, "#fff6c0", 500); at(250, () => { beam(x.from, x.to, { color: "#ffb13b", glow: "#ffffff" }, { width: 40, dur: 900 }); screenFlash("#fff2c0", 500, 0.5); }); at(500, () => shake(14, 650)); return 1300; },
    "Giga Impact": (x) => { selfAura(x, { color: "#ffb13b", glow: "#ffffff" }, "orb", { dur: 400 }); at(350, () => { heavy(x, { color: "#ffb13b", glow: "#ffffff" }, { shake: 16, size: 140 }); screenFlash("#ffffff", 350, 0.6); }); return 1200; },
    "Explosion": (x) => { K.glowSprite(x.atkSprite, "#ffffff", 400); at(300, () => explosion(x.from)); at(400, () => K.burst(x.to, { ...C.fire, shape: "flame" }, 12)); return 1400; },
    "Self Destruct": (x) => { K.glowSprite(x.atkSprite, "#ffffff", 400); at(300, () => explosion(x.from)); return 1300; },
    "Quick Attack": (x) => dash(x, C.white),
    "Extreme Speed": (x) => { dash(x, C.white, { dur: 330 }); at(150, () => hits(x.to, C.white, 2, 100, { size: 60 })); return 700; },
    "Swift": (x) => stream(x.from, x.to, C.gold, { shape: "star", count: 10, dur: 800, travel: 450, spread: 24, min: 14, max: 20 }),
    "Hyper Voice": (x) => { soundWaves(x.from, x.to, C.white, { count: 6, dur: 850 }); at(450, () => shake(7, 400)); return 950; },
    "Boomburst": (x) => { pulse(x.from, C.white, { count: 4, scale: 12, thick: true }); at(200, () => shake(14, 700)); return 1200; },
    "Uproar": (x) => { soundWaves(x.from, x.to, C.white, { count: 5, dur: 800 }); at(300, () => shake(5, 500)); return 900; },
    "Screech": (x) => { soundWaves(x.from, x.to, C.white, { count: 6, dur: 800 }); at(400, () => distort(x.tgtSprite, 400)); return 900; },
    "Growl": (x) => soundWaves(x.from, x.to, C.white, { count: 3, dur: 700 }),
    "Roar": (x) => { soundWaves(x.from, x.to, C.white, { count: 5, dur: 800 }); shake(6, 500); return 900; },
    "Sing": (x) => { for (let i = 0; i < 4; i++) at(i * 150, () => { const n = el("fx-note", x.from.x, x.from.y - 30); n.textContent = i % 2 ? "♫" : "♪"; run(n, [{ transform: "translate(-50%,-50%)", opacity: 0 }, { transform: `translate(calc(-50% + ${(x.to.x - x.from.x) * 0.5}px), calc(-50% + ${(x.to.y - x.from.y) * 0.5 - 20}px))`, opacity: 1, offset: 0.5 }, { transform: `translate(calc(-50% + ${x.to.x - x.from.x}px), calc(-50% + ${x.to.y - x.from.y}px))`, opacity: 0 }], { duration: 800 }); }); return 1300; },
    "Perish Song": (x) => { screenTint("#1a0f24", 1100, 0.5); pulse(x.from, C.ghost, { count: 3, scale: 10 }); return 1200; },
    "Body Slam": (x) => { x.atk.animate([{ transform: "translate(0,0)" }, { transform: "translate(0,-60px)", offset: 0.35 }, { transform: `translate(${(x.to.x - x.from.x) * 0.7}px, ${(x.to.y - x.from.y) * 0.7}px)`, offset: 0.65 }, { transform: "translate(0,0)" }], { duration: 750 }); at(500, () => hit(x.to, C.normal, { size: 110, shake: 13 })); return 950; },
    "Double Edge": (x) => heavy(x, C.normal, { shake: 12, size: 110 }),
    "Take Down": (x) => heavy(x, C.normal, { shake: 9 }),
    "Headbutt": (x) => heavy(x, C.normal, { shake: 7 }),
    "Skull Bash": (x) => { K.glowSprite(x.atkSprite, "#ffffff", 400); at(250, () => heavy(x, C.normal, { shake: 12, size: 110 })); return 1050; },
    "Slam": (x) => heavy(x, C.normal, { shake: 8 }),
    "Tackle": (x) => heavy(x, C.normal, { shake: 4, size: 60 }),
    "Pound": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => hit(x.to, C.normal, { size: 55 })); return 700; },
    "Scratch": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => slashes(x.to, C.white, { count: 3 })); return 800; },
    "Slash": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => slashes(x.to, C.white, { count: 1, length: 160 })); return 800; },
    "False Swipe": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => slashes(x.to, C.white, { count: 1, length: 140 })); return 800; },
    "Fury Swipes": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => slashes(x.to, C.white, { count: 3, interval: 150 })); return 900; },
    "Fury Attack": (x) => { K.lunge(x.atk, x.from, x.to); return hits(x.to, C.white, 3, 150, { size: 45 }); },
    "Double Hit": (x) => { K.lunge(x.atk, x.from, x.to); return hits(x.to, C.normal, 2, 180); },
    "Mega Punch": (x) => heavy(x, C.normal, { shake: 9, size: 100 }),
    "Mega Kick": (x) => heavy(x, C.normal, { shake: 11, size: 110 }),
    "Dizzy Punch": (x) => { heavy(x, C.normal, { shake: 6 }); at(400, () => vortex(x.to, C.gold, { shape: "star", count: 6, dur: 600, radius: 24 })); return 1000; },
    "Last Resort": (x) => heavy(x, C.gold, { shake: 12, size: 120 }),
    "Trump Card": (x) => { for (let i = 0; i < 3; i++) at(i * 100, () => orb(x.from, x.to, C.gold, { shape: "shard", size: 26, dur: 400 })); at(500, () => hit(x.to, C.gold, { size: 90 })); return 950; },
    "Chip Away": (x) => { K.lunge(x.atk, x.from, x.to); return hits(x.to, C.normal, 2, 160, { size: 55 }); },
    "Endeavor": (x) => heavy(x, C.red, { shake: 7 }),
    "Guillotine": (x) => { K.lunge(x.atk, x.from, x.to); at(250, () => { slashes(x.to, C.white, { count: 2, cross: true, spacing: 0, length: 170 }); shake(10, 400); }); return 900; },
    "Psyshock": (x) => { for (let i = 0; i < 5; i++) at(i * 90, () => orb({ x: x.to.x + rand(-50, 50), y: -20 }, { x: x.to.x + rand(-20, 20), y: x.to.y }, C.psychic, { size: 22, dur: 380 })); at(550, () => distort(x.tgtSprite, 400)); return 1050; },
    "Spit Up": (x) => { orb(x.from, x.to, C.white, { size: 34, dur: 450 }); at(450, () => hit(x.to, C.white)); return 850; },
    "Egg Bomb": (x) => { orb(x.from, x.to, C.white, { size: 30, arc: 70, dur: 550 }); at(550, () => explosion(x.to)); return 1300; },
    "Fling": (x) => { orb(x.from, x.to, C.gold, { shape: "star", size: 22, arc: 60, dur: 450 }); at(450, () => hit(x.to, C.normal)); return 850; },
    "Wrap": (x) => vortex(x.to, C.normal, { shape: "slash", count: 10, radius: 34 }),
    "Bind": (x) => vortex(x.to, C.normal, { shape: "slash", count: 10, radius: 34 }),
    "Wring Out": (x) => { vortex(x.to, C.normal, { shape: "slash", count: 12, radius: 30 }); at(500, () => shake(6, 300)); return 1000; },
    "Swords Dance": (x) => swords(x.atk),
    "Belly Drum": (x) => { for (let i = 0; i < 4; i++) at(i * 180, () => { K.flash(x.from, C.red, 70); shake(3, 150); }); return selfAura(x, C.red, "orb", { dur: 900 }); },
    "Focus Energy": (x) => selfAura(x, C.fighting, "flame"),
    "Shell Smash": (x) => { K.burst(x.from, { ...C.white, shape: "shard" }, 14); return selfAura(x, C.red, "orb"); },
    "Coil": (x) => { vortex(x.from, C.normal, { shape: "slash", count: 10, radius: 30 }); return 950; },
    "Agility": (x) => { for (let i = 0; i < 5; i++) at(i * 120, () => { if (x.atkSprite) x.atkSprite.animate([{ transform: "translateX(0)" }, { transform: `translateX(${i % 2 ? 14 : -14}px)` }, { transform: "translateX(0)" }], { duration: 110 }); }); return selfAura(x, C.white, "slash"); },
    "Protect": (x) => shield(x.atk, { color: "#6fe07a", glow: "#e0ffe4" }),
    "Endure": (x) => shield(x.atk, C.red),
    "Wide Guard": (x) => shield(x.atk, C.fighting),
    "Safeguard": (x) => shield(x.atk, { color: "#9f8cff", glow: "#e8e0ff" }),
    "Lucky Chant": (x) => shield(x.atk, C.gold),
    "Recover": heal, "Slack Off": heal, "Milk Drink": heal, "Soft Boiled": heal, "Wish": heal,
    "Heal Bell": (x) => { soundWaves(x.from, { x: x.from.x + 1, y: x.from.y - 80 }, C.gold, { count: 3 }); return heal(x); },
    "Healing Wish": (x) => { screenTint("#ffe8a0", 900, 0.4); return heal(x); },
    "Leer": (x) => glare(x.from, x.to),
    "Scary Face": (x) => glare(x.from, x.to),
    "Mean Look": (x) => glare(x.from, x.to, C.ghost),
    "Glare": (x) => glare(x.from, x.to, C.gold),
    "Swagger": (x) => { glare(x.from, x.to, C.red); at(600, () => distort(x.tgtSprite, 400)); return 1100; },
    "Flatter": (x) => { orb(x.from, x.to, C.fairy, { shape: "star", size: 20, arc: 40 }); at(560, () => distort(x.tgtSprite, 400)); return 1050; },
    "Captivate": (x) => { for (let i = 0; i < 3; i++) at(i * 120, () => orb(x.from, x.to, C.fairy, { shape: "star", size: 18, arc: 30, dur: 500 })); return 900; },
    "Block": (x) => shield(x.tgt, C.red),
    "Lock On": (x) => { pulse(x.to, C.red, { count: 3, scale: 1.8 }); return 800; },
    "Mind Reader": (x) => { pulse(x.to, C.psychic, { count: 3, scale: 1.8 }); return 800; },
    "Haze": (x) => { screenTint("#aab0c0", 900, 0.45); return 900; },
    "Splash": (x) => { x.atk.animate([{ transform: "translateY(0)" }, { transform: "translateY(-26px)" }, { transform: "translateY(0)" }, { transform: "translateY(-14px)" }, { transform: "translateY(0)" }], { duration: 700 }); K.splash(x.from, { ...C.water, shape: "bubble" }); return 800; },
    "Baton Pass": (x) => { orb(x.from, { x: x.from.x - 80, y: x.from.y + 20 }, C.gold, { shape: "star", size: 20, dur: 500 }); return 700; },
    "Mirror Move": (x) => { screenFlash("#ffffff", 250, 0.4); return 400; },
    "Mirror Coat": (x) => { shield(x.atk, C.psychic); at(400, () => beam(x.from, x.to, C.psychic, { width: 16 })); return 1150; },
    "Heal Block": (x) => { pulse(x.to, C.red, { count: 2, scale: 2 }); return 700; },
    "Spikes": (x) => { for (let i = 0; i < 4; i++) at(i * 80, () => orb(x.from, { x: x.to.x + rand(-60, 60), y: x.to.y + rand(20, 40) }, C.steel, { shape: "shard", size: 14, arc: 50, dur: 480 })); return 900; },
    "Memento": (x) => { K.glowSprite(x.atkSprite, "#000000", 600); at(300, () => screenTint("#000000", 700, 0.5)); return 1000; },
    "Transform": (x) => { distort(x.atkSprite, 800); return 900; },
    "Natural Gift": (x) => orb(x.from, x.to, C.gold, { size: 26 }) + 200,
    "Stockpile": (x) => selfAura(x, C.gold, "orb"),
    "Harden": (x) => { K.glowSprite(x.atkSprite, "#ffffff", 700); return 750; },
    "Snore": (x) => soundWaves(x.from, x.to, C.white, { count: 3 }),
  };

  // Name-pattern fallbacks for moves without their own entry. `c` is the
  // move's type colors.
  const PATTERNS = [
    [/Punch$/, (x, c) => { K.lunge(x.atk, x.from, x.to); at(250, () => hit(x.to, c, { size: 80 })); return 760; }],
    [/Kick$/, (x, c) => { K.lunge(x.atk, x.from, x.to); at(250, () => hit(x.to, c, { size: 85, shake: 5 })); return 760; }],
    [/Fang$|Bite$/, (x, c) => jaws(x.to, c)],
    [/Claw$|Slash$|Cut$|Scissor|Cutter$/, (x, c) => { K.lunge(x.atk, x.from, x.to); at(250, () => slashes(x.to, c, { count: 3 })); return 900; }],
    [/Beam$|Ray$|Cannon$/, (x, c) => beam(x.from, x.to, c, { width: 20 }) + 150],
    [/Ball$|Bomb$|Sphere$|Blast$|Shot$/, (x, c) => { orb(x.from, x.to, c, { size: 36, trail: true }); at(560, () => K.burst(x.to, c, 10)); return 900; }],
    [/Pulse$|Wave$/, (x, c) => pulse(x.from, c, { count: 3, scale: 9 })],
    [/Storm$|Blizzard$/, (x, c) => vortex(x.to, c, { count: 22, radius: 55 })],
    [/Quake|Tremor/, (x, c) => { shake(12, 800); dust(x.to); bounce(x.tgtSprite); K.rise(x.to, { ...c, shape: "rock" }); return 850; }],
    [/Powder$|Spore$/, (x, c) => powder(x.to, c)],
    [/Drain$|Absorb/, (x) => drain(x.to, x.from)],
    [/Dance$/, (x, c) => selfAura(x, c, "star")],
    [/Wind$|Gust$/, (x, c) => stream(x.from, x.to, c, { shape: "slash", count: 16, dur: 700, travel: 450, spread: 26 })],
    [/Voice$|Song$|Sing|Screech|Roar|Growl/, (x, c) => soundWaves(x.from, x.to, c, { count: 5 })],
    [/Horn$|Drill|Peck$|Jab$/, (x, c) => { K.lunge(x.atk, x.from, x.to); at(250, () => hit(x.to, c, { size: 55 })); return 760; }],
    [/Tail$|Slam$|Hammer$|Crash$|Rush$|Charge$|Tackle$|Impact$|Smash$/, (x, c) => heavy(x, c, { shake: 9 })],
  ];

  function recipeFor(name) {
    if (!name) return null;
    if (MOVES[name]) return MOVES[name];
    for (const [re, fn] of PATTERNS) if (re.test(name)) return fn;
    return null;
  }

  // Returns a promise resolving when the move's animation is done, or null
  // when this move has no specific animation (caller falls back).
  function play(event, slots) {
    const fn = recipeFor(event.move_name);
    if (!fn) return null;
    const ctx = {
      ...slots,
      side: event.side,
      from: centerOf(slots.atk),
      to: centerOf(slots.tgt),
      type: event.move_type,
    };
    let ms;
    try {
      ms = fn(ctx, C[event.move_type] || C.normal);
    } catch (e) {
      console.error("Move animation failed:", event.move_name, e);
      return null;
    }
    return new Promise((resolve) => setTimeout(resolve, Math.max(300, ms || 800)));
  }

  return { play, hasAnimation: (name) => !!recipeFor(name), MOVES };
})();
