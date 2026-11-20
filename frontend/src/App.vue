<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import { Modal, message, theme } from "ant-design-vue";
import zhCN from "ant-design-vue/es/locale/zh_CN";
import TranscriptPhoto from "./TranscriptPhoto.vue";
import MarkdownPreview from "./MarkdownPreview.vue";
import PolishSettings from "./PolishSettings.vue";
import PolishAction from "./PolishAction.vue";
import {
  AudioOutlined,
  CameraOutlined,
  ArrowDownOutlined,
  ArrowRightOutlined,
  CheckOutlined,
  CloudDownloadOutlined,
  CodeOutlined,
  CopyOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EditOutlined,
  FileTextOutlined,
  FolderOpenOutlined,
  InfoCircleOutlined,
  LoadingOutlined,
  PlusOutlined,
  PauseOutlined,
  CaretRightOutlined,
  SoundOutlined,
  SettingOutlined,
  HistoryOutlined,
  CloseOutlined,
} from "@ant-design/icons-vue";
import {
  api,
  time,
  toMarkdown,
  fetchMarkdown,
  type Segment,
  type Session,
  type Settings,
  type Status,
  type AudioSource,
} from "./api";

import {
  collapsePanel,
  ensureCapturePermission,
  openCaptureSettings,
  copyText,
  inDesktop,
  saveMarkdown,
  takeScreenshot,
  loadAutoScroll,
  saveAutoScroll,
  loadAudioSource,
  saveAudioSource,
  readCapturePermissions,
  type CapturePermissions,
  updateOrb,
} from "./desktop";

const settingsOpen = ref(false);
const historyOpen = ref(false);
const exporting = ref(false);
const screenshotPending = ref(false);
const markdownView = ref<"source" | "preview">("source");
const isDesktop = inDesktop();
const headerTooltipProps = {
  placement: "bottom" as const,
  arrow: false,
  color: "#fff",
  overlayClassName: "header-action-tooltip",
  mouseEnterDelay: 0.35,
  mouseLeaveDelay: 0,
  destroyTooltipOnHide: true,
};

const settings = reactive<Settings>({
  title: "",
  audio_source: "system",
  language: "zh",
  model: "large-v3",
  engine: "auto",
});
const status = ref<Status | null>(null);
const sessions = ref<Session[]>([]);
const selected = ref<Session | null>(null);
const tab = ref("transcript");
const pending = ref(false);
const pausePending = ref(false);
const connected = ref(false);
const initialLoading = ref(true);
const networkError = ref("");
const helpOpen = ref(false);
const capturePermissionDenied = ref<AudioSource | null>(null);
const audioSourceLoading = ref(true);
const capturePermissions = ref<CapturePermissions | null>(null);
const permissionCheckFailed = ref(false);
const authorizationPending = ref(false);
const modelRequestPending = ref(false);
let permissionRefresh: Promise<void> | undefined;
const transcript = ref<HTMLElement>();
const autoScroll = ref(true);
const autoScrollLoading = ref(true);
const editingId = ref<number | null>(null);
const editText = ref("");
const savePending = ref(false);
let timer: ReturnType<typeof setTimeout> | undefined;
let disposed = false;
let selectionRevision = 0;
const activeStates = ["preparing", "recording", "pausing", "paused", "resuming", "stopping"];
const busy = computed(() => !!status.value?.active_id || pending.value || audioSourceLoading.value || authorizationPending.value);
const modelLoading = computed(() => status.value?.model.status === "loading");
const currentActive = computed(
  () => !!selected.value && activeStates.includes(selected.value.status),
);
const showListeningActions = computed(
  () =>
    currentActive.value &&
    !(status.value?.active_id && selected.value?.id !== status.value.active_id),
);
const segments = computed(() => selected.value?.segments ?? []);
const reviewCount = computed(
  () => segments.value.filter((segment) => segment.review).length,
);
const characterCount = computed(() =>
  tab.value === "markdown" ? markdown.value.replace(/\s/g, "").length : segments.value.reduce(
    (sum, segment) => sum + (segment.kind === "image" ? 0 : segment.text.replace(/\s/g, "").length),
    0,
  ),
);
const markdownDrafts = reactive<Record<string, {
  text: string;
  saved: string;
  saving: boolean;
  error: boolean;
}>>({});
const markdownTimers = new Map<string, ReturnType<typeof setTimeout>>();
const markdownSaves = new Map<string, Promise<void>>();
const markdownDraft = computed(() => selected.value ? markdownDrafts[selected.value.id] : undefined);
const markdown = computed({
  get: () => markdownDraft.value?.text ?? (selected.value ? toMarkdown(selected.value) : ""),
  set: (text: string) => {
    if (!selected.value) return;
    const id = selected.value.id;
    const draft = markdownDrafts[id] ??= {
      text: toMarkdown(selected.value), saved: toMarkdown(selected.value), saving: false, error: false,
    };
    draft.text = text;
    draft.error = false;
    clearTimeout(markdownTimers.get(id));
    markdownTimers.set(id, setTimeout(() => void saveMarkdownDraft(id), 600));
  },
});
const markdownSaveVisible = computed(() => {
  const draft = markdownDraft.value;
  return !!draft && (draft.error || draft.saving || draft.text !== draft.saved);
});
const markdownSaveLabel = computed(() => {
  const draft = markdownDraft.value;
  if (draft?.error) return "保存失败，点击重试";
  if (draft?.saving) return "正在保存…";
  return "待保存";
});

