// 图表导出：硬约束（SPEC §6.9）——下载产物必须是干净静态图，不带动画。
// 做法：取容器内渲染好的 <svg>（动画已结束的终态），序列化为 svg/png/jpg。

import { downloadBlob } from "./api";

export type ChartFormat = "svg" | "png" | "jpg";

export async function exportChart(
  container: HTMLElement | null,
  format: ChartFormat,
  filename = "swiftgrid_chart",
): Promise<void> {
  const svg = container?.querySelector('svg[role="img"]');
  if (!svg) throw new Error("未找到图表");

  // 克隆并补上命名空间 + 白底，保证脱离页面后样式干净
  const clone = svg.cloneNode(true) as SVGSVGElement;
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  const rect = svg.getBoundingClientRect();
  const w = Math.max(rect.width, 480);
  const h = Math.max(rect.height, 300);
  clone.setAttribute("width", String(w));
  clone.setAttribute("height", String(h));

  const xml = new XMLSerializer().serializeToString(clone);

  if (format === "svg") {
    downloadBlob(
      new Blob([xml], { type: "image/svg+xml;charset=utf-8" }),
      `${filename}.svg`,
    );
    return;
  }

  // 位图：SVG → Image → Canvas → png/jpg
  const dataUrl = "data:image/svg+xml;base64," + btoa(unescape(encodeURIComponent(xml)));
  const img = new Image();
  await new Promise<void>((resolve, reject) => {
    img.onload = () => resolve();
    img.onerror = () => reject(new Error("图表渲染失败"));
    img.src = dataUrl;
  });

  const scale = 2; // 高清导出
  const canvas = document.createElement("canvas");
  canvas.width = w * scale;
  canvas.height = h * scale;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas 不可用");
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

  const mime = format === "png" ? "image/png" : "image/jpeg";
  const blob: Blob = await new Promise((resolve, reject) =>
    canvas.toBlob(
      (b) => (b ? resolve(b) : reject(new Error("导出失败"))),
      mime,
      0.95,
    ),
  );
  downloadBlob(blob, `${filename}.${format}`);
}
