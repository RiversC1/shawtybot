// Rankings page: one payload of every trainer's stats (GET /api/rankings),
// sorted client-side per category. The selected tab is kept in the URL hash
// (e.g. /rankings#pvp) so a specific leaderboard can be linked to.
(function () {
  const data = JSON.parse(document.getElementById("rankings-data").textContent || "{}");
  const trainers = data.trainers || [];
  const totals = data.totals || {};

  const tabsEl = document.getElementById("rank-tabs");
  const podiumEl = document.getElementById("rank-podium");
  const listEl = document.getElementById("rank-list");
  const emptyEl = document.getElementById("rank-empty");
  const titleEl = document.getElementById("rank-title");
  const subtitleEl = document.getElementById("rank-subtitle");
  const youEl = document.getElementById("rank-you");

  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

  // value: what's ranked. tiebreak: orders equal values (not shown as a
  // separate rank). detail: the small line under each trainer's name.
  const CATEGORIES = [
    {
      key: "wins", joinHint: "Win a battle", label: "Battle Wins", icon: "⚔️",
      subtitle: "Every battle won: gyms, trainers, the Poké League and PvP",
      value: (t) => t.wins, format: (t) => plural(t.wins, "win"),
      tiebreak: (t) => -t.losses,
      detail: (t) => `${t.wins}W · ${t.losses}L${t.win_rate !== null ? ` · ${t.win_rate}%` : ""}`,
    },
    {
      key: "pvp", joinHint: "Beat another trainer in PvP", label: "PvP Wins", icon: "🤝",
      subtitle: "Battles won against other trainers",
      value: (t) => t.pvp_wins, format: (t) => plural(t.pvp_wins, "win"),
      tiebreak: (t) => t.wins,
      detail: (t) => `${plural(t.wins, "total win")}`,
    },
    {
      key: "winrate", label: "Win Rate", icon: "📈",
      subtitle: `Share of battles won (at least ${totals.win_rate_min_battles || 5} battles to qualify)`,
      value: (t) => t.win_rate, format: (t) => `${t.win_rate}%`,
      qualifies: (t) => t.win_rate !== null,
      tiebreak: (t) => t.battles,
      detail: (t) => `${t.wins}W · ${t.losses}L`,
    },
    {
      key: "badges", joinHint: "Earn a Gym Badge", label: "Gym Badges", icon: "🏅",
      subtitle: `Badges earned out of ${totals.badges || 32}`,
      value: (t) => t.badges, format: (t) => `${t.badges} / ${totals.badges || 32}`,
      tiebreak: (t) => t.league,
      detail: (t) => (t.league ? `${plural(t.league, "League win")}` : "No League wins yet"),
    },
    {
      key: "league", joinHint: "Win a Poké League battle", label: "Poké League", icon: "👑",
      subtitle: `Elite Four and Champion victories out of ${totals.league || 20}`,
      value: (t) => t.league, format: (t) => `${t.league} / ${totals.league || 20}`,
      tiebreak: (t) => t.badges,
      detail: (t) => `${t.badges} badges`,
    },
    {
      key: "pokedex", joinHint: "Catch a Pokémon", label: "Pokédex", icon: "📖",
      subtitle: `Species registered out of ${totals.pokedex || 493}`,
      value: (t) => t.pokedex, format: (t) => `${t.pokedex} species`,
      tiebreak: (t) => t.caught,
      detail: (t) => `${plural(t.caught, "Pokémon")} caught`,
    },
    {
      key: "shinies", joinHint: "Catch a shiny", label: "Shinies", icon: "✨",
      subtitle: "Shiny Pokémon in their collection",
      value: (t) => t.shinies, format: (t) => plural(t.shinies, "shiny").replace("shinys", "shinies"),
      tiebreak: (t) => t.caught,
      detail: (t) => `${plural(t.caught, "Pokémon")} caught`,
    },
    {
      key: "level", joinHint: "Earn some XP", label: "Level", icon: "⭐",
      subtitle: "Trainer level from XP earned",
      value: (t) => t.level, format: (t) => `Lv. ${t.level}`,
      tiebreak: (t) => t.xp,
      detail: (t) => `${t.xp.toLocaleString("en-US")} XP`,
    },
  ];

  function esc(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  }

  // Sorted entries with competition ranks: equal values share a rank, and
  // the next rank skips accordingly (1, 2, 2, 4).
  function ranked(cat) {
    const rows = trainers
      .filter((t) => (cat.qualifies ? cat.qualifies(t) : true))
      .slice()
      .sort((a, b) =>
        cat.value(b) - cat.value(a) ||
        cat.tiebreak(b) - cat.tiebreak(a) ||
        a.username.localeCompare(b.username));
    let rank = 0;
    let prev = null;
    return rows.map((t, i) => {
      const v = cat.value(t);
      // Nothing in this category yet (e.g. 0 PvP wins): listed, but unranked,
      // rather than everyone tying for some rank. Win rate is exempt, since
      // 0% is a real result once you've qualified.
      if (v <= 0 && cat.key !== "winrate") return { t, rank: null };
      if (v !== prev) {
        rank = i + 1;
        prev = v;
      }
      return { t, rank };
    });
  }

  function profileUrl(t) {
    return t.is_you ? "/profile" : `/trainer/${t.user_id}`;
  }

  function avatarHtml(t, cls) {
    return `<img class="${cls}" src="${esc(t.character_sprite)}" alt="" loading="lazy">`;
  }

  function renderPodium(cat, rows) {
    const top = rows.filter(({ rank }) => rank).slice(0, 3);
    if (!top.length) {
      podiumEl.innerHTML = "";
      podiumEl.hidden = true;
      return;
    }
    podiumEl.hidden = false;
    // Visual order 2nd, 1st, 3rd, like a real podium.
    const order = [top[1], top[0], top[2]].filter(Boolean);
    podiumEl.innerHTML = order
      .map(({ t, rank }) => {
        const place = top.findIndex((r) => r.t === t) + 1;
        return `
        <a class="podium-spot place-${place}${t.is_you ? " is-you" : ""}" href="${profileUrl(t)}">
          <div class="podium-medal">${["🥇", "🥈", "🥉"][place - 1]}</div>
          ${avatarHtml(t, "podium-avatar")}
          <div class="podium-name">${esc(t.username)}${t.is_you ? ' <span class="you-tag">You</span>' : ""}</div>
          <div class="podium-value">${esc(cat.format(t))}</div>
          <div class="podium-base"><span>#${rank}</span></div>
        </a>`;
      })
      .join("");
  }

  function renderList(cat, rows) {
    emptyEl.hidden = rows.length > 0;
    listEl.innerHTML = rows
      .map(({ t, rank }) => `
        <li class="rank-row${t.is_you ? " is-you" : ""}${rank && rank <= 3 ? ` top-${rank}` : ""}${rank ? "" : " is-unranked"}">
          <span class="rank-num">${rank || "–"}</span>
          <a class="rank-trainer" href="${profileUrl(t)}">
            ${avatarHtml(t, "rank-avatar")}
            <span class="rank-trainer-text">
              <span class="rank-name">${esc(t.username)}${t.is_you ? ' <span class="you-tag">You</span>' : ""}</span>
              <span class="rank-detail">${esc(cat.detail(t))}</span>
            </span>
          </a>
          <span class="rank-value">${esc(cat.format(t))}</span>
        </li>`)
      .join("");
  }

  function renderYou(cat, rows) {
    const mine = rows.find(({ t }) => t.is_you);
    const me = trainers.find((t) => t.is_you);
    if (!me) {
      youEl.hidden = true;
      return;
    }
    youEl.hidden = false;
    const rankedCount = rows.filter(({ rank }) => rank).length;
    if (mine && !mine.rank) {
      youEl.innerHTML = `<span class="rank-you-label">Your rank</span> <strong>Unranked</strong>
        <span class="muted">· ${esc(cat.joinHint || "Play more")} to get on the board</span>`;
      return;
    }
    if (!mine) {
      const need = totals.win_rate_min_battles || 5;
      youEl.innerHTML = `<span class="rank-you-label">Your rank</span> <strong>Unranked</strong>
        <span class="muted">· play ${need - me.battles} more battle${need - me.battles === 1 ? "" : "s"} to qualify</span>`;
      return;
    }
    youEl.innerHTML = `<span class="rank-you-label">Your rank</span> <strong>#${mine.rank}</strong>
      <span class="muted">of ${rankedCount} · ${esc(cat.format(me))}</span>`;
  }

  function select(key) {
    const cat = CATEGORIES.find((c) => c.key === key) || CATEGORIES[0];
    for (const btn of tabsEl.querySelectorAll(".rank-tab")) {
      const on = btn.dataset.key === cat.key;
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
    }
    titleEl.textContent = cat.label;
    subtitleEl.textContent = cat.subtitle;
    const rows = ranked(cat);
    renderPodium(cat, rows);
    renderList(cat, rows);
    renderYou(cat, rows);
    if (location.hash.slice(1) !== cat.key) history.replaceState(null, "", `#${cat.key}`);
  }

  tabsEl.innerHTML = CATEGORIES.map((c) =>
    `<button type="button" class="rank-tab" role="tab" data-key="${c.key}"><span aria-hidden="true">${c.icon}</span> ${c.label}</button>`
  ).join("");
  tabsEl.addEventListener("click", (e) => {
    const btn = e.target.closest(".rank-tab");
    if (btn) select(btn.dataset.key);
  });

  window.addEventListener("hashchange", () => select(location.hash.slice(1) || "wins"));
  select(location.hash.slice(1) || "wins");
})();
