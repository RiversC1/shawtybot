// Shared rich hover tooltip for move cards — used by the team, collection,
// and Pokédex pages instead of the plain browser `title` tooltip.
window.MoveTooltip = (function () {
  let el = null;

  function capitalize(s) {
    return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
  }

  function ensure() {
    if (el) return el;
    el = document.createElement("div");
    el.className = "move-tooltip";
    el.hidden = true;
    document.body.appendChild(el);
    return el;
  }

  function position(anchorEl) {
    const tip = ensure();
    const rect = anchorEl.getBoundingClientRect();
    let top = rect.bottom + window.scrollY + 8;
    let left = rect.left + window.scrollX;

    tip.style.top = `${top}px`;
    tip.style.left = `${left}px`;

    requestAnimationFrame(() => {
      const tw = tip.offsetWidth;
      const th = tip.offsetHeight;
      const maxLeft = window.scrollX + document.documentElement.clientWidth - tw - 12;
      if (left > maxLeft) tip.style.left = `${Math.max(12, maxLeft)}px`;
      // Flip above the card if there's not enough room below.
      if (rect.bottom + th + 16 > window.innerHeight) {
        tip.style.top = `${rect.top + window.scrollY - th - 8}px`;
      }
    });
  }

  const STAT_NAMES = {
    attack: "Attack", defense: "Defense", sp_attack: "Sp. Atk", sp_defense: "Sp. Def",
    special_attack: "Sp. Atk", special_defense: "Sp. Def", speed: "Speed", accuracy: "Accuracy", evasion: "Evasion",
  };
  const AILMENTS = {
    paralysis: "paralyze", burn: "burn", poison: "poison", toxic: "badly poison", sleep: "put to sleep",
    freeze: "freeze", confusion: "confuse",
  };

  // Plain-language effect bullets built from the move's mechanics fields
  // (only present where the page has full move data, e.g. the battle page).
  function effectLines(move) {
    const lines = [];
    const who = move.target === "self" ? "the user" : "the target";
    if (move.ailment && move.ailment !== "none" && AILMENTS[move.ailment]) {
      const chance = move.ailment_chance && move.ailment_chance < 100 ? `${move.ailment_chance}% chance to ` : "";
      lines.push(`${chance ? capitalize(chance) : "Will "}${AILMENTS[move.ailment]} ${who}`);
    }
    const statsOnUser = move.stat_self != null ? move.stat_self : move.target === "self";
    for (const sc of move.stat_changes || []) {
      const stat = STAT_NAMES[sc.stat] || sc.stat;
      const n = Math.abs(sc.change);
      const verb = sc.change > 0 ? "Raises" : "Lowers";
      const chance = move.stat_chance && move.stat_chance < 100 ? ` (${move.stat_chance}% chance)` : "";
      lines.push(`${verb} ${statsOnUser ? "the user's" : "the target's"} ${stat} by ${n} stage${n > 1 ? "s" : ""}${chance}`);
    }
    if (move.drain_percent) lines.push(`Heals the user by ${move.drain_percent}% of the damage dealt`);
    if (move.recoil_percent) lines.push(`User takes ${move.recoil_percent}% of the damage dealt as recoil`);
    if (move.healing_percent) lines.push(`Restores ${move.healing_percent}% of the user's max HP`);
    if (move.flinch_chance) lines.push(`${move.flinch_chance}% chance to make the target flinch`);
    if (move.min_hits && move.max_hits && move.max_hits > 1) {
      lines.push(move.min_hits === move.max_hits ? `Hits ${move.max_hits} times` : `Hits ${move.min_hits}–${move.max_hits} times`);
    }
    if (move.crit_rate) lines.push("High critical-hit ratio");
    const flags = new Set(move.flags || []);
    if (flags.has("charge")) lines.push(flags.has("semi_invulnerable") ? "Charges on turn 1 (out of reach), strikes on turn 2" : "Charges on turn 1, strikes on turn 2");
    if (flags.has("recharge")) lines.push("User must recharge next turn");
    if (flags.has("always_hit")) lines.push("Never misses");
    if (flags.has("ohko")) lines.push("Knocks out the target in one hit if it lands");
    if (flags.has("multi_turn_lock")) lines.push("Locks the user in for several turns");
    const WEATHER = {
      "Sunny Day": "Harsh sunlight for 5 turns: Fire moves x1.5, Water moves x0.5",
      "Rain Dance": "Rain for 5 turns: Water moves x1.5, Fire moves x0.5, Thunder never misses",
      "Sandstorm": "Sandstorm for 5 turns: hurts non-Rock/Ground/Steel Pokémon each turn, Rock types get 1.5x Sp. Def",
      "Hail": "Hail for 5 turns: hurts non-Ice Pokémon each turn, Blizzard never misses",
      "Weather Ball": "Changes type and doubles in power in any weather",
      "Solar Beam": "Fires instantly in harsh sunlight (halved in rain, sand or hail)",
    };
    if (WEATHER[move.name]) lines.push(WEATHER[move.name]);
    if (move.priority > 0) lines.push(`Moves first (priority +${move.priority})`);
    if (move.priority < 0) lines.push(`Moves last (priority ${move.priority})`);
    return lines;
  }


  function show(move, anchorEl, opts) {
    const tip = ensure();
    const lines = effectLines(move);
    tip.innerHTML = `
      <div class="move-tooltip-header">${move.name}</div>
      <div class="move-tooltip-badges">
        <span class="type-badge type-${move.type}">${capitalize(move.type)}</span>
        <span class="type-badge move-tooltip-category">${capitalize(move.category)}</span>
      </div>
      <div class="move-tooltip-grid">
        <div><div class="move-tooltip-label">Power</div><div class="move-tooltip-value">${move.power ?? "—"}</div></div>
        <div><div class="move-tooltip-label">Accuracy</div><div class="move-tooltip-value">${move.accuracy != null ? move.accuracy + "%" : "—"}</div></div>
        <div><div class="move-tooltip-label">Base PP</div><div class="move-tooltip-value">${move.pp ?? "—"}</div></div>
        <div><div class="move-tooltip-label">Priority</div><div class="move-tooltip-value">${move.priority ?? 0}</div></div>
      </div>
      ${move.description ? `<hr><div class="move-tooltip-label">Effect</div><div class="move-tooltip-desc">${move.description.replace(/\s+/g, " ")}</div>` : ""}
      ${lines.length ? `<ul class="move-tooltip-effects">${lines.map((l) => `<li>${l}</li>`).join("")}</ul>` : ""}
    `;
    tip.hidden = false;
    position(anchorEl);
    watchAnchor(anchorEl);
  }

  // Pages that rebuild their buttons (the battle action panel re-renders
  // every turn) can remove the hovered element without a mouseleave ever
  // firing, which used to leave the tooltip stranded on screen. Hide it as
  // soon as its anchor is gone or hidden.
  let watching = null;
  function watchAnchor(anchorEl) {
    watching = anchorEl;
    const check = () => {
      if (watching !== anchorEl || !el || el.hidden) return;
      if (!anchorEl.isConnected || anchorEl.offsetParent === null) {
        hide();
        return;
      }
      requestAnimationFrame(check);
    };
    requestAnimationFrame(check);
  }

  function hide() {
    watching = null;
    if (el) el.hidden = true;
  }

  function attach(cardEl, move, opts) {
    if (!move) return;
    cardEl.removeAttribute("title");
    if (opts && opts.mouseOnly) {
      // For buttons, a tap has to work on the first try. Touch browsers (iOS
      // Safari above all) treat a first tap that makes hover content appear
      // as only a hover and drop the click, so only a mouse gets the tooltip.
      cardEl.addEventListener("pointerenter", (e) => {
        if (e.pointerType === "mouse") show(move, cardEl, opts);
      });
      cardEl.addEventListener("pointerleave", hide);
      return;
    }
    cardEl.addEventListener("mouseenter", () => show(move, cardEl, opts));
    cardEl.addEventListener("mouseleave", hide);
  }

  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  }

  // A simple titled tooltip (abilities, items): { title, kicker, description, note }.
  function showInfo(info, anchorEl) {
    const tip = ensure();
    tip.innerHTML = `
      <div class="move-tooltip-header">${esc(info.title)}</div>
      ${info.kicker ? `<div class="move-tooltip-label">${esc(info.kicker)}</div>` : ""}
      <div class="move-tooltip-desc">${esc(info.description || "No description available.")}</div>
      ${info.note ? `<div class="move-tooltip-note${info.noteMuted ? " is-muted" : ""}">${esc(info.note)}</div>` : ""}
    `;
    tip.hidden = false;
    position(anchorEl);
    watchAnchor(anchorEl);
  }

  function attachInfo(el, info) {
    if (!info) return;
    el.removeAttribute("title");
    el.addEventListener("mouseenter", () => showInfo(info, el));
    el.addEventListener("mouseleave", hide);
    el.addEventListener("focus", () => showInfo(info, el));
    el.addEventListener("blur", hide);
  }

  // Tooltip content for an ability from the API's ability_info shape.
  function abilityInfo(a) {
    if (!a) return null;
    return {
      title: a.label, kicker: "Ability", description: a.description,
      note: a.in_battle ? "✓ Active in battles"
        : a.no_battle_effect ? "No effect in these battles (it only matters outside battle or in double battles)"
        : "Its battle effect isn't simulated yet",
      noteMuted: !a.in_battle,
    };
  }

  return { attach, attachInfo, abilityInfo, hide };
})();
