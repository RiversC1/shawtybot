(function () {
  const modal = document.getElementById("customize-modal");
  const openBtn = document.getElementById("customize-btn");
  const closeBtn = document.getElementById("modal-close");
  const characterGrid = document.getElementById("character-grid");
  const favoriteGrid = document.getElementById("favorite-grid");
  const favoriteSearch = document.getElementById("favorite-search");
  const favoriteClear = document.getElementById("favorite-clear");

  if (!modal) return;

  let allSpecies = [];
  let currentCharacterKey = null;

  function openModal() {
    modal.hidden = false;
    loadCharacters();
    loadFavoriteOptions();
  }

  function closeModal() {
    modal.hidden = true;
  }

  openBtn.addEventListener("click", openModal);
  closeBtn.addEventListener("click", closeModal);
  modal.addEventListener("click", (e) => {
    if (e.target === modal) closeModal();
  });

  async function loadCharacters() {
    characterGrid.innerHTML = "<p class='muted'>Loading...</p>";
    const res = await fetch("/api/proxy/characters");
    if (!res.ok) {
      characterGrid.innerHTML = "<p class='error'>Couldn't load characters.</p>";
      return;
    }
    const characters = await res.json();
    characterGrid.innerHTML = "";
    for (const c of characters) {
      const item = document.createElement("div");
      item.className = "picker-item";
      item.dataset.key = c.key;
      item.innerHTML = `<img src="${c.sprite}" alt="${c.label}"><div>${c.label}</div><div class="muted">${c.generation}</div>`;
      item.addEventListener("click", () => selectCharacter(c.key, c, item));
      characterGrid.appendChild(item);
    }
  }

  async function selectCharacter(key, data, itemEl) {
    const res = await fetch("/api/proxy/character", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ character: key }),
    });
    if (!res.ok) return;

    for (const child of characterGrid.children) child.classList.remove("selected");
    itemEl.classList.add("selected");
    currentCharacterKey = key;

    const spriteImg = document.querySelector(".trainer-sprite");
    if (spriteImg) {
      spriteImg.src = data.sprite;
      spriteImg.alt = data.label;
    }
  }

  async function loadFavoriteOptions() {
    favoriteGrid.innerHTML = "<p class='muted'>Loading...</p>";
    const res = await fetch("/api/proxy/pokedex");
    if (!res.ok) {
      favoriteGrid.innerHTML = "<p class='error'>Couldn't load your Pokédex.</p>";
      return;
    }
    allSpecies = await res.json();
    renderFavoriteGrid(allSpecies);
  }

  function renderFavoriteGrid(species) {
    favoriteGrid.innerHTML = "";
    if (!species.length) {
      favoriteGrid.innerHTML = "<p class='muted'>You haven't caught any Pokémon yet!</p>";
      return;
    }
    for (const mon of species) {
      const item = document.createElement("div");
      item.className = "picker-item";
      item.innerHTML = `<img src="${mon.artwork}" alt="${mon.name}"><div>${mon.name}${mon.has_shiny ? " ✨" : ""}</div>`;
      item.addEventListener("click", () => selectFavorite(mon.dex_id, mon));
      favoriteGrid.appendChild(item);
    }
  }

  favoriteSearch.addEventListener("input", () => {
    const q = favoriteSearch.value.toLowerCase();
    renderFavoriteGrid(allSpecies.filter((m) => m.name.toLowerCase().includes(q)));
  });

  async function selectFavorite(dexId, mon) {
    const res = await fetch("/api/proxy/favorite", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dex_id: dexId }),
    });
    if (!res.ok) return;
    updateFavoriteDisplay(mon);
  }

  favoriteClear.addEventListener("click", async () => {
    const res = await fetch("/api/proxy/favorite", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dex_id: null }),
    });
    if (!res.ok) return;
    updateFavoriteDisplay(null);
  });

  function updateFavoriteDisplay(mon) {
    const visual = document.querySelector(".favorite-visual");
    if (!visual) return;
    if (mon) {
      visual.innerHTML = `
        <div class="favorite-circle"><img src="${mon.artwork}" alt="${mon.name}"></div>
        <div class="favorite-label">Favorite Pokémon</div>
        <div class="favorite-name">${mon.name}</div>
        <div class="favorite-types">${(mon.types || [])
          .map((t) => `<span class="type-badge type-${t}">${t.charAt(0).toUpperCase() + t.slice(1)}</span>`)
          .join("")}</div>
      `;
    } else {
      visual.innerHTML = `
        <div class="favorite-circle favorite-empty">?</div>
        <div class="favorite-label">Favorite Pokémon</div>
        <div class="favorite-name muted">None selected</div>
      `;
    }
  }
})();
