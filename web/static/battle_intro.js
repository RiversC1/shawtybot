// The Elite Four / Champion / Legend intro, played over the battle scene before the
// first send-out (like Pokémon Platinum's): a blue panel sweeps in from the
// bottom-left carrying the player, another from the top-right carrying the
// opponent; each arrives as a silhouette and lights up with a flash; they
// hold, then slide back out as the screen washes to white and the battle
// begins. play() resolves when it's over; a click or key skips it.
window.BattleIntro = (function () {
  const T = { playerIn: 0, playerLit: 520, foeIn: 700, foeLit: 1220, exit: 2600, white: 3050, done: 3500 };
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  function el(tag, cls, parent) {
    const node = document.createElement(tag);
    node.className = cls;
    if (parent) parent.appendChild(node);
    return node;
  }

  /**
   * opts: { scene, playerArt, playerName, foeArt, foeName, motion (bool),
   *         onStart() — called the moment the effect appears (music) }
   */
  async function play(opts) {
    const overlay = el("div", "bi-overlay", opts.scene);
    overlay.setAttribute("aria-hidden", "true");
    if (!opts.motion) overlay.classList.add("bi-still");
    const player = el("div", "bi-side bi-player", overlay);
    el("div", "bi-band", player);
    const pArt = el("img", "bi-art", player);
    pArt.src = opts.playerArt;
    pArt.alt = "";
    const foe = el("div", "bi-side bi-foe", overlay);
    el("div", "bi-band", foe);
    const fArt = el("img", "bi-art", foe);
    fArt.src = opts.foeArt;
    fArt.alt = "";
    const flash = el("div", "bi-flash", overlay);
    const label = el("div", "bi-label", overlay);
    label.innerHTML = `<span class="bi-name">${esc(opts.playerName)}</span><span class="bi-vs">VS</span><span class="bi-name">${esc(opts.foeName)}</span>`;

    // Make sure both pictures are ready before the show starts.
    await Promise.all([pArt, fArt].map((img) => (img.decode ? img.decode().catch(() => {}) : Promise.resolve())));

    let skipped = false;
    const skip = () => { skipped = true; };
    window.addEventListener("pointerdown", skip, { once: true });
    window.addEventListener("keydown", skip, { once: true });

    const flashOnce = () => {
      flash.classList.remove("is-on");
      void flash.offsetWidth;
      flash.classList.add("is-on");
    };
    const steps = [
      [T.playerIn, () => { overlay.classList.add("is-open"); player.classList.add("is-in"); opts.onStart && opts.onStart(); }],
      [T.playerLit, () => { flashOnce(); player.classList.add("is-lit"); }],
      [T.foeIn, () => foe.classList.add("is-in")],
      [T.foeLit, () => { flashOnce(); foe.classList.add("is-lit"); label.classList.add("is-in"); }],
      [T.exit, () => { player.classList.add("is-out"); foe.classList.add("is-out"); label.classList.remove("is-in"); }],
      [T.white, () => overlay.classList.add("is-white")],
    ];
    let t = 0;
    for (const [at, fn] of steps) {
      if (skipped) break;
      await wait(at - t);
      t = at;
      if (!skipped) fn();
    }
    if (skipped) {
      opts.onStart && !overlay.classList.contains("is-open") && opts.onStart();
      overlay.classList.add("is-white");
      await wait(200);
    } else {
      await wait(T.done - t);
    }
    overlay.classList.add("is-gone");
    await wait(450);
    window.removeEventListener("pointerdown", skip);
    window.removeEventListener("keydown", skip);
    overlay.remove();
  }

  // Browsers only allow sound after a click on this page. When the music
  // can't start on its own, show a "Start battle" button: its click starts
  // the music and the intro together.
  function gate(scene) {
    return new Promise((resolve) => {
      const g = el("div", "bi-gate", scene);
      const btn = el("button", "btn-primary bi-gate-btn", g);
      btn.type = "button";
      btn.textContent = "⚔️ Start battle";
      btn.addEventListener("click", () => {
        g.remove();
        resolve();
      }, { once: true });
      btn.focus();
    });
  }

  function esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  }

  return { play, gate };
})();
