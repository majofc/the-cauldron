/* Mobile sheets — drag a sheet's head down to dismiss it (ARCANO_STYLE_GUIDE.md § Mobile sheets).
   Shared by the Forge's dialogs and the Journal's goal cauldron (#119).
   Exposes window.CauldronSheet.wireSwipe(sheet, head, close).
   Phones only: on desktop the head is not a drag target. Past SWIPE_CLOSE_PX the
   sheet closes; short of that it settles back. While dragging the sheet carries
   .is-dragging, while settling .is-settling — each sheet's CSS styles the motion
   (and drops it under prefers-reduced-motion). */
(function () {
  "use strict";

  const SWIPE_CLOSE_PX = 80;
  const phoneSheets = window.matchMedia("(max-width: 768px)");

  function wireSwipe(sheet, head, close) {
    if (!sheet || !head) return;
    let startY = null;
    let dy = 0;
    head.addEventListener("pointerdown", (e) => {
      if (!phoneSheets.matches || e.target.closest("button")) return;
      startY = e.clientY;
      dy = 0;
      sheet.classList.remove("is-settling");
      sheet.classList.add("is-dragging");
      head.setPointerCapture(e.pointerId);
    });
    head.addEventListener("pointermove", (e) => {
      if (startY === null) return;
      dy = Math.max(0, e.clientY - startY);
      sheet.style.transform = `translateY(${dy}px)`;
    });
    const end = () => {
      if (startY === null) return;
      startY = null;
      sheet.classList.remove("is-dragging");
      if (dy > SWIPE_CLOSE_PX) {
        sheet.style.transform = "";
        close();
        return;
      }
      sheet.classList.add("is-settling");
      sheet.style.transform = "";
    };
    head.addEventListener("pointerup", end);
    head.addEventListener("pointercancel", end);
  }

  window.CauldronSheet = { wireSwipe: wireSwipe, SWIPE_CLOSE_PX: SWIPE_CLOSE_PX };
})();
