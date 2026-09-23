(function () {
  const modal = document.getElementById("league-modal");
  const closeBtn = document.getElementById("lm-close");
  const title = document.getElementById("lm-title");
  const body = document.getElementById("lm-body");

  if (!modal) return;

  closeBtn.addEventListener("click", () => (modal.hidden = true));
  modal.addEventListener("click", (e) => {
    if (e.target === modal) modal.hidden = true;
  });

  document.querySelectorAll("[data-member-key]").forEach((card) =>
    card.addEventListener("click", () => openMember(card.dataset.generation, card.dataset.memberKey))
  );

  function typeBadges(types) {
    return (types || [])
      .map((t) => `<span class="type-badge type-${t}">${t.charAt(0).toUpperCase() + t.slice(1)}</span>`)
      .join("");
  }

  function titleCase(s) {
    return s.charAt(0).toUpperCase() + s.slice(1);
  }

  async function openMember(generation, memberKey) {
    modal.hidden = false;
    title.textContent = "Loading...";
    body.innerHTML = "<p class='muted'>Loading...</p>";

    const res = await fetch(`/api/proxy/league/${generation}/${memberKey}`);
    if (!res.ok) {
      body.innerHTML = "<p class='error'>Couldn't load this trainer.</p>";
      return;
    }
    const member = await res.json();
    const isChampion = member.role === "champion";
    title.textContent = isChampion ? `${member.name} — Champion` : `${member.name} — Elite Four`;

    const rosterHtml = member.roster
      .map(
        (mon) => `
        <div class="gym-roster-mon">
            <img src="${mon.artwork}" alt="${mon.name}">
            <div class="gym-roster-mon-name">${mon.name}</div>
            <div class="favorite-types" style="justify-content:center;margin-top:2px;">${typeBadges(mon.types)}</div>
        </div>`
      )
      .join("");

    const typeLabel = member.type_theme ? titleCase(member.type_theme) : null;
    let statusLine;
    let actionsHtml = "";
    if (member.earned) {
      statusLine = isChampion ? "🏆 You've defeated this Champion." : "✅ You've defeated this Elite Four member.";
    } else if (member.is_next) {
      statusLine = "This is your next challenge — fight from Discord or right here on the web.";
      actionsHtml = `
        <div class="gym-hero-actions">
            <button id="lm-fight-btn" class="btn-primary">Fight ${member.name}</button>
        </div>
        <p class="error" id="lm-fight-error" hidden></p>`;
    } else {
      statusLine = isChampion
        ? "🔒 Defeat this region's Elite Four first to unlock the Champion."
        : "🔒 Beat the earlier Elite Four members first, in order.";
    }

    body.innerHTML = `
        <div class="gym-hero">
            ${typeLabel ? `<span class="type-badge type-${member.type_theme} gym-hero-type-tag">${typeLabel}</span>` : ""}
            <div class="gym-hero-main">
                <img class="gym-hero-leader" src="${member.portrait}" alt="${member.name}">
            </div>
        </div>
        <div class="gym-hero-info">
            <div class="gym-hero-eyebrow">
                ${titleCase(generation)} · ${isChampion ? "Champion" : `${typeLabel}-type Elite Four`}
            </div>
            <h2 class="gym-hero-title">${member.name}</h2>
            <p class="muted">${member.flavor}</p>
            <p class="muted">${statusLine}</p>
            ${actionsHtml}
        </div>
        <hr>
        <h3>Team</h3>
        <div class="gym-roster-grid">${rosterHtml}</div>
    `;

    const fightBtn = document.getElementById("lm-fight-btn");
    if (fightBtn) {
      fightBtn.addEventListener("click", () => startFight(generation, memberKey, isChampion, fightBtn));
    }
  }

  async function startFight(generation, memberKey, isChampion, btn) {
    const originalLabel = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Starting battle...";
    const errorEl = document.getElementById("lm-fight-error");

    const url = isChampion
      ? `/api/proxy/battles/champion/${generation}`
      : `/api/proxy/battles/elite4/${generation}/${memberKey}`;

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
