(function () {
  const grid = document.getElementById("team-grid");
  const modal = document.getElementById("team-modal");
  const closeBtn = document.getElementById("team-modal-close");
  const pickerGrid = document.getElementById("team-picker-grid");
  const search = document.getElementById("team-search");
  const saveBtn = document.getElementById("team-save");
  const status = document.getElementById("team-status");

  if (!grid) return;

  const TEAM_SIZE = 6;
  const initial = JSON.parse(document.getElementById("team-data").textContent || "[]");
  const teamState = Array.from({ length: TEAM_SIZE }, (_, i) => initial[i] || null);

  let allSpecies = [];
  let activeSlot = null;

  function typeBadges(types) {
    return (types || [])
      .map((t) => `<span class="type-badge type-${t}">${t.charAt(0).toUpperCase() + t.slice(1)}</span>`)
      .join("");
  }

  function render() {
    grid.innerHTML = "";
    teamState.forEach((mon, i) => {
      const slot = document.createElement("div");
      if (mon) {
        slot.className = "team-slot filled";
        slot.innerHTML = `
          <button class="team-slot-remove" aria-label="Remove" data-index="${i}">&times;</button>
          <img src="${mon.artwork}" alt="${mon.name}">
          <div class="team-slot-name">${mon.name}</div>
          <div class="favorite-types">${typeBadges(mon.types)}</div>
        `;
        slot.querySelector(".team-slot-remove").addEventListener("click", () => {
          teamState[i] = null;
          render();
        });
      } else {
        slot.className = "team-slot empty";
        slot.innerHTML = `<button class="team-slot-add" data-index="${i}">+ Add Pokémon</button>`;
        slot.querySelector(".team-slot-add").addEventListener("click", () => openPicker(i));
      }
      grid.appendChild(slot);
    });
  }

  function openPicker(index) {
    activeSlot = index;
    modal.hidden = false;
    search.value = "";
    loadSpecies();
  }

  function closePicker() {
    modal.hidden = true;
    activeSlot = null;
  }

  closeBtn.addEventListener("click", closePicker);
  modal.addEventListener("click", (e) => {
    if (e.target === modal) closePicker();
  });

  async function loadSpecies() {
    if (allSpecies.length) {
      renderPickerGrid(allSpecies);
      return;
    }
    pickerGrid.innerHTML = "<p class='muted'>Loading...</p>";
    const res = await fetch("/api/proxy/pokedex");
    if (!res.ok) {
      pickerGrid.innerHTML = "<p class='error'>Couldn't load your Pokédex.</p>";
      return;
    }
    allSpecies = await res.json();
    renderPickerGrid(allSpecies);
  }

  function renderPickerGrid(species) {
    pickerGrid.innerHTML = "";
    if (!species.length) {
      pickerGrid.innerHTML = "<p class='muted'>You haven't caught any Pokémon yet!</p>";
      return;
    }
    for (const mon of species) {
      const item = document.createElement("div");
      item.className = "picker-item";
      item.innerHTML = `<img src="${mon.artwork}" alt="${mon.name}"><div>${mon.name}${mon.has_shiny ? " ✨" : ""}</div>`;
      item.addEventListener("click", () => {
        teamState[activeSlot] = {
          dex_id: mon.dex_id, name: mon.name, artwork: mon.artwork, types: mon.types,
        };
        closePicker();
        render();
      });
      pickerGrid.appendChild(item);
    }
  }

  search.addEventListener("input", () => {
    const q = search.value.toLowerCase();
    renderPickerGrid(allSpecies.filter((m) => m.name.toLowerCase().includes(q)));
  });

  saveBtn.addEventListener("click", async () => {
    status.textContent = "Saving...";
    status.classList.remove("error");
    const dex_ids = teamState.filter(Boolean).map((m) => m.dex_id);
    const res = await fetch("/api/proxy/team", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dex_ids }),
    });
    const data = await res.json();
    if (!res.ok) {
      status.textContent = data.detail || "Couldn't save your team.";
      status.classList.add("error");
      return;
    }
    status.textContent = "Team saved!";
  });

  render();
})();
