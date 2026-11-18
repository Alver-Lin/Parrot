<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from "vue";
import { message, theme } from "ant-design-vue";
import zhCN from "ant-design-vue/es/locale/zh_CN";
import {
  AudioOutlined,
  ArrowDownOutlined,
  ArrowRightOutlined,
  CheckOutlined,
  CloudDownloadOutlined,
  CodeOutlined,
  CopyOutlined,
  DownloadOutlined,
  EditOutlined,
  FileTextOutlined,
  FolderOpenOutlined,
  InfoCircleOutlined,
  LoadingOutlined,
  PlusOutlined,
  SafetyCertificateOutlined,
  SoundOutlined,
  StopOutlined,
  UploadOutlined,
} from "@ant-design/icons-vue";
import {
  api,
  time,
  toMarkdown,
  type Session,
  type Settings,
  type Status,
} from "./api";

const settings = reactive<Settings>({
  title: "",
  language: "zh",
  model: "large-v3",
  engine: "auto",
  vocabulary: "",
});
const status = ref<Status | null>(null);
const sessions = ref<Session[]>([]);
const selected = ref<Session | null>(null);
const tab = ref("transcript");
const pending = ref(false);
const connected = ref(false);
const initialLoading = ref(true);
const networkError = ref("");
const helpOpen = ref(false);
const fileInput = ref<HTMLInputElement>();
const player = ref<HTMLAudioElement>();
const transcript = ref<HTMLElement>();
const autoScroll = ref(true);
const editingId = ref<number | null>(null);
const editText = ref("");
const savePending = ref(false);
let timer: ReturnType<typeof setTimeout> | undefined;
let disposed = false;
let selectionRevision = 0;
const activeStates = ["preparing", "recording", "transcribing", "stopping"];
const busy = computed(() => !!status.value?.active_id || pending.value);
const modelLoading = computed(() => status.value?.model.status === "loading");
const currentActive = computed(
  () => !!selected.value && activeStates.includes(selected.value.status),
);
const segments = computed(() => selected.value?.segments ?? []);
const reviewCount = computed(
  () => segments.value.filter((segment) => segment.review).length,
);
const characterCount = computed(() =>
  segments.value.reduce(
    (sum, segment) => sum + segment.text.replace(/\s/g, "").length,
    0,
  ),
);
const markdown = computed(() =>
  selected.value ? toMarkdown(selected.value) : "",
);
const lag = computed(() =>
  Math.max(
    0,
    (selected.value?.duration ?? 0) - (selected.value?.processed ?? 0),
  ),
);
const statusNames: Record<string, string> = {
  preparing: "准备中",
  recording: "正在聆听",
  transcribing: "正在转写",
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
  { value: "zh", label: "中文（可包含英文术语）" },
  { value: "en", label: "英语" },
  { value: "auto", label: "自动检测语言" },
];
const modelLabel = computed(() =>
  status.value?.model.status === "ready"
    ? "模型已就绪"
    : modelLoading.value
      ? "模型加载中"
      : status.value?.model.status === "error"
        ? "模型加载失败"
        : "模型尚未加载",
);

