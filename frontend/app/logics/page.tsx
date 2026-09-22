"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { motion } from "framer-motion";
import {
  BookMarked,
  Clock,
  Pencil,
  Play,
  Trash2,
  WandSparkles,
} from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { SloganHeader } from "@/components/SloganHeader";
import { NewTaskMenu } from "@/components/NewTaskMenu";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import * as api from "@/lib/api";
import type { LogicMeta } from "@/lib/types";

function Library() {
  const router = useRouter();
  const [logics, setLogics] = React.useState<LogicMeta[] | null>(null);
  const [renaming, setRenaming] = React.useState<LogicMeta | null>(null);

  const reload = React.useCallback(() => {
    api
      .listLogics()
      .then(setLogics)
      .catch(() => setLogics([]));
  }, []);

  React.useEffect(() => reload(), [reload]);

  async function remove(id: string) {
    if (!confirm("确定删除这条逻辑？删除后不可恢复。")) return;
    await api.deleteLogic(id).catch(() => {});
    reload();
  }

  return (
    <>
      <div className="flex items-start justify-between">
        <SloganHeader
          title="我的逻辑库"
          subtitle="教过一次的逻辑都在这里，随时复用到新表。"
        />
        <div className="px-8 pt-9">
          <NewTaskMenu />
        </div>
      </div>

      <div className="mx-auto w-full max-w-5xl flex-1 px-8 pb-10">
        {logics === null ? (
          <div className="py-20 text-center text-sm text-slate-400">加载中…</div>
        ) : logics.length === 0 ? (
          <EmptyState onTeach={() => router.push("/")} />
        ) : (
          <div className="grid gap-4 sm:grid-cols-2">
            {logics.map((l, i) => (
              <motion.div
                key={l.id}
                initial={{ opacity: 0, y: 14 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: i * 0.06 }}
                className="group rounded-2xl border border-slate-100 bg-white p-5 shadow-apple smooth hover:shadow-apple-hover"
              >
                <div className="flex items-start gap-3">
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-brand-light/10 text-brand">
                    <BookMarked className="h-5 w-5" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <h3 className="truncate text-sm font-semibold text-brand-dark">
                      {l.name}
                    </h3>
                    <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-400">
                      <span className="flex items-center gap-1">
                        <Clock className="h-3 w-3" /> 上次使用 {l.last_used_at || "—"}
                      </span>
                      <span>创建 {l.created_at || "—"}</span>
                    </div>
                  </div>
                </div>

                <div className="mt-4 flex items-center gap-2">
                  <Button
                    size="sm"
                    onClick={() => router.push(`/run?logic=${l.id}`)}
                    className="flex-1"
                  >
                    <Play className="h-3.5 w-3.5" /> 用它处理新表
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => setRenaming(l)}
                    aria-label="重命名"
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </Button>
                  <Button
                    size="sm"
                    variant="danger"
                    onClick={() => remove(l.id)}
                    aria-label="删除"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </motion.div>
            ))}
          </div>
        )}
      </div>

      <RenameDialog
        logic={renaming}
        onClose={() => setRenaming(null)}
        onSaved={() => {
          setRenaming(null);
          reload();
        }}
      />
    </>
  );
}

function EmptyState({ onTeach }: { onTeach: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-white py-20 text-center">
      <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-brand-light/10 text-brand">
        <WandSparkles className="h-6 w-6" />
      </div>
      <h3 className="text-base font-semibold text-brand-dark">还没有逻辑</h3>
      <p className="mb-5 mt-1 max-w-xs text-sm text-slate-400">
        教 AI 一次「源表 → 目标样例」，它就会沉淀成可复用的逻辑。
      </p>
      <Button onClick={onTeach}>
        <WandSparkles className="h-4 w-4" /> 去教第一条逻辑
      </Button>
    </div>
  );
}

function RenameDialog({
  logic,
  onClose,
  onSaved,
}: {
  logic: LogicMeta | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  React.useEffect(() => setName(logic?.name ?? ""), [logic]);

  return (
    <Dialog open={!!logic} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>重命名逻辑</DialogTitle>
        </DialogHeader>
        <div className="space-y-3 pt-1">
          <Input value={name} onChange={(e) => setName(e.target.value)} />
          <Button
            size="lg"
            className="w-full"
            disabled={saving || !name.trim() || !logic}
            onClick={async () => {
              if (!logic) return;
              setSaving(true);
              await api.renameLogic(logic.id, name.trim()).catch(() => {});
              setSaving(false);
              onSaved();
            }}
          >
            {saving ? "保存中…" : "保存"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export default function Page() {
  return (
    <AppShell>
      <Library />
    </AppShell>
  );
}
