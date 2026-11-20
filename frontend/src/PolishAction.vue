<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { EditOutlined, UndoOutlined } from "@ant-design/icons-vue";
import { message } from "ant-design-vue";
import MarkdownPreview from "./MarkdownPreview.vue";
import { cancelPolish, loadPolishSettings, polishTextChunks } from "./desktop";
import { createPolishPlan, restorePolishPlan, type PolishPlan } from "./markdownPolish";
import { usePolishSettings } from "./usePolishSettings";

const props = defineProps<{
  sessionId: string; content: string; active: boolean; unsaved: boolean; revisionId?: number | null;
  prepare: (id: string) => Promise<{ source: string; localContent: string }>;
  apply: (id: string, content: string, source: string, localContent: string) => Promise<void>;
  undo: (id: string, revision: number) => Promise<void>;
}>();
const emit = defineEmits<{ "open-settings": [] }>();
const { isDesktop, saved, dirty, busy: settingsBusy, generating, initialize } = usePolishSettings();
const open = ref(false);
const tooltipOpen = ref(false);
const preparing = ref(false);
const applying = ref(false);
const undoing = ref(false);
const running = ref(false);
const error = ref("");
const result = ref("");
const source = ref("");
const localContent = ref("");
const sessionId = ref("");
const progress = ref({ done: 0, total: 0 });
let plan: PolishPlan | undefined;
let jobId: string | undefined;
let cancelled = false;
let disposed = false;
const frozen = computed(() => preparing.value || applying.value || undoing.value);
const changed = computed(() => props.sessionId !== sessionId.value || props.content !== localContent.value || props.active);
const progressLabel = computed(() => progress.value.total
  ? `正在润色 ${progress.value.done}/${progress.value.total} 批` : "正在准备润色请求…");

function onTooltipOpenChange(visible: boolean) {
  tooltipOpen.value = visible && !open.value && !frozen.value && !running.value;
}

async function showDialog() {
  tooltipOpen.value = false;
  if (props.active || frozen.value || running.value) return;
  preparing.value = true;
  error.value = "";
  try {
    await initialize();
    if (!isDesktop || !saved.value.configured || dirty.value || settingsBusy.value) {
      emit("open-settings");
      message.info("请先在设置中填写 AI 润色配置并测试连接");
      return;
    }
    const id = props.sessionId;
    const prepared = await props.prepare(id);
    plan = createPolishPlan(prepared.source);
    sessionId.value = id;
    source.value = prepared.source;
    localContent.value = prepared.localContent;
    result.value = "";
    progress.value = { done: 0, total: 0 };
    open.value = true;
  } catch (cause) { message.error(cause instanceof Error ? cause.message : "无法准备润色文稿"); }
  finally { preparing.value = false; }
}

async function generate() {
  if (!plan || running.value || applying.value || changed.value) return;
  running.value = true;
  generating.value = true;
  cancelled = false;
  error.value = "";
  result.value = "";
  jobId = crypto.randomUUID();
  try {
    const response = await polishTextChunks(jobId, sessionId.value, plan);
    if (cancelled || disposed) return;
    const restored = restorePolishPlan(plan, response.chunks);
    result.value = restored.content;
    saved.value = response.settings;
    if (restored.content === source.value) message.info("文稿无需调整，已保留原文");
  } catch (cause) {
    if (!cancelled && !disposed) {
      error.value = cause instanceof Error ? cause.message : "润色失败，原文未改动";
      try { saved.value = await loadPolishSettings(); } catch { /* Retain the original error. */ }
    }
  } finally {
    running.value = false;
    generating.value = false;
    jobId = undefined;
  }
}

async function stop() {
  cancelled = true;
  if (jobId) await cancelPolish(jobId);
}

async function closeDialog() {
  if (applying.value) return;
  if (running.value) {
    try { await stop(); } catch { /* The native job may already have completed. */ }
  }
  open.value = false;
}

async function applyResult() {
  if (!result.value || changed.value || running.value || applying.value) return;
  applying.value = true;
  error.value = "";
  try {
    await props.apply(sessionId.value, result.value, source.value, localContent.value);
    open.value = false;
    message.success("润色已应用，可撤销恢复原文");
  } catch (cause) { error.value = cause instanceof Error ? cause.message : "应用失败，预览已保留"; }
  finally { applying.value = false; }
}

async function undo() {
  if (!props.revisionId || frozen.value || running.value || props.active || props.unsaved) return;
  undoing.value = true;
  try {
    await props.undo(props.sessionId, props.revisionId);
    message.success("已撤销润色，恢复原文");
  } catch (cause) { message.error(cause instanceof Error ? cause.message : "撤销失败"); }
  finally { undoing.value = false; }
}

function onProgress(event: Event) {
  const detail = (event as CustomEvent<{ job_id: string; done: number; total: number }>).detail;
  if (detail?.job_id === jobId) progress.value = { done: detail.done, total: detail.total };
}
onMounted(() => { window.addEventListener("parrot-polish-progress", onProgress); });
onUnmounted(() => {
  disposed = true;
  cancelled = true;
  if (jobId) void cancelPolish(jobId);
  window.removeEventListener("parrot-polish-progress", onProgress);
});
</script>

<template>
  <span class="polish-button-group">
    <a-tooltip v-if="revisionId" title="撤销上次 AI 润色">
      <a-button type="text" size="small" aria-label="撤销 AI 润色" :loading="undoing"
        :disabled="active || unsaved || frozen || running" @click="undo"><UndoOutlined /></a-button>
    </a-tooltip>
    <a-tooltip :open="tooltipOpen && !open && !frozen && !running" @openChange="onTooltipOpenChange"
      :title="active ? '请先结束当前记录的转写' : '清理转写噪声、生成标题并整理学习笔记，预览后再应用'">
      <a-button type="text" size="small" aria-label="AI 润色" :loading="preparing || running"
        :disabled="active || frozen || running || !content.trim()" @click="showDialog"><EditOutlined /> AI 润色</a-button>
    </a-tooltip>
  </span>
  <a-modal :open="open" title="AI 润色" width="820px" wrap-class-name="client-modal polish-dialog"
    :footer="null" :mask-closable="!running && !applying" :keyboard="!applying" @cancel="closeDialog">
    <a-alert v-if="changed" type="warning" show-icon message="原文或记录状态已变化，请关闭预览后重新生成。" />
    <a-alert v-if="error" class="polish-result-error" type="error" show-icon :message="error" />
    <p v-if="running" class="polish-progress" role="status">{{ progressLabel }}，原文尚未改动。</p>
    <div class="polish-comparison" :class="{ 'has-result': !!result }">
      <section><h3>原文</h3><MarkdownPreview :content="source" :session-id="sessionId" /></section>
      <section v-if="result"><h3>润色后</h3><MarkdownPreview :content="result" :session-id="sessionId" /></section>
    </div>
    <div class="polish-result-actions">
      <a-button :disabled="applying" @click="closeDialog">{{ running ? '取消润色' : '取消' }}</a-button>
      <a-button v-if="!result" type="primary" aria-label="开始 AI 润色" :loading="running"
        :disabled="changed || running || applying" @click="generate">开始润色</a-button>
      <a-button v-else type="primary" aria-label="应用 AI 润色" :loading="applying"
        :disabled="changed || running || applying || result === source" @click="applyResult">应用润色</a-button>
    </div>
  </a-modal>
</template>