function saveMarkdownDraft(id = selected.value?.id): Promise<void> {
  if (!id) return Promise.resolve();
  clearTimeout(markdownTimers.get(id));
  markdownTimers.delete(id);
  const draft = markdownDrafts[id];
  if (!draft) return Promise.resolve();
  const existing = markdownSaves.get(id);
  if (existing) return existing;
  if (draft.text === draft.saved) return Promise.resolve();
  const saving = (async () => {
    draft.saving = true;
    draft.error = false;
    try {
      // Serialize writes and save any edits made while the previous request ran.
      while (draft.text !== draft.saved) {
        const content = draft.text;
        const result = await api<{ title: string; content: string }>(`/sessions/${id}/markdown`, {
          method: "PATCH", body: JSON.stringify({ content, base_content: draft.saved }),
        });
        const added = result.content.slice(content.length);
        draft.text += added;
        draft.saved = result.content;
        if (selected.value?.id === id) {
          selected.value.markdown_content = result.content;
          selected.value.title = result.title;
        }
        const session = sessions.value.find((item) => item.id === id);
        if (session) session.title = result.title;
      }
    } catch (error) {
      draft.error = true;
      showError(error);
    } finally {
      draft.saving = false;
      markdownSaves.delete(id);
    }
  })();
  markdownSaves.set(id, saving);
  return saving;
}
const lag = computed(() =>
  Math.max(
    0,
    (selected.value?.duration ?? 0) - (selected.value?.processed ?? 0),
  ),
);
const statusNames: Record<string, string> = {
  preparing: "准备中",
  recording: "正在聆听",
  pausing: "正在暂停",
  paused: "已暂停",
  resuming: "正在继续",
  stopping: "完成剩余转写",
  completed: "已完成",
  failed: "转写失败",
  interrupted: "已中断",
};
const modelOptions = [
  { value: "large-v3", label: "Large v3 · 准确优先" },
  { value: "large-v3-turbo", label: "Large v3 Turbo · 速度优先" },
  { value: "small", label: "Small · 低内存" },
];
const languageOptions = [
  { value: "zh", label: "中文" },
  { value: "en", label: "英语" },
  { value: "auto", label: "自动检测语言" },
];
const audioSourceOptions = [
  { value: "microphone", label: "识别麦克风音频" },
  { value: "system", label: "识别本机音频" },
  { value: "mixed", label: "识别麦克风与本机音频" },
];
const missingPermissions = computed(() => {
  if (!capturePermissions.value) return [];
  const missing: { source: AudioSource; label: string }[] = [];
  if (settings.audio_source !== "system" && !capturePermissions.value.microphone)
    missing.push({ source: "microphone", label: "麦克风" });
  if (settings.audio_source !== "microphone" && !capturePermissions.value.system)
    missing.push({ source: "system", label: "本机音频" });
  return missing;
});
const authorizationReady = computed(() => !!capturePermissions.value && !missingPermissions.value.length);
const authorizationLabel = computed(() => permissionCheckFailed.value ? "授权状态获取失败"
  : !isDesktop ? "授权状态待确认"
    : !capturePermissions.value ? "正在检查授权状态"
      : authorizationReady.value ? "程序已授权" : "程序未授权");
const authorizationDescription = computed(() => !isDesktop ? "请在 Parrot 桌面客户端查看授权状态。"
  : permissionCheckFailed.value ? "暂时无法检查权限，请点击重试。"
    : !capturePermissions.value ? "正在检查当前音频来源所需权限。"
      : authorizationReady.value ? "当前音频来源所需权限已开启。"
        : `缺少${missingPermissions.value.map(item => item.label).join("、")}权限，点击前往授权。`);
const selectedEngine = computed(() => settings.engine === "auto"
  ? status.value?.model_cache.auto_engine : settings.engine);
const selectedModelDownloaded = computed(() => !!selectedEngine.value &&
  !!status.value?.model_cache.downloaded[selectedEngine.value]?.[settings.model]);
const selectedModelMatches = computed(() => status.value?.model.model === settings.model &&
  status.value?.model.engine === selectedEngine.value);
const modelLabel = computed(() => selectedModelDownloaded.value ? "识别模型已下载"
  : selectedModelMatches.value && modelLoading.value ? "识别模型下载中"
    : selectedModelMatches.value && status.value?.model.status === "error"
      ? "识别模型下载失败" : "识别模型尚未下载");

