<script setup>
// A read-only, searchable change log (#112, #166). The same modal serves server
// templates and mod templates — they differ only in which endpoint they read, so
// the caller passes the URL and this owns everything else: loading, grouping by
// save event, the search box, and the "this can't be edited" promise.
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
import { categoryBadge as catBadge, filterEntries, groupEntries } from '../changelog'

const props = defineProps({
  url: { type: String, required: true },   // e.g. /api/templates/7/changelog
  subtitle: { type: String, default: '' }, // the thing's name, shown under the title
})
defineEmits(['close'])

const entries = ref([])
const loading = ref(true)
const error = ref('')
const search = ref('')

// Client-side search: the log is already loaded, so filtering is instant. Lines
// from one save are grouped under its timestamp, with its comment (#223) on top.
const groups = computed(() => groupEntries(filterEntries(entries.value, search.value)))

onMounted(async () => {
  try {
    entries.value = await api(props.url)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="modal d-block" tabindex="-1" style="background: rgba(0,0,0,.5)">
    <div class="modal-dialog modal-lg modal-dialog-scrollable">
      <div class="modal-content">
        <div class="modal-header">
          <div>
            <h5 class="modal-title mb-0">Change log</h5>
            <div class="small text-secondary">{{ subtitle }}</div>
          </div>
          <button type="button" class="btn-close" @click="$emit('close')"></button>
        </div>
        <div class="modal-body">
          <input
            v-model="search"
            class="form-control form-control-sm mb-3"
            placeholder="Search the change log…"
            autocomplete="off"
          />
          <div v-if="error" class="alert alert-warning py-2">{{ error }}</div>
          <div v-else-if="loading" class="text-secondary">Loading…</div>
          <div v-else-if="!entries.length" class="text-secondary">
            No changes recorded yet.
          </div>
          <div v-else-if="!groups.length" class="text-secondary">
            Nothing matches “{{ search }}”.
          </div>
          <div v-else class="d-flex flex-column gap-3">
            <div v-for="g in groups" :key="g.key">
              <div class="small text-secondary border-bottom pb-1 mb-2">{{ g.display }}</div>
              <p v-for="(note, i) in g.notes" :key="i" class="rsm-note mb-2">“{{ note }}”</p>
              <ul class="list-unstyled mb-0 d-flex flex-column gap-1">
                <li v-for="e in g.items" :key="e.id" class="d-flex align-items-start gap-2">
                  <span class="badge mt-1" :class="catBadge(e.category).cls" style="min-width: 4.5rem">
                    {{ catBadge(e.category).label }}
                  </span>
                  <span>{{ e.summary }}</span>
                </li>
              </ul>
            </div>
          </div>
        </div>
        <div class="modal-footer">
          <span class="small text-secondary me-auto">This log can't be edited or deleted.</span>
          <button class="btn btn-outline-secondary" @click="$emit('close')">Close</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.rsm-note {
  font-style: italic;
  border-left: 3px solid var(--bs-border-color);
  padding-left: 0.5rem;
}
</style>
