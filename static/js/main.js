// Servima v1 — tiny helpers (vanilla JS)
document.addEventListener("DOMContentLoaded", () => {
  // delete confirmations
  document.querySelectorAll("[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (e) => {
      if (!confirm(form.dataset.confirm)) e.preventDefault();
    });
  });
});