function dateLabel(value: string) {
  return new Date(value).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

async function refresh() {
  if (settingsOpen.value) void refreshCapturePermissions();
  try {
    const [health, history] = await Promise.all([
      api<Status>("/status"),
      api<Session[]>("/sessions"),
    ]);
    if (disposed) return;
    status.value = health;
    sessions.value = history;
    connected.value = true;
    updateOrb(!!health.active_id, health.level, true,
      ["pausing", "paused", "resuming"].includes(health.active_status ?? ""));
    networkError.value = "";
    if (!selected.value && health.active_id)
      await selectSession(health.active_id);
    if (selected.value) {
      const id = selected.value.id;
      const revision = selectionRevision;
      const before = segments.value.length;
      const detail = await api<Session>(`/sessions/${id}`);
      if (selected.value?.id === id && revision === selectionRevision) {
        selected.value = detail;
        const draft = markdownDrafts[id];
        if (draft && !draft.saving && draft.text === draft.saved) {
          draft.text = draft.saved = toMarkdown(detail);
        }
        if (
          autoScroll.value &&
          before !== detail.segments?.length &&
          editingId.value === null
        ) {
          await nextTick();
          scrollToLatest();
        }
      }
    }
  } catch (error) {
    connected.value = false;
    updateOrb(false, 0, false);
    networkError.value =
      error instanceof Error ? error.message : "无法连接后端";
  } finally {
    initialLoading.value = false;
    if (!disposed) timer = setTimeout(refresh, 1000);
  }
}

async function selectSession(id: string) {
  const revision = ++selectionRevision;
  try {
    const detail = await api<Session>(`/sessions/${id}`);
    if (revision !== selectionRevision) return;
    selected.value = detail;
    editingId.value = null;
    tab.value = "transcript";
    historyOpen.value = false;
  } catch (error) {
    showError(error);
  }
}

function newSession() {
  if (status.value?.active_id) {
    void selectSession(status.value.active_id);
    return;
  }
  ++selectionRevision;
  selected.value = null;
  editingId.value = null;
  historyOpen.value = false;
  tab.value = "transcript";
}

function canDeleteSession(item: Session) {
  return !activeStates.includes(item.status) && status.value?.active_id !== item.id;
}

function removeSession(item: Session, event: Event) {
  event.stopPropagation();
  event.preventDefault();
  if (!canDeleteSession(item)) {
    message.error("请先停止当前聆听，再删除这条记录");
    return;
  }
  Modal.confirm({
    title: "删除学习记录",
    content: `确定删除「${item.title}」？删除后无法恢复。`,
    okText: "删除",
    okType: "danger",
    cancelText: "取消",
    centered: true,
    async onOk() {
      try {
        await api(`/sessions/${item.id}`, { method: "DELETE" });
        clearTimeout(markdownTimers.get(item.id));
        markdownTimers.delete(item.id);
        delete markdownDrafts[item.id];
        sessions.value = sessions.value.filter((session) => session.id !== item.id);
        if (selected.value?.id === item.id) {
          ++selectionRevision;
          selected.value = null;
          editingId.value = null;
        }
        message.success("已删除");
      } catch (error) {
        showError(error);
        throw error;
      }
    },
  });
}

function options(): Settings {
  return {
    ...settings,
    title: `学习记录 ${new Date().toLocaleDateString("zh-CN")}`,
  };
}

function showError(error: unknown) {
  message.error(error instanceof Error ? error.message : "操作失败，请重试");
}

async function preparePolishDocument(id: string) {
  const revision = selectionRevision;
  if (selected.value?.id !== id || currentActive.value) throw new Error("请先结束当前记录的转写");
  await saveMarkdownDraft(id);
  if (markdownDrafts[id]?.error) throw new Error("请先保存当前文稿，再进行润色");
  const localContent = markdown.value;
  const source = await fetchMarkdown(id);
  if (selected.value?.id !== id || selectionRevision !== revision || markdown.value !== localContent)
    throw new Error("文稿已变化，请重新点击 AI 润色");
  return { source, localContent };
}

function acceptPolishedSession(detail: Session) {
  if (selected.value?.id !== detail.id) return;
  ++selectionRevision;
  clearTimeout(markdownTimers.get(detail.id));
  markdownTimers.delete(detail.id);
  selected.value = detail;
  markdownDrafts[detail.id] = { text: toMarkdown(detail), saved: toMarkdown(detail), saving: false, error: false };
  const item = sessions.value.find(item => item.id === detail.id);
  if (item) item.title = detail.title;
}

async function applyPolishResult(id: string, content: string, source: string, localContent: string) {
  if (selected.value?.id !== id || markdown.value !== localContent || currentActive.value)
    throw new Error("原文已变化，润色预览已保留，请重新生成");
  await saveMarkdownDraft(id);
  if (markdownDrafts[id]?.error) throw new Error("请先保存当前文稿");
  const detail = await api<Session>(`/sessions/${id}/polish/apply`, {
    method: "POST", body: JSON.stringify({ content, base_content: source }),
  });
  acceptPolishedSession(detail);
}

async function undoPolishResult(id: string, revision: number) {
  if (selected.value?.id !== id || markdownSaveVisible.value || currentActive.value)
    throw new Error("当前文稿有修改，请先保存后再操作");
  const detail = await api<Session>(`/sessions/${id}/polish/undo`, {
    method: "POST", body: JSON.stringify({ revision_id: revision, base_content: markdown.value }),
  });
  acceptPolishedSession(detail);
}

async function start() {
  if (pending.value || busy.value) return;
  const session = selected.value;
  const revision = selectionRevision;
  pending.value = true;
  try {
    capturePermissionDenied.value = null;
    const permission = await ensureCapturePermission(session?.audio_source ?? settings.audio_source);
    if (!permission.granted) {
      capturePermissionDenied.value = permission.source;
      return;
    }
    if (selectionRevision !== revision) return;
    if (session) {
      await saveMarkdownDraft(session.id);
      if (markdownDrafts[session.id]?.error || selectionRevision !== revision) return;
    }
    const detail = await api<Session>(session ? `/sessions/${session.id}/continue` : "/sessions", {
      method: "POST",
      ...(session ? {} : { body: JSON.stringify(options()) }),
    });
    if (status.value) status.value.active_id = detail.id;
    if (selectionRevision === revision) {
      selected.value = detail;
      ++selectionRevision;
      tab.value = "transcript";
      editingId.value = null;
      await nextTick();
      if (autoScroll.value) scrollToLatest();
    }
  } catch (error) {
    showError(error);
  } finally {
    pending.value = false;
  }
}

async function stop() {
  if (!status.value?.active_id) return;
  pending.value = true;
  try {
    const detail = await api<Session>(
      `/sessions/${status.value.active_id}/stop`,
      {
        method: "POST",
      },
    );
    if (selected.value?.id === detail.id) selected.value = detail;
    message.info("已停止采集，正在保存并转写剩余音频");
  } catch (error) {
    showError(error);
  } finally {
    pending.value = false;
  }
}

async function togglePause() {
  if (!selected.value || pausePending.value) return;
  const sid = selected.value.id;
  const revision = selectionRevision;
  const action = selected.value.status === "paused" ? "resume" : "pause";
  pausePending.value = true;
  try {
    const detail = await api<Session>(`/sessions/${sid}/${action}`, { method: "POST" });
    if (status.value?.active_id === sid) {
      status.value.active_status = detail.status;
      updateOrb(true, status.value.level, connected.value,
        ["pausing", "paused", "resuming"].includes(detail.status));
    }
    if (selected.value?.id === sid && selectionRevision === revision) selected.value = detail;
  } catch (error) {
    showError(error);
  } finally {
    pausePending.value = false;
  }
}

async function prepareModel() {
  if (busy.value || modelLoading.value || modelRequestPending.value) return;
  modelRequestPending.value = true;
  pending.value = true;
  try {
    await api("/model/prepare", {
      method: "POST",
      body: JSON.stringify(options()),
    });
    if (status.value) status.value.model = {
      status: "loading", message: "正在下载所选模型，请稍等片刻。",
      engine: selectedEngine.value ?? null, model: settings.model,
    };
    message.info("开始下载模型，请保持网络连接");
  } catch (error) {
    showError(error);
  } finally {
    pending.value = false;
    modelRequestPending.value = false;
  }
}

async function saveEdit() {
  if (!selected.value || editingId.value === null) return;
  savePending.value = true;
  const sessionId = selected.value.id;
  const segmentId = editingId.value;
  const text = editText.value;
  try {
    await api(`/sessions/${sessionId}/segments/${segmentId}`, {
      method: "PATCH",
      body: JSON.stringify({ text }),
    });
    if (selected.value?.id === sessionId) {
      const segment = segments.value.find((item) => item.id === segmentId);
      if (segment) {
        segment.text = text;
        segment.review = 0;
      }
      editingId.value = null;
    }
    message.success("校对已保存");
  } catch (error) {
    showError(error);
  } finally {
    savePending.value = false;
  }
}

function scrollToLatest() {
  transcript.value?.scrollTo({
    top: transcript.value.scrollHeight,
    behavior: "smooth",
  });
}

async function copyMarkdown() {
  try {
    await copyText(markdown.value);
    message.success("Markdown 已复制");
  } catch {
    message.error("复制失败，请使用导出 Markdown");
  }
}

async function exportMarkdown() {
  if (!selected.value) return;
  exporting.value = true;
  try {
    const result = await saveMarkdown(
      selected.value.id,
      selected.value.title,
      markdown.value,
      segments.value.some((segment) => segment.kind === "image"),
    );
    if (result.saved) message.success("已导出为文件夹");
  } catch (error) {
    showError(error);
  } finally {
    exporting.value = false;
  }
}

function openSettings() {
  settingsOpen.value = true;
}

function refreshCapturePermissions(): Promise<void> {
  if (permissionRefresh) return permissionRefresh;
  permissionRefresh = (async () => {
    try {
      const permissions = await readCapturePermissions();
      if (!disposed) {
        capturePermissions.value = permissions;
        permissionCheckFailed.value = false;
      }
    } catch {
      if (!disposed) {
        capturePermissions.value = null;
        permissionCheckFailed.value = true;
      }
    } finally {
      permissionRefresh = undefined;
    }
  })();
  return permissionRefresh;
}

async function authorizeFromSettings() {
  if (authorizationPending.value || !isDesktop) return;
  authorizationPending.value = true;
  try {
    const permission = await ensureCapturePermission(settings.audio_source);
    // A status read started before the permission dialog may still be in flight.
    await permissionRefresh;
    await refreshCapturePermissions();
    if (!permission.granted) await openCaptureSettings(permission.source);
    else if (missingPermissions.value[0]) await openCaptureSettings(missingPermissions.value[0].source);
    if (authorizationReady.value) capturePermissionDenied.value = null;
  } catch (error) {
    showError(error);
  } finally {
    authorizationPending.value = false;
  }
}

function refreshPermissionsOnFocus() {
  if (settingsOpen.value) void refreshCapturePermissions();
}

watch([settingsOpen, () => settings.audio_source], ([open]) => {
  if (open) void refreshCapturePermissions();
});

async function refreshSessionAfterPhotoChange(sessionId: string, revision: number, scroll = false) {
  const detail = await api<Session>(`/sessions/${sessionId}`);
  if (selected.value?.id !== sessionId || revision !== selectionRevision) return;
  selected.value = detail;
  const draft = markdownDrafts[sessionId];
  if (draft && !draft.saving) draft.text = draft.saved = toMarkdown(detail);
  if (scroll) {
    tab.value = "transcript";
    await nextTick();
    scrollToLatest();
  }
}

async function captureScreenshot(segmentId?: number) {
  if (!selected.value || screenshotPending.value) return;
  const sessionId = selected.value.id;
  const revision = selectionRevision;
  screenshotPending.value = true;
  try {
    await saveMarkdownDraft(sessionId);
    if (markdownDrafts[sessionId]?.error || revision !== selectionRevision) return;
    const result = await takeScreenshot(sessionId, segmentId);
    if (!result.captured) return;
    await refreshSessionAfterPhotoChange(sessionId, revision, segmentId == null);
    if (segmentId != null) message.success("截图已更新");
  } catch (error) {
    showError(error);
  } finally {
    screenshotPending.value = false;
  }
}

function removeScreenshot(segment: Segment) {
  if (!selected.value || screenshotPending.value) return;
  const sessionId = selected.value.id;
  const revision = selectionRevision;
  Modal.confirm({
    title: "删除截图",
    content: "确定删除这张截图？删除后无法恢复。",
    okText: "删除",
    okType: "danger",
    cancelText: "取消",
    centered: true,
    async onOk() {
      try {
        await api(`/sessions/${sessionId}/segments/${segment.id}`, { method: "DELETE" });
        await refreshSessionAfterPhotoChange(sessionId, revision);
        message.success("截图已删除");
      } catch (error) {
        showError(error);
        throw error;
      }
    },
  });
}

async function changeAutoScroll(value: boolean | string | number) {
  const previous = autoScroll.value;
  autoScroll.value = Boolean(value);
  autoScrollLoading.value = true;
  try {
    await saveAutoScroll(autoScroll.value);
  } catch (error) {
    autoScroll.value = previous;
    showError(error);
  } finally {
    autoScrollLoading.value = false;
  }
}

async function changeAudioSource(source: AudioSource) {
  const previous = settings.audio_source;
  settings.audio_source = source;
  audioSourceLoading.value = true;
  try {
    await saveAudioSource(source);
    capturePermissionDenied.value = null;
  } catch (error) {
    settings.audio_source = previous;
    showError(error);
  } finally {
    audioSourceLoading.value = false;
  }
}

onMounted(async () => {
  window.addEventListener("parrot-open-settings", openSettings);
  window.addEventListener("focus", refreshPermissionsOnFocus);
  void refresh();
  try {
    settings.audio_source = await loadAudioSource();
  } catch (error) {
    showError(error);
  } finally {
    audioSourceLoading.value = false;
  }
  try {
    autoScroll.value = await loadAutoScroll();
  } catch (error) {
    showError(error);
  } finally {
    autoScrollLoading.value = false;
  }
});
onUnmounted(() => {
  window.removeEventListener("parrot-open-settings", openSettings);
  window.removeEventListener("focus", refreshPermissionsOnFocus);
  disposed = true;
  clearTimeout(timer);
  for (const id of markdownTimers.keys()) void saveMarkdownDraft(id);
});
</script>

<template>
  <a-config-provider
    :locale="zhCN"
    :theme="{
      algorithm: theme.defaultAlgorithm,
      token: {
        colorPrimary: '#4263eb',
        borderRadius: 10,
        fontFamily:
          '-apple-system, BlinkMacSystemFont, PingFang SC, sans-serif',
        colorText: '#27344c',
        colorBorder: '#e7ebf3',
        controlHeight: 38,
      },
    }"
  >
    <div class="desktop-workspace" data-testid="desktop-workspace">
      <header class="panel-header">
        <div class="panel-brand">
          <span class="brand-mark">
            <svg class="parrot-logo" viewBox="0 0 32 32" role="img" aria-label="Parrot 鹦鹉图标">
              <path fill="#fff" d="M11.6 23.6C8.5 22.1 6.8 19.2 6.8 15.8c0-3.1 1.5-5.4 2.6-7C9.8 5.5 12.4 3.5 16 3.5c3.9 0 6.6 2.5 6.6 6.2v6.1c0 4.3-2.2 7-5.8 8.2l.7 5-3.6-2.7-3.6 2.7z" />
              <path fill="#ffc85b" d="M22 9.3c4.1 0 6 1.9 6 4.7 0 2.3-1.6 4-3.9 4.4.9-1.9 1-3.5-.6-4.4l-2.1-.8z" />
              <path d="M10 14.3c-1.3 3.6 1.3 7.3 5.9 7.7 2.6-1.3 3.8-3.3 3.5-5.7M11.4 16.2c.8 1.6 2.1 2.6 3.9 3" fill="none" stroke="#4263eb" stroke-width="1.45" stroke-linecap="round" stroke-linejoin="round" />
              <circle cx="18.6" cy="8.7" r="1.15" fill="#263564" />
            </svg>
          </span
          ><strong>Parrot<span>.</span></strong>
        </div>
        <div class="header-actions">
          <a-tooltip v-bind="headerTooltipProps" title="新建学习记录"
            ><a-button type="text" aria-label="新建学习记录" @click="newSession"
              ><template #icon><PlusOutlined /></template></a-button
          ></a-tooltip>
          <a-tooltip v-bind="headerTooltipProps" title="历史记录"
            ><a-button
              type="text"
              aria-label="历史记录"
              @click="historyOpen = true"
              ><template #icon><HistoryOutlined /></template></a-button
          ></a-tooltip>
          <a-tooltip v-bind="headerTooltipProps" title="识别设置"
            ><a-button
              type="text"
              aria-label="识别设置"
              @click="settingsOpen = true"
              ><template #icon><SettingOutlined /></template></a-button
          ></a-tooltip>
          <a-tooltip v-bind="headerTooltipProps" v-if="isDesktop" title="收起到悬浮球，转写继续"
            ><a-button
              type="text"
              aria-label="收起转写面板"
              @click="collapsePanel"
              ><template #icon><CloseOutlined /></template></a-button
          ></a-tooltip>
        </div>
      </header>

      <a-alert
        v-if="networkError"
        class="notice"
        type="error"
        show-icon
        message="语音服务暂未连接"
        :description="
          isDesktop
            ? '正在尝试重新连接。持续失败时，请退出并重新启动 Parrot。'
            : networkError
        "
      />
      <a-alert
        v-if="capturePermissionDenied"
        class="notice"
        type="warning"
        show-icon
        :message="capturePermissionDenied === 'microphone' ? '需要允许麦克风采集' : '需要允许本机声音采集'"
        :description="capturePermissionDenied === 'microphone'
          ? '请在系统设置 → 隐私与安全性 → 麦克风中允许 Parrot，然后重试。'
          : '请在系统设置中允许 Parrot，然后从菜单栏退出并重新打开。若开关已开启，请删除旧的 Parrot 条目，重新添加当前应用并授权。'"
      >
        <template #action>
          <a-button size="small" @click="openCaptureSettings(capturePermissionDenied ?? 'system').catch(showError)">打开权限设置</a-button>
        </template>
      </a-alert>
      <a-alert
        v-if="selected?.error"
        class="notice"
        type="error"
        show-icon
        :message="statusNames[selected.status]"
        :description="selected.error"
      >
        <template v-if="isDesktop && (selected.error.includes('系统声音权限未生效') || selected.error.includes('麦克风权限未生效'))" #action>
          <a-button size="small" @click="openCaptureSettings(selected.error.includes('麦克风权限未生效') ? 'microphone' : 'system').catch(showError)">打开权限设置</a-button>
        </template>
      </a-alert>

      <section class="document-panel">
        <div class="document-header">
          <a-tabs v-model:activeKey="tab"
            ><a-tab-pane key="transcript"
              ><template #tab
                ><FileTextOutlined /> 转写内容
                <span v-if="segments.length" class="tab-count">{{
                  segments.length
                }}</span></template
              ></a-tab-pane
            ><a-tab-pane key="markdown"
              ><template #tab><CodeOutlined /> Markdown</template></a-tab-pane
            ></a-tabs
          ><a-button
            type="text"
            size="small"
            :disabled="!segments.length"
            :loading="exporting"
            @click="exportMarkdown"
            ><DownloadOutlined /> 导出</a-button
          >
        </div>

        <div v-if="!segments.length" class="empty-document">
          <div class="empty-illustration">
            <FileTextOutlined /><span><SoundOutlined /></span>
          </div>
          <h2>
            {{
              selected?.status === "paused"
                ? "聆听已暂停"
                : currentActive
                ? selected?.status === "preparing"
                  ? "正在准备好好聆听"
                  : "声音，正在变成文字"
                : "专心听懂，其余交给 Parrot"
            }}
          </h2>
          <p v-if="currentActive">
            {{
              selected?.status === "paused"
                ? "点击继续，接着当前记录转写；暂停期间不采集声音。"
                : selected?.status === "preparing"
                  ? "正在准备识别模型，请稍等片刻。"
                  : "通常积累 8–24 秒音频后输出一批文字。"
            }}
          </p>
          <span v-if="currentActive" class="working-label"
            ><PauseOutlined v-if="selected?.status === 'paused'" /><LoadingOutlined v-else /> {{ statusNames[selected!.status] }}</span
          >
        </div>

        <div
          v-else-if="tab === 'transcript'"
          ref="transcript"
          class="transcript-scroll"
        >
          <article
            v-for="segment in segments"
            :key="segment.id"
            class="transcript-segment"
          >
            <span class="timestamp">{{ time(segment.start) }}</span>
            <div class="segment-content">
              <TranscriptPhoto v-if="segment.kind === 'image' && segment.photo_filename"
                :session-id="selected!.id" :filename="segment.photo_filename"
                :alt="`截图 ${time(segment.start)}`" />
              <template v-else-if="editingId === segment.id"
                ><a-textarea
                  v-model:value="editText"
                  aria-label="校对转写文字"
                  :auto-size="{ minRows: 2, maxRows: 8 }"
                />
                <div class="edit-actions">
                  <a-button size="small" @click="editingId = null"
                    >取消</a-button
                  ><a-button
                    type="primary"
                    size="small"
                    :loading="savePending"
                    @click="saveEdit"
                    ><CheckOutlined /> 保存</a-button
                  >
                </div></template
              ><template v-else
                ><p>{{ segment.text }}</p>
                <span v-if="segment.review" class="review-label"
                  >建议校对</span
                ></template
              >
            </div>
            <div v-if="segment.kind === 'image'" class="segment-actions">
              <a-tooltip v-if="isDesktop" title="重新截图">
                <a-button
                  type="text"
                  size="small"
                  class="edit-button"
                  aria-label="重新截图"
                  :disabled="screenshotPending"
                  :loading="screenshotPending"
                  @click="captureScreenshot(segment.id)"
                  ><CameraOutlined
                /></a-button>
              </a-tooltip>
              <a-tooltip title="删除截图">
                <a-button
                  type="text"
                  size="small"
                  class="edit-button delete-button"
                  aria-label="删除截图"
                  :disabled="screenshotPending"
                  @click="removeScreenshot(segment)"
                  ><DeleteOutlined
                /></a-button>
              </a-tooltip>
            </div>
            <a-button
              v-else-if="editingId !== segment.id"
              type="text"
              size="small"
              class="edit-button"
              aria-label="编辑这一段"
              @click="
                editingId = segment.id;
                editText = segment.text;
              "
              ><EditOutlined
            /></a-button>
          </article>
        </div>

        <div v-else class="markdown-area">
          <div class="markdown-toolbar">
            <div class="markdown-view-switch" role="tablist" aria-label="Markdown 视图">
              <button
                type="button"
                role="tab"
                :aria-selected="markdownView === 'source'"
                :class="{ active: markdownView === 'source' }"
                @click="markdownView = 'source'"
              >
                Markdown
              </button>
              <button
                type="button"
                role="tab"
                :aria-selected="markdownView === 'preview'"
                :class="{ active: markdownView === 'preview' }"
                @click="markdownView = 'preview'; saveMarkdownDraft()"
              >
                Preview
              </button>
            </div>
            <div class="markdown-actions">
              <a-button
                v-if="markdownView === 'source' && markdownSaveVisible"
                type="text"
                size="small"
                :loading="markdownDraft?.saving"
                :disabled="!!markdownDraft?.saving"
                aria-label="保存 Markdown"
                @click="saveMarkdownDraft()"
              >{{ markdownSaveLabel }}</a-button>
              <PolishAction v-if="selected" :session-id="selected.id" :content="markdown" :active="currentActive"
                :unsaved="!!markdownSaveVisible" :revision-id="selected.polish_revision" :prepare="preparePolishDocument"
                :apply="applyPolishResult" :undo="undoPolishResult" @open-settings="openSettings" />
              <a-button type="text" size="small" aria-label="复制全文" @click="copyMarkdown"
              ><CopyOutlined /> 复制全文</a-button
              >
            </div>
          </div>
          <textarea
            v-if="markdownView === 'source'"
            v-model="markdown"
            class="markdown-editor"
            aria-label="编辑 Markdown"
            spellcheck="false"
            @blur="saveMarkdownDraft()"
            @keydown.meta.s.prevent="saveMarkdownDraft()"
            @keydown.ctrl.s.prevent="saveMarkdownDraft()"
          ></textarea>
          <MarkdownPreview
            v-else-if="selected"
            :content="markdown"
            :session-id="selected.id"
          />
        </div>

        <div class="capture-dock">
          <div
            class="capture-controls"
            :class="{ 'is-recording': selected?.status === 'recording' }"
            role="group"
            aria-label="聆听控制"
          >
            <span class="record-time" :aria-label="`已聆听 ${time(selected?.duration)}`">{{
              time(selected?.duration)
            }}</span>
            <Transition name="capture-expand">
              <div
                v-if="isDesktop && showListeningActions"
                key="screenshot"
                class="capture-expand-slot"
              >
                <a-tooltip title="截图并插入当前记录">
                  <a-button
                    class="capture-action secondary-action"
                    shape="circle"
                    aria-label="插入截图"
                    :loading="screenshotPending"
                    :disabled="!connected || screenshotPending || pending"
                    @click="captureScreenshot()"
                    ><template #icon><CameraOutlined /></template
                  ></a-button>
                </a-tooltip>
              </div>
            </Transition>
            <Transition name="capture-expand">
              <div
                v-if="showListeningActions"
                key="pause"
                class="capture-expand-slot"
              >
                <a-tooltip :title="selected?.status === 'paused' ? '继续聆听' : '暂停聆听'">
                  <a-button
                    class="capture-action secondary-action"
                    shape="circle"
                    :aria-label="selected?.status === 'paused' ? '继续聆听' : '暂停聆听'"
                    :disabled="pending || !connected || !['recording', 'paused'].includes(selected?.status || '')"
                    :loading="pausePending || ['pausing', 'resuming'].includes(selected?.status || '')"
                    @click="togglePause"
                  >
                    <template #icon>
                      <CaretRightOutlined v-if="selected?.status === 'paused'" />
                      <PauseOutlined v-else />
                    </template>
                  </a-button>
                </a-tooltip>
              </div>
            </Transition>
            <a-tooltip
              v-if="status?.active_id && selected?.id !== status.active_id"
              title="返回正在进行的转写"
            >
              <a-button
                class="capture-action"
                type="primary"
                shape="circle"
                aria-label="返回转写"
                @click="selectSession(status!.active_id!)"
                ><template #icon><ArrowRightOutlined /></template></a-button
              >
            </a-tooltip>
            <a-tooltip
              v-else-if="currentActive"
              :title="selected?.status === 'stopping' ? '正在完成剩余转写' : '停止聆听'"
            >
              <a-button
                class="capture-action"
                type="primary"
                shape="circle"
                aria-label="停止聆听"
                :disabled="pausePending || selected?.status === 'stopping'"
                :loading="pending || selected?.status === 'stopping'"
                @click="stop"
                ><template #icon><span class="stop-glyph" aria-hidden="true"></span></template
              ></a-button>
            </a-tooltip>
            <a-tooltip v-else :title="selected ? '继续聆听，接着当前记录转写' : '开始聆听'">
              <a-button
                class="capture-action"
                type="primary"
                shape="circle"
                :aria-label="selected ? '续写当前记录' : '开始聆听'"
                :disabled="
                  !connected || busy || modelLoading || !status?.capture_available
                "
                :loading="pending"
                @click="start"
                ><template #icon><AudioOutlined class="capture-start-symbol" /></template></a-button
              >
            </a-tooltip>
            <div
              class="audio-meter"
              :style="{
                '--meter-energy':
                  selected?.status === 'recording'
                    ? String(0.28 + Math.min(1, Math.max(0, status?.level || 0)) * 0.72)
                    : '0',
              }"
              aria-hidden="true"
            >
              <i
                v-for="(height, index) in [12, 22, 28]"
                :key="index"
                :style="{ height: `${height}px` }"
              ></i>
            </div>
          </div>
        </div>

      </section>

      <footer class="panel-footer">
        <div class="footer-info">
          <span v-if="characterCount" class="character-count">{{ characterCount.toLocaleString() }} 字符</span>
          <span v-if="currentActive">待处理约 {{ time(lag) }}</span>
          <span v-else-if="reviewCount" class="review-summary">{{ reviewCount }} 段待校对</span>
        </div>
        <div class="footer-actions">
          <button @click="helpOpen = true">
            <InfoCircleOutlined /> 使用指南
          </button>
        </div>
      </footer>

      <a-modal
        v-model:open="historyOpen"
        title="学习记录"
        :footer="null"
        width="480px"
        wrap-class-name="client-modal"
        ><div class="history-top">
          <span>{{ sessions.length }} 条记录 · 保存在本地</span>
        </div>
        <a-spin v-if="initialLoading" />
        <div v-else-if="!sessions.length" class="history-empty">
          <FolderOpenOutlined />
          <p>从一次聆听开始吧</p>
        </div>
        <div v-else class="history-list">
          <div
            v-for="item in sessions"
            :key="item.id"
            class="history-item session-item"
            :class="{ selected: selected?.id === item.id }"
            role="button"
            tabindex="0"
            @click="selectSession(item.id)"
            @keydown.enter.prevent="selectSession(item.id)"
          >
            <span class="history-icon"><FileTextOutlined /></span
            ><span
              ><strong>{{ item.title }}</strong
              ><small
                >{{ dateLabel(item.created_at) }} · {{ time(item.duration) }} ·
                {{ item.segment_count }} 段</small
              ></span
            ><a-tag
              :color="item.status === 'completed' ? 'green' : item.status === 'failed' ? 'red' : activeStates.includes(item.status) ? 'blue' : 'default'"
              >{{ statusNames[item.status] || item.status }}</a-tag
            >
            <a-tooltip title="删除记录">
              <a-button
                class="history-delete"
                type="text"
                size="small"
                aria-label="删除记录"
                :disabled="!canDeleteSession(item)"
                @click="removeSession(item, $event)"
                ><template #icon><DeleteOutlined /></template
              ></a-button>
            </a-tooltip>
          </div>
        </div></a-modal
      >

      <a-modal
        v-model:open="settingsOpen"
        title="识别设置"
        :footer="null"
        width="480px"
        wrap-class-name="client-modal"
      >
        <a-form layout="vertical" :disabled="busy || modelLoading">
          <a-form-item label="音频来源">
            <a-select
              id="audio-source-select"
              aria-label="音频来源"
              :value="settings.audio_source"
              :options="audioSourceOptions"
              :disabled="busy || modelLoading || audioSourceLoading"
              @change="changeAudioSource"
            />
          </a-form-item>
          <div class="settings-pair">
            <a-form-item label="音频语言"
              ><a-select
                v-model:value="settings.language"
                :options="languageOptions" /></a-form-item
            ><a-form-item label="识别模型"
              ><a-select v-model:value="settings.model" :options="modelOptions"
            /></a-form-item>
          </div>
          <a-form-item label="推理引擎"
            ><a-select
              v-model:value="settings.engine"
              :options="[
                { value: 'auto', label: '自动选择（推荐）' },
                { value: 'mlx', label: 'MLX · Apple 芯片' },
                { value: 'faster-whisper', label: 'faster-whisper · CPU' },
              ]" /></a-form-item
        ></a-form>
        <div class="setting-toggle">
          <div>
            <label for="auto-scroll-switch">跟随最新内容</label>
            <p class="field-help">转写时自动滚动到最新文字，关闭后可停留阅读。</p>
          </div>
          <a-switch id="auto-scroll-switch" aria-label="跟随最新内容"
            :checked="autoScroll" :loading="autoScrollLoading" :disabled="autoScrollLoading"
            @change="changeAutoScroll" />
        </div>
        <PolishSettings />
        <div class="recognition-status" aria-label="程序状态">
          <div class="readiness-row permission-status"
            :class="{ 'is-ready': authorizationReady, 'is-error': permissionCheckFailed || (capturePermissions && !authorizationReady) }"
            role="status">
            <div class="readiness-copy">
              <strong>
                <span v-if="authorizationReady" class="status-dot" aria-hidden="true"></span>
                <CloseOutlined v-else-if="permissionCheckFailed || capturePermissions" class="status-cross" />
                <LoadingOutlined v-else-if="isDesktop" class="status-pending" />
                <InfoCircleOutlined v-else class="status-pending" />
                {{ authorizationLabel }}
              </strong>
              <p>{{ authorizationDescription }}</p>
            </div>
            <a-button v-if="capturePermissions && !authorizationReady"
              type="link" class="readiness-action" :loading="authorizationPending"
              :disabled="authorizationPending || audioSourceLoading"
              @click="authorizeFromSettings">前往授权</a-button>
            <a-button v-else-if="permissionCheckFailed" type="link" class="readiness-action"
              @click="refreshCapturePermissions">重试</a-button>
          </div>
          <div v-if="status?.model_cache" class="readiness-row model-preparation"
            :class="{ 'is-ready': selectedModelDownloaded, 'is-error': !selectedModelDownloaded && !modelLoading }"
            role="status">
            <div class="readiness-copy">
              <strong>
                <span v-if="selectedModelDownloaded" class="status-dot" aria-hidden="true"></span>
                <LoadingOutlined v-else-if="modelLoading || modelRequestPending" class="status-pending" />
                <CloseOutlined v-else class="status-cross" />
                {{ modelLabel }}
              </strong>
              <p>{{ selectedModelDownloaded ? '开始转写时自动载入，无需重复下载。' : modelLoading ? '正在下载所选模型，请保持网络连接。' : '点击下载所选模型，完成后会保存在本地。' }}</p>
            </div>
            <a-button
              v-if="!selectedModelDownloaded"
              type="link" class="readiness-action"
              :disabled="!connected || busy || modelLoading || modelRequestPending"
              :loading="modelLoading || modelRequestPending"
              @click="prepareModel"
              ><CloudDownloadOutlined /> {{ modelLoading ? '下载中' : '下载模型' }}</a-button>
          </div>
          <div v-else class="readiness-row" role="status">
            <div class="readiness-copy">
              <strong><LoadingOutlined class="status-pending" /> 正在检查识别模型状态</strong>
            </div>
          </div>
          <PolishSettings status-only />
        </div>
        <a-alert
          v-if="selectedModelMatches && status?.model.status === 'error'"
          class="model-error"
          type="error"
          show-icon
          :message="status.model.message"
      /></a-modal>

      <a-modal
        v-model:open="helpOpen"
        title="从悬浮球开始学习"
        :footer="null"
        width="480px"
        wrap-class-name="client-modal"
        ><div class="help-content">
          <h3>1. 点开悬浮球，开始聆听</h3>
          <p>
            Parrot
            悬浮在屏幕上。点击展开面板，可拖动到屏幕任意位置，松手后保持当前位置。点击「开始聆听」，等状态变为「正在聆听」后再播放课程。
          </p>
          <h3>2. 选择音频来源并授权</h3>
          <p>
            在识别设置中选择麦克风、本机音频或两者。使用麦克风时需允许麦克风权限，使用本机音频时需在「系统设置 → 隐私与安全性 → 屏幕与系统音频录制」中允许
            Parrot。双声源模式建议戴耳机，避免电脑外放被麦克风重复采集。仅支持 macOS 13 及以上。
          </p>
          <h3>3. 收起面板，保持专注</h3>
          <p>
            点击悬浮球或面板右上角的收起按钮，转写会继续。再次点击即可查看。历史记录和设置位于面板顶部，退出程序请使用菜单栏的
            Parrot 菜单。
          </p>
          <h3>4. 校对并导出 Markdown</h3>
          <p>
            停止后等待剩余转写完成，逐段校对或复制、导出
            Markdown。只保存文字，不保留完整录音。
          </p>
          <a-alert
            type="info"
            show-icon
            message="识别为分段输出，通常积累 8–24 秒音频后出现一批文字。术语和口音仍可能识别错误。"
          /></div
      ></a-modal>
    </div>
  </a-config-provider>
</template>
