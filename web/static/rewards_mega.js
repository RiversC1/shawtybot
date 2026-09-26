// Rewards page: pick your one Mega Evolution (POST /api/rewards/mega).
(function () {
  const options = JSON.parse(document.getElementById("mega-data").textContent || "[]");
  const grid = document.getElementById("mega-grid");
  const search = document.getElementById("mega-search");
  const confirmBox = document.getElementById("mega-confirm");
  const confirmImg = document.getElementById("mega-confirm-img");
  const confirmName = document.getElementById("mega-confirm-name");
  const claimBtn = document.getElementById("mega-claim");
  const cancelBtn = document.getElementById("mega-cancel");
  const errorEl = document.getElementById("mega-error");
  let selected = null;

  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  }

  function render() {
    const q = search.value.trim().toLowerCase();
    const shown = options.filter((m) => !q || m.name.toLowerCase().includes(q) || m.types.some((t) => t.startsWith(q)));
    grid.innerHTML = shown.length
      ? shown
          .map((m) => `
          <button type="button" class="mega-option tint-${esc(m.types[0])}${selected && selected.dex_id === m.dex_id ? " is-selected" : ""}" data-dex="${m.dex_id}">
            <img src="${esc(m.artwork)}" alt="" loading="lazy">
            <span class="mega-option-name">${esc(m.name)}</span>
            <span class="team-picker-types">${m.types.map((t) => `<span class="type-badge type-${esc(t)}">${esc(cap(t))}</span>`).join("")}</span>
            <span class="mega-option-meta">${esc(m.ability || "")} · BST ${m.stat_total}</span>
          </button>`)
          .join("")
      : `<p class="muted">No Megas match that search.</p>`;
  }

  grid.addEventListener("click", (e) => {
    const btn = e.target.closest(".mega-option");
    if (!btn) return;
    selected = options.find((m) => m.dex_id === parseInt(btn.dataset.dex, 10));
    confirmImg.src = selected.artwork;
    confirmName.textContent = selected.name;
    errorEl.hidden = true;
    confirmBox.hidden = false;
    render();
    confirmBox.scrollIntoView({ behavior: "smooth", block: "nearest" });
  });

  cancelBtn.addEventListener("click", () => {
    selected = null;
    confirmBox.hidden = true;
    render();
  });

  claimBtn.addEventListener("click", async () => {
    if (!selected) return;
    claimBtn.disabled = true;
    claimBtn.textContent = "Claiming...";
    try {
      const res = await fetch("/api/proxy/rewards/mega", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dex_id: selected.dex_id }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || "Couldn't claim that Mega.");
      window.location.reload();
    } catch (err) {
      errorEl.textContent = err.message;
      errorEl.hidden = false;
      claimBtn.disabled = false;
      claimBtn.textContent = "Claim this Mega";
    }
  });

  search.addEventListener("input", render);
  render();
})();
