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
  };
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
  (function setupArena() {
    const kind = { gym: "gym", elite_four: "elite", champion: "champion" }[truth.battle_type] || "field";
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

  function wait(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  // "Audible" means the user can actually hear the battle right now: sound
  // isn't muted AND, if a theme should be playing, it really is. Browsers
  // block audio until the first click, so a saved "sound on" preference alone
  // isn't enough — until playback starts the button offers to turn it on.
  function battleHasMusic() {
    return truth.status === "active" || truth.status === "awaiting_forced_switch";
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
    } else {
      BattleAudio.stopMusic();
    }
    if (truth.status === "finished" && !resultSoundPlayed) {
      resultSoundPlayed = true;
      if (truth.you && truth.you.side) {
        BattleAudio.playSfx(truth.winner_side === truth.you.side ? "victory" : "defeat");
      } else if (truth.winner_side) {
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
      motionToggle.addEventListener("change", () => BattleFX.setMotion(motionToggle.checked));
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
  function renderTrainerPanel(panelEl, name, avatar, roster, isWinner) {
    const avatarHtml = avatar ? `<img class="battle-trainer-portrait" src="${avatar}" alt="${name}">` : "";
    const rosterStrip = (roster || [])
      .map(
        (m) =>
          `<img class="battle-roster-mon${m.is_fainted ? " is-fainted" : ""}${m.is_active ? " is-active" : ""}" src="${m.artwork}" alt="${m.name}" title="${m.name}">`
      )
      .join("");
    panelEl.classList.toggle("is-winner", !!isWinner);
    panelEl.innerHTML = `
        ${avatarHtml}
        <div class="battle-trainer-name">${name}</div>
        <div class="battle-roster-strip">${rosterStrip}</div>
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

  function renderSprite(spriteEl, activeMon, animClass) {
    if (!activeMon) {
      spriteEl.style.visibility = "hidden";
      return;
    }
    spriteEl.style.visibility = "visible";
    const desiredSrc = activeMon.is_fainted && animClass !== "anim-faint" ? "" : activeMon.sprite;
    if (desiredSrc && spriteEl.dataset.mon !== `${activeMon.dex_id}:${activeMon.sprite}`) {
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
  function formatEvent(event, nameBySide, monBySide) {
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
      case "move_failed":
        return `${mon}'s move failed!`;
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
        return `${mon} restored <strong>${event.amount}</strong> HP!`;
      case "charge_start": {
        const flavor = {
          Fly: "flew up high!", Bounce: "sprang up!", Dig: "burrowed underground!", Dive: "hid underwater!",
        };
        return `${mon} ${flavor[event.move_name] || "is charging its attack!"}`;
      }
      case "faint":
        return `💀 <strong>${owned(event.side, event.name)}</strong> fainted!`;
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
    }
    logEl.innerHTML = lines.length
      ? lines.map((l) => `<div class="battle-log-line">${l}</div>`).reverse().join("")
      : "<p class='muted'>The battle begins!</p>";
  }

  function renderMeta() {
    titleEl.textContent = `${truth.name_a} vs ${truth.name_b}`;
    const BATTLE_TYPE_LABELS = { pvp: "PvP", gym: "Gym", trainer: "Trainer", elite_four: "Elite Four", champion: "Champion" };
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
    if (truth.battle_type === "gym") return "/gyms";
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

  function renderFrame(animA, animB) {
    renderTrainerPanel(trainerPanelA, truth.name_a, truth.avatar_a, truth.roster_a, truth.winner_side === "A");
    renderTrainerPanel(trainerPanelB, truth.name_b, truth.avatar_b, truth.roster_b, truth.winner_side === "B");
    renderHpLabel(hpLabelA, visibleA, truth.winner_side === "A");
    renderHpLabel(hpLabelB, visibleB, truth.winner_side === "B");
    renderSprite(spriteA, visibleA, animA);
    renderSprite(spriteB, visibleB, animB);
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
    const match = roster.find((m) => m.dex_id === event.dex_id && !m.is_fainted) || roster.find((m) => m.dex_id === event.dex_id);
    if (isA) visibleA = match ? { ...match } : visibleA;
    else visibleB = match ? { ...match } : visibleB;

    BattleAudio.handleEvent(event); // the cry plays as the Pokémon itself appears
    renderFrame(isA ? "anim-switch-in" : null, !isA ? "anim-switch-in" : null);
    await wait(SEND_OUT_SETTLE_MS);
  }

  // The log line for an event as of *now* (who's active on each side), for
  // the in-scene caption box.
  function currentLineFor(event) {
    const nameBySide = { A: truth.name_a, B: truth.name_b };
    const monBySide = { A: null, B: null };
    for (const e of revealed) if (e.type === "switch_in") monBySide[e.side] = e.name;
    return formatEvent(event, nameBySide, monBySide);
  }

  // Events that begin a new "beat" (someone acting) start a fresh caption;
  // their consequences (damage, status, stat changes) append beneath it.
  const CAPTION_STARTERS = new Set(["move_used", "charge_start", "cannot_act", "switch_in", "status_damage"]);

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
    renderFrame(null, null);
    renderResultBanner();
    renderActionPanel(truth);
    syncMusicAndResult();
  }

  function applyUpdate(battle) {
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
    truth.events.length > 0 && truth.events.every((e) => e.type === "turn_start" || e.type === "switch_in");

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
    renderFrame(null, null);
    renderResultBanner();
    renderActionPanel(truth);
    syncMusicAndResult();
  }

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
