/* The MCP info page (#89): copy buttons, and which connect option was used
   (api/mcp/connect-clicked/, the target enum only). */
(function () {
  "use strict";

  function getCookie(name) {
    const row = document.cookie.split("; ").find((c) => c.startsWith(name + "="));
    return row ? decodeURIComponent(row.slice(name.length + 1)) : "";
  }

  function track(target) {
    fetch("/cauldron/api/mcp/connect-clicked/", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRFToken": getCookie("csrftoken") },
      body: JSON.stringify({ target }),
    }).catch(() => {});
  }

  document.addEventListener("click", async (e) => {
    const el = e.target.closest("[data-target]");
    if (!el) return;
    track(el.dataset.target);
    if (!el.dataset.copy) return;
    const source = document.querySelector(el.dataset.copy);
    const label = el.textContent;
    try {
      await navigator.clipboard.writeText(source.textContent.trim());
      el.textContent = el.dataset.copied || "✓";
    } catch {
      window.getSelection().selectAllChildren(source);
    }
    setTimeout(() => { el.textContent = label; }, 1600);
  });
})();
