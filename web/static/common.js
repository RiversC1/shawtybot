// Helpers every page shares (loaded by base.html before any page script).

// Small cached thumbnail for official artwork (see /img/art in main.py);
// any other URL is returned unchanged.
window.artThumb = function (url) {
  const m = /^https:\/\/raw\.githubusercontent\.com\/PokeAPI\/sprites\/master\/sprites\/pokemon\/other\/official-artwork\/((?:shiny\/)?\d+)\.png$/.exec(url || "");
  return m ? `/img/art/${m[1]}.webp` : url;
};

// Escapes text for safe use inside HTML (trainer names and nicknames are user-written).
window.esc = function (s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
};

// One colored badge per type: "fire" -> <span class="type-badge type-fire">Fire</span>.
window.typeBadges = function (types) {
  return (types || [])
    .map((t) => `<span class="type-badge type-${esc(t)}">${esc(t.charAt(0).toUpperCase() + t.slice(1))}</span>`)
    .join("");
};

// POSTs JSON to one of this app's /api/proxy routes. Returns the reply, or
// null after telling the user why it failed.
window.postAction = async function (path, body) {
  let res;
  try {
    res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
  } catch (e) {
    alert("Couldn't reach the server. Check your connection and try again.");
    return null;
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    alert(data.detail || "That action couldn't be completed.");
    return null;
  }
  return data;
};
