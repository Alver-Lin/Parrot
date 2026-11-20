import type { AudioSource } from "./api";

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
