export interface Settings {
  title: string;
  language: "auto" | "zh" | "en";
  model: "large-v3" | "large-v3-turbo" | "small";
  engine: "auto" | "mlx" | "faster-whisper";
  vocabulary: string;
}

export interface Segment {
  id: number;
  start: number;
  end: number;
  text: string;
  original_text: string;
  review: number;
}

export interface Session extends Settings {
  id: string;
  source: string;
  created_at: string;
  status: string;
  duration: number;
  processed: number;
  error: string | null;
  segments?: Segment[];
  segment_count?: number;
}

export interface Status {
  model: {
    status: string;
    message: string;
    engine: string | null;
    model: string | null;
  };
  active_id: string | null;
  level: number;
  platform: string;
  apple_silicon: boolean;
  capture_available: boolean;
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...options,
    headers:
      options.body instanceof FormData
        ? options.headers
        : { "Content-Type": "application/json", ...options.headers },
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : `请求失败（${response.status}）`,
    );
  }
  return response.json() as Promise<T>;
}

export function time(seconds = 0) {
  const value = Math.max(0, Math.floor(seconds));
  const parts = [
    Math.floor(value / 3600),
    Math.floor(value / 60) % 60,
    value % 60,
  ];
  return parts.map((part) => String(part).padStart(2, "0")).join(":");
}

export function toMarkdown(session: Session) {
  const lines = [
    `# ${session.title.replace(/[\r\n]/g, " ")}`,
    "",
    `- 创建时间：${session.created_at}`,
    `- 识别模型：Whisper ${session.model}`,
    `- 音频时长：${time(session.duration)}`,
    "",
    "## 转写原文",
    "",
  ];
  for (const segment of session.segments ?? []) {
    lines.push(
      `### ${time(segment.start)}${segment.review ? "（待校对）" : ""}`,
      "",
      segment.text,
      "",
    );
  }
  return lines.join("\n");
}
