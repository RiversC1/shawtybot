// Battle visual effects: per-type move animations (physical lunges,
// special-move projectiles, status waves, self auras), floating damage/heal/
// status/stat labels, and the in-scene caption box describing what's
// happening. Everything is drawn into one absolutely-positioned layer inside
// the battle scene using DOM particles + the Web Animations API — no canvas,
// no external assets. battle_room.js decides *when* things happen; this
// module only decides how they look and how long they take.
window.BattleFX = (function () {
  // Per-type look: particle color/glow, particle shape, and how the hit lands.
  const TYPE_FX = {
    normal:   { color: "#ece6d6", glow: "#ffffff", shape: "star",   impact: "burst" },
    fire:     { color: "#ff7a2f", glow: "#ffd24a", shape: "flame",  impact: "burst" },
    water:    { color: "#4aa3ff", glow: "#c4e6ff", shape: "bubble", impact: "splash" },
    electric: { color: "#ffd93b", glow: "#fff8b8", shape: "spark",  impact: "strike" },
    grass:    { color: "#5fd35f", glow: "#caffb4", shape: "leaf",   impact: "burst" },
    ice:      { color: "#9aeaff", glow: "#ffffff", shape: "shard",  impact: "burst" },
    fighting: { color: "#ee5a3e", glow: "#ffbca8", shape: "star",   impact: "burst" },
    poison:   { color: "#b561e6", glow: "#e6bbff", shape: "bubble", impact: "splash" },
    ground:   { color: "#caa15c", glow: "#efd9a8", shape: "rock",   impact: "rise" },
    flying:   { color: "#c2d0ff", glow: "#ffffff", shape: "slash",  impact: "burst" },
    psychic:  { color: "#ff6fae", glow: "#ffc6df", shape: "orb",    impact: "rings" },
    bug:      { color: "#aecb3c", glow: "#e7f7a0", shape: "orb",    impact: "burst" },
    rock:     { color: "#bb9d5f", glow: "#e4d3a4", shape: "rock",   impact: "fall" },
    ghost:    { color: "#8a6fdb", glow: "#cdbcff", shape: "wisp",   impact: "rings" },
    dragon:   { color: "#7a66ff", glow: "#bdb2ff", shape: "orb",    impact: "burst" },
    dark:     { color: "#6b5a53", glow: "#b39c90", shape: "wisp",   impact: "burst" },
    steel:    { color: "#c3ccd8", glow: "#ffffff", shape: "shard",  impact: "burst" },
    fairy:    { color: "#ffa2e2", glow: "#ffe4f6", shape: "star",   impact: "burst" },
  };

  const STATUS_FX = {
    paralysis: { label: "Paralyzed", color: "#f5d33c", type: "electric" },
    burn:      { label: "Burned", color: "#ff7a2f", type: "fire" },
    poison:    { label: "Poisoned", color: "#b561e6", type: "poison" },
    toxic:     { label: "Badly poisoned", color: "#9c3fd6", type: "poison" },
    sleep:     { label: "Fell asleep", color: "#a8a8c0", type: null },
    freeze:    { label: "Frozen", color: "#9aeaff", type: "ice" },
    confusion: { label: "Confused", color: "#ff9ee0", type: null },
  };

  // Motion effects (screen shake, lunges, particles) are on unless the
  // viewer turns them off with the battle page's "Motion effects" switch.
  // Deliberately NOT tied to the OS "reduce motion" preference: Windows turns
  // that on whenever "Animation effects" is off, which silently disabled the
  // move animations for people who never meant to opt out of them.
  function readMotionPref() {
    try {
      return localStorage.getItem("sb_battle_motion") !== "0";
    } catch (e) {
      return true;
    }
  }
  let motionOn = readMotionPref();
  function reduceMotionNow() {
    return !motionOn;
  }
  function setMotion(on) {
    motionOn = !!on;
    try {
      localStorage.setItem("sb_battle_motion", motionOn ? "1" : "0");
    } catch (e) {
      // Not persisted; applies for this page view.
    }
  }

  let scene = null;
  let layer = null;
  let captionEl = null;
  let captionTimer = null;

  function init(sceneEl) {
    scene = sceneEl;
    layer = document.createElement("div");
    layer.className = "battle-fx-layer";
    layer.setAttribute("aria-hidden", "true");
    captionEl = document.createElement("div");
    captionEl.className = "battle-caption";
    captionEl.setAttribute("role", "status");
    captionEl.setAttribute("aria-live", "polite");
    captionEl.hidden = true;
    scene.append(layer, captionEl);
  }

  function fxFor(type) {
    return TYPE_FX[type] || TYPE_FX.normal;
  }

  function wait(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }

  // Center of an element in scene-local coordinates.
  function centerOf(el, yBias) {
    const s = scene.getBoundingClientRect();
    const r = el.getBoundingClientRect();
    return { x: r.left - s.left + r.width / 2, y: r.top - s.top + r.height * (yBias ?? 0.55), w: r.width, h: r.height };
  }

  function rand(min, max) {
    return min + Math.random() * (max - min);
  }

  // Particle sizes below are authored small; scale them up so effects read
  // clearly at the scene's size.
  const PARTICLE_SCALE = 1.4;

  function particle(shape, x, y, size, fx) {
    size *= PARTICLE_SCALE;
    const p = document.createElement("div");
    p.className = `fx-p fx-${shape || "orb"}`;
    p.style.left = `${x}px`;
    p.style.top = `${y}px`;
    p.style.width = `${size}px`;
    p.style.height = `${size}px`;
    p.style.setProperty("--c", fx.color);
    p.style.setProperty("--g", fx.glow);
    layer.appendChild(p);
    return p;
  }

  function run(el, frames, opts) {
    const anim = el.animate(frames, { fill: "forwards", ...opts });
    anim.finished.then(() => el.remove(), () => el.remove());
    return anim;
  }

  // ---------- impacts ----------

  function flash(at, fx, size) {
    const f = particle("flash", at.x, at.y, size || 70, fx);
    run(f, [
      { transform: "translate(-50%,-50%) scale(0.2)", opacity: 0.95 },
      { transform: "translate(-50%,-50%) scale(1.3)", opacity: 0 },
    ], { duration: 380, easing: "ease-out" });
  }

  function burst(at, fx, count) {
    for (let i = 0; i < (count || 12); i++) {
      const a = rand(0, Math.PI * 2);
      const d = rand(28, 64);
      const p = particle(fx.shape, at.x, at.y, rand(8, 15), fx);
      run(p, [
        { transform: `translate(-50%,-50%) rotate(${rand(0, 360)}deg) scale(1)`, opacity: 1 },
        { transform: `translate(calc(-50% + ${Math.cos(a) * d}px), calc(-50% + ${Math.sin(a) * d}px)) rotate(${rand(0, 360)}deg) scale(0.3)`, opacity: 0 },
      ], { duration: rand(420, 620), easing: "cubic-bezier(.2,.7,.3,1)", delay: rand(0, 60) });
    }
  }

  function splash(at, fx) {
    for (let i = 0; i < 12; i++) {
      const dx = rand(-50, 50);
      const up = rand(30, 70);
      const p = particle(fx.shape, at.x, at.y, rand(7, 14), fx);
      run(p, [
        { transform: "translate(-50%,-50%) scale(0.6)", opacity: 1 },
        { transform: `translate(calc(-50% + ${dx * 0.6}px), calc(-50% - ${up}px)) scale(1)`, opacity: 1, offset: 0.55 },
        { transform: `translate(calc(-50% + ${dx}px), calc(-50% - ${up * 0.4}px)) scale(0.4)`, opacity: 0 },
      ], { duration: rand(560, 760), easing: "ease-out", delay: rand(0, 80) });
    }
  }

  function strike(at, fx) {
    // A lightning bolt drops onto the target from above.
    const bolt = particle("bolt", at.x, at.y - 70, 34, fx);
    bolt.style.height = "110px";
    run(bolt, [
      { transform: "translate(-50%,-60%) scaleY(0.2)", opacity: 0 },
      { transform: "translate(-50%,-50%) scaleY(1)", opacity: 1, offset: 0.3 },
      { transform: "translate(-50%,-50%) scaleY(1)", opacity: 1, offset: 0.7 },
      { transform: "translate(-50%,-50%) scaleY(1)", opacity: 0 },
    ], { duration: 420, easing: "ease-out" });
    setTimeout(() => burst(at, fx, 10), 120);
  }

  function rise(at, fx) {
    // Rocks/earth erupt up from the ground under the target.
    for (let i = 0; i < 9; i++) {
      const x = at.x + rand(-45, 45);
      const p = particle(fx.shape, x, at.y + at.h * 0.4, rand(10, 18), fx);
      run(p, [
        { transform: "translate(-50%,0) scale(0.5)", opacity: 0 },
        { transform: `translate(-50%,-${rand(40, 80)}px) rotate(${rand(-90, 90)}deg) scale(1)`, opacity: 1, offset: 0.5 },
        { transform: `translate(-50%,-${rand(10, 30)}px) rotate(${rand(-180, 180)}deg) scale(0.6)`, opacity: 0 },
      ], { duration: rand(520, 700), easing: "ease-out", delay: i * 30 });
    }
  }

  function fall(at, fx) {
    // Rocks rain down onto the target.
    for (let i = 0; i < 8; i++) {
      const x = at.x + rand(-40, 40);
      const p = particle(fx.shape, x, at.y - 110, rand(11, 19), fx);
      run(p, [
        { transform: "translate(-50%,-50%) rotate(0deg)", opacity: 0 },
        { transform: "translate(-50%,-50%) rotate(40deg)", opacity: 1, offset: 0.15 },
        { transform: `translate(-50%, calc(-50% + ${110 + rand(-10, 15)}px)) rotate(${rand(120, 260)}deg)`, opacity: 1, offset: 0.8 },
        { transform: `translate(-50%, calc(-50% + ${110}px)) rotate(260deg) scale(0.5)`, opacity: 0 },
      ], { duration: rand(480, 620), easing: "ease-in", delay: i * 45 });
    }
  }

  function rings(at, fx) {
    for (let i = 0; i < 3; i++) {
      const r = particle("ring", at.x, at.y, 30, fx);
      run(r, [
        { transform: "translate(-50%,-50%) scale(0.3)", opacity: 0.9 },
        { transform: "translate(-50%,-50%) scale(3.2)", opacity: 0 },
      ], { duration: 620, easing: "ease-out", delay: i * 130 });
    }
  }

  function impact(at, fx) {
    flash(at, fx);
    if (reduceMotionNow()) return;
    ({ burst, splash, strike, rise, fall, rings })[fx.impact](at, fx);
  }

  // ---------- movement ----------

  function lunge(attackerSlot, from, to) {
    const dx = (to.x - from.x) * 0.62;
    const dy = (to.y - from.y) * 0.62;
    attackerSlot.animate([
      { transform: "translate(0,0)" },
      { transform: `translate(${-dx * 0.08}px, ${-dy * 0.08}px)`, offset: 0.15 },
      { transform: `translate(${dx}px, ${dy}px) scale(1.06)`, offset: 0.45, easing: "ease-in" },
      { transform: "translate(0,0)" },
    ], { duration: 560, easing: "ease-out" });
  }

  function projectile(from, to, fx, count, spread, duration) {
    for (let i = 0; i < count; i++) {
      const p = particle(fx.shape === "spark" ? "orb" : fx.shape, from.x, from.y, rand(9, 16), fx);
      const off = rand(-spread, spread);
      const nx = -(to.y - from.y), ny = to.x - from.x;
      const len = Math.hypot(nx, ny) || 1;
      const mx = (from.x + to.x) / 2 + (nx / len) * off - from.x;
      const my = (from.y + to.y) / 2 + (ny / len) * off - from.y;
      run(p, [
        { transform: "translate(-50%,-50%) scale(0.6)", opacity: 0 },
        { transform: `translate(calc(-50% + ${mx}px), calc(-50% + ${my}px)) scale(1)`, opacity: 1, offset: 0.5 },
        { transform: `translate(calc(-50% + ${to.x - from.x}px), calc(-50% + ${to.y - from.y}px)) scale(0.8)`, opacity: 0.9 },
      ], { duration, easing: "ease-in", delay: i * 32 });
    }
  }

  function chargeGlow(at, fx) {
    const g = particle("flash", at.x, at.y, 60, fx);
    run(g, [
      { transform: "translate(-50%,-50%) scale(1.4)", opacity: 0 },
      { transform: "translate(-50%,-50%) scale(0.5)", opacity: 0.85 },
      { transform: "translate(-50%,-50%) scale(0.3)", opacity: 0 },
    ], { duration: 300, easing: "ease-in" });
  }

  function aura(at, fx, count, up) {
    const dir = up === false ? 1 : -1;
    for (let i = 0; i < (count || 10); i++) {
      const x = at.x + rand(-at.w * 0.35, at.w * 0.35);
      const y = at.y + (dir < 0 ? at.h * 0.35 : -at.h * 0.35);
      const p = particle(fx.shape === "slash" ? "orb" : fx.shape, x, y, rand(7, 13), fx);
      run(p, [
        { transform: "translate(-50%,-50%) scale(0.5)", opacity: 0 },
        { transform: `translate(-50%, calc(-50% + ${dir * 30}px)) scale(1)`, opacity: 1, offset: 0.4 },
        { transform: `translate(-50%, calc(-50% + ${dir * rand(60, 90)}px)) scale(0.4)`, opacity: 0 },
      ], { duration: rand(650, 900), easing: "ease-out", delay: i * 45 });
    }
  }

  function glowSprite(spriteEl, color, duration) {
    if (!spriteEl) return;
    spriteEl.animate([
      { filter: "drop-shadow(0 6px 6px rgba(0,0,0,0.4)) brightness(1)" },
      { filter: `drop-shadow(0 0 14px ${color}) brightness(1.35)`, offset: 0.4 },
      { filter: "drop-shadow(0 6px 6px rgba(0,0,0,0.4)) brightness(1)" },
    ], { duration: duration || 700, easing: "ease-in-out" });
  }

  /**
   * Animates one move. `event` is a move_used event (move_type/category/
   * target, possibly missing on old events), the slots are the sprite
   * containers. Resolves when the animation has visually finished.
   */
  function playMove(event, attackerSlot, targetSlot, attackerSprite, targetSprite) {
    if (!layer) return Promise.resolve();
    // Game-accurate per-move animations (battle_moves.js) take priority; the
    // generic type/category animation below is the fallback.
    if (window.BattleMoves) {
      const custom = window.BattleMoves.play(event, {
        atk: attackerSlot, tgt: targetSlot, atkSprite: attackerSprite, tgtSprite: targetSprite,
      });
      if (custom) return custom;
    }
    const fx = fxFor(event.move_type);
    const from = centerOf(attackerSlot);
    const to = centerOf(targetSlot);
    const selfTarget = event.target === "self";
    const category = event.category || "physical";

    if (selfTarget) {
      glowSprite(attackerSprite, fx.glow, 800);
      if (!reduceMotionNow()) aura(from, fx, 12, true);
      return wait(800);
    }
    if (category === "physical") {
      if (!reduceMotionNow()) lunge(attackerSlot, from, to);
      setTimeout(() => impact(to, fx), 250);
      return wait(760);
    }
    if (category === "special") {
      chargeGlow(from, fx);
      glowSprite(attackerSprite, fx.glow, 500);
      if (!reduceMotionNow()) projectile(from, to, fx, 11, 18, 420);
      setTimeout(() => impact(to, fx), 520);
      return wait(960);
    }
    // Status move aimed at the opponent: a softer wave of sparkles.
    glowSprite(attackerSprite, fx.glow, 500);
    if (!reduceMotionNow()) projectile(from, to, { ...fx, shape: "star" }, 7, 26, 560);
    setTimeout(() => {
      flash(to, fx, 54);
      if (!reduceMotionNow()) rings(to, fx);
    }, 560);
    return wait(1000);
  }

  // ---------- floating labels ----------

  function floatText(slot, text, kind) {
    if (!layer || !slot) return;
    const at = centerOf(slot, 0.2);
    const t = document.createElement("div");
    t.className = `fx-float fx-float-${kind || "info"}`;
    t.textContent = text;
    t.style.left = `${at.x + rand(-10, 10)}px`;
    t.style.top = `${at.y}px`;
    layer.appendChild(t);
    run(t, [
      { transform: "translate(-50%, 0) scale(0.7)", opacity: 0 },
      { transform: "translate(-50%, -14px) scale(1.08)", opacity: 1, offset: 0.2 },
      { transform: "translate(-50%, -26px) scale(1)", opacity: 1, offset: 0.75 },
      { transform: "translate(-50%, -40px) scale(1)", opacity: 0 },
    ], { duration: 1300, easing: "ease-out" });
  }

  function statusEffect(slot, status, spriteEl) {
    const info = STATUS_FX[status];
    if (!info || !slot) return;
    const at = centerOf(slot);
    glowSprite(spriteEl, info.color, 700);
    if (reduceMotionNow()) return;
    if (status === "sleep") {
      for (let i = 0; i < 3; i++) {
        const z = document.createElement("div");
        z.className = "fx-float fx-float-sleep";
        z.textContent = "z";
        z.style.left = `${at.x + 18 + i * 10}px`;
        z.style.top = `${at.y - 20 - i * 8}px`;
        layer.appendChild(z);
        run(z, [
          { transform: "translate(0,0) scale(0.6)", opacity: 0 },
          { transform: "translate(8px,-12px) scale(1)", opacity: 1, offset: 0.4 },
          { transform: "translate(18px,-30px) scale(1.2)", opacity: 0 },
        ], { duration: 1100, delay: i * 220, easing: "ease-out" });
      }
      return;
    }
    if (status === "confusion") {
      for (let i = 0; i < 5; i++) {
        const p = particle("star", at.x, at.y - at.h * 0.35, 11, { color: "#ffe35a", glow: "#fff6b8" });
        const a0 = (i / 5) * 360;
        run(p, [0, 0.25, 0.5, 0.75, 1].map((k) => {
          const a = ((a0 + k * 360) * Math.PI) / 180;
          return { transform: `translate(calc(-50% + ${Math.cos(a) * 26}px), calc(-50% + ${Math.sin(a) * 8}px))`, opacity: k === 1 ? 0 : 1 };
        }), { duration: 1000, easing: "linear" });
      }
      return;
    }
    const fx = fxFor(info.type);
    if (status === "paralysis") {
      for (let i = 0; i < 6; i++) {
        const p = particle("spark", at.x + rand(-30, 30), at.y + rand(-30, 20), rand(10, 16), fx);
        run(p, [
          { transform: `translate(-50%,-50%) rotate(${rand(0, 360)}deg) scale(0.4)`, opacity: 0 },
          { transform: `translate(-50%,-50%) rotate(${rand(0, 360)}deg) scale(1.2)`, opacity: 1, offset: 0.3 },
          { transform: "translate(-50%,-50%) scale(0.2)", opacity: 0 },
        ], { duration: 380, delay: i * 90 });
      }
      return;
    }
    aura(at, fx, 9, status !== "freeze");
  }

  function statArrows(slot, up) {
    if (!slot || reduceMotionNow()) return;
    const at = centerOf(slot);
    const fx = up ? { color: "#ff9a3c", glow: "#ffd9a0" } : { color: "#5aa2ff", glow: "#bcdcff" };
    for (let i = 0; i < 8; i++) {
      const a = document.createElement("div");
      a.className = "fx-arrow";
      a.textContent = up ? "▲" : "▼";
      a.style.color = fx.color;
      a.style.textShadow = `0 0 8px ${fx.glow}`;
      a.style.left = `${at.x + rand(-at.w * 0.35, at.w * 0.35)}px`;
      a.style.top = `${at.y + (up ? at.h * 0.3 : -at.h * 0.4)}px`;
      layer.appendChild(a);
      run(a, [
        { transform: "translate(-50%,-50%)", opacity: 0 },
        { transform: `translate(-50%, calc(-50% + ${up ? -25 : 25}px))`, opacity: 1, offset: 0.4 },
        { transform: `translate(-50%, calc(-50% + ${up ? -60 : 60}px))`, opacity: 0 },
      ], { duration: 800, delay: i * 55, easing: "ease-out" });
    }
  }

  function healSparkles(slot) {
    if (!slot || reduceMotionNow()) return;
    aura(centerOf(slot), { color: "#63e38a", glow: "#c9ffd8", shape: "star" }, 9, true);
  }

  // ---------- caption box ----------

  // `html` is a pre-escaped log line (battle_room's formatEvent output).
  function caption(html, append) {
    if (!captionEl || !html) return;
    clearTimeout(captionTimer);
    const line = document.createElement("div");
    line.className = "battle-caption-line";
    line.innerHTML = html;
    if (!append) captionEl.replaceChildren();
    captionEl.appendChild(line);
    while (captionEl.children.length > 3) captionEl.firstChild.remove();
    captionEl.hidden = false;
    captionEl.classList.remove("is-fading");
  }

  function fadeCaption(delay) {
    if (!captionEl) return;
    clearTimeout(captionTimer);
    captionTimer = setTimeout(() => {
      captionEl.classList.add("is-fading");
      captionTimer = setTimeout(() => {
        captionEl.hidden = true;
        captionEl.classList.remove("is-fading");
      }, 400);
    }, delay ?? 2200);
  }

  function colorForType(type) {
    return TYPE_FX[type] ? TYPE_FX[type].color : null;
  }

  // Building blocks for battle_moves.js.
  const kit = {
    scene: () => scene, layer: () => layer, TYPE_FX, fxFor, wait, centerOf, rand, particle, run,
    flash, burst, splash, strike, rise, fall, rings, impact, lunge, projectile, chargeGlow, aura, glowSprite,
    healSparkles, floatText,
  };

  Object.defineProperty(kit, "reduceMotion", { get: reduceMotionNow });

  return {
    isMotionOn: () => motionOn, setMotion,
    init, playMove, floatText, statusEffect, statArrows, healSparkles, glowSprite,
    caption, fadeCaption, colorForType, STATUS_FX, kit,
  };
})();
