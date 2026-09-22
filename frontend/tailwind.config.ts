import type { Config } from "tailwindcss";

// 设计 token 直接落地（SPEC §2，绿色系 #0F8A4D，非 Teal）
const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        brand: {
          light: "#2FBF71", // 亮绿：高亮、徽标、New 角标
          DEFAULT: "#0F8A4D", // 核心主色：主按钮/Active/Tab 底边/Logo
          dark: "#0B0F14", // 中性深色：标题/Logo 文字
        },
        surface: "#f8fafc", // 全局背景浅灰
        warning: { bg: "#fffbeb", border: "#fde68a", text: "#78350f" }, // HITL 琥珀
      },
      boxShadow: {
        apple:
          "0 4px 24px -6px rgba(15,138,77,0.12), 0 0 0 1px rgba(15,138,77,0.04)",
        "apple-hover":
          "0 12px 32px -8px rgba(15,138,77,0.2), 0 0 0 1px rgba(15,138,77,0.05)",
        btn: "0 8px 20px rgba(15,138,77,0.25), inset 0 1.5px 0 rgba(255,255,255,0.4)",
        amber: "0 8px 20px rgba(217,119,6,.25)",
      },
      fontFamily: {
        sans: ["Inter", "-apple-system", "sans-serif"],
      },
      backgroundImage: {
        "grad-brand": "linear-gradient(180deg,#2FBF71 0%,#0F8A4D 100%)",
      },
      borderRadius: {
        "2xl": "1rem",
        "3xl": "1.5rem",
      },
      keyframes: {
        fadeUp: {
          "0%": { opacity: "0", transform: "translateY(14px) scale(.98)" },
          "100%": { opacity: "1", transform: "none" },
        },
        slideIn: {
          "0%": { opacity: "0", transform: "translateX(24px)" },
          "100%": { opacity: "1", transform: "none" },
        },
        breathe: {
          "0%,100%": { boxShadow: "0 0 0 0 rgba(15,138,77,.35)" },
          "50%": { boxShadow: "0 0 0 8px rgba(15,138,77,0)" },
        },
        breatheAmber: {
          "0%,100%": { boxShadow: "0 0 0 0 rgba(217,119,6,.35)" },
          "50%": { boxShadow: "0 0 0 8px rgba(217,119,6,0)" },
        },
        spring: {
          "0%": { opacity: "0", transform: "translate(-50%, calc(-50% + 18px)) scale(.96)" },
          "60%": { transform: "translate(-50%, calc(-50% - 3px)) scale(1.01)" },
          "100%": { opacity: "1", transform: "translate(-50%, -50%)" },
        },
        sweep: {
          to: { transform: "translateX(120%)" },
        },
        flow: {
          to: { backgroundPosition: "0 -200%" },
        },
        grow: {
          from: { width: "0" },
        },
        dashflow: {
          to: { strokeDashoffset: "-20" },
        },
      },
      animation: {
        "fade-up": "fadeUp .5s cubic-bezier(.16,1,.3,1) both",
        "slide-in": "slideIn .5s cubic-bezier(.16,1,.3,1) both",
        breathe: "breathe 1.8s ease-in-out infinite",
        "breathe-amber": "breatheAmber 1.8s ease-in-out infinite",
        spring: "spring .55s cubic-bezier(.34,1.56,.64,1) both",
        sweep: "sweep 1.4s ease-in-out infinite",
        flow: "flow 1.2s linear infinite",
        grow: "grow 1s cubic-bezier(.16,1,.3,1) both",
      },
    },
  },
  plugins: [],
};

export default config;
