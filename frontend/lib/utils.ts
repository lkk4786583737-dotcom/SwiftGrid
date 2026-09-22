import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/** 把 0~1 的分数转成百分比整数。 */
export function pct(score: number): number {
  return Math.round((score ?? 0) * 100);
}

export function formatScore(score: number): string {
  return `${pct(score)}%`;
}
