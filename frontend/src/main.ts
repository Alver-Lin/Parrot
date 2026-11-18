import { createApp } from "vue";
import {
  Alert,
  Button,
  Checkbox,
  Collapse,
  ConfigProvider,
  Form,
  Input,
  Modal,
  Select,
  Spin,
  Tabs,
  Tag,
} from "ant-design-vue";
import "ant-design-vue/dist/reset.css";
import App from "./App.vue";
import "./style.css";

const app = createApp(App);
for (const component of [
  Alert,
  Button,
  Checkbox,
  Collapse,
  ConfigProvider,
  Form,
  Input,
  Modal,
  Select,
  Spin,
  Tabs,
  Tag,
]) {
  app.use(component);
}
app.mount("#app");
