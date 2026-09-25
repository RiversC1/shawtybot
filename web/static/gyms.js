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
    card.addEventListener("click", () => {
      if (card.dataset.customOwner) openCustomGym(card.dataset.customOwner);
      else openGym(card.dataset.gymKey);
    })
  );

  // Trainer names, and everything about custom gyms, are user-written.
  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  }

  function typeBadges(types) {
    return (types || [])
      .map((t) => `<span class="type-badge type-${esc(t)}">${esc(t.charAt(0).toUpperCase() + t.slice(1))}</span>`)
      .join("");
  }

  function rosterHtml(roster) {
    return roster
      .map(
        (mon) => `
        <div class="gym-roster-mon">
            <img src="${esc(mon.artwork)}" alt="${esc(mon.name)}">
            <div class="gym-roster-mon-name">${esc(mon.name)}</div>
            <div class="favorite-types" style="justify-content:center;margin-top:2px;">${typeBadges(mon.types)}</div>
        </div>`
      )
      .join("");
  }

  function clearedByHtml(clearedBy) {
    return clearedBy.length
      ? `<ul class="gym-clearers-list">${clearedBy.map((c) => `<li>${esc(c.username)}</li>`).join("")}</ul>`
      : `<p class="muted">No one has beaten this gym yet — be the first!</p>`;
  }

  function fightButtonHtml(label) {
    return `
        <div class="gym-hero-actions">
            <button id="gm-fight-btn" class="btn-primary">${esc(label)}</button>
        </div>
        <p class="error" id="gm-fight-error" hidden></p>`;
  }

  function renderGym({ heading, leaderImage, leaderAlt, leaderClass, badgeImage, badgeName, typeTheme, eyebrow,
    leaderName, flavor, statusLine, actionsHtml, roster, clearedBy }) {
    const typeLabel = typeTheme.charAt(0).toUpperCase() + typeTheme.slice(1);
    title.textContent = heading;
    body.innerHTML = `
        <div class="gym-hero">
            <span class="type-badge type-${esc(typeTheme)} gym-hero-type-tag">${esc(typeLabel)}</span>
            <div class="gym-hero-main">
                <img class="gym-hero-leader ${leaderClass || ""}" src="${esc(leaderImage)}" alt="${esc(leaderAlt)}">
                <div class="gym-hero-badge-showcase">
                    <img class="gym-hero-badge-img" src="${esc(badgeImage)}" alt="${esc(badgeName)}">
                    <div class="gym-hero-badge-label">Gym Badge</div>
                    <div class="gym-hero-badge-name">${esc(badgeName)}</div>
                </div>
            </div>
        </div>
        <div class="gym-hero-info">
            <div class="gym-hero-eyebrow">${esc(eyebrow)}</div>
            <h2 class="gym-hero-title">${esc(leaderName)}</h2>
            ${flavor ? `<p class="muted">${esc(flavor)}</p>` : ""}
            <p class="muted">${esc(statusLine)}</p>
            ${actionsHtml}
        </div>
        <hr>
        <h3>Gym Team</h3>
        <div class="gym-roster-grid">${rosterHtml(roster)}</div>
        <hr>
        <h3>Trainers Who've Beaten This Gym (${clearedBy.length})</h3>
        ${clearedByHtml(clearedBy)}
    `;
  }

  async function openGym(gymKey) {
    modal.hidden = false;
    title.textContent = "Loading...";
    body.innerHTML = "<p class='muted'>Loading...</p>";

    const res = await fetch(`/api/proxy/gyms/${encodeURIComponent(gymKey)}`);
    if (!res.ok) {
      body.innerHTML = "<p class='error'>Couldn't load this gym.</p>";
      return;
    }
    const gym = await res.json();
    const typeLabel = gym.type_theme.charAt(0).toUpperCase() + gym.type_theme.slice(1);

    let statusLine;
    let actionsHtml = "";
    if (gym.earned) {
      statusLine = "✅ You've earned this badge.";
    } else if (gym.is_next) {
      statusLine = "This is your next gym — challenge it from Discord or right here on the web.";
      actionsHtml = fightButtonHtml(`Fight ${gym.leader_name}`);
    } else {
      statusLine = "🔒 Beat the earlier gyms first to unlock this challenge.";
    }

    renderGym({
      heading: `${gym.location} Gym`, leaderImage: gym.leader_image, leaderAlt: gym.leader_name,
      badgeImage: gym.badge_image, badgeName: gym.badge_name, typeTheme: gym.type_theme,
      eyebrow: `${gym.location} · ${typeLabel}-type Gym`, leaderName: gym.leader_name, flavor: gym.flavor,
      statusLine, actionsHtml, roster: gym.roster, clearedBy: gym.cleared_by || [],
    });

    const fightBtn = document.getElementById("gm-fight-btn");
    if (fightBtn) fightBtn.addEventListener("click", () => startFight(`/api/proxy/battles/gym/${encodeURIComponent(gymKey)}`, fightBtn));
  }

  async function openCustomGym(ownerId) {
    modal.hidden = false;
    title.textContent = "Loading...";
    body.innerHTML = "<p class='muted'>Loading...</p>";

    const res = await fetch(`/api/proxy/custom-gyms/${encodeURIComponent(ownerId)}`);
    if (!res.ok) {
      body.innerHTML = "<p class='error'>Couldn't load this gym.</p>";
      return;
    }
    const gym = await res.json();
    const typeLabel = gym.type_theme.charAt(0).toUpperCase() + gym.type_theme.slice(1);

    let statusLine;
    let actionsHtml = "";
    if (gym.is_yours) {
      statusLine = "This is your gym.";
      actionsHtml = `<div class="gym-hero-actions"><a class="btn-secondary" href="/gyms/mine">Edit your gym</a></div>`;
    } else if (gym.earned) {
      statusLine = "✅ You've earned this badge.";
    } else {
      statusLine = "A custom gym run by another trainer. Beat it to earn its badge.";
      actionsHtml = fightButtonHtml(`Challenge ${gym.owner_name}`);
    }

    renderGym({
      heading: gym.gym_name, leaderImage: gym.leader_image, leaderAlt: gym.owner_name, leaderClass: "is-sprite",
      badgeImage: `/custom-badges/${encodeURIComponent(ownerId)}.svg?v=${encodeURIComponent(gym.badge_version)}`,
      badgeName: gym.badge_name, typeTheme: gym.type_theme,
      eyebrow: `Custom Gym · ${typeLabel}-type`, leaderName: gym.owner_name, flavor: gym.flavor,
      statusLine, actionsHtml, roster: gym.roster, clearedBy: gym.cleared_by || [],
    });

    const fightBtn = document.getElementById("gm-fight-btn");
    if (fightBtn) fightBtn.addEventListener("click", () => startFight(`/api/proxy/battles/custom-gym/${encodeURIComponent(ownerId)}`, fightBtn));
  }

  async function startFight(url, btn) {
    const originalLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Starting battle...";
    const errorEl = document.getElementById("gm-fight-error");

    try {
      const res = await fetch(url, { method: "POST" });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || "Couldn't start this battle.");
      }
      window.location.href = `/battles/${data.battle_id}`;
    } catch (err) {
      btn.disabled = false;
      btn.textContent = originalLabel;
      if (errorEl) {
        errorEl.textContent = err.message;
        errorEl.hidden = false;
      }
    }
  }
})();
