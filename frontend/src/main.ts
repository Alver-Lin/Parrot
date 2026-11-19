import { createApp } from "vue";
import {
  Alert,
  Button,
  Switch,
  Collapse,
  ConfigProvider,
  Form,
  Input,
  Modal,
  Select,
  Spin,
  Tabs,
  Tag,
  Tooltip,
} from "ant-design-vue";
import "ant-design-vue/dist/reset.css";
import App from "./App.vue";
import "./style.css";

const app = createApp(App);
for (const component of [
  Alert,
  Button,
  Switch,
  Collapse,
  ConfigProvider,
  Form,
  Input,
  Modal,
  Select,
  Spin,
  Tabs,
  Tag,
  Tooltip,
]) {
  app.use(component);
}
app.mount("#app");
