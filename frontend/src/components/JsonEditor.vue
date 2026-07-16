<script setup>
// CodeMirror 6 wrapped as a v-model'd textarea replacement, so the wizard never
// has to know CodeMirror exists (#29).
//
// Chosen over Monaco: ~130 KB gzipped against several MB, no web-worker plumbing
// to fight with under Vite, and it bundles offline — the Docker image serves the
// built assets with no CDN reachable.
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { EditorState } from '@codemirror/state'
import { EditorView, keymap, lineNumbers, highlightActiveLine } from '@codemirror/view'
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands'
import {
  bracketMatching,
  foldGutter,
  foldKeymap,
  indentOnInput,
  indentUnit,
  syntaxHighlighting,
  defaultHighlightStyle,
} from '@codemirror/language'
import { closeBrackets, closeBracketsKeymap } from '@codemirror/autocomplete'
import { json, jsonParseLinter } from '@codemirror/lang-json'
import { lintGutter, linter } from '@codemirror/lint'
import { oneDark } from '@codemirror/theme-one-dark'
import { indentationMarkers } from '@replit/codemirror-indentation-markers'

const props = defineProps({
  modelValue: { type: String, default: '' },
  readonly: { type: Boolean, default: false },
  maxHeight: { type: String, default: '70vh' },
})
const emit = defineEmits(['update:modelValue'])

const host = ref(null)
let view = null

function extensions() {
  return [
    lineNumbers(),
    foldGutter(),
    lintGutter(),
    history(),
    highlightActiveLine(),
    bracketMatching(),
    closeBrackets(),
    indentOnInput(),
    syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
    json(),
    // Inline squiggles on malformed JSON, live as you type.
    linter(jsonParseLinter()),
    // The "shows indentation clearly" half of #29: vertical guides down each
    // nesting level, at the same 2 spaces json.dumps(indent=2) writes.
    indentationMarkers({ hideFirstIndent: true, highlightActiveBlock: true }),
    indentUnit.of('  '),
    keymap.of([
      ...closeBracketsKeymap,
      ...defaultKeymap,
      ...historyKeymap,
      ...foldKeymap,
      indentWithTab,
    ]),
    oneDark,
    EditorView.theme({
      '&': { fontSize: '0.8125rem', maxHeight: props.maxHeight },
      '.cm-scroller': { overflow: 'auto', fontFamily: 'var(--bs-font-monospace)' },
      '&.cm-focused': { outline: 'none' },
    }),
    EditorView.editable.of(!props.readonly),
    EditorState.readOnly.of(props.readonly),
    EditorView.updateListener.of((u) => {
      if (u.docChanged) emit('update:modelValue', u.state.doc.toString())
    }),
  ]
}

onMounted(() => {
  view = new EditorView({
    state: EditorState.create({ doc: props.modelValue, extensions: extensions() }),
    parent: host.value,
  })
})

onBeforeUnmount(() => view?.destroy())

// Only push a parent change in when it isn't just our own edit echoing back —
// dispatching on every keystroke would fight the cursor.
watch(
  () => props.modelValue,
  (value) => {
    if (!view || value === view.state.doc.toString()) return
    view.dispatch({
      changes: { from: 0, to: view.state.doc.length, insert: value },
    })
  },
)

watch(
  () => props.readonly,
  () => {
    if (!view) return
    view.dispatch({
      effects: EditorState.reconfigure.of(extensions()),
    })
  },
)
</script>

<template>
  <div ref="host" class="rsm-json-editor"></div>
</template>

<style scoped>
.rsm-json-editor :deep(.cm-editor) {
  border-radius: 0 0 var(--bs-border-radius) var(--bs-border-radius);
}
</style>
