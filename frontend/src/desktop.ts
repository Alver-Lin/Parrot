import type { AudioSource } from "./api";
import type { PolishPlan } from "./markdownPolish";

declare global {
  interface Window {
    __PARROT_DESKTOP__?: Readonly<{ token: string; platform: string }>;
    parrotNative?: {
      request: <T = unknown>(
        action: string,
        payload?: Record<string, unknown>,
      ) => Promise<T>;
      send: (action: string, payload?: Record<string, unknown>) => void;
    };
  }
}

export function inDesktop() {
  return !!window.parrotNative;
}

export function desktopHeaders(): Record<string, string> {
  const token = window.__PARROT_DESKTOP__?.token;
  return token ? { "X-Parrot-Token": token } : {};
}

export async function copyText(text: string) {
  if (window.parrotNative) {
    await window.parrotNative.request("copy", { text });
  } else {
    await navigator.clipboard.writeText(text);
  }
}

export function collapsePanel() {
  window.parrotNative?.send("collapse");
}

export function updateOrb(active: boolean, level: number, connected: boolean, paused = false) {
  window.parrotNative?.send("state", { active, level, connected, paused });
}

export async function saveMarkdown(id: string, title: string, content: string, hasPhotos = false) {
  if (window.parrotNative) {
    return window.parrotNative.request<{ saved: boolean }>("export", {
      sessionId: id,
      title,
      content,
    });
  }
  let blob: Blob;
  if (hasPhotos) {
    const response = await fetch(`/api/sessions/${id}/bundle`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...desktopHeaders() },
      body: JSON.stringify({ content }),
    });
    if (!response.ok) throw new Error("导出截图失败，请重试");
    blob = await response.blob();
  } else {
    blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  }
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = title.replace(/[\\/]/g, "_") + (hasPhotos ? ".zip" : ".md");
  link.click();
  URL.revokeObjectURL(url);
  return { saved: true };
}

export async function takeScreenshot(sessionId: string, segmentId?: number) {
  if (!window.parrotNative) throw new Error("请在 Parrot 桌面客户端中截图");
  return window.parrotNative.request<{ captured: boolean }>("screenshot", {
    sessionId,
    ...(segmentId != null ? { segmentId } : {}),
  });
}

export async function ensureCapturePermission(source: AudioSource) {
  if (!window.parrotNative) return { granted: true, source };
  return window.parrotNative.request<{ granted: boolean; source: AudioSource }>("capturePermission", { source });
}

export interface CapturePermissions {
  microphone: boolean;
  system: boolean;
}

export async function readCapturePermissions(): Promise<CapturePermissions | null> {
  if (!window.parrotNative) return null;
  return window.parrotNative.request<CapturePermissions>("capturePermissionStatus");
}

export interface PolishSettings {
  base_url: string;
  model: string;
  has_api_key: boolean;
  configured: boolean;
  status: "unconfigured" | "untested" | "testing" | "connected" | "failed";
  message: string;
}

export interface PolishSettingsInput {
  base_url: string;
  model: string;
  api_key?: string;
}

export async function loadPolishSettings(): Promise<PolishSettings> {
  if (!window.parrotNative) return {
    base_url: "", model: "", has_api_key: false, configured: false, status: "unconfigured", message: "",
  };
  return window.parrotNative.request<PolishSettings>("polishSettings");
}

export async function savePolishSettings(settings: PolishSettingsInput) {
  if (!window.parrotNative) throw new Error("请在 Parrot 桌面客户端保存 AI 配置");
  return window.parrotNative.request<PolishSettings>("savePolishSettings", { ...settings });
}

export async function revealPolishKey(baseURL: string) {
  if (!window.parrotNative) throw new Error("请在 Parrot 桌面客户端查看 API Key");
  return window.parrotNative.request<{ api_key: string }>("revealPolishKey", { base_url: baseURL });
}

export async function testPolishConnection() {
  if (!window.parrotNative) throw new Error("请在 Parrot 桌面客户端测试连接");
  return window.parrotNative.request<PolishSettings>("testPolishConnection");
}

export async function polishTextChunks(jobId: string, sessionId: string, plan: PolishPlan) {
  if (!window.parrotNative) throw new Error("请在 Parrot 桌面客户端中使用 AI 润色");
  return window.parrotNative.request<{ chunks: string[]; settings: PolishSettings }>("polishTextChunks", {
    job_id: jobId, session_id: sessionId, chunks: plan.chunks, anchors: plan.anchors, images: plan.images,
  });
}

export async function cancelPolish(jobId: string) {
  return window.parrotNative?.request("cancelPolish", { job_id: jobId });
}

export async function openCaptureSettings(source: AudioSource = "system") {
  await window.parrotNative?.request("openCaptureSettings", { source });
}

export async function loadAudioSource(): Promise<AudioSource> {
  const source = window.parrotNative
    ? (await window.parrotNative.request<{ source: string }>("audioSource")).source
    : localStorage.getItem("parrot.audioSource");
  return source === "microphone" || source === "mixed" ? source : "system";
}

export async function saveAudioSource(source: AudioSource) {
  if (window.parrotNative) {
    await window.parrotNative.request("audioSource", { source });
  } else {
    localStorage.setItem("parrot.audioSource", source);
  }
}

export async function loadAutoScroll(): Promise<boolean> {
  if (window.parrotNative) {
    const result = await window.parrotNative.request<{ enabled: boolean }>("autoScroll");
    return result.enabled;
  }
  return localStorage.getItem("parrot.autoScroll") !== "false";
}

export async function saveAutoScroll(enabled: boolean) {
  if (window.parrotNative) {
    await window.parrotNative.request("autoScroll", { enabled });
  } else {
    localStorage.setItem("parrot.autoScroll", String(enabled));
  }
}
