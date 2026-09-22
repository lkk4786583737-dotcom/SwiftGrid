'use client';

import * as React from 'react';
import { FileSpreadsheet } from 'lucide-react';
import type { ComposeSourcePreview } from '@/lib/types';
import { cn } from '@/lib/utils';

export function SourceTablePreview({
  preview,
  className,
}: {
  preview: ComposeSourcePreview;
  className?: string;
}) {
  const sheets = preview.files.flatMap((file) =>
    file.sheets.map((sheet) => ({ ...sheet, filename: file.filename })),
  );
  const [activeIndex, setActiveIndex] = React.useState(0);
  React.useEffect(() => setActiveIndex(0), [preview]);
  const active = sheets[Math.min(activeIndex, Math.max(0, sheets.length - 1))];

  if (!active) {
    return (
      <div className={cn('flex min-h-[188px] items-center justify-center rounded-lg border border-slate-200 bg-slate-50 text-xs text-slate-400', className)}>
        没有读取到可预览的工作表
      </div>
    );
  }

  return (
    <div className={cn('overflow-hidden rounded-lg border border-slate-200 bg-white', className)}>
      <div className='flex min-h-11 items-center justify-between gap-3 border-b border-slate-200 bg-slate-50 px-3'>
        <div className='flex min-w-0 items-center gap-2'>
          <FileSpreadsheet className='h-4 w-4 shrink-0 text-brand' />
          <div className='min-w-0'>
            <strong className='block truncate text-[11px] text-brand-dark' title={active.filename}>{active.filename}</strong>
            <span className='block text-[9px] text-slate-400'>表头第 {active.header_row} 行 · 当前显示 {active.preview_rows} 行真实数据</span>
          </div>
        </div>
        <select
          aria-label='切换预览工作表'
          value={activeIndex}
          onChange={(event) => setActiveIndex(Number(event.target.value))}
          className='h-8 max-w-[240px] rounded-md border border-slate-200 bg-white px-2 text-[10px] text-slate-600 outline-none focus:border-brand'
        >
          {sheets.map((sheet, index) => (
            <option key={sheet.filename + ':' + sheet.name + ':' + index} value={index}>
              {sheet.filename} / {sheet.name}
            </option>
          ))}
        </select>
      </div>
      <div className='max-h-[330px] overflow-auto'>
        <table className='min-w-full border-collapse text-[10px]'>
          <thead className='sticky top-0 z-[1] bg-white'>
            <tr>
              {active.columns.map((column) => (
                <th key={column} className='whitespace-nowrap border-b border-r border-slate-200 px-2.5 py-2 text-left font-bold text-brand-dark last:border-r-0'>
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {active.rows.map((row, rowIndex) => (
              <tr key={rowIndex} className='odd:bg-slate-50/60'>
                {active.columns.map((column, columnIndex) => (
                  <td key={column + ':' + columnIndex} className='max-w-[220px] truncate whitespace-nowrap border-b border-r border-slate-100 px-2.5 py-1.5 text-slate-600 last:border-r-0' title={String(row[columnIndex] ?? '')}>
                    {String(row[columnIndex] ?? '')}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {preview.truncated_sheets && (
        <div className='border-t border-slate-100 px-3 py-1.5 text-[9px] text-slate-400'>
          工作表较多，当前仅展示前 {preview.sheet_count} 个工作表。
        </div>
      )}
    </div>
  );
}
