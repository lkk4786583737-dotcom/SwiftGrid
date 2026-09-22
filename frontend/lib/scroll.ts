/** 平滑滚到元素（用于上传后露出底部主操作按钮） */
export function scrollToElement(el: HTMLElement | null, block: ScrollLogicalPosition = "end") {
  if (!el) return;
  requestAnimationFrame(() => {
    const prefersReducedMotion =
      typeof window !== "undefined" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    el.scrollIntoView({
      behavior: prefersReducedMotion ? "auto" : "smooth",
      block,
      inline: "nearest",
    });
  });
}
