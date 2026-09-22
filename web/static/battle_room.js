(function () {
  const POLL_INTERVAL_MS = 2000;
  const WS_CONNECT_TIMEOUT_MS = 3000;

  const titleEl = document.getElementById("br-title");
  const subtitleEl = document.getElementById("br-subtitle");
  const resultEl = document.getElementById("br-result");
  const turnBadgeEl = document.getElementById("br-turn-badge");
  const hudA = document.getElementById("br-hud-a");
  const hudB = document.getElementById("br-hud-b");
  const spriteA = document.getElementById("br-sprite-a");
  const spriteB = document.getElementById("br-sprite-b");
  const logEl = document.getElementById("br-log");
  const actionPanel = document.getElementById("br-action-panel");
  const connectionStatusEl = document.getElementById("br-connection-status");
  const forfeitBtn = document.getElementById("br-forfeit-btn");

  let current = JSON.parse(document.getElementById("battle-data").textContent);
  let animatedEventCount = 0; // events at/before this index have already had their animation played
  let pollTimer = null;
  let ws = null;
  let wsConnectTimer = null;
  let usingWs = false;

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

  // Picks a single animation class for `side` from the events that happened
  // since the last render — faint beats a switch-in beats getting hit beats
  // attacking, since that's roughly their visual importance if more than one
  // happened in the same batch (e.g. you attack and then get KO'd).
  // `activeIsFainted`: whether the side's CURRENTLY displayed (post-render)
  // mon is the fainted one. When an NPC's forced switch auto-resolves in the
  // same batch as the faint (gym/trainer battles never wait a turn on that),
  // the sprite is already showing the freshly-arrived mon by the time this
  // runs — so switch-in must win, or anim-faint's fill-forwards would leave
  // the *new*, perfectly healthy Pokémon stuck invisible.
  function pickAnimationForSide(newEvents, side, activeIsFainted) {
    const mine = newEvents.filter((e) => e.side === side);
    if (mine.some((e) => e.type === "switch_in")) return "anim-switch-in";
    if (activeIsFainted && mine.some((e) => e.type === "faint")) return "anim-faint";
    if (mine.some((e) => e.type === "damage" || e.type === "confusion_self_hit" || e.type === "status_damage" || e.type === "recoil")) {
      return "anim-hit";
    }
    if (mine.some((e) => e.type === "move_used")) return side === "A" ? "anim-attack-a" : "anim-attack-b";
    return null;
  }

  function renderHud(hudEl, name, avatar, roster, isWinner) {
    const avatarHtml = avatar ? `<img class="battle-hud-avatar" src="${avatar}" alt="${name}">` : "";
    const active = roster.find((m) => m.is_active) || roster[0];
    if (!active) {
      hudEl.innerHTML = `
        <div class="battle-hud-header">${avatarHtml}<div class="battle-hud-name">${name}</div></div>
        <p class="muted">No Pokémon</p>`;
      return;
    }
    const pct = active.max_hp > 0 ? Math.max(0, Math.min(100, (active.current_hp / active.max_hp) * 100)) : 0;
    const rosterStrip = roster
      .map(
        (m) =>
          `<img class="battle-roster-mon${m.is_fainted ? " is-fainted" : ""}${m.is_active ? " is-active" : ""}" src="${m.artwork}" alt="${m.name}" title="${m.name}">`
      )
      .join("");

    hudEl.classList.toggle("is-winner", !!isWinner);
    hudEl.innerHTML = `
        <div class="battle-hud-header">${avatarHtml}<div class="battle-hud-name">${name}</div></div>
        <div>${active.name}${active.status ? ` · ${active.status}` : ""}${active.is_fainted ? " (fainted)" : ""}</div>
        <div class="hp-bar-track"><div class="hp-bar-fill ${hpClass(active.current_hp, active.max_hp)}" style="width:${pct}%"></div></div>
        <div class="muted">${Math.max(0, active.current_hp)}/${active.max_hp} HP</div>
        <div class="battle-roster-strip">${rosterStrip}</div>
    `;
  }

  function renderSprite(spriteEl, roster, animClass) {
    const active = roster.find((m) => m.is_active) || roster[0];
    if (!active) {
      spriteEl.style.visibility = "hidden";
      return;
    }
    spriteEl.style.visibility = "visible";
    const desiredSrc = active.is_fainted && animClass !== "anim-faint" ? "" : active.sprite;
    if (desiredSrc && spriteEl.dataset.mon !== `${active.dex_id}:${active.sprite}`) {
      spriteEl.src = active.sprite;
      spriteEl.onerror = () => {
        spriteEl.onerror = null;
        spriteEl.src = active.artwork;
      };
      spriteEl.dataset.mon = `${active.dex_id}:${active.sprite}`;
    }
    spriteEl.alt = active.name;

    // Base visibility is driven by fainted-state alone, independent of any
    // one-shot animation class below — anim-switch-in doesn't use
    // fill-mode:forwards (it should snap back to normal, visible styling
    // once it finishes), so opacity has to be set here rather than left for
    // the animation to leave behind.
    spriteEl.style.opacity = active.is_fainted && animClass !== "anim-faint" ? "0" : "1";

    if (animClass) {
      spriteEl.classList.remove("anim-attack-a", "anim-attack-b", "anim-hit", "anim-faint", "anim-switch-in");
      void spriteEl.offsetWidth; // force reflow so the animation restarts even if the same class was just used
      spriteEl.classList.add(animClass);
    }
  }

  function typeBadge(t) {
    if (!t) return "";
    return `<span class="type-badge type-${t}">${t.charAt(0).toUpperCase() + t.slice(1)}</span>`;
  }

  function renderActionPanel(battle) {
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
          return `<button class="btn-secondary br-move-option" data-index="${i}" ${disabled ? "disabled" : ""}>
              <strong>${m.name}</strong> ${typeBadge(m.type)}<br><span class="muted">${m.pp}/${m.max_pp} PP</span>
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
      actionPanel.querySelectorAll(".br-move-option").forEach((btn) =>
        btn.addEventListener("click", async () => {
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

  function formatEvent(event, nameBySide) {
    const t = event.type;
    const side = nameBySide[event.side] || "";
    switch (t) {
      case "turn_start":
      case "switch_out":
      case "battle_end":
        return null;
      case "switch_in":
        return `🔁 ${side} sends out <strong>${event.name}</strong>!`;
      case "move_used":
        return `<strong>${side}</strong> used <strong>${event.move_name}</strong>!`;
      case "move_missed":
        return `${side}'s attack missed!`;
      case "move_failed":
        return `${side}'s move failed!`;
      case "cannot_act": {
        const reasons = {
          recharge: "must recharge!", asleep: "is fast asleep.", frozen: "is frozen solid!",
          flinched: "flinched and couldn't move!", paralyzed: "is paralyzed and can't move!",
        };
        return `${side} ${reasons[event.reason] || "could not act."}`;
      }
      case "confusion_self_hit":
        return `${side} is confused and hurt itself for <strong>${event.amount}</strong> damage!`;
      case "damage": {
        const suffix = {
          super_effective: " It's super effective!",
          not_very_effective: " It's not very effective...",
          no_effect: " It had no effect!",
        }[event.effectiveness] || "";
        const crit = event.is_crit ? " A critical hit!" : "";
        return `${side} took <strong>${event.amount}</strong> damage.${crit}${suffix}`;
      }
      case "multi_hit_summary":
        return `Hit <strong>${event.hits}</strong> time(s) for <strong>${event.total_damage}</strong> total damage!`;
      case "status_applied": {
        if (!event.status || event.status === "none") {
          const texts = { woke_up: "woke up!", thawed: "thawed out!", confusion_ended: "snapped out of confusion!" };
          return `${side} ${texts[event.reason] || "recovered!"}`;
        }
        const labels = {
          burn: "was burned!", paralysis: "was paralyzed!", poison: "was poisoned!",
          toxic: "was badly poisoned!", sleep: "fell asleep!", freeze: "was frozen solid!", confusion: "became confused!",
        };
        return `${side} ${labels[event.status] || `was afflicted with ${event.status}!`}`;
      }
      case "stat_changed": {
        const dir = event.change > 0 ? "rose" : "fell";
        const sharply = Math.abs(event.change) >= 2 ? "sharply " : "";
        const statName = event.stat.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
        return `${side}'s ${statName} ${sharply}${dir}!`;
      }
      case "status_damage":
      case "recoil":
        return `${side} was hurt${event.status ? ` by its ${event.status}` : ""}! (-${event.amount})`;
      case "drain":
      case "heal":
        return `${side} restored <strong>${event.amount}</strong> HP!`;
      case "charge_start":
        return `${side} is charging its attack!`;
      case "faint":
        return `💀 <strong>${event.name}</strong> fainted!`;
      default:
        return null;
    }
  }

  function render(battle, animate) {
    current = battle;
    titleEl.textContent = `${battle.name_a} vs ${battle.name_b}`;
    const typeLabel = battle.battle_type.charAt(0).toUpperCase() + battle.battle_type.slice(1);
    subtitleEl.textContent =
      battle.status === "finished" ? `${typeLabel} battle · Finished`
      : battle.status === "abandoned" ? `${typeLabel} battle · Abandoned`
      : battle.status === "pending" ? `${typeLabel} battle · Awaiting response`
      : `${typeLabel} battle · Turn ${battle.turn_number}`;
    turnBadgeEl.textContent = battle.status === "pending" ? "Challenge" : `Turn ${battle.turn_number}`;

    const newEvents = animate ? battle.events.slice(animatedEventCount >= 0 ? animatedEventCount : 0) : [];
    animatedEventCount = battle.events.length;

    renderHud(hudA, battle.name_a, battle.avatar_a, battle.roster_a, battle.winner_side === "A");
    renderHud(hudB, battle.name_b, battle.avatar_b, battle.roster_b, battle.winner_side === "B");
    const activeA = battle.roster_a.find((m) => m.is_active);
    const activeB = battle.roster_b.find((m) => m.is_active);
    renderSprite(spriteA, battle.roster_a, pickAnimationForSide(newEvents, "A", !!(activeA && activeA.is_fainted)));
    renderSprite(spriteB, battle.roster_b, pickAnimationForSide(newEvents, "B", !!(activeB && activeB.is_fainted)));

    if (battle.status === "finished") {
      const lastEvent = battle.events[battle.events.length - 1];
      const forfeited = lastEvent && lastEvent.type === "battle_end" && lastEvent.reason === "forfeit";
      resultEl.hidden = false;
      resultEl.textContent = battle.winner_name
        ? `🏆 ${battle.winner_name} wins!${forfeited ? " (forfeit)" : ""}`
        : "The battle ended in a draw.";
    } else if (battle.status === "abandoned") {
      resultEl.hidden = false;
      resultEl.textContent = "⏱️ This battle was abandoned due to inactivity.";
    } else {
      resultEl.hidden = true;
    }

    const nameBySide = { A: battle.name_a, B: battle.name_b };
    const lines = battle.events.map((e) => formatEvent(e, nameBySide)).filter(Boolean);
    logEl.innerHTML = lines.length
      ? lines.map((l) => `<div class="battle-log-line">${l}</div>`).reverse().join("")
      : "<p class='muted'>The battle begins!</p>";

    renderActionPanel(battle);
  }

  function applyUpdate(battle) {
    render(battle, true);
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
        // render()'s own event-count diffing already skips animating
        // anything already accounted for by the initial static render, so a
        // reconnect's first push doesn't replay history — it only animates
        // whatever genuinely happened since we last saw this battle.
        applyUpdate(JSON.parse(event.data));
      } catch (e) {
        // ignore malformed frame
      }
    });

    socket.addEventListener("close", () => {
      if (current.status === "finished" || current.status === "abandoned") return;
      usingWs = false;
      startPolling();
      // Try to reconnect in the background; if it works we drop back to push updates.
      setTimeout(connectWs, 4000);
    });

    socket.addEventListener("error", () => {
      // "close" fires right after — handled there.
    });
  }

  render(current, false);
  animatedEventCount = current.events.length;
  if (current.status !== "finished" && current.status !== "abandoned") {
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
