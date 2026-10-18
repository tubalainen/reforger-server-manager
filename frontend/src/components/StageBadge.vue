<script setup>
// A template's or mod list's release stage (#223): "Prod v3", "Dev", "Test".
import { computed } from 'vue'
import { stageInfo } from '../stages'

const props = defineProps({
  stage: { type: String, default: '' },
  version: { type: Number, default: null }, // the Prod version, shown after "Prod"
})

const info = computed(() => stageInfo(props.stage))
const text = computed(() =>
  props.stage === 'prod' && props.version ? `${info.value.label} v${props.version}` : info.value.label,
)
</script>

<template>
  <span v-if="stage" class="badge" :class="info.cls" :title="info.hint">{{ text }}</span>
</template>
