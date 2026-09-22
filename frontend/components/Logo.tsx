import { cn } from "@/lib/utils";

// 品牌稿的增长柱 + AI 火花。保持为矢量，适配侧栏、欢迎页和折叠态。
export function LogoMark({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "grad-brand flex items-center justify-center overflow-hidden rounded-xl shadow-btn",
        className,
      )}
    >
      <svg viewBox="0 0 120 120" className="h-[68%] w-[68%]" fill="none">
        <path d="M9 68h15" stroke="#A8E3C4" strokeWidth="6" strokeLinecap="round" />
        <path d="M7 83h20" stroke="#A8E3C4" strokeWidth="6" strokeLinecap="round" />
        <rect x="30" y="68" width="12" height="26" rx="6" fill="#BDEDD3" />
        <rect x="46" y="57" width="12" height="37" rx="6" fill="#CFF3DF" />
        <rect x="62" y="45" width="12" height="49" rx="6" fill="#DDF7E8" />
        <rect x="78" y="31" width="12" height="63" rx="6" fill="#EDFBF3" />
        <rect x="94" y="18" width="12" height="76" rx="6" fill="#fff" />
        <path d="M101 7c1.2 7 4.5 10.5 11 11.6-6.5 1.1-9.8 4.6-11 11.6-1.2-7-4.5-10.5-11-11.6C96.5 17.5 99.8 14 101 7Z" fill="#fff" />
      </svg>
    </div>
  );
}

export function Logo({ className }: { className?: string }) {
  return (
    <div className={cn("group flex cursor-pointer items-center gap-3", className)}>
      <LogoMark className="h-10 w-10 smooth group-hover:scale-105" />
      <div className="min-w-0">
        <h1 className="text-[17px] font-bold leading-none text-brand-dark">
          表速通
        </h1>
        <p className="mt-1 text-[10px] font-medium text-slate-400">
          SwiftGrid
        </p>
      </div>
    </div>
  );
}
