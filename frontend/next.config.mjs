/** @type {import('next').NextConfig} */
// 架构硬约束（CURSOR_TASKS §2）：
// - output:'export' 纯客户端静态导出；不用 SSR / server actions / app/api 路由。
// - 发布(B)由 FastAPI 同源托管 frontend/out；开发(A) next dev:3000 + uvicorn:8000，走 CORS。
const nextConfig = {
  output: "export",
  reactStrictMode: true,
  // 静态导出不支持 next/image 优化 loader
  images: { unoptimized: true },
  // 导出为带斜杠目录，便于 FastAPI StaticFiles(html=True) 同源托管
  trailingSlash: true,
};

export default nextConfig;
