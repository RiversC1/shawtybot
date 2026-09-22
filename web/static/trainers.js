(function () {
  document.querySelectorAll(".trainer-trade-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const originalLabel = btn.textContent;
      btn.disabled = true;
      btn.textContent = "Starting...";
      try {
        const res = await fetch(`/api/proxy/trades/start/${btn.dataset.targetUserId}`, { method: "POST" });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Couldn't start a trade.");
        window.location.href = `/trades/${data.trade_id}`;
      } catch (err) {
        btn.disabled = false;
        btn.textContent = originalLabel;
        alert(err.message);
      }
    });
  });
})();
