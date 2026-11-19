<script setup lang="ts">
import { onUnmounted, ref, watch } from "vue";
import { desktopHeaders } from "./desktop";

const props = defineProps<{ sessionId: string; filename: string; alt: string }>();
const source = ref("");
const failed = ref(false);
let controller: AbortController | undefined;

async function loadPhoto() {
  controller?.abort();
  const request = new AbortController();
  controller = request;
  failed.value = false;
  if (source.value) URL.revokeObjectURL(source.value);
  source.value = "";
  try {
    const response = await fetch(`/api/sessions/${props.sessionId}/photo/${props.filename}`, {
      headers: desktopHeaders(), signal: request.signal,
    });
    if (!response.ok) throw new Error("图片无法读取");
    const blob = await response.blob();
    if (!request.signal.aborted) source.value = URL.createObjectURL(blob);
  } catch {
    if (!request.signal.aborted) failed.value = true;
  }
}

watch(() => [props.sessionId, props.filename], loadPhoto, { immediate: true });
onUnmounted(() => {
  controller?.abort();
  if (source.value) URL.revokeObjectURL(source.value);
});
</script>

<template>
  <div class="transcript-photo">
    <a-image v-if="source" :src="source" :alt="alt" />
    <button v-else-if="failed" class="photo-retry" @click="loadPhoto">截图加载失败，点击重试</button>
    <span v-else class="photo-loading">截图加载中…</span>
  </div>
</template>
