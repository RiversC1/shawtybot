(function () {
  const POLL_INTERVAL_MS = 2000;
  const WS_CONNECT_TIMEOUT_MS = 3000;

  // How long to hold each individual event on screen before advancing to the
  // next one — this is what makes a turn play out like the real games (one
  // Pokémon acts and everything about it resolves, then the other), instead
  // of both sides' whole turn snapping to its final state at once.
  const EVENT_DELAYS = {
    move_used: 500, damage: 600, confusion_self_hit: 600, status_damage: 600, recoil: 600,
    drain: 500, heal: 500, faint: 750, status_applied: 550, stat_changed: 500,
    cannot_act: 550, move_missed: 550, move_failed: 500, charge_start: 500, multi_hit_summary: 450,
    badge_awarded: 3200,
    weather_start: 1100, weather_end: 700, weather_damage: 650,
    item_used: 750, item_damage: 600, item_activated: 650, self_ko: 400,
    bide: 600, stockpile: 500, magnitude: 550,
  };
  // Mega Stones whose name isn't just "<species>ite" (Garchomp -> Garchompite).
  const MEGA_STONES = { Lucario: "Lucarionite" };
  const DEFAULT_EVENT_DELAY = 250; // structural events with no on-screen effect (turn_start, switch_out, battle_end)

  // switch_in plays as its own two-phase mini-sequence (see playSwitchIn)
  // rather than through the generic per-event delay table above.
  const SEND_OUT_THROW_MS = 650;
  const SEND_OUT_SETTLE_MS = 500;

  const titleEl = document.getElementById("br-title");
  const subtitleEl = document.getElementById("br-subtitle");
  const resultEl = document.getElementById("br-result");
  const turnBadgeEl = document.getElementById("br-turn-badge");
  const trainerPanelA = document.getElementById("br-trainer-a");
  const trainerPanelB = document.getElementById("br-trainer-b");
  const hpLabelA = document.getElementById("br-hp-a");
  const hpLabelB = document.getElementById("br-hp-b");
  const spriteA = document.getElementById("br-sprite-a");
  const spriteB = document.getElementById("br-sprite-b");
  const ballA = document.getElementById("br-ball-a");
  const slotA = document.getElementById("br-slot-a");
  const slotB = document.getElementById("br-slot-b");
  const sceneEl = document.getElementById("br-scene");
  const ballB = document.getElementById("br-ball-b");
  const logEl = document.getElementById("br-log");
  const actionPanel = document.getElementById("br-action-panel");
  const connectionStatusEl = document.getElementById("br-connection-status");
  const forfeitBtn = document.getElementById("br-forfeit-btn");
  const muteBtn = document.getElementById("br-mute-btn");

  // `truth` is always the latest full state from the server. `visibleA`/
  // `visibleB` are what's actually on screen right now, which can lag behind
  // truth while a batch of new events plays out one at a time. `revealed`
  // is the subset of truth.events whose effects have already been shown —
  // the battle log grows in step with it, exactly like a real turn's
  // message box.
  let truth = JSON.parse(document.getElementById("battle-data").textContent);

  // Arena backdrop: themed by battle kind, tinted by the gym's or Elite Four
  // member's specialty type when there is one (see battle_store.arena_type_for).
  BattleFX.init(sceneEl);

  // ---------- Weather (sun / rain / sandstorm / hail) ----------
  const WEATHER_INFO = {
    sun: { icon: "☀️", label: "Harsh sunlight" },
    rain: { icon: "🌧️", label: "Rain" },
    sand: { icon: "🌪️", label: "Sandstorm" },
    hail: { icon: "🌨️", label: "Hail" },
  };
  const weatherLayer = document.createElement("div");
  weatherLayer.className = "battle-weather-layer";
  weatherLayer.setAttribute("aria-hidden", "true");
  const weatherChip = document.createElement("div");
  weatherChip.className = "battle-weather-chip";
  weatherChip.hidden = true;
  sceneEl.append(weatherLayer, weatherChip);
  const weatherFx = window.BattleWeather ? window.BattleWeather.attach(sceneEl) : null;
  // What the scene currently shows; follows weather events during playback
  // and resyncs to the server's state afterward.
  let visibleWeather = null; // { weather, turns, suppressed }

  function weatherFromTruth() {
    return truth.weather_raw
      ? { weather: truth.weather_raw, turns: truth.weather_turns, suppressed: !truth.weather }
      : null;
  }

  function renderWeather() {
    const w = visibleWeather;
    for (const k of Object.keys(WEATHER_INFO)) sceneEl.classList.toggle(`weather-${k}`, !!w && w.weather === k && !w.suppressed);
    if (weatherFx) weatherFx.set(w && !w.suppressed ? w.weather : null);
    if (!w) {
      weatherChip.hidden = true;
      return;
    }
    const info = WEATHER_INFO[w.weather] || { icon: "", label: w.weather };
    const turns = w.turns ? ` · ${w.turns} turn${w.turns === 1 ? "" : "s"}` : "";
    weatherChip.textContent = `${info.icon} ${info.label}${w.suppressed ? " (no effect)" : turns}`;
    weatherChip.hidden = false;
  }
  (function setupArena() {
    const kind = { gym: "gym", custom_gym: "gym", elite_four: "elite", champion: "champion" }[truth.battle_type] || "field";
    sceneEl.classList.add(`arena-${kind}`);
    const tint = BattleFX.colorForType(truth.arena_type);
    if (tint) sceneEl.style.setProperty("--arena-tint", tint);
  })();
  let visibleA = null;
  let visibleB = null;
  let revealed = [];
  // The server only sends the latest 60 events, so "new" events are found by
  // id (see battle_store.serialize_battle_detail), not by counting — once a
  // battle passed 60 events, counting saw nothing new and silently skipped
  // every later send-out animation and cry. Falls back to counting if a
  // payload has no ids (an older API still deploying).
  let animatedEventCount = 0;
  let lastEventId = 0;

  function markAnimated(events) {
    animatedEventCount = events.length;
    const last = events[events.length - 1];
    if (last && last.event_id != null) lastEventId = Math.max(lastEventId, last.event_id);
  }

  function unseenEvents(events) {
    const hasIds = events.length > 0 && events.every((e) => e.event_id != null);
    return hasIds ? events.filter((e) => e.event_id > lastEventId) : events.slice(animatedEventCount);
  }
  let pendingEvents = [];
  let playing = false;
  let pollTimer = null;
  let ws = null;
  let wsConnectTimer = null;
  let usingWs = false;
  let resultSoundPlayed = false;
  // The victory theme only plays for a win that happens while this page is
  // open, not when revisiting an already-finished battle from history.
  const wasLiveOnLoad = ["pending", "active", "awaiting_forced_switch"].includes(truth.status);
  let victoryMusicOn = false;

  function wait(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  // "Audible" means the user can actually hear the battle right now: sound
  // isn't muted AND, if a theme should be playing, it really is. Browsers
  // block audio until the first click, so a saved "sound on" preference alone
  // isn't enough — until playback starts the button offers to turn it on.
  function battleHasMusic() {
    return truth.status === "active" || truth.status === "awaiting_forced_switch" || victoryMusicOn;
  }

  function isAudible() {
    return !BattleAudio.isMuted() && (BattleAudio.isMusicPlaying() || !battleHasMusic());
  }

  function updateMuteBtn() {
    const audible = isAudible();
    const onLabel = battleHasMusic() ? "Turn on music" : "Turn on sound";
    muteBtn.innerHTML = audible
      ? `<span aria-hidden="true">🔇</span> Mute`
      : `<span aria-hidden="true">🔊</span> ${onLabel}`;
    muteBtn.title = audible ? "Mute battle music and sounds" : "Play battle music and sounds";
    muteBtn.setAttribute("aria-pressed", audible ? "true" : "false");
    muteBtn.classList.toggle("btn-primary", !audible);
    muteBtn.classList.toggle("btn-secondary", audible);
    muteBtn.classList.toggle("is-off", !audible);
  }

  // Keeps the background battle theme in sync with the current battle
  // status, and plays a one-time victory/defeat (or neutral fanfare for a
  // spectator) jingle the first time a battle is seen as finished.
  function syncMusicAndResult() {
    updateMuteBtn();
    if (truth.status === "active" || truth.status === "awaiting_forced_switch") {
      BattleAudio.startMusic();
    } else if (!victoryMusicOn) {
      BattleAudio.stopMusic();
    }
    playResultSound();
  }

  // The victory theme (or win/lose jingle), played once. Normally triggered
  // by playEventFx at the moment the win is shown on screen: the badge or
  // Elite Four/Champion message if there is one, otherwise the battle_end
  // event. syncMusicAndResult is the fallback for loading an already-
  // finished battle.
  function playResultSound() {
    if (truth.status === "finished" && !resultSoundPlayed) {
      resultSoundPlayed = true;
      const iWon = truth.you && truth.you.side && truth.winner_side === truth.you.side;
      if (iWon && wasLiveOnLoad) {
        victoryMusicOn = true;
        BattleAudio.playVictoryMusic();
        updateMuteBtn();
      } else if (truth.you && truth.you.side) {
        BattleAudio.stopMusic();
        BattleAudio.playSfx(iWon ? "victory" : "defeat");
      } else if (truth.winner_side) {
        BattleAudio.stopMusic();
        BattleAudio.playSfx("victory");
      }
    }
  }

  muteBtn.addEventListener("click", () => {
    try {
      if (isAudible()) {
        BattleAudio.setMuted(true);
      } else {
        // This click is the user gesture browsers require before audio can
        // play, so start everything from inside it.
        BattleAudio.ensureCtx();
        if (BattleAudio.isMuted()) BattleAudio.setMuted(false);
        if (battleHasMusic()) BattleAudio.startMusic();
      }
    } catch (e) {
      console.error("Battle sound toggle failed:", e);
    }
    updateMuteBtn();
  });
  BattleAudio.onStateChange(updateMuteBtn);
  updateMuteBtn();

  // ---------- Volume mixer ----------
  (function setupVolumeMixer() {
    const btn = document.getElementById("br-volume-btn");
    const panel = document.getElementById("br-volume-panel");
    const musicSlider = document.getElementById("br-vol-music");
    const fxSlider = document.getElementById("br-vol-fx");
    const musicValue = document.getElementById("br-vol-music-value");
    const fxValue = document.getElementById("br-vol-fx-value");
    if (!btn || !panel) return;

    function paint(slider, label) {
      label.textContent = `${slider.value}%`;
      slider.style.setProperty("--fill", `${slider.value}%`);
    }

    const vols = BattleAudio.getVolumes();
    musicSlider.value = Math.round(vols.music * 100);
    fxSlider.value = Math.round(vols.effects * 100);
    paint(musicSlider, musicValue);
    paint(fxSlider, fxValue);

    musicSlider.addEventListener("input", () => {
      BattleAudio.setMusicVolume(musicSlider.value / 100);
      paint(musicSlider, musicValue);
    });
    fxSlider.addEventListener("input", () => {
      BattleAudio.setEffectsVolume(fxSlider.value / 100);
      paint(fxSlider, fxValue);
    });
    // Play a short sample on release so the new effects level can be heard.
    fxSlider.addEventListener("change", () => {
      if (!BattleAudio.isMuted()) {
        BattleAudio.ensureCtx();
        BattleAudio.playSfx("hit");
      }
    });

    const motionToggle = document.getElementById("br-motion-toggle");
    if (motionToggle) {
      motionToggle.checked = BattleFX.isMotionOn();
      sceneEl.classList.toggle("motion-off", !motionToggle.checked);
      motionToggle.addEventListener("change", () => {
        BattleFX.setMotion(motionToggle.checked);
        sceneEl.classList.toggle("motion-off", !motionToggle.checked);
      });
    }

    function setOpen(open) {
      panel.hidden = !open;
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    }
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      setOpen(panel.hidden);
    });
    panel.addEventListener("click", (e) => e.stopPropagation());
    document.addEventListener("click", () => setOpen(false));
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !panel.hidden) {
        setOpen(false);
        btn.focus();
      }
    });
  })();
  // A click anywhere (a move button, accept, etc.) also counts as the user
  // gesture browsers require before audio can actually play — retry starting
  // the music here too in case the very first play() attempt was blocked.
  document.addEventListener("click", () => {
    if (!BattleAudio.isMuted()) {
      BattleAudio.ensureCtx();
      BattleAudio.retryMusic();
    }
  });

  async function postAction(path, body) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      alert(data.detail || "That action couldn't be completed.");
      return null;
    }
    return data;
  }

  function hpClass(cur, max) {
    if (max <= 0) return "hp-low";
    const pct = cur / max;
    if (pct > 0.5) return "hp-high";
    if (pct > 0.2) return "hp-mid";
    return "hp-low";
  }

  const STATUS_BADGES = {
    burn: { label: "BRN", cls: "status-brn" },
    poison: { label: "PSN", cls: "status-psn" },
    toxic: { label: "PSN", cls: "status-psn" },
    paralysis: { label: "PAR", cls: "status-par" },
    sleep: { label: "SLP", cls: "status-slp" },
    freeze: { label: "FRZ", cls: "status-frz" },
  };

  function statusBadgeHtml(status) {
    const info = STATUS_BADGES[status];
    return info ? ` <span class="status-badge ${info.cls}" title="${status}">${info.label}</span>` : "";
  }

  function animationForEvent(event) {
    switch (event.type) {
      case "faint": return "anim-faint";
      case "damage": case "confusion_self_hit": case "status_damage": case "recoil":
        return "anim-hit";
      default: return null;
    }
  }

  // Advances the visible display state for whichever side `event` is about,
  // using the fields that event type is guaranteed to carry (see
  // battle_engine.py) — every HP-changing event includes new_hp, so the HP
  // bar drains to the true intermediate value at each step rather than
  // jumping straight to the end of the whole batch.
  function applyEventEffect(event) {
    const isA = event.side === "A";
    const current = isA ? visibleA : visibleB;

    switch (event.type) {
      case "damage": case "confusion_self_hit": case "status_damage": case "recoil": case "drain": case "heal": {
        if (current && event.new_hp !== undefined) {
          const updated = { ...current, current_hp: event.new_hp };
          if (isA) visibleA = updated; else visibleB = updated;
        }
        break;
      }
      case "status_applied": {
        if (current) {
          let updated;
          if (event.status === "confusion") updated = { ...current, confused: true };
          else if (event.reason === "confusion_ended") updated = { ...current, confused: false };
          else updated = { ...current, status: event.status === "none" ? null : event.status };
          if (isA) visibleA = updated; else visibleB = updated;
        }
        break;
      }
      case "weather_start":
        visibleWeather = { weather: event.weather, turns: event.turns, suppressed: false };
        break;
      case "item_used":
      case "item_damage": {
        if (current) {
          const updated = { ...current };
          if (event.new_hp !== undefined) updated.current_hp = event.new_hp;
          if (event.type === "item_used") {
            updated.item = null;
            if (event.cured === "confusion") updated.confused = false;
            else if (event.cured) updated.status = null;
          }
          if (isA) visibleA = updated; else visibleB = updated;
        }
        break;
      }
      case "weather_tick":
        if (visibleWeather) visibleWeather = { ...visibleWeather, turns: event.turns_left };
        break;
      case "weather_end":
        visibleWeather = null;
        break;
      case "weather_damage": {
        if (current && event.new_hp !== undefined) {
          const updated = { ...current, current_hp: event.new_hp };
          if (isA) visibleA = updated; else visibleB = updated;
        }
        break;
      }
      case "stat_changed": {
        if (current) {
          const stages = { ...(current.stat_stages || {}) };
          stages[event.stat] = Math.max(-6, Math.min(6, (stages[event.stat] || 0) + event.change));
          if (!stages[event.stat]) delete stages[event.stat];
          const updated = { ...current, stat_stages: stages };
          if (isA) visibleA = updated; else visibleB = updated;
        }
        break;
      }
      case "faint": {
        if (current) {
          const updated = { ...current, current_hp: 0, is_fainted: true };
          if (isA) visibleA = updated; else visibleB = updated;
        }
        break;
      }
      default:
        break;
    }
    BattleAudio.handleEvent(event);
    revealed.push(event);
  }

  // The trainer/gym-leader/elite-four panel: portrait + name + team roster.
  // This is per-side identity, independent of whichever Pokémon is currently
  // out — that's renderHpLabel below.
  // Trainer names (and custom gym leaders, who are trainers) are user-chosen
  // text; everything else in the log/HUD comes from the game's own data.
  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  }

  function renderTrainerPanel(panelEl, name, avatar, roster, isWinner, cheers) {
    name = esc(name);
    const avatarHtml = avatar ? `<img class="battle-trainer-portrait" src="${avatar}" alt="${name}">` : "";
    const rosterStrip = (roster || [])
      .map(
        (m) =>
          `<img class="battle-roster-mon${m.is_fainted ? " is-fainted" : ""}${m.is_active ? " is-active" : ""}" src="${artThumb(m.artwork)}" alt="${m.name}" title="${m.name}">`
      )
      .join("");
    panelEl.classList.toggle("is-winner", !!isWinner);
    panelEl.innerHTML = `
        ${avatarHtml}
        <div class="battle-trainer-name">${name}</div>
        <div class="battle-roster-strip">${rosterStrip}</div>
        ${cheers ? `<div class="battle-trainer-cheers" title="Cheers from spectators">📣 ${cheers}</div>` : ""}
    `;
  }

  function renderHpLabel(labelEl, activeMon, isWinner) {
    labelEl.classList.toggle("is-winner", !!isWinner);
    if (!activeMon) {
      labelEl.innerHTML = `<p class="muted">No Pokémon</p>`;
      return;
    }
    const pct = activeMon.max_hp > 0 ? Math.max(0, Math.min(100, (activeMon.current_hp / activeMon.max_hp) * 100)) : 0;
    labelEl.innerHTML = `
        <div class="battle-hp-name">${activeMon.name}${statusBadgeHtml(activeMon.status)}${activeMon.is_fainted ? " (fainted)" : ""}</div>
        <div class="hp-bar-track"><div class="hp-bar-fill ${hpClass(activeMon.current_hp, activeMon.max_hp)}" style="width:${pct}%"></div></div>
        <div class="muted">${Math.max(0, activeMon.current_hp)}/${activeMon.max_hp} HP</div>
        ${conditionChipsHtml(activeMon)}
        ${activeMon.item ? `<div class="battle-held-item" title="${esc(activeMon.item.description)}"><img src="${activeMon.item.icon}" alt="">${esc(activeMon.item.label)}</div>` : ""}
    `;
  }

  const STAT_SHORT = {
    attack: "Atk", defense: "Def", sp_attack: "SpA", sp_defense: "SpD", speed: "Spe", accuracy: "Acc", evasion: "Eva",
  };

  // Stat-stage and confusion chips under the HP bar ("+2 Atk", "-1 Spe").
  function conditionChipsHtml(mon) {
    const chips = Object.entries(mon.stat_stages || {})
      .filter(([, v]) => v)
      .map(([k, v]) => `<span class="cond-chip ${v > 0 ? "is-up" : "is-down"}">${v > 0 ? "+" : "−"}${Math.abs(v)} ${STAT_SHORT[k] || k}</span>`);
    if (mon.confused) chips.push(`<span class="cond-chip is-confused">Confused</span>`);
    return chips.length ? `<div class="cond-chips">${chips.join("")}</div>` : "";
  }

  // Pokémon are drawn at a height that follows their real height (Charizard
  // 1.7 m stands taller than Weavile 1.1 m), compressed (height^0.7) so tiny
  // ones stay visible, and capped to fit the slot so giants like Onix don't
  // overflow. Your side's slot has a bigger scale (it's nearer the camera).
  // Trainer avatars (no height) scale from their natural size by
  // --sprite-zoom instead.
  function sizeSprite(img) {
    if (!img.naturalWidth) return;
    const cs = getComputedStyle(img);
    const maxW = parseFloat(cs.getPropertyValue("--sprite-max-w")) || 200;
    const maxH = parseFloat(cs.getPropertyValue("--sprite-max-h")) || 200;
    const metres = parseFloat(img.dataset.height);
    let scale;
    if (metres > 0) {
      const perM = parseFloat(cs.getPropertyValue("--sprite-px-per-m")) || 100;
      const minH = parseFloat(cs.getPropertyValue("--sprite-min-h")) || 40;
      const targetH = Math.max(minH, perM * Math.pow(metres, 0.7));
      scale = targetH / img.naturalHeight;
    } else {
      scale = parseFloat(cs.getPropertyValue("--sprite-zoom")) || 1;
    }
    const w = img.naturalWidth * scale;
    const h = img.naturalHeight * scale;
    const k = Math.min(1, maxW / w, maxH / h);
    img.style.width = `${Math.round(w * k)}px`;
    img.style.height = `${Math.round(h * k)}px`;
    // Pixel art stays crisp when enlarged; shrinking it looks better smoothed.
    img.style.imageRendering = scale * k >= 1 ? "pixelated" : "auto";
  }
  for (const img of [spriteA, spriteB]) {
    img.addEventListener("load", () => sizeSprite(img));
  }
  window.addEventListener("resize", () => { sizeSprite(spriteA); sizeSprite(spriteB); });

  function renderSprite(spriteEl, activeMon, animClass) {
    if (!activeMon) {
      spriteEl.style.visibility = "hidden";
      return;
    }
    spriteEl.style.visibility = "visible";
    const desiredSrc = activeMon.is_fainted && animClass !== "anim-faint" ? "" : activeMon.sprite;
    if (desiredSrc && spriteEl.dataset.mon !== `${activeMon.dex_id}:${activeMon.sprite}`) {
      spriteEl.dataset.height = activeMon.height || 1;
      spriteEl.src = activeMon.sprite;
      spriteEl.onerror = () => {
        spriteEl.onerror = null;
        spriteEl.src = activeMon.artwork;
      };
      spriteEl.dataset.mon = `${activeMon.dex_id}:${activeMon.sprite}`;
    }
    spriteEl.alt = activeMon.name;

    spriteEl.style.opacity = activeMon.is_fainted && animClass !== "anim-faint" ? "0" : "1";

    if (animClass) {
      spriteEl.classList.remove("anim-attack-a", "anim-attack-b", "anim-hit", "anim-faint", "anim-switch-in");
      void spriteEl.offsetWidth; // force reflow so the animation restarts even if the same class was just used
      spriteEl.classList.add(animClass);
    }
  }

  // Phase 1 of a send-out: show the trainer standing where their Pokémon
  // will appear, and throw a ball at them. dataset.mon is cleared so the
  // next renderSprite() call (phase 2, showing the actual Pokémon) always
  // re-assigns .src even if it happens to be the same species as before.
  function showTrainerStanding(spriteEl, ballEl, avatarUrl, name, isA) {
    spriteEl.style.visibility = "visible";
    spriteEl.style.opacity = "1";
    spriteEl.dataset.height = "";
    spriteEl.src = avatarUrl || "";
    spriteEl.alt = name;
    spriteEl.dataset.mon = "";
    spriteEl.classList.remove("anim-attack-a", "anim-attack-b", "anim-hit", "anim-faint", "anim-switch-in");
    void spriteEl.offsetWidth;
    spriteEl.classList.add("anim-switch-in");

    if (ballEl) {
      ballEl.classList.remove("anim-throw-ball-a", "anim-throw-ball-b");
      void ballEl.offsetWidth;
      ballEl.classList.add(isA ? "anim-throw-ball-a" : "anim-throw-ball-b");
    }
  }

  function typeBadge(t) {
    if (!t) return "";
    return `<span class="type-badge type-${t}">${t.charAt(0).toUpperCase() + t.slice(1)}</span>`;
  }

  function renderActionPanel(battle) {
    if (window.MoveTooltip) window.MoveTooltip.hide();
    const you = battle.you;
    forfeitBtn.hidden = true;

    if (!you || !you.side) {
      actionPanel.hidden = true;
      return;
    }

    if (battle.status === "finished" || battle.status === "abandoned") {
      actionPanel.hidden = true;
      return;
    }

    const myRoster = you.side === "A" ? battle.roster_a : battle.roster_b;
    const oppName = you.side === "A" ? battle.name_b : battle.name_a;

    if (battle.status === "pending") {
      if (you.is_pending_target) {
        actionPanel.hidden = false;
        actionPanel.innerHTML = `
            <p>${oppName} has challenged you to a battle!</p>
            <div class="battle-action-buttons">
                <button id="br-accept" class="btn-primary">Accept</button>
                <button id="br-decline" class="btn-secondary">Decline</button>
            </div>`;
        document.getElementById("br-accept").addEventListener("click", async () => {
          const data = await postAction(`/api/proxy/battles/${window.BATTLE_ID}/accept`);
          if (data) applyUpdate(data);
        });
        document.getElementById("br-decline").addEventListener("click", async () => {
          await postAction(`/api/proxy/battles/${window.BATTLE_ID}/decline`);
          location.reload();
        });
      } else {
        actionPanel.hidden = false;
        actionPanel.innerHTML = `<p class="muted">Waiting for ${oppName} to accept your challenge...</p>`;
      }
      return;
    }

    forfeitBtn.hidden = false;

    if (you.needs_forced_switch) {
      actionPanel.hidden = false;
      const options = myRoster
        .filter((_, i) => you.switchable_indices.includes(i))
        .map((m) => {
          const i = myRoster.indexOf(m);
          return `<button class="btn-secondary br-switch-option" data-index="${i}">${m.name}</button>`;
        })
        .join("");
      actionPanel.innerHTML = `<p><strong>Your Pokémon fainted!</strong> Choose your next Pokémon:</p>
          <div class="battle-action-buttons">${options}</div>`;
      actionPanel.querySelectorAll(".br-switch-option").forEach((btn) =>
        btn.addEventListener("click", async () => {
          const data = await postAction(`/api/proxy/battles/${window.BATTLE_ID}/forced-switch`, {
            team_index: parseInt(btn.dataset.index, 10),
          });
          if (data) applyUpdate(data);
        })
      );
      return;
    }

    if (you.already_locked_in) {
      actionPanel.hidden = false;
      actionPanel.innerHTML = you.waiting_on_opponent
        ? `<p class="muted">✅ Move locked in — waiting for ${oppName}...</p>`
        : `<p class="muted">✅ Move locked in.</p>`;
      return;
    }

    if (you.can_act) {
      actionPanel.hidden = false;
      const active = myRoster.find((m) => m.is_active);
      const moveButtons = (active.moves || [])
        .map((m, i) => {
          const disabled = !you.usable_move_indices.includes(i);
          return `<button class="br-move-option type-${m.type || "normal"}" data-index="${i}" ${disabled ? "disabled" : ""}>
              <span class="br-move-name">${m.name}</span>
              <span class="br-move-meta">${typeBadge(m.type)}<span class="br-move-cat">${m.category || ""}</span></span>
              <span class="br-move-pp">${m.pp}/${m.max_pp} PP</span>
          </button>`;
        })
        .join("");
      const switchOptions = myRoster
        .map((m, i) => ({ m, i }))
        .filter(({ i }) => you.switchable_indices.includes(i))
        .map(({ m, i }) => `<button class="btn-secondary br-switch-option" data-index="${i}">${m.name}</button>`)
        .join("");

      actionPanel.innerHTML = `
          <div class="battle-action-section-label">Attack</div>
          <div class="battle-action-buttons battle-move-grid">${moveButtons || "<p class='muted'>No moves available.</p>"}</div>
          ${
            you.can_switch
              ? `<div class="battle-action-section-label" style="margin-top:14px;">Switch</div>
                 <div class="battle-action-buttons">${switchOptions}</div>`
              : ""
          }
      `;
      actionPanel.querySelectorAll(".br-move-option").forEach((btn) => {
        const move = (active.moves || [])[parseInt(btn.dataset.index, 10)];
        if (move && window.MoveTooltip) window.MoveTooltip.attach(btn, move);
      });
      actionPanel.querySelectorAll(".br-move-option").forEach((btn) =>
        btn.addEventListener("click", async () => {
          if (window.MoveTooltip) window.MoveTooltip.hide();
          const data = await postAction(`/api/proxy/battles/${window.BATTLE_ID}/action`, {
            kind: "move", move_index: parseInt(btn.dataset.index, 10),
          });
          if (data) applyUpdate(data);
        })
      );
      actionPanel.querySelectorAll(".br-switch-option").forEach((btn) =>
        btn.addEventListener("click", async () => {
          const data = await postAction(`/api/proxy/battles/${window.BATTLE_ID}/action`, {
            kind: "switch", switch_to_index: parseInt(btn.dataset.index, 10),
          });
          if (data) applyUpdate(data);
        })
      );
      return;
    }

    actionPanel.hidden = true;
  }

  // Two different "subjects" show up in the log: the trainer (switch_in —
  // nameBySide) and the Pokémon acting or being acted on (moves, charging,
  // damage, recoil, status, stat changes, etc. — monBySide, tracked below from switch_in events as
  // the log is built, since the active Pokémon per side changes over the
  // battle). Mixing these up is exactly the bug where a damage line showed
  // the trainer's name instead of the Pokémon actually taking the hit.
  function formatEvent(event, rawNameBySide, monBySide) {
    const nameBySide = { A: esc(rawNameBySide.A), B: esc(rawNameBySide.B) };
    const t = event.type;
    const side = nameBySide[event.side] || "";
    // When both active Pokémon are the same species, a bare "Charizard" is
    // ambiguous, so prefix the trainer ("Brock's Charizard") in that case only.
    const mirror = Boolean(monBySide.A) && monBySide.A === monBySide.B;
    const owned = (s, name) => (mirror && nameBySide[s] ? `${nameBySide[s]}'s ${name}` : name);
    const mon = monBySide[event.side] ? owned(event.side, monBySide[event.side]) : side;
    const otherSide = event.side === "A" ? "B" : "A";
    switch (t) {
      case "turn_start":
      case "switch_out":
      case "battle_end":
        return null;
      case "switch_in":
        return `🔁 ${side} sends out <strong>${event.name}</strong>!`;
      case "move_used":
        return `<strong>${mon}</strong> used <strong>${event.move_name}</strong>!`;
      case "move_missed":
        if (event.reason === "invulnerable") {
          return `${mon}'s attack missed! <strong>${owned(otherSide, event.target_name)}</strong> was out of reach!`;
        }
        return `${mon}'s attack missed!`;
      case "move_failed": {
        const why = {
          nothing_to_return: "But there was nothing to hit back!",
          nothing_stored: "But it had no energy stored!",
          nothing_stockpiled: "But it had nothing stockpiled!",
          stockpile_full: "It can't stockpile any more!",
          no_item: "But it has no item to use!",
          immune: "It doesn't affect the target...",
          no_effect: "But it had no effect!",
        }[event.reason];
        return why ? `${mon}'s move failed! ${why}` : `${mon}'s move failed!`;
      }
      case "bide":
        return {
          start: `${mon} is storing energy!`,
          storing: `${mon} is storing energy!`,
          release: `${mon} unleashed its energy!`,
        }[event.stage] || null;
      case "stockpile":
        return `${mon} stockpiled ${event.count}!`;
      case "magnitude":
        return `Magnitude ${event.level}!`;
      case "mega_evolution":
        return `💎 <strong>${owned(event.side, event.name)}</strong>'s ${esc(MEGA_STONES[event.name] || `${event.name}ite`)} is reacting to ${side}'s Key Stone!<br>`
          + `✨ <strong>${owned(event.side, event.name)}</strong> has <span class="mega-line">Mega Evolved</span> into <strong>${esc(event.mega_name)}</strong>!`;
      case "cannot_act": {
        const reasons = {
          recharge: "must recharge!", asleep: "is fast asleep.", frozen: "is frozen solid!",
          flinched: "flinched and couldn't move!", paralyzed: "is paralyzed and can't move!",
        };
        return `${mon} ${reasons[event.reason] || "could not act."}`;
      }
      case "confusion_self_hit":
        return `${mon} is confused and hurt itself for <strong>${event.amount}</strong> damage!`;
      case "damage": {
        const suffix = {
          super_effective: " It's super effective!",
          not_very_effective: " It's not very effective...",
          no_effect: " It had no effect!",
        }[event.effectiveness] || "";
        const crit = event.is_crit ? " A critical hit!" : "";
        return `${mon} took <strong>${event.amount}</strong> damage.${crit}${suffix}`;
      }
      case "multi_hit_summary":
        return `Hit <strong>${event.hits}</strong> time(s) for <strong>${event.total_damage}</strong> total damage!`;
      case "status_applied": {
        if (!event.status || event.status === "none") {
          const texts = { woke_up: "woke up!", thawed: "thawed out!", confusion_ended: "snapped out of confusion!" };
          return `${mon} ${texts[event.reason] || "recovered!"}`;
        }
        const labels = {
          burn: "was burned!", paralysis: "was paralyzed!", poison: "was poisoned!",
          toxic: "was badly poisoned!", sleep: "fell asleep!", freeze: "was frozen solid!", confusion: "became confused!",
        };
        return `${mon} ${labels[event.status] || `was afflicted with ${event.status}!`}`;
      }
      case "stat_changed": {
        const dir = event.change > 0 ? "rose" : "fell";
        const sharply = Math.abs(event.change) >= 2 ? "sharply " : "";
        const statName = event.stat.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
        return `${mon}'s ${statName} ${sharply}${dir}!`;
      }
      case "status_damage":
      case "recoil":
        return `${mon} was hurt${event.status ? ` by its ${event.status}` : ""}! (-${event.amount})`;
      case "drain":
      case "heal":
        if (event.reason === "Leftovers") return `${mon} restored <strong>${event.amount}</strong> HP using its Leftovers!`;
        if (event.reason === "Healing Wish") return `✨ The healing wish came true for <strong>${mon}</strong>!`;
        if (event.reason === "Lunar Dance") return `🌙 <strong>${mon}</strong> became cloaked in mystical moonlight!`;
        return event.reason
          ? `${mon} restored <strong>${event.amount}</strong> HP with ${esc(event.reason)}!`
          : `${mon} restored <strong>${event.amount}</strong> HP!`;
      case "item_used": {
        if (event.reason === "Fling") return `${mon} flung its <strong>${esc(event.label)}</strong>!`;
        if (event.reason === "Natural Gift") return `${mon} drew power from its <strong>${esc(event.label)}</strong>!`;
        if (event.item === "focus-sash") return `${mon} hung on using its <strong>Focus Sash</strong>!`;
        if (event.item === "sitrus-berry") return `${mon} restored <strong>${event.amount}</strong> HP with its <strong>Sitrus Berry</strong>!`;
        if (event.item === "lum-berry") {
          const what = { burn: "burn", paralysis: "paralysis", poison: "poison", toxic: "poison", sleep: "sleep", freeze: "freeze", confusion: "confusion" }[event.cured] || "status";
          return `${mon}'s <strong>Lum Berry</strong> cured its ${what}!`;
        }
        return `${mon} used its <strong>${esc(event.label)}</strong>!`;
      }
      case "item_damage":
        return `${mon} lost some of its HP to its <strong>${esc(event.label)}</strong>! (-${event.amount})`;
      case "item_activated":
        return `${mon}'s <strong>${esc(event.label)}</strong> let it move first!`;
      case "self_ko":
        return event.move_name === "Explosion" || event.move_name === "Self Destruct"
          ? null  // the faint line right after says it all
          : `${mon} gave everything it had!`;
      case "weather_start": {
        const text = {
          sun: "The sunlight turned harsh!", rain: "It started to rain!",
          sand: "A sandstorm kicked up!", hail: "It started to hail!",
        }[event.weather] || "The weather changed!";
        const icon = (WEATHER_INFO[event.weather] || {}).icon || "";
        const rock = event.item ? ` <span class="muted">(${esc(event.item)}: ${event.turns} turns)</span>` : "";
        return event.source === "ability"
          ? `${icon} <strong>${mon}</strong>'s ${esc(event.ability)}: ${text}${rock}`
          : `${icon} ${text}${rock}`;
      }
      case "weather_end":
        return {
          sun: "☀️ The harsh sunlight faded.", rain: "🌧️ The rain stopped.",
          sand: "🌪️ The sandstorm subsided.", hail: "🌨️ The hail stopped.",
        }[event.weather] || "The weather cleared.";
      case "weather_damage":
        if (event.reason === "sand") return `${mon} is buffeted by the sandstorm!`;
        if (event.reason === "hail") return `${mon} is pelted by hail!`;
        return `${mon} is hurt by its ${esc(event.reason)}!`;
      case "charge_start": {
        const flavor = {
          Fly: "flew up high!", Bounce: "sprang up!", Dig: "burrowed underground!", Dive: "hid underwater!",
        };
        return `${mon} ${flavor[event.move_name] || "is charging its attack!"}`;
      }
      case "faint":
        return `💀 <strong>${owned(event.side, event.name)}</strong> fainted!`;
      case "league_defeated": {
        const isYou = truth.you && truth.you.side === event.side;
        const region = event.region ? event.region.charAt(0).toUpperCase() + event.region.slice(1) : "";
        const speech = event.quote ? `💬 <strong>${esc(event.name)}</strong>: “${esc(event.quote)}”<br>` : "";
        if (event.role === "champion") {
          return `${speech}👑 Congratulations! ${isYou ? "You are" : `${side} is`} the new <strong>${esc(region)} Champion</strong>!`;
        }
        return `${speech}🏆 ${isYou ? "You" : side} defeated Elite Four <strong>${esc(event.name)}</strong>!`;
      }
      case "badge_awarded": {
        const recipient = truth.you && truth.you.side === event.side ? "you" : side;
        return `🏅 <strong>${esc(event.leader_name)}</strong> rewards ${recipient} with the <strong>${esc(event.badge_name)}</strong>! Congratulations!`;
      }
      default:
        return null;
    }
  }

  function renderLog() {
    const nameBySide = { A: truth.name_a, B: truth.name_b };
    // Walk the log in chronological order tracking which Pokémon is active
    // per side as of each event (switch_in is the only event that changes
    // it), so every line resolves against whoever was actually on the
    // field at that point rather than always using the current one.
    const monBySide = { A: null, B: null };
    const lines = [];
    for (const e of revealed) {
      if (e.type === "switch_in") monBySide[e.side] = e.name;
      const line = formatEvent(e, nameBySide, monBySide);
      if (line) lines.push(line);
      if (e.type === "mega_evolution") monBySide[e.side] = e.mega_name;
    }
    logEl.innerHTML = lines.length
      ? lines.map((l) => `<div class="battle-log-line">${l}</div>`).reverse().join("")
      : "<p class='muted'>The battle begins!</p>";
  }

  function renderMeta() {
    titleEl.textContent = `${truth.name_a} vs ${truth.name_b}`;
    const BATTLE_TYPE_LABELS = { pvp: "PvP", gym: "Gym", custom_gym: "Custom Gym", trainer: "Trainer", elite_four: "Elite Four", champion: "Champion" };
    const typeLabel = BATTLE_TYPE_LABELS[truth.battle_type]
      || truth.battle_type.charAt(0).toUpperCase() + truth.battle_type.slice(1);
    subtitleEl.textContent =
      truth.status === "finished" ? `${typeLabel} battle · Finished`
      : truth.status === "abandoned" ? `${typeLabel} battle · Abandoned`
      : truth.status === "pending" ? `${typeLabel} battle · Awaiting response`
      : `${typeLabel} battle · Turn ${truth.turn_number}`;
    turnBadgeEl.textContent = truth.status === "pending" ? "Challenge" : `Turn ${truth.turn_number}`;
  }

  // Where "Leave battle" goes: back to the page the battle was started from.
  function leaveDestination() {
    if (truth.battle_type === "gym" || truth.battle_type === "custom_gym") return "/gyms";
    if (truth.battle_type === "elite_four" || truth.battle_type === "champion") return "/league";
    return "/battles";
  }

  // Result text plus a Leave button colored by the viewer's own outcome:
  // green if they won, red if they lost, neutral for spectators, draws and
  // abandoned battles. Built with DOM nodes (not innerHTML) since the
  // winner's name is a user-chosen display name.
  function renderResultBanner() {
    let message = null;
    if (truth.status === "finished") {
      const lastEvent = truth.events[truth.events.length - 1];
      const forfeited = lastEvent && lastEvent.type === "battle_end" && lastEvent.reason === "forfeit";
      message = truth.winner_name
        ? `🏆 ${truth.winner_name} wins!${forfeited ? " (forfeit)" : ""}`
        : "The battle ended in a draw.";
    } else if (truth.status === "abandoned") {
      message = "⏱️ This battle was abandoned due to inactivity.";
    }
    if (!message) {
      resultEl.hidden = true;
      return;
    }

    const mySide = truth.you && truth.you.side;
    const outcome = !mySide || !truth.winner_side || truth.status !== "finished"
      ? "neutral"
      : truth.winner_side === mySide ? "win" : "loss";

    resultEl.replaceChildren();
    const text = document.createElement("div");
    text.className = "battle-result-text";
    text.textContent = message;
    const leave = document.createElement("a");
    leave.href = leaveDestination();
    leave.className = `battle-leave-btn is-${outcome}`;
    leave.textContent = outcome === "win" ? "🎉 Leave battle" : "Leave battle";
    resultEl.append(text, leave);
    resultEl.hidden = false;
  }

  // `truth` is already the end-of-turn state while a turn is still being
  // played back, so anything drawn from it directly would spoil the outcome.
  // The winner highlight waits until the battle_end event has played, and
  // the roster strip only shows faints / the active Pokémon as of playback.
  function shownWinner() {
    return revealed.some((e) => e.type === "battle_end") ? truth.winner_side : null;
  }

  function rosterAsShown(roster, side, visibleMon) {
    const unplayedFaints = new Set(
      pendingEvents.filter((e) => e.type === "faint" && e.side === side).map((e) => e.dex_id)
    );
    return (roster || []).map((m) => {
      const shown = megaNotYetShown(m) ? { ...m, ...m.pre_mega } : m;
      return {
        ...shown,
        is_fainted: m.is_fainted && !unplayedFaints.has(m.dex_id),
        is_active: visibleMon ? sameMon(m, visibleMon) : m.is_active,
      };
    });
  }

  // A Pokémon that Mega Evolved this batch still looks like its base form
  // until that event plays. Roster entries are the Mega from then on.
  function megaNotYetShown(m) {
    return Boolean(m.pre_mega) && pendingEvents.some((e) => e.type === "mega_evolution" && e.mega_dex_id === m.dex_id);
  }

  function sameMon(rosterMon, shown) {
    return rosterMon.dex_id === shown.dex_id || Boolean(rosterMon.pre_mega && rosterMon.pre_mega.dex_id === shown.dex_id);
  }

  function renderFrame(animA, animB) {
    const cheers = truth.cheers || {};
    const winner = shownWinner();
    renderTrainerPanel(trainerPanelA, truth.name_a, truth.avatar_a, rosterAsShown(truth.roster_a, "A", visibleA), winner === "A", cheers.A);
    renderTrainerPanel(trainerPanelB, truth.name_b, truth.avatar_b, rosterAsShown(truth.roster_b, "B", visibleB), winner === "B", cheers.B);
    renderHpLabel(hpLabelA, visibleA, winner === "A");
    renderHpLabel(hpLabelB, visibleB, winner === "B");
    renderSprite(spriteA, visibleA, animA);
    renderSprite(spriteB, visibleB, animB);
    renderWeather();
    renderLog();
    renderMeta();
  }

  // A switch-in plays as its own two-phase beat instead of the generic
  // per-event loop: first the trainer appears and throws a ball (with the
  // "X sends out Y!" log line showing immediately), then after a beat the
  // ball vanishes and the actual Pokémon is revealed.
  async function playSwitchIn(event) {
    const isA = event.side === "A";
    revealed.push(event);
    renderLog();
    renderMeta();
    BattleFX.caption(currentLineFor(event), false);

    const avatarUrl = isA ? truth.avatar_a : truth.avatar_b;
    const trainerName = isA ? truth.name_a : truth.name_b;
    showTrainerStanding(isA ? spriteA : spriteB, isA ? ballA : ballB, avatarUrl, trainerName, isA);
    await wait(SEND_OUT_THROW_MS);

    const roster = isA ? truth.roster_a : truth.roster_b;
    const match = roster.find((m) => sameMon(m, event) && !m.is_fainted) || roster.find((m) => sameMon(m, event));
    const shown = match && match.dex_id !== event.dex_id && match.pre_mega ? { ...match, ...match.pre_mega } : match;
    if (isA) visibleA = shown ? { ...shown } : visibleA;
    else visibleB = shown ? { ...shown } : visibleB;

    BattleAudio.handleEvent(event); // the cry plays as the Pokémon itself appears
    renderFrame(isA ? "anim-switch-in" : null, !isA ? "anim-switch-in" : null);
    await wait(SEND_OUT_SETTLE_MS);
  }

  // Mega Evolution plays as its own beat: the energy gathers around the
  // base form, then at the flash the sprite, name and types become the Mega's.
  async function playMegaEvolution(event) {
    const isA = event.side === "A";
    revealed.push(event);
    renderLog();
    BattleFX.caption(currentLineFor(event), false);
    // Load and decode the Mega's sprite during the build-up, so the swap at
    // the flash doesn't stall on it.
    const megaEntry = (isA ? truth.roster_a : truth.roster_b).find((m) => m.dex_id === event.mega_dex_id);
    if (megaEntry && megaEntry.sprite) {
      const pre = new Image();
      pre.src = megaEntry.sprite;
      if (pre.decode) pre.decode().catch(() => {});
    }
    await BattleFX.megaEvolve(isA ? slotA : slotB, isA ? spriteA : spriteB, () => {
      const roster = isA ? truth.roster_a : truth.roster_b;
      const mega = roster.find((m) => m.dex_id === event.mega_dex_id);
      const current = isA ? visibleA : visibleB;
      if (mega && current) {
        const { dex_id, name, sprite, artwork, types, height, pre_mega } = mega;
        const updated = { ...current, dex_id, name, sprite, artwork, types, height, pre_mega };
        if (isA) visibleA = updated; else visibleB = updated;
      }
      BattleAudio.playCry(event.mega_dex_id);
      renderFrame(null, null);
    });
  }

  // The log line for an event as of *now* (who's active on each side), for
  // the in-scene caption box.
  function currentLineFor(event) {
    const nameBySide = { A: truth.name_a, B: truth.name_b };
    const monBySide = { A: null, B: null };
    for (const e of revealed) {
      if (e === event) break;
      if (e.type === "switch_in") monBySide[e.side] = e.name;
      if (e.type === "mega_evolution") monBySide[e.side] = e.mega_name;
    }
    return formatEvent(event, nameBySide, monBySide);
  }

  // Events that begin a new "beat" (someone acting) start a fresh caption;
  // their consequences (damage, status, stat changes) append beneath it.
  const CAPTION_STARTERS = new Set(["move_used", "charge_start", "cannot_act", "switch_in", "status_damage", "badge_awarded", "league_defeated", "weather_end", "weather_damage", "item_activated"]);

  function pctOf(amount, max) {
    if (!max) return "?";
    const pct = Math.round((amount / max) * 100);
    return pct < 1 && amount > 0 ? "<1" : String(pct);
  }

  // Visual effects for one event; resolves when its animation is done.
  function playEventFx(event) {
    const line = currentLineFor(event);
    if (line) BattleFX.caption(line, !CAPTION_STARTERS.has(event.type));

    const isA = event.side === "A";
    const slot = isA ? slotA : slotB;
    const otherSlot = isA ? slotB : slotA;
    const sprite = isA ? spriteA : spriteB;
    const mon = isA ? visibleA : visibleB;
    const maxHp = event.max_hp || (mon && mon.max_hp);

    switch (event.type) {
      case "move_used": {
        // Older events predate move_type/category, so fall back to the
        // viewer's own move data when it's theirs, else a generic hit.
        let e = event;
        if (!e.move_type) {
          const roster = isA ? truth.roster_a : truth.roster_b;
          const known = roster.flatMap((m) => m.moves || []).find((m) => m.name === e.move_name);
          e = { ...e, move_type: known && known.type, category: (known && known.category) || "physical", target: known && known.target };
        }
        return BattleFX.playMove(e, slot, otherSlot, sprite, isA ? spriteB : spriteA);
      }
      case "charge_start":
        BattleFX.glowSprite(sprite, BattleFX.colorForType(event.move_type) || "#ffffff", 700);
        return wait(500);
      case "damage":
      case "confusion_self_hit": {
        if (event.effectiveness === "no_effect") {
          BattleFX.floatText(slot, "No effect", "info");
          return null;
        }
        BattleFX.floatText(slot, `−${pctOf(event.amount, maxHp)}%`, event.is_crit ? "crit" : "damage");
        if (event.is_crit) setTimeout(() => BattleFX.floatText(slot, "Critical hit!", "crit"), 180);
        if (event.effectiveness === "super_effective") setTimeout(() => BattleFX.floatText(slot, "Super effective!", "super"), 320);
        if (event.effectiveness === "not_very_effective") setTimeout(() => BattleFX.floatText(slot, "Not very effective", "info"), 320);
        return null;
      }
      case "status_damage":
        BattleFX.statusEffect(slot, event.status, sprite);
        BattleFX.floatText(slot, `−${pctOf(event.amount, maxHp)}%`, "damage");
        return null;
      case "recoil":
        BattleFX.floatText(slot, `−${pctOf(event.amount, maxHp)}% recoil`, "damage");
        return null;
      case "drain":
      case "heal":
        BattleFX.healSparkles(slot);
        BattleFX.floatText(slot, `+${pctOf(event.amount, maxHp)}%`, "heal");
        return null;
      case "status_applied": {
        if (event.status === "none") {
          const label = { woke_up: "Woke up!", thawed: "Thawed out!", confusion_ended: "Snapped out of it" }[event.reason] || "Recovered";
          BattleFX.floatText(slot, label, "info");
          return null;
        }
        const info = BattleFX.STATUS_FX[event.status];
        BattleFX.statusEffect(slot, event.status, sprite);
        if (info) BattleFX.floatText(slot, info.label, `status-${event.status}`);
        return wait(300);
      }
      case "stat_changed": {
        const up = event.change > 0;
        BattleFX.statArrows(slot, up);
        BattleFX.floatText(slot, `${STAT_SHORT[event.stat] || event.stat} ${up ? "+" : "−"}${Math.abs(event.change)}`, up ? "stat-up" : "stat-down");
        return wait(250);
      }
      case "battle_end":
        // Wait for the badge / defeat message if one follows, so the music
        // lands together with it.
        if (!pendingEvents.some((e) => e.type === "badge_awarded" || e.type === "league_defeated")) playResultSound();
        return null;
      case "weather_start":
        // A move (Sunny Day...) already animated itself; an ability gets a glow
        // on its Pokémon plus the weather sweeping in.
        if (event.source === "ability") {
          BattleFX.glowSprite(sprite, "#ffe08a", 900);
          if (event.ability) BattleFX.floatText(slot, event.ability, "info");
          if (window.BattleMoves && window.BattleMoves.weather && !BattleFX.kit.reduceMotion) window.BattleMoves.weather(event.weather);
        }
        return null;
      case "weather_damage":
        BattleFX.floatText(slot, `\u2212${pctOf(event.amount, maxHp)}%`, "damage");
        sprite.classList.remove("anim-hit");
        void sprite.offsetWidth;
        sprite.classList.add("anim-hit");
        return null;
      case "item_used":
        BattleFX.glowSprite(sprite, "#ffe08a", 700);
        BattleFX.floatText(slot, event.label, "info");
        if (event.amount) {
          BattleFX.healSparkles(slot);
          setTimeout(() => BattleFX.floatText(slot, `+${pctOf(event.amount, maxHp)}%`, "heal"), 250);
        }
        return wait(250);
      case "item_damage":
        BattleFX.floatText(slot, `−${pctOf(event.amount, maxHp)}% ${event.label}`, "damage");
        return null;
      case "item_activated":
        BattleFX.glowSprite(sprite, "#ffffff", 500);
        BattleFX.floatText(slot, event.label, "info");
        return null;
      case "badge_awarded":
        playResultSound();
        if (event.badge_image) BattleFX.showBadge(event.badge_image, event.badge_name);
        return null;
      case "league_defeated": {
        playResultSound();
        // Hold long enough to actually read the speech (Champions say more).
        const grand = event.role === "champion";
        const readMs = Math.min(14000, 2600 + (event.quote || "").length * 32);
        if (event.portrait) BattleFX.showBadge(event.portrait, event.name, { grand, duration: readMs });
        return wait(readMs);
      }
      case "stat_change_fizzled":
        BattleFX.floatText(slot, `${STAT_SHORT[event.stat] || event.stat} won't change`, "info");
        return null;
      case "move_missed":
        BattleFX.floatText(otherSlot, "Missed!", "miss");
        return null;
      case "move_failed":
        BattleFX.floatText(slot, "Failed!", "miss");
        return null;
      case "cannot_act": {
        const byReason = { paralyzed: "paralysis", asleep: "sleep", frozen: "freeze" };
        if (byReason[event.reason]) BattleFX.statusEffect(slot, byReason[event.reason], sprite);
        const label = { recharge: "Recharging", flinched: "Flinched!", paralyzed: "Fully paralyzed", asleep: "Asleep", frozen: "Frozen solid" }[event.reason];
        if (label) BattleFX.floatText(slot, label, "info");
        return null;
      }
      default:
        return null;
    }
  }

  // Plays every queued event one at a time — HP bars drain to each event's
  // true intermediate value, sprites animate per-event, and the log grows
  // one line at a time, so a turn reads the same way it would in-game: one
  // side's whole action resolves before the other's starts.
  async function playQueue() {
    while (pendingEvents.length) {
      const event = pendingEvents.shift();
      if (event.type === "switch_in") {
        await playSwitchIn(event);
        continue;
      }
      if (event.type === "mega_evolution") {
        await playMegaEvolution(event);
        continue;
      }
      applyEventEffect(event);
      const animA = event.side === "A" ? animationForEvent(event) : null;
      const animB = event.side === "B" ? animationForEvent(event) : null;
      renderFrame(animA, animB);
      const fx = playEventFx(event);
      await Promise.all([wait(EVENT_DELAYS[event.type] || DEFAULT_EVENT_DELAY), fx]);
    }
    BattleFX.fadeCaption();

    // Safety-net resync in case any edge case in applyEventEffect drifted
    // from the server's authoritative state.
    visibleA = truth.roster_a.find((m) => m.is_active) || truth.roster_a[0] || null;
    visibleB = truth.roster_b.find((m) => m.is_active) || truth.roster_b[0] || null;
    visibleWeather = weatherFromTruth();
    renderFrame(null, null);
    renderResultBanner();
    renderActionPanel(truth);
    syncMusicAndResult();
  }

  // ---------- Spectator cheers ----------
  const cheerBar = document.getElementById("br-cheer-bar");
  const cheerStatus = document.getElementById("br-cheer-status");
  const CHEER_LINES = ["Go, go, {t}!", "You got this, {t}!", "Let's goooo {t}!", "{t} for the win!", "Come on, {t}!", "Show 'em, {t}!"];
  const CHEER_EMOJIS = ["📣", "🎉", "👏", "🔥", "⭐", "💪"];
  let lastCheerId = Math.max(0, ...((truth.cheers && truth.cheers.recent) || []).map((c) => c.id));
  let cheerCooldownUntil = 0;

  // New cheers since the last update pop up by the cheered trainer's panel,
  // whether or not a turn is mid-animation.
  function handleCheers(battle) {
    const recent = (battle.cheers && battle.cheers.recent) || [];
    for (const c of recent) {
      if (c.id <= lastCheerId) continue;
      lastCheerId = c.id;
      showCheer(c);
    }
    renderCheerBar(battle);
    const cheers = battle.cheers || {};
    const countA = trainerPanelA.querySelector(".battle-trainer-cheers");
    const countB = trainerPanelB.querySelector(".battle-trainer-cheers");
    if (playing) {
      // renderFrame will redraw the panels at the next step; update the
      // counters now so they don't lag behind a long animation.
      if (countA && cheers.A) countA.textContent = `📣 ${cheers.A}`;
      if (countB && cheers.B) countB.textContent = `📣 ${cheers.B}`;
    }
  }

  function showCheer(c) {
    const panel = c.side === "A" ? trainerPanelA : trainerPanelB;
    const trainer = c.side === "A" ? truth.name_a : truth.name_b;
    const line = CHEER_LINES[c.id % CHEER_LINES.length].replace("{t}", trainer);
    const bubble = document.createElement("div");
    bubble.className = `battle-cheer-bubble is-${c.side === "A" ? "a" : "b"}`;
    bubble.innerHTML = `<strong>${esc(c.name)}</strong>: ${esc(line)}`;
    sceneEl.appendChild(bubble);
    setTimeout(() => bubble.remove(), 2600);
    // Just above your side's panel (bottom-left), just below the foe's (top-right).
    const pr = panel.getBoundingClientRect();
    const sr = sceneEl.getBoundingClientRect();
    if (c.side === "A") {
      bubble.style.left = `${Math.max(8, pr.left - sr.left)}px`;
      bubble.style.bottom = `${sr.bottom - pr.top + 8}px`;
    } else {
      bubble.style.right = `${Math.max(8, sr.right - pr.right)}px`;
      bubble.style.top = `${pr.bottom - sr.top + 8}px`;
    }
    if (BattleFX.kit.reduceMotion) return;
    for (let i = 0; i < 6; i++) {
      const e = document.createElement("span");
      e.className = "battle-cheer-emoji";
      e.textContent = CHEER_EMOJIS[(c.id + i) % CHEER_EMOJIS.length];
      e.style.left = `${pr.left - sr.left + pr.width * (0.15 + Math.random() * 0.7)}px`;
      e.style.top = `${pr.top - sr.top + pr.height * 0.4}px`;
      e.style.setProperty("--dx", `${(Math.random() - 0.5) * 60}px`);
      e.style.animationDelay = `${i * 70}ms`;
      sceneEl.appendChild(e);
      setTimeout(() => e.remove(), 1800 + i * 70);
    }
  }

  function renderCheerBar(battle) {
    const cheers = battle.cheers || {};
    cheerBar.hidden = !cheers.can_cheer;
    if (!cheers.can_cheer) return;
    for (const btn of cheerBar.querySelectorAll(".battle-cheer-btn")) {
      const side = btn.dataset.side;
      const name = side === "A" ? battle.name_a : battle.name_b;
      btn.innerHTML = `📣 Cheer for <strong>${esc(name)}</strong> <span class="battle-cheer-count">${cheers[side] || 0}</span>`;
      btn.disabled = Date.now() < cheerCooldownUntil;
    }
  }

  cheerBar.addEventListener("click", async (e) => {
    const btn = e.target.closest(".battle-cheer-btn");
    if (!btn || btn.disabled) return;
    cheerCooldownUntil = Date.now() + 2100;
    for (const b of cheerBar.querySelectorAll(".battle-cheer-btn")) b.disabled = true;
    setTimeout(() => renderCheerBar(truth), 2150);
    cheerStatus.textContent = "";
    try {
      const res = await fetch(`/api/proxy/battles/${window.BATTLE_ID}/cheer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ side: btn.dataset.side }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        cheerStatus.textContent = data.detail || "Couldn't cheer right now.";
      } else if (!usingWs) {
        poll();
      }
    } catch (err) {
      cheerStatus.textContent = "Couldn't cheer right now.";
    }
  });

  function applyUpdate(battle) {
    handleCheers(battle);
    truth = battle;
    const newEvents = unseenEvents(battle.events);
    markAnimated(battle.events);

    if (!newEvents.length) {
      // No new history to play (e.g. accept/decline, a reconnect with
      // nothing new, or this exact update arriving twice — submitting a
      // move gets its result both as the fetch response AND as this same
      // client's own WebSocket broadcast). If an animation is already
      // mid-flight, `truth` is now current but the in-progress playQueue()
      // owns visibleA/visibleB/revealed until it finishes and runs this
      // same resync itself — stomping on it here mid-faint is what used to
      // make the wrong Pokémon appear to faint. Just leave it alone.
      if (playing) return;
      visibleA = truth.roster_a.find((m) => m.is_active) || truth.roster_a[0] || null;
      visibleB = truth.roster_b.find((m) => m.is_active) || truth.roster_b[0] || null;
      revealed = truth.events.slice();
      visibleWeather = weatherFromTruth();
      renderFrame(null, null);
      renderResultBanner();
      renderActionPanel(truth);
      syncMusicAndResult();
      return;
    }

    pendingEvents.push(...newEvents);
    if (!playing) {
      playing = true;
      playQueue().finally(() => {
        playing = false;
      });
    }
  }

  async function poll() {
    try {
      const res = await fetch(`/api/proxy/battles/${window.BATTLE_ID}`);
      if (!res.ok) return;
      applyUpdate(await res.json());
    } catch (e) {
      // transient network hiccup — next tick retries
    }
  }

  function startPolling() {
    if (pollTimer) return;
    connectionStatusEl.textContent = "Live (polling)";
    pollTimer = setInterval(poll, POLL_INTERVAL_MS);
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function connectWs() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(`${proto}//${location.host}/ws/battles/${window.BATTLE_ID}`);
    ws = socket;

    wsConnectTimer = setTimeout(() => {
      if (!usingWs) {
        socket.close();
        startPolling();
      }
    }, WS_CONNECT_TIMEOUT_MS);

    socket.addEventListener("message", (event) => {
      usingWs = true;
      clearTimeout(wsConnectTimer);
      stopPolling();
      connectionStatusEl.textContent = "Live";
      try {
        applyUpdate(JSON.parse(event.data));
      } catch (e) {
        // ignore malformed frame
      }
    });

    socket.addEventListener("close", () => {
      if (truth.status === "finished" || truth.status === "abandoned") return;
      usingWs = false;
      startPolling();
      // Try to reconnect in the background; if it works we drop back to push updates.
      setTimeout(connectWs, 4000);
    });

    socket.addEventListener("error", () => {
      // "close" fires right after — handled there.
    });
  }

  // Initial render: normally shows the full existing history immediately,
  // with no playback delay, so reopening a battle already many turns in
  // doesn't replay all of it — only events that arrive AFTER this point get
  // the one-at-a-time treatment. The one exception is a battle nobody has
  // acted in yet (just the opening send-outs): that gets the same animated
  // beat a live switch-in gets, so the very first "X sends out Y!" throw
  // isn't the one send-out in the whole battle that never plays.
  const onlyOpeningSendOuts =
    truth.events.length > 0 && truth.events.every((e) => e.type === "turn_start" || e.type === "switch_in" || e.type === "weather_start");

  if (onlyOpeningSendOuts) {
    visibleA = null;
    visibleB = null;
    revealed = [];
    // Mark these as already claimed for animation *before* playQueue starts
    // (not after) — connectWs()'s first message can otherwise land mid- or
    // right-after-animation and, seeing nothing marked as played yet,
    // re-queue the exact same send-outs a second time.
    markAnimated(truth.events);
    pendingEvents = truth.events.slice();
    renderFrame(null, null);
    playing = true;
    playQueue().finally(() => {
      playing = false;
    });
  } else {
    visibleA = truth.roster_a.find((m) => m.is_active) || truth.roster_a[0] || null;
    visibleB = truth.roster_b.find((m) => m.is_active) || truth.roster_b[0] || null;
    revealed = truth.events.slice();
    markAnimated(truth.events);
    visibleWeather = weatherFromTruth();
    renderFrame(null, null);
    renderResultBanner();
    renderActionPanel(truth);
    syncMusicAndResult();
  }
  renderCheerBar(truth);

  if (truth.status !== "finished" && truth.status !== "abandoned") {
    connectWs();
  } else {
    connectionStatusEl.textContent = "Finished";
  }

  forfeitBtn.addEventListener("click", async () => {
    if (!confirm("Forfeit this battle?")) return;
    const data = await postAction(`/api/proxy/battles/${window.BATTLE_ID}/forfeit`);
    if (data) applyUpdate(data);
  });
})();
