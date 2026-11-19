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

export async function saveMarkdown(id: string, title: string, content: string) {
  if (window.parrotNative) {
    return window.parrotNative.request<{ saved: boolean }>("export", {
      sessionId: id,
      title,
      content,
    });
  }
  const url = URL.createObjectURL(
    new Blob([content], { type: "text/markdown;charset=utf-8" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = title.replace(/[\\/]/g, "_") + ".md";
  link.click();
  URL.revokeObjectURL(url);
  return { saved: true };
}

export async function ensureCapturePermission() {
  if (!window.parrotNative) return true;
  const result = await window.parrotNative.request<{ granted: boolean }>("capturePermission");
  return result.granted;
}

export async function openCaptureSettings() {
  await window.parrotNative?.request("openCaptureSettings");
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
