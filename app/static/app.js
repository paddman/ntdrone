(() => {
  "use strict";

  document.querySelectorAll("[data-confirm]").forEach((element) => {
    element.addEventListener("click", (event) => {
      const message = element.getAttribute("data-confirm") || "ยืนยันการดำเนินการ?";
      if (!window.confirm(message)) event.preventDefault();
    });
  });

  document.querySelectorAll(".flash").forEach((element) => {
    window.setTimeout(() => {
      element.style.opacity = "0";
      element.style.transform = "translateY(-8px)";
      element.style.transition = "opacity .25s ease, transform .25s ease";
      window.setTimeout(() => element.remove(), 300);
    }, 6500);
  });

  document.querySelectorAll("form").forEach((form) => {
    form.addEventListener("submit", () => {
      const submit = form.querySelector("button[type='submit']");
      if (!submit || submit.dataset.noBusy === "true") return;
      submit.disabled = true;
      submit.dataset.originalText = submit.textContent || "";
      submit.textContent = "กำลังดำเนินการ…";
    });
  });
})();
