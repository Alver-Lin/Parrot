import { Lexer } from "marked";

type NestedToken = { type: string; href?: string; text?: string; tokens?: NestedToken[]; items?: NestedToken[] };
type AnchorKind = "image" | "metadata" | "protected";
export interface PolishImage { id: string; marker: string; filename: string; label: string }
export interface PolishAnchor { marker: string; kind: AnchorKind }
export interface PolishPlan {
  source: string;
  chunks: string[];
  anchors: PolishAnchor[];
  images: PolishImage[];
  protected: { marker: string; text: string }[];
}

function lockedToken(token: NestedToken): boolean {
  return ["image", "code", "codespan", "html", "link", "def", "table"].includes(token.type)
    || !!token.tokens?.some(lockedToken) || !!token.items?.some(lockedToken);
}

function imageTokens(token: NestedToken): NestedToken[] {
  if (token.type === "image") return [token];
  if (["code", "codespan"].includes(token.type)) return [];
  return [...(token.tokens ?? []), ...(token.items ?? [])].reduce<NestedToken[]>((all, child) => all.concat(imageTokens(child)), []);
}

function lockedSyntax(text: string) {
  return /!\[|<\s*(?:img|picture|svg|video|audio)\b|data:image|\bphoto\/|`|\\\(|\\\[|\$\$|(^|[^\\])\$[^$\n]+\$/i.test(text);
}

export function createPolishPlan(source: string): PolishPlan {
  if (source.length > 500_000) throw new Error("文稿过长，请拆分后润色。");
  const plan: PolishPlan = { source, chunks: [], anchors: [], images: [], protected: [] };
  // Keep original byte ranges for code, metadata and images despite Marked's CRLF normalization.
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
  const nonce = crypto.randomUUID().replace(/-/g, "");
  let wire = "";
  let editable = false;
  let offset = 0;
  for (const token of tokens) {
    const raw = source.slice(boundaries[offset], boundaries[offset + token.raw.length]);
    offset += token.raw.length;
    const metadata = /^\s*[-*+]\s*(?:创建时间|识别模型|音频时长)[：:]/m.test(raw);
    if (metadata || lockedToken(token as NestedToken) || lockedSyntax(raw)) {
      const prefix = raw.match(/^(?:\r\n|\r|\n)*/)?.[0] ?? "";
      const suffix = raw.slice(prefix.length).match(/(?:\r\n|\r|\n)*$/)?.[0] ?? "";
      const text = raw.slice(prefix.length, raw.length - suffix.length);
      const marker = `[[PARROT_FIXED_${nonce}_${plan.anchors.length}]]`;
      const allImages = imageTokens(token as NestedToken);
      const pictures = allImages.filter(image => /^photo\/[A-Za-z0-9._-]+\.png$/.test(image.href ?? ""));
      plan.anchors.push({ marker, kind: pictures.length ? "image" : metadata ? "metadata" : "protected" });
      plan.protected.push({ marker, text });
      for (const picture of pictures) {
        plan.images.push({ id: `image_${plan.images.length}`, marker, filename: picture.href!.slice("photo/".length), label: picture.text ?? "截图" });
      }
      wire += prefix + marker + suffix;
    } else {
      if (token.type !== "space" && token.type !== "heading" && raw.trim()) editable = true;
      wire += raw;
    }
  }
  if (!editable && !plan.images.length) throw new Error("没有可整理的正文或本地截图；代码、链接和表格会保留原样。");
  // Each part contains several paragraphs, so the model can merge, structure and relocate images.
  // Do not split a protected marker or a UTF-16 surrogate pair at the boundary.
  while (wire.length > 8000) {
    let end = 8000;
    const paragraph = wire.lastIndexOf("\n\n", end - 2);
    if (paragraph >= 4000) end = paragraph + 2;
    else {
      const pauses = [...wire.slice(0, end).matchAll(/[。！？!?；;\n\s]/g)];
      const pause = pauses[pauses.length - 1]?.index;
      if (pause != null && pause >= 4000) end = pause + 1;
    }
    const markerStart = wire.lastIndexOf("[[PARROT_FIXED_", end - 1);
    if (markerStart >= 0 && wire.indexOf("]]", markerStart) + 2 > end) end = markerStart;
    if (/[\uD800-\uDBFF]/.test(wire[end - 1]!)) end--;
    plan.chunks.push(wire.slice(0, end));
    wire = wire.slice(end);
  }
  if (wire.trim()) plan.chunks.push(wire);
  if (plan.images.length > 100) throw new Error("截图过多，请拆分记录后再整理。");
  return plan;
}


function chineseIndex(value: number, leading = true): string {
  const digits = "零一二三四五六七八九";
  if (value >= 10000) {
    const rest = value % 10000;
    return chineseIndex(Math.floor(value / 10000)) + "万"
      + (rest ? (rest < 1000 ? "零" : "") + chineseIndex(rest, false) : "");
  }
  const units = ["", "十", "百", "千"];
  const values = String(value).split("").map(Number);
  let result = "";
  let zero = false;
  values.forEach((digit, index) => {
    const position = values.length - index - 1;
    if (!digit) { if (result) zero = true; return; }
    if (zero) result += "零";
    if (!(leading && !result && position === 1 && digit === 1)) result += digits[digit];
    result += units[position];
    zero = false;
  });
  return result || digits[0]!;
}

function numberNoteHeadings(source: string): string {
  const normalized = source.replace(/\r\n?/g, "\n");
  const tokens = Lexer.lex(normalized, { gfm: true });
  let cursor = 0;
  let section = 0;
  let subsection = 0;
  const stripOrdinal = (text: string) => text.replace(
    /^(?:[一二三四五六七八九十百千万零〇两]+、\s*|\d{1,3}(?:\.(?=\D)|[、)）])\s*|[（(](?:[一二三四五六七八九十百千万零〇两]+|\d{1,3})[)）]\s*)/, "",
  );
  return tokens.map(token => {
    const start = cursor;
    // Keep original CRLF and protected code byte-for-byte while editing only headings.
    for (let index = 0; index < token.raw.length; index++)
      cursor += source[cursor] === "\r" && source[cursor + 1] === "\n" ? 2 : 1;
    const raw = source.slice(start, cursor);
    if (token.type !== "heading" || ![2, 3].includes(token.depth)) return raw;
    const ending = raw.match(/(?:\r\n|\r|\n)*$/)?.[0] ?? "";
    if (token.depth === 2) {
      section++;
      subsection = 0;
      return `## ${chineseIndex(section)}、${stripOrdinal(token.text)}${ending}`;
    }
    subsection++;
    return `### （${chineseIndex(subsection)}）${stripOrdinal(token.text)}${ending}`;
  }).join("");
}

