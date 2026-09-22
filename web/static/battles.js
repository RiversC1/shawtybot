(function () {
  const REFRESH_INTERVAL_MS = 10000;

  const tabBtnLive = document.getElementById("tab-btn-live");
  const tabBtnRecent = document.getElementById("tab-btn-recent");
  const tabBtnMine = document.getElementById("tab-btn-mine");
  const tabLive = document.getElementById("tab-live");
  const tabRecent = document.getElementById("tab-recent");
  const tabMine = document.getElementById("tab-mine");
  const liveList = document.getElementById("live-battles-list");
  const recentList = document.getElementById("recent-battles-list");
  const mineList = document.getElementById("mine-battles-list");

  tabBtnLive.addEventListener("click", () => switchTab("live"));
  tabBtnRecent.addEventListener("click", () => switchTab("recent"));
  tabBtnMine.addEventListener("click", () => switchTab("mine"));

  function switchTab(which) {
    tabBtnLive.classList.toggle("active", which === "live");
    tabBtnRecent.classList.toggle("active", which === "recent");
    tabBtnMine.classList.toggle("active", which === "mine");
    tabLive.hidden = which !== "live";
    tabRecent.hidden = which !== "recent";
    tabMine.hidden = which !== "mine";
  }

  const STATUS_LABELS = {
    pending: "Awaiting response",
    active: "Live",
    awaiting_forced_switch: "Choosing next move",
    finished: "Finished",
    abandoned: "Abandoned",
  };

  function typeLabel(battleType) {
    if (battleType === "pvp") return "PvP";
    return battleType.charAt(0).toUpperCase() + battleType.slice(1);
  }

  function battleRow(b) {
    const statusLabel = STATUS_LABELS[b.status] || b.status;

    let resultHtml = "";
    if (b.status === "finished" && b.your_result) {
      resultHtml = `<span class="battle-result-tag result-${b.your_result}">${b.your_result === "win" ? "Win" : "Loss"}</span>`;
    } else if (b.status === "finished" && !b.your_result && b.winner_name) {
      resultHtml = `<span class="muted">🏆 ${b.winner_name} won</span>`;
    } else if (b.status !== "finished" && b.status !== "abandoned") {
      resultHtml = `<span class="battle-live-dot"></span>`;
    }

    return `
      <a class="card battle-list-row" href="/battles/${b.battle_id}">
          <div class="battle-list-main">
              <div class="battle-list-tag">${statusLabel} &middot; ${typeLabel(b.battle_type)}</div>
              <div class="battle-list-title">
                  <img class="battle-list-avatar" src="${b.avatar_a}" alt="">
                  <span>${b.name_a}</span>
                  <span class="battle-list-vs-sep">vs</span>
                  <img class="battle-list-avatar" src="${b.avatar_b}" alt="">
                  <span>${b.name_b}</span>
              </div>
              <div class="muted battle-list-date">${new Date(b.finished_at || b.created_at).toLocaleString()}</div>
          </div>
          <div class="battle-list-side">
              ${resultHtml}
              <span class="btn-secondary">View Room →</span>
          </div>
      </a>`;
  }

  async function load() {
    const res = await fetch("/api/proxy/battles");
    if (!res.ok) {
      liveList.innerHTML = "<p class='error'>Couldn't load battles.</p>";
      recentList.innerHTML = "<p class='error'>Couldn't load battles.</p>";
      mineList.innerHTML = "<p class='error'>Couldn't load battles.</p>";
      return;
    }
    const data = await res.json();

    liveList.innerHTML = (data.live || []).length
      ? data.live.map(battleRow).join("")
      : "<p class='battle-empty'>No battles happening right now.</p>";

    recentList.innerHTML = (data.recent || []).length
      ? data.recent.map(battleRow).join("")
      : "<p class='battle-empty'>No finished battles yet.</p>";

    mineList.innerHTML = (data.mine || []).length
      ? data.mine.map(battleRow).join("")
      : "<p class='battle-empty'>You haven't battled yet.</p>";
  }

  load();
  setInterval(load, REFRESH_INTERVAL_MS);
})();
