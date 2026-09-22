"use client";

import * as React from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  ClipboardList,
  Loader2,
  TriangleAlert,
  Upload,
} from "lucide-react";
import {
  ParsedTablePreview,
  PREVIEW_HEIGHT_SOURCE,
  PREVIEW_HEIGHT_TARGET,
} from "./ParsedTablePreview";
import { useAppState } from "@/components/providers";
import { useToast } from "@/components/Toast";
import * as api from "@/lib/api";
import { humanizeError } from "@/lib/errors";
import type { ParseResp } from "@/lib/types";
import { cn } from "@/lib/utils";

type Status = "idle" | "parsing" | "done" | "error";

export function UniversalInput({
  slot,
  label,
  badge,
  compact,
  previewHeight,
  onParsed,
}: {
  slot: "source" | "target";
  label: string;
  badge?: string;
  compact?: boolean;
  /** 预览区固定高度；默认源表 240 / 目标 200 */
  previewHeight?: number;
  onParsed: (resp: ParseResp | null) => void;
}) {
  const { configured, configurationChecked, requestConfig } = useAppState();
  const toast = useToast();
  const [status, setStatus] = React.useState<Status>("idle");
  const [resp, setResp] = React.useState<ParseResp | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [oversize, setOversize] = React.useState(false);
  const [dragging, setDragging] = React.useState(false);
  const fileRef = React.useRef<HTMLInputElement>(null);

  const resolvedPreviewHeight =
    previewHeight ?? (compact ? PREVIEW_HEIGHT_TARGET : PREVIEW_HEIGHT_SOURCE);

  function ensureConfigured(): boolean {
    if (!configurationChecked) {
      toast.info("正在检查模型连接", "稍等片刻，再上传表格。");
      return false;
    }
    if (configured) return true;
    toast.info("请先连接 AI 模型", "填写 API Key 后再上传表格。");
    requestConfig();
    return false;
  }

  async function submit(fd: FormData) {
    fd.set("slot", slot);
    setStatus("parsing");
    setError(null);
    try {
      const r = await api.parseInput(fd);
      setResp(r);
      setOversize(r.oversize);
      setStatus("done");
      onParsed(r);
    } catch (e) {
      setStatus("error");
      const friendly = humanizeError(e);
      setError(`${friendly.title}。${friendly.advice}`);
      onParsed(null);
    }
  }

  function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    if (!ensureConfigured()) {
      if (fileRef.current) fileRef.current.value = "";
      return;
    }
    const fd = new FormData();
    Array.from(files).forEach((f) => fd.append("files", f));
    void submit(fd);
  }

  function handlePaste(e: React.ClipboardEvent) {
    const text = e.clipboardData.getData("text");
    if (text && text.trim()) {
      e.preventDefault();
      if (!ensureConfigured()) return;
      const fd = new FormData();
      fd.set("paste_text", text);
      void submit(fd);
    }
  }

  function clearCurrent() {
    setResp(null);
    setStatus("idle");
    setError(null);
    setOversize(false);
    if (fileRef.current) fileRef.current.value = "";
    onParsed(null);
  }

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <label className="text-sm font-semibold text-slate-800">{label}</label>
        {badge && (
          <span className="rounded-full border border-slate-200 bg-white px-2 py-0.5 text-[10px] text-slate-400">
            {badge}
          </span>
        )}
      </div>

      <div
        tabIndex={0}
        onPaste={handlePaste}
        onClick={() => {
          if (status !== "done" && ensureConfigured()) fileRef.current?.click();
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          if (!ensureConfigured()) return;
          handleFiles(e.dataTransfer.files);
        }}
        className={cn(
          "dz group relative flex cursor-pointer flex-col items-center justify-center rounded-xl bg-white p-4 smooth hover:shadow-md focus:outline-none focus:ring-2 focus:ring-brand/30",
          compact ? "min-h-[96px]" : "min-h-[120px]",
          status === "done" && "cursor-default items-stretch p-0 hover:shadow-none",
          dragging && "ring-2 ring-brand",
        )}
      >
        {status !== "done" && (
          <svg className="pointer-events-none absolute inset-0 h-full w-full">
            <rect
              className="dz-rect"
              width="100%"
              height="100%"
              rx="12"
              fill="none"
              stroke={dragging ? "#0F8A4D" : "#CBD5E1"}
              strokeWidth="2"
              strokeDasharray="8,8"
            />
          </svg>
        )}

        <input
          ref={fileRef}
          type="file"
          multiple
          accept=".xlsx,.xls"
          className="hidden"
          onChange={(e) => handleFiles(e.target.files)}
        />

        {status === "parsing" ? (
          <div className="relative z-10 flex flex-col items-center text-brand">
            <Loader2 className="mb-2 h-6 w-6 animate-spin" />
            <span className="text-sm font-medium">正在解析...</span>
          </div>
        ) : status === "done" && resp ? (
          <div className="relative z-10 w-full">
            <ParsedTablePreview
              resp={resp}
              previewHeight={resolvedPreviewHeight}
              onClear={clearCurrent}
            />
          </div>
        ) : (
          <EmptyPrompt compact={compact} />
        )}
      </div>

      <AnimatePresence>
        {oversize && (
          <motion.p
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="mt-2 flex items-center gap-1.5 text-[11px] font-medium text-amber-600"
          >
            <TriangleAlert className="h-3.5 w-3.5" />
            粘贴行数较多，建议改用上传文件，解析更稳定。
          </motion.p>
        )}
        {error && (
          <motion.p
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="mt-2 text-[11px] font-medium text-red-500"
          >
            {error}
          </motion.p>
        )}
      </AnimatePresence>
    </div>
  );
}

function EmptyPrompt({ compact }: { compact?: boolean }) {
  return (
    <>
      <div
        className={cn(
          "relative z-10 mb-2 flex items-center justify-center rounded-full smooth group-hover:scale-110",
          compact
            ? "h-9 w-9 bg-slate-100 text-slate-400"
            : "h-10 w-10 bg-brand-light/15 text-brand",
        )}
      >
        {compact ? (
          <ClipboardList className="h-4 w-4" />
        ) : (
          <Upload className="h-4 w-4" />
        )}
      </div>
      <span
        className={cn(
          "relative z-10 text-center font-medium",
          compact ? "text-xs text-slate-500" : "text-sm text-slate-600",
        )}
      >
        {compact ? (
          "上传或粘贴目标表样例"
        ) : (
          <>
            拖入 / 点击上传 Excel，或直接{" "}
            <kbd className="rounded border border-slate-200 bg-slate-100 px-1.5 py-0.5 text-[11px]">
              Ctrl+V
            </kbd>{" "}
            粘贴
          </>
        )}
      </span>
      {!compact && (
        <>
          <span className="relative z-10 mt-1 text-[11px] text-slate-400">
            支持 .xlsx / .xls / 多文件；小样本可直接粘贴
          </span>
          <span className="relative z-10 mt-1.5 text-[10px] text-slate-400/90">
            表格在本机解析；AI 推断逻辑时只使用列名和少量样例行
          </span>
        </>
      )}
    </>
  );
}
