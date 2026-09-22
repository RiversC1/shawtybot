(function () {
  const POLL_INTERVAL_MS = 2500;

  const titleEl = document.getElementById("br-title");
  const subtitleEl = document.getElementById("br-subtitle");
  const resultEl = document.getElementById("br-result");
  const sideAEl = document.getElementById("br-side-a");
  const sideBEl = document.getElementById("br-side-b");
  const logEl = document.getElementById("br-log");

  const initial = JSON.parse(document.getElementById("battle-data").textContent);
  let pollTimer = null;
  let lastEventCount = 0;

  function hpClass(current, max) {
    if (max <= 0) return "hp-low";
    const pct = current / max;
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

  function render(battle) {
    titleEl.textContent = `${battle.name_a} vs ${battle.name_b}`;
    subtitleEl.textContent =
      battle.status === "finished"
        ? `${battle.battle_type.charAt(0).toUpperCase() + battle.battle_type.slice(1)} battle · Finished`
        : `${battle.battle_type.charAt(0).toUpperCase() + battle.battle_type.slice(1)} battle · Turn ${battle.turn_number}`;

    renderSide(sideAEl, battle.name_a, battle.roster_a, battle.winner_side === "A");
    renderSide(sideBEl, battle.name_b, battle.roster_b, battle.winner_side === "B");

    if (battle.status === "finished") {
      resultEl.hidden = false;
      resultEl.textContent = battle.winner_name ? `🏆 ${battle.winner_name} wins!` : "The battle ended in a draw.";
    } else {
      resultEl.hidden = true;
    }

    const nameBySide = { A: battle.name_a, B: battle.name_b };
    const lines = battle.events
      .map((e) => formatEvent(e, nameBySide))
      .filter(Boolean);
    logEl.innerHTML = lines.length
      ? lines.map((l) => `<div class="battle-log-line">${l}</div>`).reverse().join("")
      : "<p class='muted'>The battle begins!</p>";
  }

  async function poll() {
    try {
      const res = await fetch(`/api/proxy/battles/${window.BATTLE_ID}`);
      if (!res.ok) return;
      const battle = await res.json();
      render(battle);
      if (battle.status === "finished") {
        clearInterval(pollTimer);
      }
    } catch (e) {
      // transient network hiccup — next tick will retry
    }
  }

  render(initial);
  if (initial.status !== "finished") {
    pollTimer = setInterval(poll, POLL_INTERVAL_MS);
  }
})();
