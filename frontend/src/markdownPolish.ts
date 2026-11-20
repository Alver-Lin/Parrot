import { Lexer } from "marked";

type Part = { kind: "fixed"; text: string } | { kind: "text"; index: number };
export interface PolishPlan { source: string; parts: Part[]; chunks: string[] }
type NestedToken = { type: string; tokens?: NestedToken[]; items?: NestedToken[] };

function lockedToken(token: NestedToken): boolean {
  return ["image", "code", "codespan", "html", "link", "def", "table"].includes(token.type)
    || !!token.tokens?.some(lockedToken) || !!token.items?.some(lockedToken);
}

function lockedSyntax(text: string) {
  return /!\[|<\s*(?:img|picture|svg|video|audio)\b|data:image|\bphoto\/|`|\\\(|\\\[|\$\$|(^|[^\\])\$[^$\n]+\$/i.test(text);
}

export function createPolishPlan(source: string): PolishPlan {
  if (source.length > 500_000) throw new Error("文稿过长，请拆分后润色。");
  const plan: PolishPlan = { source, parts: [], chunks: [] };
  // Marked normalizes line endings. Keep a boundary map so protected content,
  // including code and images, is restored byte-for-byte from the original.
  let normalized = "";
  const boundaries = [0];
  for (let index = 0; index < source.length;) {
    if (source[index] === "\r") {
      index += source[index + 1] === "\n" ? 2 : 1;
      normalized += "\n";
    } else { normalized += source[index++]; }
    boundaries.push(index);
  }
  const tokens = Lexer.lex(normalized, { gfm: true });
  if (tokens.map(token => token.raw).join("") !== normalized)
    throw new Error("文稿格式暂不支持安全润色，原文未改动。");
  const fixed = (text: string) => { if (text) plan.parts.push({ kind: "fixed", text }); };
  const editable = (raw: string) => {
    const prefix = raw.match(/^\s*/)?.[0] ?? "";
    const suffix = raw.slice(prefix.length).match(/\s*$/)?.[0] ?? "";
    const text = raw.slice(prefix.length, raw.length - suffix.length);
    fixed(prefix);
    if (text) {
      plan.parts.push({ kind: "text", index: plan.chunks.length });
      plan.chunks.push(text);
    }
    fixed(suffix);
  };
  let offset = 0;
  for (const token of tokens) {
    const raw = source.slice(boundaries[offset], boundaries[offset + token.raw.length]);
    offset += token.raw.length;
    const metadata = /^\s*[-*+]\s*(?:创建时间|识别模型|音频时长)[：:]/m.test(raw);
    if (!["paragraph", "list"].includes(token.type) || metadata || lockedToken(token as NestedToken) || lockedSyntax(raw)) {
      fixed(raw);
      continue;
    }
    let remaining = raw;
    while (remaining.length > 2400) {
      let end = 2400;
      if (/[\uD800-\uDBFF]/.test(remaining[end - 1])) end--;
      const matches = [...remaining.slice(0, end).matchAll(/[。！？!?；;\n\s]/g)];
      const pause = matches[matches.length - 1]?.index;
      if (pause != null && pause >= 1200) end = pause + 1;
      editable(remaining.slice(0, end));
      remaining = remaining.slice(end);
    }
    editable(remaining);
  }
  if (!plan.chunks.length) throw new Error("没有可润色的文字；图片、链接、代码和表格会保留原样。");
  if (plan.chunks.length > 1000) throw new Error("文稿段落过多，请拆分后润色。");
  return plan;
}

function listMarkers(text: string) {
  return [...text.matchAll(/^(\s*(?:[-*+]\s+|\d+[.)]\s+))/gm)].map(match => match[1]);
}

export function restorePolishPlan(plan: PolishPlan, rewritten: string[]) {
  if (rewritten.length !== plan.chunks.length) throw new Error("润色结果缺少段落，原文未改动。");
  const warnings: string[] = [];
  const checked = rewritten.map((value, index) => {
    if (typeof value !== "string" || !value.trim()) throw new Error("模型返回了空段落，原文未改动。");
    const text = value.trim();
    const original = plan.chunks[index]!;
    const tokens = Lexer.lex(text, { gfm: true });
    if (lockedSyntax(text) || tokens.some(token => !["paragraph", "list", "space"].includes(token.type)
      || lockedToken(token as NestedToken)))
      throw new Error("模型新增了图片、链接或其他受保护格式，原文未改动。");
    if (JSON.stringify(listMarkers(text)) !== JSON.stringify(listMarkers(original)))
      throw new Error("模型改变了列表结构，原文未改动。");
    const numbers = (source: string) => [...source.matchAll(/\d+(?:[.,:/-]\d+)*(?:[%％])?/g)].map(match => match[0]).sort();
    if (JSON.stringify(numbers(text)) !== JSON.stringify(numbers(original)))
      warnings.push(`第 ${index + 1} 段的数字出现变化，请对照原文核对。`);
    return text;
  });
  const content = plan.parts.map(part => part.kind === "fixed" ? part.text : checked[part.index]).join("");
  if (content.length > 1_000_000) throw new Error("润色结果过长，原文未改动。");
  return { content, warnings };
}