function dateLabel(value: string) {
  return new Date(value).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

async function refresh() {
  try {
    const [health, history] = await Promise.all([
      api<Status>("/status"),
      api<Session[]>("/sessions"),
    ]);
    if (disposed) return;
    status.value = health;
    sessions.value = history;
    connected.value = true;
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
    autoScroll.value = true;
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
  settings.title = "";
  tab.value = "transcript";
}

function options(): Settings {
  return {
    ...settings,
    title:
      settings.title.trim() ||
      `学习记录 ${new Date().toLocaleDateString("zh-CN")}`,
  };
}

function showError(error: unknown) {
  message.error(error instanceof Error ? error.message : "操作失败，请重试");
}

async function start() {
  pending.value = true;
  try {
    selected.value = await api<Session>("/sessions", {
      method: "POST",
      body: JSON.stringify(options()),
    });
    ++selectionRevision;
    status.value!.active_id = selected.value.id;
    tab.value = "transcript";
    editingId.value = null;
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
      { method: "POST" },
    );
    if (selected.value?.id === detail.id) selected.value = detail;
    message.info("已停止采集，正在保存并转写剩余音频");
  } catch (error) {
    showError(error);
  } finally {
    pending.value = false;
  }
}

async function upload(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  if (file.size > 2 * 1024 ** 3) {
    message.error("文件不能超过 2GB");
    input.value = "";
    return;
  }
  pending.value = true;
  const body = new FormData();
  body.append("file", file);
  body.append(
    "settings",
    JSON.stringify({
      ...options(),
      title: settings.title.trim() || file.name.replace(/\.[^.]+$/, ""),
    }),
  );
  try {
    selected.value = await api<Session>("/sessions/upload", {
      method: "POST",
      body,
    });
    ++selectionRevision;
    status.value!.active_id = selected.value.id;
    tab.value = "transcript";
    editingId.value = null;
  } catch (error) {
    showError(error);
  } finally {
    pending.value = false;
    input.value = "";
  }
}

async function prepareModel() {
  pending.value = true;
  try {
    await api("/model/prepare", {
      method: "POST",
      body: JSON.stringify(options()),
    });
    message.info("开始加载模型，首次下载请保持网络连接");
  } catch (error) {
    showError(error);
  } finally {
    pending.value = false;
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

async function seek(seconds: number) {
  if (!player.value || currentActive.value) return;
  player.value.currentTime = seconds;
  try {
    await player.value.play();
  } catch {
    message.info("请点击播放器开始回听");
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
    await navigator.clipboard.writeText(markdown.value);
    message.success("Markdown 已复制");
  } catch {
    message.error("复制失败，请使用导出 Markdown");
  }
}

onMounted(refresh);
onUnmounted(() => {
  disposed = true;
  clearTimeout(timer);
});
</script>

<template>
  <a-config-provider
    :locale="zhCN"
    :theme="{
      algorithm: theme.defaultAlgorithm,
      token: {
        colorPrimary: '#4263eb',
        borderRadius: 9,
        fontFamily:
          'Inter, -apple-system, BlinkMacSystemFont, PingFang SC, sans-serif',
        colorText: '#243047',
        colorBorder: '#e5e9f0',
        controlHeight: 38,
      },
    }"
  >
    <div class="app-shell">
      <aside class="sidebar">
        <a class="brand" href="/" @click.prevent="newSession"
          ><span class="brand-icon"><i></i><i></i><i></i><i></i></span
          ><span>Parrot<span class="brand-dot">.</span></span></a
        >
        <div class="sidebar-caption">让知识留下来</div>
        <a-button type="primary" block class="new-button" @click="newSession"
          ><PlusOutlined /> 新建学习记录</a-button
        >
        <div class="sidebar-section">
          <span>学习记录</span><span class="count">{{ sessions.length }}</span>
        </div>
        <div class="session-list">
          <a-spin v-if="initialLoading" class="sidebar-spinner" />
          <div v-else-if="!sessions.length" class="history-empty">
            <FolderOpenOutlined />
            <p>还没有学习记录</p>
            <span>从一次聆听开始吧</span>
          </div>
          <button
            v-for="item in sessions"
            :key="item.id"
            class="session-item"
            :class="{ selected: selected?.id === item.id }"
            @click="selectSession(item.id)"
          >
            <span class="session-icon"><FileTextOutlined /></span>
            <span class="session-info"
              ><strong>{{ item.title }}</strong
              ><small
                >{{ dateLabel(item.created_at) }}<span>·</span
                >{{ time(item.duration) }}</small
              ></span
            >
            <span
              v-if="activeStates.includes(item.status)"
              class="live-dot"
            ></span>
          </button>
        </div>
        <div class="local-note">
          <SafetyCertificateOutlined />
          <div>
            <strong>在你的电脑上处理</strong><span>录音与笔记保存在本地</span>
          </div>
        </div>
        <button class="sidebar-help" @click="helpOpen = true">
          <InfoCircleOutlined /> 使用指南 <ArrowRightOutlined />
        </button>
      </aside>

      <div class="main-shell">
        <header class="topbar">
          <div>
            <span class="breadcrumb">工作空间</span><span class="slash">/</span
            ><span>{{ selected ? "学习记录" : "新建记录" }}</span>
          </div>
          <div class="connection">
            <span class="connection-dot" :class="{ online: connected }"></span
            >{{ connected ? "本地服务已连接" : "正在连接本地服务"
            }}<span class="local-badge">LOCAL</span>
          </div>
        </header>
        <main>
          <div class="page-heading">
            <div>
              <div class="eyebrow">LISTEN. CAPTURE. LEARN.</div>
              <h1>{{ selected?.title || "听见，记下来。" }}</h1>
              <p>
                {{
                  selected
                    ? `${dateLabel(selected.created_at)} · ${selected.source === "system" ? "系统音频" : "导入文件"} · Whisper ${selected.model}`
                    : "把视频里的每一个知识点，变成可以反复阅读的文字。"
                }}
              </p>
            </div>
            <a-button
              v-if="selected"
              class="export-top"
              :href="`/api/sessions/${selected.id}/markdown`"
              :disabled="!segments.length"
              ><DownloadOutlined /> 导出 Markdown</a-button
            ><span v-else class="version-badge"
              >学习工作台 <span>01</span></span
            >
          </div>

          <a-alert
            v-if="networkError"
            type="error"
            show-icon
            class="notice"
            message="无法连接本地服务"
            :description="'请确认后端已启动。' + networkError"
          />
          <a-alert
            v-if="selected?.error"
            type="error"
            show-icon
            class="notice"
            :message="statusNames[selected.status]"
            :description="selected.error"
          />
          <a-alert
            v-if="status?.active_id && selected?.id !== status.active_id"
            type="info"
            show-icon
            class="notice"
            message="另一个学习记录正在转写"
            ><template #action
              ><a-button size="small" @click="selectSession(status!.active_id!)"
                >查看正在转写的记录</a-button
              ></template
            ></a-alert
          >

          <div class="workspace-grid">
            <div class="workspace-main">
              <section class="capture-card">
                <div class="capture-top">
                  <span class="source-icon"><SoundOutlined /></span>
                  <div>
                    <h2>
                      {{
                        selected?.source === "file"
                          ? "文件转写"
                          : "系统音频转写"
                      }}
                    </h2>
                    <p>
                      {{
                        currentActive
                          ? selected?.status === "preparing"
                            ? "加载模型后开始采集，请等待状态变为「正在聆听」"
                            : selected?.status === "stopping"
                              ? "采集已停止，剩余音频转写完成后即可回听"
                              : "音频已保存在本地，识别结果会陆续显示"
                          : "直接捕捉电脑正在播放的声音，戴耳机也没关系"
                      }}
                    </p>
                  </div>
                  <a-tag
                    v-if="selected"
                    :color="
                      currentActive
                        ? 'blue'
                        : selected.status === 'completed'
                          ? 'green'
                          : 'default'
                    "
                    class="status-tag"
                    ><span
                      v-if="selected.status === 'recording'"
                      class="live-dot"
                    ></span
                    >{{ statusNames[selected.status] }}</a-tag
                  ><span v-else class="ready-label">准备好开始学习</span>
                </div>
                <div class="capture-body">
                  <div
                    class="audio-visual"
                    :class="{ listening: selected?.status === 'recording' }"
                  >
                    <span
                      v-for="i in 43"
                      :key="i"
                      :style="{
                        height: `${currentActive && selected?.status === 'recording' ? 5 + (status?.level || 0) * (15 + ((i * 17) % 44)) : 4 + ((i * 13) % 17)}px`,
                        opacity: currentActive
                          ? 0.45 + (i % 4) * 0.15
                          : 0.25 + (i % 5) * 0.12,
                      }"
                    ></span>
                  </div>
                  <div class="timer">
                    {{ time(selected?.duration)
                    }}<span>{{
                      selected ? "已记录音频" : "等待声音输入"
                    }}</span>
                  </div>
                </div>
                <div class="capture-actions">
                  <div class="action-buttons">
                    <a-button
                      v-if="!currentActive"
                      type="primary"
                      size="large"
                      :loading="pending"
                      :disabled="
                        !connected ||
                        busy ||
                        modelLoading ||
                        !status?.capture_available
                      "
                      @click="start"
                      ><AudioOutlined /> 开始聆听</a-button
                    ><a-button
                      v-else
                      danger
                      size="large"
                      :loading="pending || selected?.status === 'stopping'"
                      :disabled="selected?.status === 'stopping'"
                      @click="stop"
                      ><StopOutlined /> 停止{{
                        selected?.source === "file" ? "转写" : "聆听"
                      }}</a-button
                    ><a-button
                      size="large"
                      :disabled="!connected || busy || modelLoading"
                      @click="fileInput?.click()"
                      ><UploadOutlined /> 导入音频 / 视频</a-button
                    ><input
                      ref="fileInput"
                      type="file"
                      accept="audio/*,video/*,.mkv,.flac,.m4a,.webm"
                      class="hidden-input"
                      @change="upload"
                    />
                  </div>
                  <span class="capture-hint">{{
                    currentActive ? `待处理约 ${time(lag)}` : "音频不上传云端"
                  }}</span>
                </div>
              </section>

              <section class="transcript-card">
                <div class="transcript-header">
                  <a-tabs v-model:activeKey="tab" class="document-tabs"
                    ><a-tab-pane key="transcript"
                      ><template #tab
                        ><FileTextOutlined /> 转写内容
                        <span v-if="segments.length" class="tab-count">{{
                          segments.length
                        }}</span></template
                      ></a-tab-pane
                    ><a-tab-pane key="markdown"
                      ><template #tab
                        ><CodeOutlined /> Markdown</template
                      ></a-tab-pane
                    ></a-tabs
                  ><span v-if="segments.length" class="word-count"
                    >{{ characterCount.toLocaleString() }} 字符<span
                      v-if="reviewCount"
                    >
                      · {{ reviewCount }} 段待校对</span
                    ></span
                  ><span v-else class="word-count">你的知识，从这里沉淀</span>
                </div>
                <div v-if="!segments.length" class="empty-transcript">
                  <div class="empty-icon">
                    <FileTextOutlined /><span><SoundOutlined /></span>
                  </div>
                  <h3>
                    {{
                      currentActive
                        ? selected?.status === "preparing"
                          ? "正在准备识别模型"
                          : "正在把声音变成文字"
                        : selected
                          ? "这条记录还没有转写内容"
                          : "让听过的知识，有迹可循"
                    }}
                  </h3>
                  <p>
                    {{
                      currentActive
                        ? selected?.status === "preparing"
                          ? "首次使用需要下载模型，下载进度可在后端终端查看。"
                          : "通常积累 8–24 秒音频后输出一批文字，实际延迟取决于设备。"
                        : selected
                          ? "可以检查录音与错误提示，或重新导入音频进行转写。"
                          : "开始聆听，或导入一个音频 / 视频文件。\n带时间戳的文字会自动出现在这里。"
                    }}
                  </p>
                  <div v-if="currentActive" class="working-label">
                    <LoadingOutlined /> {{ statusNames[selected!.status] }}
                  </div>
                  <div v-else-if="!selected" class="empty-steps">
                    <span><b>1</b> 播放课程</span><ArrowRightOutlined /><span
                      ><b>2</b> 自动转写</span
                    ><ArrowRightOutlined /><span><b>3</b> 导出笔记</span>
                  </div>
                </div>
                <template v-else
                  ><div
                    v-if="tab === 'transcript'"
                    ref="transcript"
                    class="transcript-scroll"
                  >
                    <article
                      v-for="segment in segments"
                      :key="segment.id"
                      class="transcript-segment"
                      :class="{ 'needs-review': segment.review }"
                    >
                      <button
                        class="timestamp"
                        :disabled="currentActive"
                        :title="
                          currentActive
                            ? '结束后可点击时间戳回听'
                            : '点击回听这一段'
                        "
                        @click="seek(segment.start)"
                      >
                        {{ time(segment.start) }}
                      </button>
                      <div class="segment-content">
                        <template v-if="editingId === segment.id"
                          ><a-textarea
                            v-model:value="editText"
                            :auto-size="{ minRows: 2, maxRows: 12 }"
                            aria-label="校对转写文字"
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
                            >建议回听校对</span
                          ></template
                        >
                      </div>
                      <a-button
                        v-if="editingId !== segment.id"
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
                      <span>MARKDOWN · UTF-8</span
                      ><a-button type="text" size="small" @click="copyMarkdown"
                        ><CopyOutlined /> 复制全文</a-button
                      >
                    </div>
                    <pre>{{ markdown }}</pre>
                  </div>
                  <div class="transcript-footer">
                    <span><span class="saved-dot"></span> 自动保存在本地</span
                    ><a-checkbox
                      v-if="currentActive"
                      v-model:checked="autoScroll"
                      >跟随最新内容</a-checkbox
                    ><a-button
                      v-else
                      type="text"
                      size="small"
                      @click="scrollToLatest"
                      ><ArrowDownOutlined /> 到最后</a-button
                    >
                  </div></template
                >
              </section>
              <div
                v-if="selected && !currentActive && selected.duration > 0"
                class="playback-card"
              >
                <div>
                  <SoundOutlined /><span>原始录音</span
                  ><small>点击文字时间戳回听</small>
                </div>
                <audio
                  :key="selected.id"
                  ref="player"
                  :src="`/api/sessions/${selected.id}/audio`"
                  controls
                  preload="metadata"
                ></audio>
              </div>
              <div class="workspace-footnote">
                <SafetyCertificateOutlined />
                本地识别，本地保存。把注意力留给学习本身。<span
                  >Parrot v0.1</span
                >
              </div>
            </div>

            <aside class="settings-column">
              <section class="settings-card">
                <div class="section-title">
                  <h2>识别设置</h2>
                  <span>为下一次记录设置</span>
                </div>
                <a-form layout="vertical" :disabled="busy || modelLoading"
                  ><a-form-item label="笔记名称"
                    ><a-input
                      v-model:value="settings.title"
                      :maxlength="120"
                      placeholder="例如：深度学习 · 第一课" /></a-form-item
                  ><a-form-item label="音频语言"
                    ><a-select
                      v-model:value="settings.language"
                      :options="languageOptions" /></a-form-item
                  ><a-form-item label="识别模型"
                    ><a-select
                      v-model:value="settings.model"
                      :options="modelOptions"
                    />
                    <div class="field-help">
                      {{
                        settings.model === "large-v3"
                          ? "优先保留识别质量，适合课程与专业内容。"
                          : settings.model === "large-v3-turbo"
                            ? "识别更快，复杂术语建议回听校对。"
                            : "资源占用较低，准确率通常低于大模型。"
                      }}
                    </div></a-form-item
                  ><a-form-item label="专业术语"
                    ><a-textarea
                      v-model:value="settings.vocabulary"
                      placeholder="例如：FastAPI、梯度下降、Transformer"
                      :maxlength="1000"
                      :auto-size="{ minRows: 3, maxRows: 5 }"
                    />
                    <div class="field-help">
                      提前填入人名、专有名词，帮助模型识别。
                    </div></a-form-item
                  ><a-collapse ghost class="advanced-settings"
                    ><a-collapse-panel key="engine" header="高级设置"
                      ><a-form-item label="推理引擎"
                        ><a-select
                          v-model:value="settings.engine"
                          :options="[
                            { value: 'auto', label: '自动选择（推荐）' },
                            { value: 'mlx', label: 'MLX · Apple 芯片' },
                            {
                              value: 'faster-whisper',
                              label: 'faster-whisper · CPU',
                            },
                          ]" /></a-form-item></a-collapse-panel></a-collapse
                ></a-form>
                <div class="model-state">
                  <span
                    class="model-state-dot"
                    :class="{
                      loaded: status?.model.status === 'ready',
                      error: status?.model.status === 'error',
                    }"
                  ></span
                  ><span>{{ modelLabel }}</span
                  ><span v-if="status?.model.engine" class="engine-label">{{
                    status.model.engine === "mlx" ? "MLX" : "CPU"
                  }}</span>
                </div>
                <a-button
                  block
                  :disabled="!connected || busy || modelLoading"
                  :loading="modelLoading"
                  @click="prepareModel"
                  ><CloudDownloadOutlined /> 加载所选模型</a-button
                >
                <p v-if="status?.model.status === 'error'" class="model-error">
                  {{ status.model.message }}
                </p>
                <p v-else class="download-hint">
                  首次使用会下载模型。Large v3 约 3GB，下载完成后可离线识别。
                </p>
              </section>
              <section class="tip-card">
                <div class="tip-title"><span>✦</span> 一点学习小提示</div>
                <p>先专注听懂，再回头阅读。<br />转写稿会替你留住细节。</p>
                <div class="tip-divider"></div>
                <p class="tip-small">
                  识别完成后，可直接校对文字、回听原音，再导出为
                  Markdown，放进你的知识库。
                </p>
              </section>
              <button class="permission-help" @click="helpOpen = true">
                <InfoCircleOutlined /> 首次使用？查看声音采集设置
                <ArrowRightOutlined />
              </button>
            </aside>
          </div>
        </main>
      </div>
    </div>
    <a-modal
      v-model:open="helpOpen"
      title="开始你的第一次学习记录"
      :footer="null"
      width="580px"
      ><div class="help-content">
        <h3>1. 允许系统声音采集</h3>
        <p>
          当前系统声音采集支持 macOS 13
          及以上。首次点击「开始聆听」后，按系统提示授权。在「系统设置 →
          隐私与安全性 →
          屏幕与系统音频录制」中允许启动后端的终端或应用，必要时重启该应用和后端。程序只保存音频，不保存屏幕画面。
        </p>
        <h3>2. 等待模型和采集就绪</h3>
        <p>
          首次使用需要联网下载模型，可先点击「加载所选模型」。看到「正在聆听」后，再播放课程或视频。戴耳机也可以采集，不需要打开麦克风。
        </p>
        <h3>3. 停止、校对、导出</h3>
        <p>
          点击停止后等待剩余音频转写完成。点击时间戳回听，点击段落旁的编辑按钮校对。右上角可导出
          Markdown。
        </p>
        <a-alert
          type="info"
          show-icon
          message="准确率与延迟说明"
          description="Whisper 是分段识别，通常积累 8–24 秒音频后输出文字。大模型在较慢设备上可能积压。口音、背景音乐和专业术语仍可能识别错误，建议先用一小段实际课程验证效果。"
        />
        <p class="help-last">
          其他系统、受保护的媒体或无法授权时，可使用「导入音频 /
          视频」。录音和笔记保存在项目的 .data 文件夹。
        </p>
      </div></a-modal
    >
  </a-config-provider>
</template>
