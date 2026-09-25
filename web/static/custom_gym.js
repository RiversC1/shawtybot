// Gym builder (/gyms/mine): details, badge designer and team picker for a
// trainer's own custom gym. Badge previews are rendered server-side by
// /custom-badges/preview.svg (web/badge_svg.py), the same code that draws
// the real badge, so what you see is exactly what you get.
(function () {
  const data = JSON.parse(document.getElementById("my-gym-data").textContent || "{}");
  const existing = data.gym;
  const REWARD_ONLY = new Set([494, 495]);

  const $ = (id) => document.getElementById(id);
  const nameInput = $("cg-name");
  const flavorInput = $("cg-flavor");
  const badgeNameInput = $("cg-badge-name");
  const primaryInput = $("cg-primary");
  const secondaryInput = $("cg-secondary");
  const typesEl = $("cg-types");
  const shapesEl = $("cg-shapes");
  const emblemsEl = $("cg-emblems");
  const badgeImg = $("cg-badge-img");
  const badgePreviewName = $("cg-badge-preview-name");
  const pickerEl = $("cg-picker");
  const selectedEl = $("cg-selected");
  const teamCountEl = $("cg-team-count");
  const searchInput = $("cg-search");
  const saveBtn = $("cg-save-btn");
  const statusEl = $("cg-status");

  const state = {
    type: existing ? existing.type_theme : "normal",
    design: existing ? { ...existing.badge_design } : { shape: "hexagon", primary: "#e03b4a", secondary: "#ffcb05", emblem: "star" },
    team: existing ? existing.dex_ids.slice() : [],
  };
  let owned = [];

  nameInput.value = existing ? existing.gym_name : "";
  flavorInput.value = existing ? existing.flavor : "";
  badgeNameInput.value = existing ? existing.badge_name : "";
  primaryInput.value = state.design.primary;
  secondaryInput.value = state.design.secondary;

  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  }

  function previewUrl(overrides) {
    const d = { ...state.design, ...overrides };
    const q = new URLSearchParams({ shape: d.shape, emblem: d.emblem, primary: d.primary, secondary: d.secondary });
    return `/custom-badges/preview.svg?${q}`;
  }

  // ---------- Type ----------
  function renderTypes() {
    typesEl.innerHTML = data.types
      .map((t) => `<button type="button" class="type-badge type-${t} cg-type${t === state.type ? " is-selected" : ""}" data-type="${t}" role="radio" aria-checked="${t === state.type}">${cap(t)}</button>`)
      .join("");
  }
  typesEl.addEventListener("click", (e) => {
    const btn = e.target.closest(".cg-type");
    if (!btn || btn.dataset.type === state.type) return;
    state.type = btn.dataset.type;
    // Team members must match the gym's type; drop any that no longer do.
    state.team = state.team.filter((id) => (owned.find((m) => m.dex_id === id)?.types || []).includes(state.type));
    renderTypes();
    renderTeam();
  });

  // ---------- Badge designer ----------
  let previewTimer = null;
  function renderBadge(immediateOptions) {
    badgeImg.src = previewUrl();
    badgePreviewName.textContent = badgeNameInput.value.trim() || "Your Badge";
    const refreshOptions = () => {
      shapesEl.innerHTML = data.shapes
        .map((s) => `<button type="button" class="cg-option${s === state.design.shape ? " is-selected" : ""}" data-shape="${s}" title="${cap(s)}"><img src="${previewUrl({ shape: s })}" alt="${cap(s)}"></button>`)
        .join("");
      emblemsEl.innerHTML = data.emblems
        .map((m) => `<button type="button" class="cg-option${m === state.design.emblem ? " is-selected" : ""}" data-emblem="${m}" title="${cap(m)}"><img src="${previewUrl({ emblem: m })}" alt="${cap(m)}"></button>`)
        .join("");
    };
    clearTimeout(previewTimer);
    if (immediateOptions) refreshOptions();
    else previewTimer = setTimeout(refreshOptions, 250); // color drags fire rapidly
  }
  shapesEl.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-shape]");
    if (btn) { state.design.shape = btn.dataset.shape; renderBadge(true); }
  });
  emblemsEl.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-emblem]");
    if (btn) { state.design.emblem = btn.dataset.emblem; renderBadge(true); }
  });
  primaryInput.addEventListener("input", () => { state.design.primary = primaryInput.value; renderBadge(false); });
  secondaryInput.addEventListener("input", () => { state.design.secondary = secondaryInput.value; renderBadge(false); });
  badgeNameInput.addEventListener("input", () => { badgePreviewName.textContent = badgeNameInput.value.trim() || "Your Badge"; });

  // ---------- Team ----------
  function renderTeam() {
    const max = data.max_team;
    teamCountEl.textContent = `${state.team.length} / ${max}`;
    selectedEl.innerHTML = state.team.length
      ? state.team
          .map((id, i) => {
            const m = owned.find((x) => x.dex_id === id);
            if (!m) return "";
            return `<button type="button" class="cg-selected-mon" data-dex="${id}" title="Remove ${esc(m.name)}">
                <span class="cg-slot-num">${i + 1}</span>
                <img src="${esc(m.artwork)}" alt="">
                <span>${esc(m.name)}</span><span class="cg-remove" aria-hidden="true">×</span>
              </button>`;
          })
          .join("")
      : `<p class="muted">No Pokémon picked yet.</p>`;

    const q = searchInput.value.trim().toLowerCase();
    const eligible = owned.filter((m) => (m.types || []).includes(state.type) && !REWARD_ONLY.has(m.dex_id));
    const shown = eligible.filter((m) => !q || m.name.toLowerCase().includes(q));
    pickerEl.innerHTML = shown.length
      ? shown
          .map((m) => {
            const on = state.team.includes(m.dex_id);
            const full = !on && state.team.length >= max;
            return `<button type="button" class="picker-item cg-pick${on ? " selected" : ""}${full ? " is-full" : ""}" data-dex="${m.dex_id}">
                <img src="${esc(m.artwork)}" alt="" loading="lazy">
                <div>${esc(m.name)}</div>
                <div class="team-picker-types">${(m.types || []).map((t) => `<span class="type-badge type-${t}">${cap(t)}</span>`).join("")}</div>
              </button>`;
          })
          .join("")
      : `<p class="muted">${eligible.length ? "No matches." : `You don't own any ${cap(state.type)}-type Pokémon yet.`}</p>`;
  }
  pickerEl.addEventListener("click", (e) => {
    const btn = e.target.closest(".cg-pick");
    if (!btn) return;
    const id = parseInt(btn.dataset.dex, 10);
    if (state.team.includes(id)) state.team = state.team.filter((x) => x !== id);
    else if (state.team.length < data.max_team) state.team.push(id);
    renderTeam();
  });
  selectedEl.addEventListener("click", (e) => {
    const btn = e.target.closest(".cg-selected-mon");
    if (!btn) return;
    state.team = state.team.filter((x) => x !== parseInt(btn.dataset.dex, 10));
    renderTeam();
  });
  searchInput.addEventListener("input", renderTeam);

  // ---------- Save ----------
  function setStatus(msg, kind) {
    statusEl.hidden = !msg;
    statusEl.textContent = msg || "";
    statusEl.className = `cg-status${kind ? ` is-${kind}` : ""}`;
  }

  saveBtn.addEventListener("click", async () => {
    if (state.team.length < data.min_team) {
      setStatus(`Pick at least ${data.min_team} Pokémon for your gym team.`, "error");
      return;
    }
    saveBtn.disabled = true;
    setStatus("Saving...");
    try {
      const res = await fetch("/api/proxy/my-gym", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          gym_name: nameInput.value, flavor: flavorInput.value, badge_name: badgeNameInput.value,
          type_theme: state.type, badge_design: state.design, dex_ids: state.team,
        }),
      });
      const out = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = Array.isArray(out.detail) ? "Please fill in every field." : out.detail;
        throw new Error(detail || "Couldn't save your gym.");
      }
      setStatus("Saved! Your gym is open for challengers.", "success");
      saveBtn.textContent = "Save changes";
    } catch (err) {
      setStatus(err.message, "error");
    } finally {
      saveBtn.disabled = false;
    }
  });

  renderTypes();
  renderBadge(true);
  renderTeam();
  fetch("/api/proxy/pokedex")
    .then((r) => (r.ok ? r.json() : []))
    .then((list) => {
      owned = Array.isArray(list) ? list : [];
      renderTeam();
    });
})();
