(function () {
  const grid = document.getElementById("store-grid");
  const coinsLabel = document.getElementById("store-coins");
  const status = document.getElementById("store-status");

  if (!grid) return;

  let coins = Number(coinsLabel.dataset.coins) || 0;
  const fmt = (n) => Number(n).toLocaleString("en-US");

  // ---------- Confirmation modal ----------
  const modal = document.getElementById("buy-modal");
  const iconEl = document.getElementById("buy-icon");
  const itemEl = document.getElementById("buy-item");
  const unitEl = document.getElementById("buy-unit");
  const totalEl = document.getElementById("buy-total");
  const afterEl = document.getElementById("buy-after");
  const warningEl = document.getElementById("buy-warning");
  const confirmBtn = document.getElementById("buy-confirm");
  let pending = null; // the purchase waiting on Confirm

  function closeModal() {
    modal.hidden = true;
    pending = null;
  }

  function askToBuy(card, quantity) {
    const price = Number(card.dataset.price) || 0;
    const total = price * quantity;
    const name = card.querySelector(".dex-name").textContent;
    pending = { card, quantity, name };
    iconEl.src = card.querySelector("img").src;
    itemEl.textContent = quantity > 1 ? `${quantity}× ${name}` : name;
    unitEl.textContent = quantity > 1 ? `🪙 ${fmt(price)} each` : "";
    totalEl.textContent = `🪙 ${fmt(total)}`;
    const after = coins - total;
    const short = after < 0;
    afterEl.textContent = short ? "—" : `🪙 ${fmt(after)}`;
    warningEl.hidden = !short;
    warningEl.textContent = short ? `You need 🪙 ${fmt(-after)} more coins.` : "";
    confirmBtn.disabled = short;
    confirmBtn.textContent = "Buy";
    modal.hidden = false;
    (short ? document.getElementById("buy-cancel") : confirmBtn).focus();
  }

  document.getElementById("buy-close").addEventListener("click", closeModal);
  document.getElementById("buy-cancel").addEventListener("click", closeModal);
  modal.addEventListener("click", (e) => {
    if (e.target === modal) closeModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !modal.hidden) closeModal();
  });
  confirmBtn.addEventListener("click", () => {
    if (pending) buy(pending);
  });

  // ---------- Buying ----------
  async function buy({ card, quantity, name }) {
    confirmBtn.disabled = true;
    confirmBtn.textContent = "Buying...";
    status.classList.remove("error", "is-success");

    let res, data;
    try {
      res = await fetch("/api/proxy/store/buy", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ item: card.dataset.key, quantity }),
      });
      data = await res.json();
    } catch (e) {
      res = { ok: false };
      data = {};
    }
    closeModal();
    status.hidden = false;

    if (!res.ok) {
      status.textContent = data.detail || "Purchase failed.";
      status.classList.add("error");
      return;
    }

    coins = Number(data.coins_left) || 0;
    coinsLabel.dataset.coins = coins;
    coinsLabel.textContent = `🪙 ${fmt(coins)}`;
    status.textContent = `Bought ${quantity}× ${name}!`;
    status.classList.add("is-success");
    if (card.querySelector('input[type="hidden"].store-qty')) {
      card.querySelector(".store-buy-row").innerHTML = '<span class="store-owned">✓ Owned</span>';
    }
  }

  grid.querySelectorAll(".store-item").forEach((card) => {
    const qtyInput = card.querySelector(".store-qty");
    const buyBtn = card.querySelector(".store-buy-btn");
    if (!buyBtn) return; // an owned key item

    buyBtn.addEventListener("click", () => {
      const quantity = Math.max(1, parseInt(qtyInput.value, 10) || 1);
      qtyInput.value = quantity;
      askToBuy(card, quantity);
    });
  });
})();
