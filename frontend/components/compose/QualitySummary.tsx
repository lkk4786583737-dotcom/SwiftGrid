"use client";

import { CircleCheck } from "lucide-react";
import type { QualitySummaryData } from "./presentation-types";
import { cn } from "@/lib/utils";

export function QualityBar({ quality }: { quality: QualitySummaryData }) {
  return (
    <div className="flex min-h-[62px] items-center justify-between gap-4 border-b border-brand/20 bg-brand/5 px-4 py-2.5">
      <div className="flex min-w-0 items-center gap-2.5">
        <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand text-white">
          <CircleCheck className="h-4 w-4" />
        </span>
        <div className="min-w-0">
          <strong className="block text-[13px] text-brand-dark/90">
            {quality.headline}
          </strong>
          <span className="block truncate text-[10px] text-brand/70">
            {quality.subline}
          </span>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-4 whitespace-nowrap">
        {quality.facts.map((fact, i) => (
          <div
            key={fact.label}
            className={cn(
              "pl-4",
              i > 0 && "border-l border-brand/20",
            )}
          >
            <b className="block text-[11px] text-brand-dark/90">{fact.value}</b>
            <span className="block text-[9px] text-brand/60">{fact.label}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function QualitySummary({ quality }: { quality: QualitySummaryData }) {
  return (
    <aside className="bg-slate-50/60 p-4">
      <h3 className="mb-3 text-[13px] font-bold text-brand-dark">
        执行与质量摘要
      </h3>
      <div className="divide-y divide-slate-100">
        <AuditRow label="数据范围" value={quality.datasetLabel ?? "全量数据"} />
        <AuditRow
          label="来源行数"
          value={quality.rowsLabel ?? "计划与实际读取一致"}
          good={quality.rowsPassed ?? true}
        />
        <AuditRow
          label="金额对账"
          value={quality.reconciliationLabel ?? "源表、客户、类型三层一致"}
          good={quality.reconciliationPassed ?? true}
        />
        <AuditRow label="分类范围" value={quality.classificationLabel ?? "全量数据中的唯一客户"} />
        <AuditRow label="数据指纹" value={quality.sourceFingerprint} />
      </div>

      {quality.warnings.map((w, i) => (
        <div
          key={i}
          className="mt-3.5 rounded-lg border border-amber-200 bg-amber-50 p-2.5 text-[10px] leading-relaxed text-amber-800"
        >
          <b className="text-amber-700">质量提示</b>
          <br />
          {w}
        </div>
      ))}

      <div className="mt-3.5 rounded-lg border border-slate-200 bg-white p-3">
        <strong className="text-[11px] text-brand-dark">
          完成后自动沉淀逻辑
        </strong>
        <p className="mt-1 text-[10px] leading-relaxed text-slate-500">
          保存结构指纹、字段角色、计算口径、输出定义和本次确认的分类映射，不保存全量源数据。
        </p>
      </div>

      {!quality.canComplete && quality.blockReason && (
        <div className="mt-3 rounded-lg border border-red-200 bg-red-50 p-2.5 text-[10px] text-red-600">
          <b>无法完成制表</b>
          <br />
          {quality.blockReason}
        </div>
      )}
    </aside>
  );
}

function AuditRow({
  label,
  value,
  good,
}: {
  label: string;
  value: string;
  good?: boolean;
}) {
  return (
    <div className="py-2.5">
      <span className="block text-[9px] text-slate-400">{label}</span>
      <strong
        className={cn(
          "mt-1 block text-[11px] leading-snug",
          good ? "text-brand" : "text-slate-600",
        )}
      >
        {value}
      </strong>
    </div>
  );
}
