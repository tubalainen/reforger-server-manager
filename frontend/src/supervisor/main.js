import 'bootstrap/dist/css/bootstrap.min.css'
import '../styles.css'
import { createApp } from 'vue'
import SupervisorApp from './SupervisorApp.vue'

// The Server Supervisor's page (#204): a second entry of the same build, served
// by supervisor.main instead of the manager.
createApp(SupervisorApp).mount('#app')
