// 错误人话化层（改进项 6）：把技术错误翻译成小周能懂的话 + 下一步建议。
// 原则：不出现 KeyError/DataFrame/HTTP 状态码等术语；技术细节收进「详情」。

export type FriendlyError = {
  /** 大白话标题 */
  title: string;
  /** 下一步建议（可为空） */
  advice: string;
  /** 原始技术细节（折叠展示用） */
  detail: string;
  /** 是否适合提供「重试」按钮 */
  retryable: boolean;
};

const RULES: {
  test: RegExp;
  title: string;
  advice: string;
  retryable: boolean;
}[] = [
  {
    test: /字段结构不一致|重复字段名|空字段名|没有可识别的表头|没有读到有效数据表|SCHEMA_CONFLICT/i,
    title: "第 3 级：需要整理源表",
    advice: "请统一各工作表的表头，移除重复或空字段名后重新上传。系统不会强行合并结构不同的表。",
    retryable: false,
  },
  {
    test: /暂不支持文件类型|加密|密码|无法读取工作簿|FILE_UNSUPPORTED|FILE_READ_FAILED/i,
    title: "第 4 级：当前无法处理",
    advice: "请解除文件密码并另存为受支持的表格格式；纯图片或损坏文件需要先转换成真实单元格。",
    retryable: false,
  },
  {
    test: /超过.*安全.*上限|FILE_TOO_LARGE|DATASET_TOO_LARGE|SCHEMA_TOO_WIDE/i,
    title: "第 3 级：数据规模需要调整",
    advice: "请按时间或业务范围拆分源表后再处理。系统已停止任务，不会截断后继续计算。",
    retryable: false,
  },
  {
    test: /超过 Excel.*行数上限|OUTPUT_LIMIT_EXCEEDED/i,
    title: "结果表超过 Excel 上限",
    advice: "返回方案页增加筛选条件，或按月份、季度等维度拆分结果表。",
    retryable: false,
  },
  {
    test: /必须确认|业务口径|暂不能全量生成|PLAN_INVALID/i,
    title: "第 2 级：确认后生成",
    advice: "这不是系统故障。请回到方案确认页回答会改变结果的业务问题。",
    retryable: false,
  },
  {
    test: /API Key|密钥|401|Unauthorized|尚未配置/i,
    title: "还没连接 AI 模型",
    advice: "点左下角「模型与密钥配置」，粘贴你的 API Key（约 1 分钟）。",
    retryable: false,
  },
  {
    test: /timeout|timed?\s*out|超时/i,
    title: "AI 响应超时了",
    advice: "网络或模型服务偶尔会慢，稍等几秒再试一次通常就好。",
    retryable: true,
  },
  {
    test: /Failed to fetch|NetworkError|ECONNREFUSED|连接中断|EventSource/i,
    title: "和本地服务的连接断了",
    advice: "请确认程序还在运行，然后点重试。你的数据都在本机，不会丢。",
    retryable: true,
  },
  {
    test: /429|rate.?limit|限流/i,
    title: "AI 服务暂时繁忙",
    advice: "稍等半分钟再试即可，不影响已保存的逻辑。",
    retryable: true,
  },
  {
    test: /句柄无效|不存在或已过期|404/i,
    title: "这份表格的缓存过期了",
    advice: "重新上传/粘贴一次表格就能继续。",
    retryable: false,
  },
  {
    test: /解析失败|未提供文件|无法解析|empty|没解析出/i,
    title: "没能读懂这份表格",
    advice: "请确认粘贴的是完整表格（带表头），或改用上传 Excel 文件。",
    retryable: false,
  },
  {
    test: /逻辑库已满/i,
    title: "逻辑库满了（最多 5 个）",
    advice: "到「我的逻辑库」删掉一个不常用的，再保存这个。",
    retryable: false,
  },
];

export function humanizeError(raw: unknown): FriendlyError {
  const detail =
    raw instanceof Error ? raw.message : typeof raw === "string" ? raw : String(raw ?? "未知错误");

  for (const r of RULES) {
    if (r.test.test(detail)) {
      return { title: r.title, advice: r.advice, detail, retryable: r.retryable };
    }
  }
  return {
    title: "这一步没成功",
    advice: "可以直接重试；若反复失败，点「重新开始」换一份数据试试。",
    detail,
    retryable: true,
  };
}
