'use client';

import * as React from 'react';
import { ArrowRight, Check, Loader2, RotateCcw, ShieldCheck, Upload, X } from 'lucide-react';
import { Button } from '@/components/ui/button';
import type { ComposeSourcePreview } from '@/lib/types';
import { cn } from '@/lib/utils';
import { ComposeBadge } from './ComposeBadge';
import type { ComposeSourceFile } from './presentation-types';
import { SourceTablePreview } from './SourceTablePreview';

export function UploadState({
  files,
  sourcePreview,
  preparingPreview = false,
  profiling = false,
  profilingProgress,
  onFilesAdd,
  onFileRemove,
  onPreparePreview,
  onResetSource,
  onStartProfiling,
  onCancelProfiling,
}: {
  files: ComposeSourceFile[];
  sourcePreview?: ComposeSourcePreview | null;
  preparingPreview?: boolean;
  profiling?: boolean;
  profilingProgress?: { label: string; percent: number };
  onFilesAdd?: (files: FileList) => void;
  onFileRemove?: (id: string) => void;
  onPreparePreview?: () => void;
  onResetSource?: () => void;
  onStartProfiling?: () => void;
  onCancelProfiling?: () => void;
}) {
  const inputRef = React.useRef<HTMLInputElement>(null);
  const hasFiles = files.length > 0;

  function chooseFiles() {
    if (!profiling && !preparingPreview) inputRef.current?.click();
  }

  function handleDrop(event: React.DragEvent) {
    event.preventDefault();
    if (!profiling && !preparingPreview && event.dataTransfer.files.length > 0) {
      onFilesAdd?.(event.dataTransfer.files);
    }
  }

  return (
    <div className='overflow-hidden rounded-lg border border-slate-100 bg-white shadow-apple'>
      <div className='flex min-h-[64px] items-center justify-between gap-4 border-b border-slate-100 px-5 py-3.5'>
        <div>
          <h2 className='text-base font-bold text-brand-dark'>上传源数据表</h2>
          <p className='text-xs text-slate-500'>先核对真实内容，再开始智能制表</p>
        </div>
        <ComposeBadge dot>最终结果强制全量计算</ComposeBadge>
      </div>

      <div className='grid min-h-[600px] grid-cols-1 xl:grid-cols-[minmax(0,1.65fr)_300px]'>
        <div className='min-w-0 border-b border-slate-100 p-6 xl:border-b-0 xl:border-r'>
          <div className='mb-2 flex min-h-8 items-center justify-between gap-3'>
            <strong className='text-[13px] text-brand-dark'>源数据表</strong>
            {sourcePreview && !profiling && (
              <Button variant='ghost' size='sm' className='h-8' onClick={onResetSource}>
                <RotateCcw className='h-3.5 w-3.5' />
                更换文件
              </Button>
            )}
          </div>

          {sourcePreview ? (
            <SourceTablePreview preview={sourcePreview} />
          ) : (
            <div
              role='button'
              tabIndex={0}
              onClick={chooseFiles}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') chooseFiles();
              }}
              onDragOver={(event) => event.preventDefault()}
              onDrop={handleDrop}
              className='flex min-h-[220px] cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed border-slate-300 bg-slate-50/50 px-6 text-center transition-colors hover:border-brand hover:bg-brand/5'
            >
              {preparingPreview ? (
                <Loader2 className='mb-3 h-8 w-8 animate-spin text-brand' />
              ) : (
                <span className='mb-3 flex h-11 w-11 items-center justify-center rounded-full bg-brand/10 text-brand'>
                  <Upload className='h-5 w-5' />
                </span>
              )}
              <strong className='text-sm text-brand-dark'>
                {preparingPreview ? '正在读取真实表格内容' : '拖入或点击上传 Excel 文件'}
              </strong>
              <span className='mt-1.5 text-xs text-slate-400'>支持 .xlsx / .xlsm / .csv / .tsv，可多选</span>
            </div>
          )}

          <input
            ref={inputRef}
            type='file'
            accept='.xlsx,.xlsm,.csv,.tsv'
            multiple
            className='hidden'
            onChange={(event) => {
              if (event.target.files?.length) onFilesAdd?.(event.target.files);
              event.target.value = '';
            }}
          />

          {hasFiles && (
            <div className='mt-4 border-t border-slate-100' aria-label='已选择文件'>
              {files.map((file) => (
                <div key={file.id} className='grid min-h-[44px] grid-cols-[24px_minmax(0,1fr)_80px_80px_28px] items-center gap-2 border-b border-slate-100 text-[10px] text-slate-500'>
                  <span className='font-extrabold text-brand'>XLS</span>
                  <strong className='truncate font-medium text-slate-700' title={file.name}>{file.name}</strong>
                  <span>{file.sheetCount > 0 ? file.sheetCount + ' 个 Sheet' : '待读取'}</span>
                  <span>{file.rowCount > 0 ? file.rowCount.toLocaleString('zh-CN') + ' 行' : '—'}</span>
                  {file.status === 'scanning' ? (
                    <Loader2 className='h-3.5 w-3.5 animate-spin text-brand' />
                  ) : sourcePreview ? (
                    <Check className='h-3.5 w-3.5 text-brand' />
                  ) : (
                    <button type='button' aria-label={'移除 ' + file.name} onClick={() => onFileRemove?.(file.id)} className='text-slate-400 hover:text-red-500'>
                      <X className='h-3.5 w-3.5' />
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}

          {profiling && profilingProgress && (
            <div className='mt-4 border border-brand/20 bg-brand/5 p-4'>
              <div className='mb-2 flex items-center justify-between text-xs'>
                <span className='font-semibold text-brand'>{profilingProgress.label}</span>
                <span className='text-slate-500'>{Math.round(profilingProgress.percent)}%</span>
              </div>
              <div className='h-1.5 overflow-hidden rounded-full bg-brand/10'>
                <div className='h-full rounded-full bg-brand transition-[width] duration-500' style={{ width: profilingProgress.percent + '%' }} />
              </div>
              <div className='mt-3 flex justify-end'>
                <Button variant='ghost' size='sm' onClick={onCancelProfiling}>取消</Button>
              </div>
            </div>
          )}
        </div>

        <aside className='bg-slate-50/60 p-5'>
          <div className='flex items-center gap-2 text-brand-dark'>
            <ShieldCheck className='h-4 w-4 text-brand' />
            <strong className='text-sm'>处理边界</strong>
          </div>
          <div className='mt-4 grid gap-0 text-[11px]'>
            {[
              ['真实预览', '确认文件、工作表和表头'],
              ['AI 建模', '理解字段并提出目标表方向'],
              ['全量生成', '本地确定性计算并核对结果'],
            ].map(([title, detail], index) => (
              <div key={title} className='flex gap-3 border-b border-slate-200 py-3'>
                <span className='flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-brand/10 text-[10px] font-bold text-brand'>{index + 1}</span>
                <div>
                  <strong className='block text-slate-700'>{title}</strong>
                  <span className='mt-0.5 block text-slate-500'>{detail}</span>
                </div>
              </div>
            ))}
          </div>
          <div className='mt-5 flex justify-end'>
            {!sourcePreview ? (
              <Button variant='brand' disabled={!hasFiles || preparingPreview} onClick={onPreparePreview}>
                {preparingPreview ? <Loader2 className='h-4 w-4 animate-spin' /> : <Upload className='h-4 w-4' />}
                读取真实预览
              </Button>
            ) : (
              <Button variant='brand' disabled={profiling} onClick={onStartProfiling} className={cn(profiling && 'pointer-events-none opacity-70')}>
                {profiling ? <Loader2 className='h-4 w-4 animate-spin' /> : <ArrowRight className='h-4 w-4' />}
                开始智能制表
              </Button>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
