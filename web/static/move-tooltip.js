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

  function show(move, anchorEl) {
    const tip = ensure();
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
      ${move.description ? `<hr><div class="move-tooltip-label">Effect</div><div class="move-tooltip-desc">${move.description}</div>` : ""}
    `;
    tip.hidden = false;
    position(anchorEl);
  }

  function hide() {
    if (el) el.hidden = true;
  }

  function attach(cardEl, move) {
    if (!move) return;
    cardEl.removeAttribute("title");
    cardEl.addEventListener("mouseenter", () => show(move, cardEl));
    cardEl.addEventListener("mouseleave", hide);
  }

  return { attach, hide };
})();