export function restorePolishPlan(plan: PolishPlan, rewritten: string[]) {
  const parts = rewritten.filter(value => typeof value === "string" && value.trim()).map(value => value.trim());
  if (!parts.length) throw new Error("模型返回了空笔记，原文未改动。");
  let content = parts.join("\n\n");
  const metadata: string[] = [];
  const missing: string[] = [];
  for (const fixed of plan.protected) {
    if (content.includes(fixed.marker)) {
      content = content.split(fixed.marker).join(fixed.text);
      continue;
    }
    // Recover omitted local material without rejecting the model's result.
    const photos = [...fixed.text.matchAll(/!\[[^\]]*\]\(photo\/([A-Za-z0-9._-]+)\)/g)];
    if (content.includes(fixed.text) || (photos.length && photos.every(photo => content.includes("photo/" + photo[1])))) continue;
    if (plan.anchors.find(anchor => anchor.marker === fixed.marker)?.kind === "metadata") metadata.push(fixed.text);
    else missing.push(fixed.text);
  }
  // Internal transport markers are not note content; unknown/damaged markers are dropped.
  content = content.replace(/\[?\[?PARROT_FIXED_[A-Za-z0-9_]+\]?\]?/g, "");
  if (metadata.length) {
    const title = content.match(/^ {0,3}#[ \t]+[^\r\n]*(?:\r?\n|$)/)?.[0];
    const header = metadata.join("\n\n");
    content = title ? title.trimEnd() + "\n\n" + header + "\n\n" + content.slice(title.length).trimStart()
      : header + "\n\n" + content;
  }
  if (missing.length) content += "\n\n" + missing.join("\n\n");
  const numbered = numberNoteHeadings(content);
  const renumbered = numbered !== content;
  content = numbered;
  if (content.length > 1_000_000) throw new Error("润色结果过长，请拆分文稿后重试。");
  if (!renumbered && rewritten.length === plan.chunks.length && rewritten.every((text, index) => text === plan.chunks[index]))
    content = plan.source;
  else content += "\n";
  return { content };
}
