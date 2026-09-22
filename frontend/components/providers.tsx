"use client";

import * as React from "react";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ToastProvider } from "./Toast";
import * as api from "@/lib/api";

type AppState = {
  configured: boolean;
  configurationChecked: boolean;
  modelLabel: string;
  setConfigured: (v: boolean, label?: string) => void;
  configOpen: boolean;
  setConfigOpen: (v: boolean) => void;
  /** 配置完成后要自动继续的动作（改进项 12） */
  requestConfig: (onConfigured?: () => void) => void;
  consumePendingAction: () => (() => void) | null;
  /** 流程忙碌中（运行/澄清），供导航守卫使用（改进项 18） */
  busy: boolean;
  setBusy: (v: boolean) => void;
  /** 触发体验向导（改进项 11） */
  tourRequested: number;
  requestTour: () => void;
  /** 向导进行中：学习流走导演模式，不调 API */
  tourActive: boolean;
  setTourActive: (v: boolean) => void;
};

const Ctx = React.createContext<AppState | null>(null);

export function useAppState(): AppState {
  const v = React.useContext(Ctx);
  if (!v) throw new Error("useAppState 必须在 <AppProviders> 内使用");
  return v;
}

const STORAGE_KEY = "swiftgrid_configured";

export function AppProviders({ children }: { children: React.ReactNode }) {
  const [configured, setConfiguredState] = React.useState(false);
  const [configurationChecked, setConfigurationChecked] = React.useState(false);
  const [modelLabel, setModelLabel] = React.useState("DeepSeek V4-pro");
  const [configOpen, setConfigOpen] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [tourRequested, setTourRequested] = React.useState(0);
  const [tourActive, setTourActive] = React.useState(false);
  const pendingRef = React.useRef<(() => void) | null>(null);

  React.useEffect(() => {
    let savedLabel = "";
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) {
        const o = JSON.parse(saved);
        savedLabel = o.modelLabel ?? "";
        if (savedLabel) setModelLabel(savedLabel);
      }
    } catch {
      /* ignore */
    }
    api
      .getConfigStatus()
      .then((status) => {
        setConfiguredState(status.configured);
        if (status.configured && !savedLabel && status.model) {
          setModelLabel(status.model);
        }
      })
      .catch(() => setConfiguredState(false))
      .finally(() => setConfigurationChecked(true));
  }, []);

  const setConfigured = React.useCallback(
    (v: boolean, label?: string) => {
      setConfiguredState(v);
      setConfigurationChecked(true);
      if (label) setModelLabel(label);
      try {
        localStorage.setItem(
          STORAGE_KEY,
          JSON.stringify({ configured: v, modelLabel: label ?? modelLabel }),
        );
      } catch {
        /* ignore */
      }
    },
    [modelLabel],
  );

  const requestConfig = React.useCallback((onConfigured?: () => void) => {
    pendingRef.current = onConfigured ?? null;
    setConfigOpen(true);
  }, []);

  const consumePendingAction = React.useCallback(() => {
    const fn = pendingRef.current;
    pendingRef.current = null;
    return fn;
  }, []);

  const requestTour = React.useCallback(() => setTourRequested((n) => n + 1), []);

  return (
    <Ctx.Provider
      value={{
        configured,
        configurationChecked,
        modelLabel,
        setConfigured,
        configOpen,
        setConfigOpen,
        requestConfig,
        consumePendingAction,
        busy,
        setBusy,
        tourRequested,
        requestTour,
        tourActive,
        setTourActive,
      }}
    >
      <ToastProvider>
        <TooltipProvider delayDuration={150}>{children}</TooltipProvider>
      </ToastProvider>
    </Ctx.Provider>
  );
}
