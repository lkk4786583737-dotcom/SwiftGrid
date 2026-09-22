import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "表速通 SwiftGrid 1.0",
  description: "丢进源表，拿走结果。你的企业级 AI 数据对齐流水线。",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN">
      <body className="text-[14px]">{children}</body>
    </html>
  );
}
