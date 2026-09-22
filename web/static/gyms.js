(function () {
  const modal = document.getElementById("gym-modal");
  const closeBtn = document.getElementById("gm-close");
  const title = document.getElementById("gm-title");
  const body = document.getElementById("gm-body");

  closeBtn.addEventListener("click", () => (modal.hidden = true));
  modal.addEventListener("click", (e) => {
    if (e.target === modal) modal.hidden = true;
  });

  document.querySelectorAll(".gym-card").forEach((card) =>
    card.addEventListener("click", () => openGym(card.dataset.gymKey))
  );

  function typeBadges(types) {
    return (types || [])
      .map((t) => `<span class="type-badge type-${t}">${t.charAt(0).toUpperCase() + t.slice(1)}</span>`)
      .join("");
  }

  async function openGym(gymKey) {
    modal.hidden = false;
    title.textContent = "Loading...";
    body.innerHTML = "<p class='muted'>Loading...</p>";

    const res = await fetch(`/api/proxy/gyms/${gymKey}`);
    if (!res.ok) {
      body.innerHTML = "<p class='error'>Couldn't load this gym.</p>";
      return;
    }
    const gym = await res.json();
    title.textContent = `${gym.leader_name} — ${gym.badge_name}`;

    const rosterHtml = gym.roster
      .map(
        (mon) => `
        <div class="gym-roster-mon">
            <img src="${mon.artwork}" alt="${mon.name}">
            <div class="gym-roster-mon-name">${mon.name}</div>
            <div class="favorite-types" style="justify-content:center;margin-top:2px;">${typeBadges(mon.types)}</div>
        </div>`
      )
      .join("");

    body.innerHTML = `
        <p class="muted">${gym.flavor}</p>
        <p><strong>${gym.location}</strong> · ${gym.type_theme.charAt(0).toUpperCase() + gym.type_theme.slice(1)}-type gym</p>
        <p class="muted">${gym.earned ? "✅ You've earned this badge." : "Challenge this gym with <code>/poke gym</code> in Discord."}</p>
        <hr>
        <h3>Gym Team</h3>
        <div class="gym-roster-grid">${rosterHtml}</div>
    `;
  }
})();
