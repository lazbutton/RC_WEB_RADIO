(function () {
  const data = window.NTR_FESTIVAL;
  const root = document.getElementById("days");
  data.days.forEach(function (day) {
    const col = document.createElement("section");
    col.className = "day";
    const h = document.createElement("h2");
    h.textContent = day.label;
    col.appendChild(h);
    day.slots.forEach(function (slot) {
      const el = document.createElement("article");
      el.className = "slot " + slot.type;
      el.innerHTML =
        "<time>" +
        slot.start +
        "–" +
        slot.end +
        "</time>" +
        "<div>" +
        slot.title +
        "</div>";
      col.appendChild(el);
    });
    root.appendChild(col);
  });

  const titleEl = document.getElementById("now-title");
  const metaEl = document.getElementById("now-meta");

  async function poll() {
    try {
      const res = await fetch(data.nowUrl, { cache: "no-store" });
      if (!res.ok) return;
      const now = await res.json();
      titleEl.textContent = (now.artist ? now.artist + " — " : "") + (now.title || "—");
      metaEl.textContent = (now.source || now.SOURCE_NAME || "auto-DJ") +
        (now.on_air ? " · " + now.on_air : "");
    } catch (_) {
      titleEl.textContent = "Now Playing injoignable";
    }
  }

  poll();
  setInterval(poll, 5000);
})();
