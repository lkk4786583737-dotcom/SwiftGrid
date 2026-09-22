"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowRight,
  CircleCheck,
  Loader2,
  RefreshCw,
  Repeat2,
  StopCircle,
  WandSparkles,
} from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { SloganHeader } from "@/components/SloganHeader";
import { UniversalInput } from "@/components/UniversalInput";
import { MatchPanel } from "@/components/MatchPanel";
import { ProgressTimeline, type Step } from "@/components/ProgressTimeline";
import { ResultTabs } from "@/components/ResultTabs";
import { Button } from "@/components/ui/button";
import { useAppState } from "@/components/providers";
import { useToast } from "@/components/Toast";
import { humanizeError } from "@/lib/errors";
import { scrollToElement } from "@/lib/scroll";
import * as api from "@/lib/api";
import type {
  LogicMeta,
  MatchResp,
  ParseResp,
  ProgressEvent,
  RunSuccessEvent,
  SSEType,
} from "@/lib/types";

type Phase =
  | "input"
  | "matching"
  | "autowait"
  | "choose"
  | "running"
  | "done";

function RunFlow() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const presetLogicId = searchParams.get("logic");

  const { configured, requestConfig, setBusy } = useAppState();
  const toast = useToast();

  const [logics, setLogics] = React.useState<LogicMeta[] | null>(null);
  const [presetName, setPresetName] = React.useState<string | null>(null);
  const [table, setTable] = React.useState<ParseResp | null>(null);
  const [match, setMatch] = React.useState<MatchResp | null>(null);
  const [phase, setPhase] = React.useState<Phase>("input");
  const [progress, setProgress] = React.useState<ProgressEvent[]>([]);
  const [result, setResult] = React.useState<RunSuccessEvent | null>(null);
  const [error, setError] = React.useState<{ title: string; advice: string } | null>(
    null,
  );
  const [autoCountdown, setAutoCountdown] = React.useState(3);
  const [inputEpoch, setInputEpoch] = React.useState(0);

  const streamRef = React.useRef<{ close: () => void } | null>(null);
  const sidRef = React.useRef<string | null>(null);
  const autoTimerRef = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const countdownRef = React.useRef<ReturnType<typeof setInterval> | null>(null);
  const flowRef = React.useRef<HTMLDivElement>(null);
  const shouldRevealFlowRef = React.useRef(false);

  React.useEffect(() => {
    api
      .listLogics()
      .then((xs) => {
        setLogics(xs);
        if (presetLogicId) {
          const hit = xs.find((l) => l.id === presetLogicId);
          setPresetName(hit?.name ?? null);
        }
      })
      .catch(() => setLogics([]));
  }, [presetLogicId]);

  React.useEffect(() => {
    setBusy(phase === "matching" || phase === "running" || phase === "autowait");
    return () => setBusy(false);
  }, [phase, setBusy]);

  React.useEffect(
    () => () => {
      streamRef.current?.close();
      clearAutoWait();
    },
    [],
  );

  React.useEffect(() => {
    if (!shouldRevealFlowRef.current || phase === "input" || phase === "done") return;
    const timer = window.setTimeout(() => {
      scrollToElement(flowRef.current, phase === "matching" ? "center" : "end");
      if (phase === "autowait" || phase === "choose" || phase === "running") {
        shouldRevealFlowRef.current = false;
      }
    }, 120);
    return () => window.clearTimeout(timer);
  }, [phase, match]);

  function clearAutoWait() {
    if (autoTimerRef.current) clearTimeout(autoTimerRef.current);
    if (countdownRef.current) clearInterval(countdownRef.current);
    autoTimerRef.current = null;
    countdownRef.current = null;
  }

  function resetAll(notify = true) {
    streamRef.current?.close();
    clearAutoWait();
    if (sidRef.current) api.runCancel(sidRef.current).catch(() => {});
    sidRef.current = null;
    setTable(null);
    setMatch(null);
    setResult(null);
    setError(null);
    setProgress([]);
    setPhase("input");
    setInputEpoch((n) => n + 1);
    if (notify) toast.info("已重新开始", "上传新表即可再次处理。");
  }

  function startAutoWait(m: MatchResp, handle: string) {
    clearAutoWait();
    setMatch(m);
    setAutoCountdown(3);
    setPhase("autowait");
    countdownRef.current = setInterval(() => {
      setAutoCountdown((c) => (c <= 1 ? 0 : c - 1));
    }, 1000);
    autoTimerRef.current = setTimeout(() => {
      clearAutoWait();
      if (m.best) void execute(m.best.id, handle);
    }, 3000);
  }

  function cancelAutoWait() {
    clearAutoWait();
    setPhase("choose");
    toast.info("已取消自动处理", "你可以换选其他逻辑，或重新上传。");
  }

  async function onParsed(r: ParseResp | null) {
    clearAutoWait();
    setTable(r);
    setMatch(null);
    setResult(null);
    setError(null);
    if (!r) {
      setPhase("input");
      return;
    }
    shouldRevealFlowRef.current = true;

    // 改进项 14：从逻辑库直通，跳过匹配
    if (presetLogicId) {
      if (!configured) {
        requestConfig(() => onParsed(r));
        return;
      }
      await execute(presetLogicId, r.handle);
      return;
    }

    if (logics !== null && logics.length === 0) {
      setPhase("input");
      return;
    }

    setPhase("matching");
    try {
      const m = await api.runMatch(r.handle);
      setMatch(m);
      if (m.auto && m.best) {
        // 改进项 9：唯一强匹配 3 秒缓冲
        startAutoWait(m, r.handle);
      } else {
        setPhase("choose");
      }
    } catch (e) {
      const fe = humanizeError(e);
      setError({ title: fe.title, advice: fe.advice });
      setPhase("input");
    }
  }

  async function execute(logicId: string, handle?: string) {
    clearAutoWait();
    const src = handle ?? table?.handle;
    if (!src) return;
    if (!configured) {
      requestConfig(() => execute(logicId, handle));
      return;
    }
    setProgress([]);
    setError(null);
    try {
      const { session_id } = await api.runExecute({
        logic_id: logicId,
        source_handle: src,
      });
      sidRef.current = session_id;
      setPhase("running");
      streamRef.current = api.streamSSE(
        `/api/run/stream/${session_id}`,
        (type: SSEType, data: unknown) => {
          if (type === "progress") {
            setProgress((p) => [...p, data as ProgressEvent]);
          } else if (type === "success") {
            setResult(data as RunSuccessEvent);
            setPhase("done");
            toast.success("处理完成", "结果已生成，可预览或下载。");
          } else if (type === "error") {
            const fe = humanizeError((data as { message: string }).message);
            setError({ title: fe.title, advice: fe.advice });
            setPhase("choose");
          } else if (type === "cancelled") {
            setPhase(table ? "choose" : "input");
            toast.info("已中断", "表格还在，可以重新选择逻辑。");
          }
        },
        "run",
      );
    } catch (e) {
      if (api.isConfigRequired(e)) {
        requestConfig(() => execute(logicId, handle));
        return;
      }
      const fe = humanizeError(e);
      setError({ title: fe.title, advice: fe.advice });
      setPhase("choose");
    }
  }

  // 改进项 2：运行中中断
  async function cancelRun() {
    streamRef.current?.close();
    if (sidRef.current) await api.runCancel(sidRef.current).catch(() => {});
    sidRef.current = null;
    setProgress([]);
    setPhase(match ? "choose" : "input");
    toast.info("已中断处理", "你的表格还在，可以换逻辑或重新上传。");
  }

  const steps: Step[] = progress.map((p, i) => ({
    label: p.label,
    status: phase === "done" || i < progress.length - 1 ? "done" : "active",
  }));
  if (steps.length === 0 && phase === "running")
    steps.push({ label: "正在连接…", status: "active" });

  const libraryEmpty = logics !== null && logics.length === 0 && !presetLogicId;
  const showRestart = phase !== "input" || !!table;

  return (
    <>
      <div className="flex items-start justify-between">
        <SloganHeader
          title="处理新表"
          subtitle="丢一张新表进来，SwiftGrid 自动匹配你教过的逻辑，直接出结果。"
        />
        {showRestart && (
          <div className="shrink-0 px-8 pt-10">
            <button
              onClick={() => {
                if (phase === "input" || confirm("重新开始会清空当前任务，确定吗？"))
                  resetAll();
              }}
              className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-medium text-slate-500 smooth hover:border-brand/40 hover:text-brand"
            >
              <RefreshCw className="h-3.5 w-3.5" /> 重新开始
            </button>
          </div>
        )}
      </div>

      <div className="mx-auto flex w-full max-w-5xl flex-1 flex-col gap-5 px-8 pb-10">
        {/* 改进项 13：空逻辑库引导 */}
        {libraryEmpty ? (
          <EmptyLibraryGuide onTeach={() => router.push("/")} />
        ) : (
          <>
            {presetLogicId && presetName && (
              <div className="flex items-center gap-2 rounded-xl border border-brand-light/30 bg-brand-light/5 px-4 py-3 text-xs font-medium text-brand-dark">
                <CircleCheck className="h-4 w-4 text-brand" />
                已选定逻辑「{presetName}」，上传新表后将直接处理
              </div>
            )}

            <div
              className="rounded-2xl bg-white p-6 shadow-apple"
              data-tour="run-input"
            >
              <UniversalInput
                key={inputEpoch}
                slot="source"
                label="新数据表"
                onParsed={onParsed}
              />
            </div>

            {error && (
              <div className="rounded-lg bg-red-50 p-3 text-xs">
                <p className="font-semibold text-red-600">{error.title}</p>
                <p className="mt-0.5 text-red-500/90">{error.advice}</p>
              </div>
            )}

            <div ref={flowRef} className="scroll-mt-6">
              <AnimatePresence mode="wait">
                {phase === "matching" && (
                  <motion.div
                    key="matching"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0 }}
                    className="flex items-center gap-2 rounded-2xl bg-white p-6 text-sm text-slate-500 shadow-apple"
                  >
                    <Loader2 className="h-4 w-4 animate-spin text-brand" />
                    正在匹配最合适的已学逻辑…
                  </motion.div>
                )}

                {(phase === "autowait" || phase === "choose") && match && (
                  <motion.div
                    key="choose"
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    data-tour="run-match"
                  >
                    {phase === "autowait" && match.auto && match.best && (
                      <div className="mb-3 flex items-center justify-between rounded-xl border border-brand-light/30 bg-brand-light/5 px-4 py-2.5 text-xs">
                        <span className="font-medium text-brand-dark">
                          {autoCountdown > 0
                            ? `${autoCountdown} 秒后自动用「${match.best.name}」处理…`
                            : "正在启动…"}
                        </span>
                        <div className="flex gap-2">
                          <button
                            onClick={() => {
                              clearAutoWait();
                              void execute(match.best!.id);
                            }}
                            className="font-semibold text-brand hover:underline"
                          >
                            立即开始
                          </button>
                          <button
                            onClick={cancelAutoWait}
                            className="text-slate-400 hover:text-slate-600"
                          >
                            取消
                          </button>
                        </div>
                      </div>
                    )}
                    <MatchPanel
                      match={match}
                      running={false}
                      onRun={(id) => execute(id)}
                    />
                  </motion.div>
                )}

                {phase === "running" && (
                  <motion.div
                    key="running"
                    initial={{ opacity: 0, y: 14 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="rounded-2xl bg-white p-6 shadow-apple"
                    data-tour="run-progress"
                  >
                    <ProgressTimeline
                      title="正在套用逻辑处理新表"
                      hint="可随时点下方按钮中断"
                      steps={steps}
                    />
                    <button
                      onClick={() => void cancelRun()}
                      className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl border border-red-200 bg-red-50 py-2.5 text-xs font-semibold text-red-600 smooth hover:bg-red-100"
                    >
                      <StopCircle className="h-4 w-4" /> 中断处理
                    </button>
                  </motion.div>
                )}

                {phase === "done" && result && (
                  <motion.div
                    key="done"
                    initial={{ opacity: 0, y: 14 }}
                    animate={{ opacity: 1, y: 0 }}
                    className="flex min-h-[420px] flex-col rounded-2xl bg-white p-6 shadow-apple"
                    data-tour="run-result"
                  >
                    <ResultTabs
                      resultPreview={result.result_preview}
                      beforePreview={result.before_preview}
                      resultHandle={result.result_handle}
                      healed={result.healed}
                      banner={
                        result.healed
                          ? "已自动自愈逻辑并处理完成"
                          : "已用已学逻辑处理完成"
                      }
                    />
                    {/* 改进项 15：出结果后下一步 */}
                    <div className="mt-4 flex gap-2 border-t border-slate-100 pt-4">
                      <Button
                        size="sm"
                        className="flex-1"
                        onClick={() => resetAll(false)}
                      >
                        <Repeat2 className="h-3.5 w-3.5" /> 处理下一张表
                      </Button>
                      <Button
                        size="sm"
                        variant="soft"
                        className="flex-1"
                        onClick={() => router.push("/logics")}
                      >
                        查看逻辑库 <ArrowRight className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </>
        )}
      </div>
    </>
  );
}

function EmptyLibraryGuide({ onTeach }: { onTeach: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-2xl border border-dashed border-slate-200 bg-white py-20 text-center shadow-apple">
      <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-brand-light/10 text-brand">
        <WandSparkles className="h-6 w-6" />
      </div>
      <h3 className="text-base font-semibold text-brand-dark">还没有可复用的逻辑</h3>
      <p className="mb-5 mt-1 max-w-sm text-sm text-slate-400">
        「处理新表」需要你先教 AI 一次。上传源表 + 目标样例，保存后即可反复使用。
      </p>
      <Button onClick={onTeach}>
        <WandSparkles className="h-4 w-4" /> 去教第一条逻辑
      </Button>
    </div>
  );
}

export default function Page() {
  return (
    <AppShell>
      <React.Suspense
        fallback={
          <div className="flex flex-1 items-center justify-center text-sm text-slate-400">
            加载中…
          </div>
        }
      >
        <RunFlow />
      </React.Suspense>
    </AppShell>
  );
}
