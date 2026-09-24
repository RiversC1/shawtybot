(function () {
  const grid = document.getElementById("store-grid");
  const coinsLabel = document.getElementById("store-coins");
  const status = document.getElementById("store-status");

  if (!grid) return;

  grid.querySelectorAll(".store-item").forEach((card) => {
    const key = card.dataset.key;
    const qtyInput = card.querySelector(".store-qty");
    const buyBtn = card.querySelector(".store-buy-btn");

    buyBtn.addEventListener("click", async () => {
      const quantity = parseInt(qtyInput.value, 10) || 1;
      buyBtn.disabled = true;
      status.hidden = false;
      status.textContent = "Buying...";
      status.classList.remove("error", "is-success");

      const res = await fetch("/api/proxy/store/buy", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ item: key, quantity }),
      });
      const data = await res.json();
      buyBtn.disabled = false;

      if (!res.ok) {
        status.textContent = data.detail || "Purchase failed.";
        status.classList.add("error");
        return;
      }

      coinsLabel.textContent = `🪙 ${Number(data.coins_left).toLocaleString("en-US")}`;
      status.textContent = `Bought ${quantity}× ${card.querySelector(".dex-name").textContent}!`;
      status.classList.add("is-success");
    });
  });
})();
