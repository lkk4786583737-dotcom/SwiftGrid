"use client";

import * as React from "react";
import { Building2, Info, Loader2, ShieldCheck } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { useAppState } from "./providers";
import { useToast } from "./Toast";
import { humanizeError } from "@/lib/errors";
import * as api from "@/lib/api";
import type { ModelOption } from "@/lib/types";

export function ConfigDialog() {
  const { configOpen, setConfigOpen, setConfigured, consumePendingAction } =
    useAppState();
  const toast = useToast();
  const [options, setOptions] = React.useState<ModelOption[]>([]);
  const [model, setModel] = React.useState("deepseek-v4-pro");
  const [apiKey, setApiKey] = React.useState("");
  const [baseUrl, setBaseUrl] = React.useState("");
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!configOpen) return;
    api
      .getModels()
      .then((r) => {
        setOptions(r.options);
        setModel(r.recommended);
      })
      .catch(() => setOptions([]));
  }, [configOpen]);

  const selected = options.find((o) => o.id === model);
  const isCustom = model === "custom";

  async function save() {
    if (!apiKey.trim()) {
      setError("请填写 API Key");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await api.setConfig({
        api_key: apiKey.trim(),
        model: isCustom ? undefined : model,
        base_url: isCustom ? baseUrl.trim() : undefined,
      });
      const label = selected?.label?.replace("（推荐）", "") ?? model;
      setConfigured(true, label);
      setConfigOpen(false);
      toast.success("已连接", `${label} 配置成功，可以开始了。`);
      // 改进项 12：配置完成后自动继续用户刚才想做的事
      const pending = consumePendingAction();
      if (pending) setTimeout(pending, 350);
    } catch (e) {
      setError(humanizeError(e).title + "。" + humanizeError(e).advice);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog open={configOpen} onOpenChange={setConfigOpen}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>模型与密钥配置</DialogTitle>
          <DialogDescription>
            选择模型并填入 API Key，约 1 分钟完成。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 pt-1">
          <div>
            <div className="mb-1.5 flex items-center gap-1.5">
              <label className="text-xs font-semibold text-slate-700">模型</label>
              {/* 改进项 29：企业私有部署 hover 弹出安全说明 */}
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="flex cursor-help items-center gap-0.5 text-[10px] text-slate-300 hover:text-brand">
                    <Building2 className="h-3 w-3" /> 企业部署?
                  </span>
                </TooltipTrigger>
                <TooltipContent className="max-w-[280px]">
                  <div className="flex items-start gap-1.5">
                    <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-brand-light" />
                    <span>
                      选「企业私有部署」可接入企业内网的自有模型（OpenAI
                      兼容接口）。逻辑推断走企业内网，表格处理本就在本机执行——
                      <b>真实数据全程不出私有环境</b>。
                    </span>
                  </div>
                </TooltipContent>
              </Tooltip>
            </div>
            <Select value={model} onValueChange={setModel}>
              <SelectTrigger>
                <SelectValue placeholder="选择模型" />
              </SelectTrigger>
              <SelectContent>
                {options.map((o) => (
                  <SelectItem key={o.id} value={o.id}>
                    {o.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {isCustom && (
            <div className="animate-fade-up space-y-3 rounded-xl border border-brand-light/30 bg-brand-light/5 p-3">
              <div className="flex items-start gap-1.5 text-[11px] leading-relaxed text-slate-500">
                <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-brand" />
                接入企业内网模型后，数据不出私有环境；处理过程全部在本机完成。
              </div>
              <div>
                <label className="mb-1.5 block text-xs font-semibold text-slate-700">
                  模型地址 base_url
                </label>
                <Input
                  placeholder="https://企业内网地址/v1（OpenAI 兼容）"
                  value={baseUrl}
                  onChange={(e) => setBaseUrl(e.target.value)}
                />
              </div>
            </div>
          )}

          <div>
            <div className="mb-1.5 flex items-center gap-1.5">
              <label className="text-xs font-semibold text-slate-700">
                API Key
              </label>
              {/* 安全提示：hover 输入框才弹 tooltip，不常驻铺底 */}
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="cursor-help text-slate-300 hover:text-brand">
                    <Info className="h-3.5 w-3.5" />
                  </span>
                </TooltipTrigger>
                <TooltipContent>
                  <div className="flex items-start gap-1.5">
                    <ShieldCheck className="mt-0.5 h-3.5 w-3.5 text-brand-light" />
                    <span>
                      密钥仅在本次会话的内存中使用，不写入磁盘、不进逻辑库，
                      关闭程序即清除。
                    </span>
                  </div>
                </TooltipContent>
              </Tooltip>
            </div>
            <Input
              type="password"
              placeholder="sk-..."
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              autoComplete="off"
            />
          </div>

          {error && <p className="text-xs font-medium text-red-500">{error}</p>}

          <Button onClick={save} disabled={saving} size="lg" className="w-full">
            {saving ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" /> 连接中…
              </>
            ) : (
              "保存并连接"
            )}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
