// 体验向导演示数据（case19 用户分层 · 前 8 行，不调 API / 不吃 Key）
import type { LearnSuccessEvent, ParseResp, Sheet } from "./types";

function sheetFrom(
  name: string,
  columns: string[],
  data: (string | number | null)[][],
  totalRows: number,
): Sheet {
  return {
    name,
    columns,
    rows: data.length,
    total_rows: totalRows,
    preview: data,
  };
}

const SRC_COLS = ["用户ID", "注册日期", "累计消费金额", "累计订单数", "近30天登录天数", "所在城市"];
const SRC_DATA: (string | number | null)[][] = [
  ["U10001", "2022-09-27", 7513, 6, 4, "武汉"],
  ["U10002", "2022-02-12", 11995, 10, 1, "深圳"],
  ["U10003", "2024-10-01", 2070, 2, 29, "北京"],
  ["U10004", "2023-04-20", 10948, 27, 19, "上海"],
  ["U10005", "2023-04-01", 68337, 59, 2, "南京"],
  ["U10006", "2022-07-13", 4994, 6, 3, "杭州"],
  ["U10007", "2022-07-20", 13705, 12, 29, "广州"],
  ["U10008", "2022-11-14", 4359, 4, 8, "广州"],
];

const TGT_COLS = [
  "用户ID", "累计消费金额", "累计订单数", "近30天登录天数",
  "客单价", "会员等级", "活跃状态", "重点维护",
];
const TGT_DATA: (string | number | null)[][] = [
  ["U10001", 7513, 6, 4, 1252.17, "白银", "沉睡", "否"],
  ["U10002", 11995, 10, 1, 1199.5, "白银", "沉睡", "否"],
  ["U10003", 2070, 2, 29, 1035.0, "普通", "活跃", "否"],
  ["U10004", 10948, 27, 19, 405.48, "白银", "活跃", "否"],
  ["U10005", 68337, 59, 2, 1158.25, "钻石", "沉睡", "否"],
  ["U10006", 4994, 6, 3, 832.33, "普通", "沉睡", "否"],
  ["U10007", 13705, 12, 29, 1142.08, "白银", "活跃", "否"],
  ["U10008", 4359, 4, 8, 1089.75, "普通", "一般", "否"],
];

export const DEMO_SOURCE_PARSE: ParseResp = {
  handle: "demo_source",
  oversize: false,
  sheets: [sheetFrom("用户行为明细", SRC_COLS, SRC_DATA, 32)],
};

export const DEMO_TARGET_PARSE: ParseResp = {
  handle: "demo_target",
  oversize: false,
  sheets: [sheetFrom("用户分层结果", TGT_COLS, TGT_DATA, 32)],
};

export const DEMO_LEARN_SUCCESS: LearnSuccessEvent = {
  code: "# demo logic",
  suggested_name: "用户分层 · RFM 价值打标",
  result_handle: "demo_result",
  result_preview: [sheetFrom("用户分层结果", TGT_COLS, TGT_DATA, 32)],
};

export const DEMO_STORY =
  "某电商运营每月要把用户行为明细，按消费额和活跃度自动打上会员等级、活跃状态、重点维护标记。";
