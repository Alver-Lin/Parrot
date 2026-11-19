<script setup lang="ts">
import { computed } from "vue";
import { marked, Renderer } from "marked";
import TranscriptPhoto from "./TranscriptPhoto.vue";

const props = defineProps<{ content: string; sessionId: string }>();

type PreviewPart =
  | { type: "html"; html: string }
  | { type: "image"; alt: string; filename: string };

const IMAGE_RE = /!\[([^\]]*)\]\((photo\/[A-Za-z0-9._-]+)\)/g;

const renderer = new Renderer();
renderer.image = () => "";
renderer.link = ({ href, text }) => {
  const safe =
    /^(https?:|mailto:)/i.test(href) && !/[\s"<>]/.test(href);
  const label = text || href;
  if (!safe) return label;
  return `<a href="${href}" target="_blank" rel="noreferrer noopener">${label}</a>`;
};

marked.setOptions({
  gfm: true,
  breaks: true,
  renderer,
});

function escapeRawHtml(source: string) {
  return source.replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function renderHtml(source: string) {
  return marked.parse(escapeRawHtml(source), { async: false }) as string;
}

const parts = computed<PreviewPart[]>(() => {
  const source = props.content;
  const result: PreviewPart[] = [];
  let last = 0;
  for (const match of source.matchAll(IMAGE_RE)) {
    const index = match.index ?? 0;
    if (index > last) {
      result.push({ type: "html", html: renderHtml(source.slice(last, index)) });
    }
    result.push({
      type: "image",
      alt: match[1] || "截图",
      filename: match[2].slice("photo/".length),
    });
    last = index + match[0].length;
  }
  if (last < source.length || result.length === 0) {
    result.push({ type: "html", html: renderHtml(source.slice(last)) });
  }
  return result;
});
</script>

<template>
  <div class="markdown-preview" aria-label="Markdown 预览">
    <template v-for="(part, index) in parts" :key="index">
      <div v-if="part.type === 'html'" class="markdown-preview-html" v-html="part.html"></div>
      <TranscriptPhoto
        v-else
        :session-id="sessionId"
        :filename="part.filename"
        :alt="part.alt"
      />
    </template>
  </div>
</template>
