"use client";

// 轻量 toast 系统（改进项 8）：保存/删除/导出/出错的即时反馈，支持「重试」动作。
import * as React from "react";
import { AnimatePresence, motion } from "framer-motion";
import { CircleAlert, CircleCheck, Info, RotateCw, X } from "lucide-react";
import type { FriendlyError } from "@/lib/errors";

type ToastKind = "success" | "error" | "info";

type ToastItem = {
  id: number;
  kind: ToastKind;
  title: string;
  body?: string;
  detail?: string;
  onRetry?: () => void;
};

type ToastApi = {
  success: (title: string, body?: string) => void;
  info: (title: string, body?: string) => void;
  error: (err: FriendlyError, onRetry?: () => void) => void;
};

const ToastCtx = React.createContext<ToastApi | null>(null);

export function useToast(): ToastApi {
  const v = React.useContext(ToastCtx);
  if (!v) throw new Error("useToast 必须在 <ToastProvider> 内使用");
  return v;
}

let seq = 1;

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = React.useState<ToastItem[]>([]);

  const dismiss = React.useCallback((id: number) => {
    setItems((xs) => xs.filter((x) => x.id !== id));
  }, []);

  const push = React.useCallback(
    (item: Omit<ToastItem, "id">, ttl: number) => {
      const id = seq++;
      setItems((xs) => [...xs.slice(-3), { ...item, id }]);
      if (ttl > 0) setTimeout(() => dismiss(id), ttl);
    },
    [dismiss],
  );

  const api = React.useMemo<ToastApi>(
    () => ({
      success: (title, body) => push({ kind: "success", title, body }, 3500),
      info: (title, body) => push({ kind: "info", title, body }, 4500),
      error: (err, onRetry) =>
        push(
          {
            kind: "error",
            title: err.title,
            body: err.advice,
            detail: err.detail,
            onRetry: err.retryable ? onRetry : undefined,
          },
          onRetry && err.retryable ? 0 : 8000, // 带重试的错误不自动消失
        ),
    }),
    [push],
  );

  return (
    <ToastCtx.Provider value={api}>
      {children}
      <div className="pointer-events-none fixed bottom-5 right-5 z-[80] flex w-80 flex-col gap-2">
        <AnimatePresence>
          {items.map((t) => (
            <ToastCard key={t.id} item={t} onClose={() => dismiss(t.id)} />
          ))}
        </AnimatePresence>
      </div>
    </ToastCtx.Provider>
  );
}

function ToastCard({ item, onClose }: { item: ToastItem; onClose: () => void }) {
  const [showDetail, setShowDetail] = React.useState(false);
  const icon =
    item.kind === "success" ? (
      <CircleCheck className="h-4 w-4 text-brand" />
    ) : item.kind === "error" ? (
      <CircleAlert className="h-4 w-4 text-red-500" />
    ) : (
      <Info className="h-4 w-4 text-sky-500" />
    );

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 16, scale: 0.97 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 8, scale: 0.98 }}
      transition={{ type: "spring", stiffness: 320, damping: 24 }}
      className="pointer-events-auto rounded-xl border border-slate-100 bg-white p-3.5 shadow-apple"
    >
      <div className="flex items-start gap-2.5">
        <span className="mt-0.5 shrink-0">{icon}</span>
        <div className="min-w-0 flex-1">
          <div className="text-[13px] font-semibold text-brand-dark">{item.title}</div>
          {item.body && (
            <div className="mt-0.5 text-xs leading-relaxed text-slate-500">{item.body}</div>
          )}
          {(item.onRetry || item.detail) && (
            <div className="mt-2 flex items-center gap-2">
              {item.onRetry && (
                <button
                  onClick={() => {
                    onClose();
                    item.onRetry?.();
                  }}
                  className="flex items-center gap-1 rounded-md bg-brand px-2.5 py-1 text-[11px] font-semibold text-white smooth hover:opacity-90"
                >
                  <RotateCw className="h-3 w-3" /> 重试
                </button>
              )}
              {item.detail && (
                <button
                  onClick={() => setShowDetail((v) => !v)}
                  className="text-[11px] text-slate-400 smooth hover:text-slate-600"
                >
                  {showDetail ? "收起详情" : "详情"}
                </button>
              )}
            </div>
          )}
          {showDetail && item.detail && (
            <pre className="mt-2 max-h-24 overflow-auto rounded-lg bg-slate-50 p-2 text-[10px] leading-relaxed text-slate-500">
              {item.detail}
            </pre>
          )}
        </div>
        <button
          onClick={onClose}
          className="shrink-0 rounded p-0.5 text-slate-300 smooth hover:bg-slate-100 hover:text-slate-500"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </motion.div>
  );
}
