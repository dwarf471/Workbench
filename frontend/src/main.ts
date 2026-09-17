import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import '@fontsource-variable/inter/wght.css'
import '@fontsource-variable/noto-sans-sc'
import App from './App.vue'
import './style.css'
import './workspace-theme.css'
createApp(App).use(ElementPlus).mount('#app')
