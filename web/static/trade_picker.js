// Pokémon picker for trades: one card per individual (duplicates are
// different Pokémon — their IVs differ), with search by name or type, a type
// filter and sorting by IV. Used by the trade builder (trades.js) and the
// live trade room (trade_room.js).
window.TradePicker = (function () {
  const PAGE_SIZE = 24;
  const TYPES = [
    "normal", "fire", "water", "grass", "electric", "ice", "fighting", "poison", "ground",
    "flying", "psychic", "bug", "rock", "ghost", "dragon", "dark", "steel", "fairy",
  ];
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  /**
   * root: element to render into. onPick(mon) is called with the chosen
   * individual. Returns { load(url) } to (re)load a collection into it.
   */
  function create(root, onPick) {
    root.innerHTML = `
      <div class="trade-picker-toolbar">
        <input type="text" class="search-input tpk-search" placeholder="Search by name or type..." aria-label="Search">
        <select class="search-input tpk-type" aria-label="Type">
          <option value="">All types</option>
          ${TYPES.map((t) => `<option value="${t}">${cap(t)}</option>`).join("")}
        </select>
        <select class="search-input tpk-sort" aria-label="Sort">
          <option value="new">Newest first</option>
          <option value="iv_desc">IV: high to low</option>
          <option value="iv_asc">IV: low to high</option>
          <option value="name">Name A–Z</option>
          <option value="dex">Pokédex #</option>
        </select>
      </div>
      <div class="muted tpk-count"></div>
      <div class="picker-grid trade-picker-grid"></div>
      <div class="picker-pagination" hidden>
        <button type="button" class="btn-secondary tpk-prev">Previous</button>
        <span class="muted tpk-page"></span>
        <button type="button" class="btn-secondary tpk-next">Next</button>
      </div>`;
    const search = root.querySelector(".tpk-search");
    const typeSel = root.querySelector(".tpk-type");
    const sortSel = root.querySelector(".tpk-sort");
    const countEl = root.querySelector(".tpk-count");
    const grid = root.querySelector(".trade-picker-grid");
    const pager = root.querySelector(".picker-pagination");
    const pageLabel = root.querySelector(".tpk-page");
    const prev = root.querySelector(".tpk-prev");
    const next = root.querySelector(".tpk-next");
    let all = [];
    let page = 0;

    function filtered() {
      const q = search.value.trim().toLowerCase();
      const type = typeSel.value;
      let list = all.filter((m) => {
        if (type && !(m.types || []).includes(type)) return false;
        if (!q) return true;
        return m.name.toLowerCase().includes(q)
          || (m.nickname || "").toLowerCase().includes(q)
          || (m.types || []).some((t) => t.startsWith(q));
      });
      const by = {
        iv_desc: (a, b) => b.iv_percent - a.iv_percent,
        iv_asc: (a, b) => a.iv_percent - b.iv_percent,
        name: (a, b) => a.name.localeCompare(b.name) || b.iv_percent - a.iv_percent,
        dex: (a, b) => a.dex_id - b.dex_id || b.iv_percent - a.iv_percent,
      }[sortSel.value];
      if (by) list = [...list].sort(by);
      return list;
    }

    function render() {
      const list = filtered();
      const pages = Math.max(1, Math.ceil(list.length / PAGE_SIZE));
      page = Math.min(page, pages - 1);
      // How many of each species are shown, so duplicates can be told apart.
      const perSpecies = {};
      for (const m of all) perSpecies[m.dex_id] = (perSpecies[m.dex_id] || 0) + 1;
      countEl.textContent = `${list.length} Pokémon`;
      grid.innerHTML = "";
      if (!list.length) grid.innerHTML = "<p class='muted'>No Pokémon match.</p>";
      for (const mon of list.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)) {
        const item = document.createElement("button");
        item.type = "button";
        item.className = "picker-item trade-picker-item";
        item.disabled = mon.tradeable === false;
        const types = (mon.types || []).map((t) => `<span class="type-badge type-${t}">${cap(t)}</span>`).join("");
        item.innerHTML = `<img src="${artThumb(mon.artwork)}" alt="" loading="lazy">
          <div class="trade-picker-name">${esc(mon.nickname || mon.name)}${mon.is_shiny ? " ✨" : ""}</div>
          <div class="trade-picker-types">${types}</div>
          <div class="trade-picker-iv${perSpecies[mon.dex_id] > 1 ? " is-dupe" : ""}">IV ${mon.iv_percent}%</div>
          ${item.disabled ? `<div class="muted">Can't be traded</div>` : ""}`;
        item.title = item.disabled ? "Mega Evolutions can't be traded" : `${mon.name} · IV ${mon.iv_percent}%`;
        item.addEventListener("click", () => onPick(mon));
        grid.appendChild(item);
      }
      pager.hidden = list.length <= PAGE_SIZE;
      pageLabel.textContent = `Page ${page + 1} of ${pages}`;
      prev.disabled = page === 0;
      next.disabled = page >= pages - 1;
    }

    const reset = () => {
      page = 0;
      render();
    };
    search.addEventListener("input", reset);
    typeSel.addEventListener("change", reset);
    sortSel.addEventListener("change", reset);
    prev.addEventListener("click", () => {
      page--;
      render();
      grid.scrollTop = 0;
    });
    next.addEventListener("click", () => {
      page++;
      render();
      grid.scrollTop = 0;
    });

    async function load(url) {
      search.value = "";
      typeSel.value = "";
      sortSel.value = "new";
      page = 0;
      all = [];
      countEl.textContent = "";
      pager.hidden = true;
      grid.innerHTML = "<p class='muted'>Loading...</p>";
      const res = await fetch(url).catch(() => null);
      if (!res || !res.ok) {
        grid.innerHTML = "<p class='error'>Couldn't load that collection.</p>";
        return;
      }
      all = await res.json();
      render();
      search.focus();
    }

    return { load };
  }

  return { create };
})();
