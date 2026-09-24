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
    if (move.priority > 0) lines.push(`Moves first (priority +${move.priority})`);
    if (move.priority < 0) lines.push(`Moves last (priority ${move.priority})`);
    return lines;
  }

  const EFFECTIVENESS = {
    super_effective: { label: "Super effective", cls: "is-super" },
    not_very_effective: { label: "Not very effective", cls: "is-weak" },
    no_effect: { label: "No effect", cls: "is-none" },
    neutral: { label: "Normal effectiveness", cls: "is-neutral" },
  };

  function show(move, anchorEl, opts) {
    const tip = ensure();
    const lines = effectLines(move);
    const eff = move.effectiveness && EFFECTIVENESS[move.effectiveness];
    const target = opts && opts.targetName;
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
      ${eff ? `<div class="move-tooltip-eff ${eff.cls}">${eff.label}${target ? ` vs ${target}` : ""}</div>` : ""}
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
    cardEl.addEventListener("mouseenter", () => show(move, cardEl, opts));
    cardEl.addEventListener("mouseleave", hide);
  }

  return { attach, hide };
})();
