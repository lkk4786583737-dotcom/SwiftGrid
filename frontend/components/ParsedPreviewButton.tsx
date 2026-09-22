"use client";

import { Eye } from "lucide-react";
import { ParsedTablePreview, PREVIEW_HEIGHT_DIALOG } from "./ParsedTablePreview";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import type { ParseResp } from "@/lib/types";

export function ParsedPreviewButton({
  resp,
  compact,
}: {
  resp: ParseResp;
  compact?: boolean;
}) {
  return (
    <Dialog>
      <DialogTrigger asChild>
        <button
          type="button"
          title="预览表格"
          className="ml-auto flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-slate-400 opacity-0 smooth hover:bg-slate-100 hover:text-brand group-hover:opacity-100 focus:opacity-100"
        >
          <Eye className="h-4 w-4" />
        </button>
      </DialogTrigger>
      <DialogContent className="left-1/2 top-1/2 flex h-[min(82vh,720px)] w-[min(92vw,1100px)] max-w-none -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden">
        <DialogHeader>
          <DialogTitle>表格预览</DialogTitle>
        </DialogHeader>
        <div className="min-h-0 flex-1">
          <ParsedTablePreview
            resp={resp}
            previewHeight={compact ? 320 : PREVIEW_HEIGHT_DIALOG}
          />
        </div>
      </DialogContent>
    </Dialog>
  );
}
