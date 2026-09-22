(function () {
  const POLL_INTERVAL_MS = 2000;
  const WS_CONNECT_TIMEOUT_MS = 3000;

  const titleEl = document.getElementById("br-title");
  const subtitleEl = document.getElementById("br-subtitle");
  const resultEl = document.getElementById("br-result");
  const sideAEl = document.getElementById("br-side-a");
  const sideBEl = document.getElementById("br-side-b");
  const logEl = document.getElementById("br-log");
  const actionPanel = document.getElementById("br-action-panel");
  const connectionStatusEl = document.getElementById("br-connection-status");
  const forfeitBtn = document.getElementById("br-forfeit-btn");

  let current = JSON.parse(document.getElementById("battle-data").textContent);
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

  function renderSide(el, name, roster, isWinner) {
    const active = roster.find((m) => m.is_active) || roster[0];
    if (!active) {
      el.innerHTML = `<div class="battle-side-name">${name}</div><p class="muted">No Pokémon</p>`;
      return;
    }
    const pct = active.max_hp > 0 ? Math.max(0, Math.min(100, (active.current_hp / active.max_hp) * 100)) : 0;
    const rosterStrip = roster
      .map(
        (m) =>
          `<img class="battle-roster-mon${m.is_fainted ? " is-fainted" : ""}${m.is_active ? " is-active" : ""}" src="${m.artwork}" alt="${m.name}" title="${m.name}">`
      )
      .join("");

    el.classList.toggle("is-winner", !!isWinner);
    el.innerHTML = `
        <div class="battle-side-name">${name}</div>
        <img class="battle-active-art" src="${active.artwork}" alt="${active.name}">
        <div>${active.name}${active.status ? ` · ${active.status}` : ""}${active.is_fainted ? " (fainted)" : ""}</div>
        <div class="hp-bar-track"><div class="hp-bar-fill ${hpClass(active.current_hp, active.max_hp)}" style="width:${pct}%"></div></div>
        <div class="muted">${Math.max(0, active.current_hp)}/${active.max_hp} HP</div>
        <div class="battle-roster-strip">${rosterStrip}</div>
    `;
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
      actionPanel.innerHTML = `<p><strong>${myRoster.find((m) => m.is_fainted && m.is_active)?.name || "Your Pokémon"} fainted!</strong> Choose your next Pokémon:</p>
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
        return null;
      case "battle_end":
        return event.reason === "forfeit" ? null : null;
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

  function render(battle) {
    current = battle;
    titleEl.textContent = `${battle.name_a} vs ${battle.name_b}`;
    const typeLabel = battle.battle_type.charAt(0).toUpperCase() + battle.battle_type.slice(1);
    subtitleEl.textContent =
      battle.status === "finished" ? `${typeLabel} battle · Finished`
      : battle.status === "abandoned" ? `${typeLabel} battle · Abandoned`
      : battle.status === "pending" ? `${typeLabel} battle · Awaiting response`
      : `${typeLabel} battle · Turn ${battle.turn_number}`;

    renderSide(sideAEl, battle.name_a, battle.roster_a, battle.winner_side === "A");
    renderSide(sideBEl, battle.name_b, battle.roster_b, battle.winner_side === "B");

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
    render(battle);
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

  render(current);
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
