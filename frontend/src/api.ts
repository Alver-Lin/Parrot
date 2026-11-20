import { desktopHeaders } from "./desktop";

export type AudioSource = "microphone" | "system" | "mixed";

export interface Settings {
  title: string;
  audio_source: AudioSource;
  language: "auto" | "zh" | "en";
  model: "large-v3" | "large-v3-turbo" | "small";
  engine: "auto" | "mlx" | "faster-whisper";
}

export interface Segment {
  kind?: "text" | "image";
  photo_filename?: string | null;
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
  markdown_content?: string | null;
  polish_revision?: number | null;
}

export interface Status {
  model_cache: {
    auto_engine: "mlx" | "faster-whisper";
    downloaded: Record<string, Record<string, boolean>>;
  };
  model: {
    status: string;
    message: string;
    engine: string | null;
    model: string | null;
  };
  active_id: string | null;
  active_status: string | null;
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
    headers: {
      "Content-Type": "application/json",
      ...desktopHeaders(),
      ...options.headers,
    },
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

export async function fetchMarkdown(id: string) {
  const response = await fetch(`/api/sessions/${id}/markdown`, { headers: desktopHeaders() });
  if (!response.ok) throw new Error("无法读取当前文稿，请稍后重试");
  return response.text();
}

export function toMarkdown(session: Session) {
  if (session.markdown_content != null) return session.markdown_content;
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
    const text = segment.text
      .replace(/\r\n?/g, "\n")
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .join("\n");
    if (text) {
      if (segment.kind === "image") lines.push("", text, "");
      else lines.push(`${segment.review ? "（待校对）" : ""}${text}`);
    }
  }
  return lines.join("\n");
}
