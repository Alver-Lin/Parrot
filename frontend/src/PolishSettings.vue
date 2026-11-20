<script setup lang="ts">
import { onMounted } from "vue";
import { CloseOutlined, EyeOutlined, EyeInvisibleOutlined, LoadingOutlined } from "@ant-design/icons-vue";
import { usePolishSettings } from "./usePolishSettings";

defineProps<{ statusOnly?: boolean }>();
const { isDesktop, loading, testing, generating, keyVisible, error, saved, draft, busy, keyAvailable, dirty, ready,
  label, description, connectionState, connectionLabel, initialize, testConnection, openConfiguration } = usePolishSettings();
onMounted(() => { void initialize(); });
</script>

<template>
  <div v-if="statusOnly" class="readiness-row polish-model-status"
    :class="{ 'is-ready': ready, 'is-error': !ready && !loading && !testing && !generating }" role="status">
    <div class="readiness-copy">
      <strong>
        <span v-if="ready" class="status-dot" aria-hidden="true"></span>
        <LoadingOutlined v-else-if="loading || testing || generating" class="status-pending" />
        <CloseOutlined v-else class="status-cross" />
        {{ label }}
      </strong>
      <p>{{ description }}</p>
    </div>
    <a-button v-if="!ready" type="link" class="readiness-action" :disabled="!isDesktop || busy" :loading="testing"
      @click="saved.configured && !dirty ? testConnection() : openConfiguration()">
      {{ saved.configured && !dirty ? '测试连接' : '前往配置' }}
    </a-button>
  </div>
  <section v-else id="polish-configuration" class="polish-configuration" aria-label="AI 润色配置">
    <h3>AI 润色 <span>OpenAI 兼容格式</span></h3>
    <a-form layout="vertical" :disabled="busy || !isDesktop">
      <a-form-item label="API 地址（Base URL）">
        <a-input id="polish-base-url" v-model:value="draft.base_url" aria-label="润色 API 地址"
          placeholder="https://api.openai.com/v1" :maxlength="2048" autocomplete="off" :spellcheck="false" />
      </a-form-item>
      <div class="settings-pair polish-fields">
        <a-form-item label="API Key">
          <a-input id="polish-api-key" v-model:value="draft.api_key" aria-label="润色 API Key"
            :type="keyVisible ? 'text' : 'password'"
            :maxlength="4096" autocomplete="new-password" :spellcheck="false"
            placeholder="请输入 API Key">
            <template #suffix>
              <button type="button" class="polish-key-visibility"
                :aria-label="keyVisible ? '隐藏 API Key' : '显示 API Key'" :aria-pressed="keyVisible"
                @mousedown.prevent @click="keyVisible = !keyVisible">
                <EyeInvisibleOutlined v-if="keyVisible" />
                <EyeOutlined v-else />
              </button>
            </template>
          </a-input>
          <span v-if="keyAvailable" class="field-help polish-key-saved">{{ dirty ? '修改后点击测试连接即可保存' : 'Key 已保存' }}</span>
        </a-form-item>
        <a-form-item label="模型名称">
          <a-input id="polish-model" v-model:value="draft.model" aria-label="润色模型名称"
            placeholder="填写服务商的模型 ID" :maxlength="200" autocomplete="off" :spellcheck="false" />
        </a-form-item>
      </div>
      <div class="polish-actions">
        <span class="polish-connection-status" :class="connectionState" role="status">
          <LoadingOutlined v-if="testing || loading" />
          <span v-else-if="connectionState === 'connected'" class="status-dot" aria-hidden="true"></span>
          <CloseOutlined v-else-if="connectionState === 'failed'" class="status-cross" />
          {{ connectionLabel }}
        </span>
        <a-button size="small" type="primary" aria-label="测试 AI 连接" :loading="testing"
          :disabled="!isDesktop || busy || !draft.base_url.trim() || !draft.model.trim()"
          @click="testConnection">测试连接</a-button>
      </div>
    </a-form>
    <a-alert v-if="error" class="polish-error" type="error" show-icon :message="error" />
  </section>
</template>
