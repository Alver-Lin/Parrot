import { computed, reactive, ref, watch } from "vue";
import { message } from "ant-design-vue";
import { inDesktop, loadPolishSettings, revealPolishKey, savePolishSettings, testPolishConnection, type PolishSettings } from "./desktop";

const isDesktop = inDesktop();
const loading = ref(true);
const testing = ref(false);
const generating = ref(false);
const keyVisible = ref(false);
const error = ref("");
const saved = ref<PolishSettings>({
  base_url: "", model: "", has_api_key: false, configured: false, status: "unconfigured", message: "",
});
const draft = reactive({ base_url: "", model: "", api_key: "" });
const busy = computed(() => loading.value || testing.value || generating.value);
let savedKey = "";
const keyAvailable = computed(() => saved.value.has_api_key
  && draft.base_url.trim() === saved.value.base_url);
const dirty = computed(() => draft.base_url.trim() !== saved.value.base_url
  || draft.model.trim() !== saved.value.model || (!!draft.api_key.trim() && draft.api_key.trim() !== savedKey));
const connectionState = computed(() => testing.value ? "testing" : loading.value ? "loading"
  : error.value ? "failed" : dirty.value ? "untested" : saved.value.status);
const connectionLabel = computed(() => connectionState.value === "testing" ? "连接中…"
  : connectionState.value === "loading" ? "读取中…"
    : connectionState.value === "connected" ? "连接成功"
      : connectionState.value === "failed" ? "连接失败" : "待测试");
const ready = computed(() => !busy.value && connectionState.value === "connected");
const label = computed(() => loading.value ? "正在读取润色配置"
  : generating.value ? "润色模型处理中"
  : testing.value ? "润色模型连接测试中"
    : connectionState.value === "failed" ? "润色模型连接失败"
      : dirty.value ? "润色模型配置已修改，待测试"
      : saved.value.status === "connected" ? "润色模型已连接"
          : saved.value.configured ? "润色模型待测试" : "润色模型未配置");
const description = computed(() => ready.value ? `${saved.value.model} · API 模型无需下载。`
  : testing.value ? "正在发送简短的连接测试请求。"
    : error.value || (saved.value.status === "failed" && !dirty.value ? saved.value.message : "")
      || "点击测试连接会自动保存当前配置。API 模型无需下载。");
let initialization: Promise<void> | undefined;

async function apply(settings: PolishSettings) {
  saved.value = settings;
  draft.base_url = settings.base_url;
  draft.model = settings.model;
  draft.api_key = "";
  savedKey = settings.has_api_key ? (await revealPolishKey(settings.base_url)).api_key : "";
  draft.api_key = savedKey;
}

function initialize() {
  if (initialization) return initialization;
  initialization = (async () => {
    try { await apply(await loadPolishSettings()); }
    catch (cause) { error.value = cause instanceof Error ? cause.message : "无法读取润色配置"; }
    finally { loading.value = false; }
  })();
  return initialization;
}

async function testConnection() {
  if (busy.value) return;
  testing.value = true;
  error.value = "";
  try {
    await apply(await savePolishSettings({ ...draft }));
    const result = await testPolishConnection();
    saved.value = result;
    if (result.status === "connected") message.success("连接成功，润色模型可访问");
  } catch (cause) { error.value = cause instanceof Error ? cause.message : "测试连接失败，请重试"; }
  finally { testing.value = false; }
}

function openConfiguration() {
  document.getElementById("polish-configuration")?.scrollIntoView({ block: "start", behavior: "smooth" });
  const id = !draft.base_url ? "polish-base-url" : !draft.model ? "polish-model" : "polish-api-key";
  document.getElementById(id)?.focus({ preventScroll: true });
}

watch(() => [draft.base_url, draft.model, draft.api_key], () => {
  if (!busy.value) error.value = "";
}, { flush: "sync" });

export function usePolishSettings() {
  return { isDesktop, loading, testing, generating, keyVisible, error, saved, draft, busy, keyAvailable, dirty, ready,
    label, description, connectionState, connectionLabel, initialize, testConnection, openConfiguration };
}
