<script setup>
// Move a template or mod list one stage up (#223). Test → Dev only asks for an
// optional note. Dev → Prod also asks where it goes: a new Prod item at v1, or
// the next version of a Prod item already there (by default the one it was
// copied from) — which uses this one up.
import { computed, reactive } from 'vue'
import { api } from '../api'
import { defaultReleaseTarget, releaseSummary, staleCopyWarning, versionLabel } from '../stages'

const props = defineProps({
  item: { type: Object, required: true },   // a row from the list page
  items: { type: Array, required: true },   // every row of the same kind
  base: { type: String, required: true },   // /api/templates | /api/mod-templates
  noun: { type: String, default: 'template' },
})
const emit = defineEmits(['close', 'done'])

const releasing = computed(() => props.item.stage === 'dev')
const prods = computed(() => props.items.filter((i) => i.stage === 'prod' && i.id !== props.item.id))

const startTarget = defaultReleaseTarget(props.item, props.items.filter((i) => i.stage === 'prod'))
const form = reactive({
  mode: startTarget != null ? 'into' : 'new',
  intoId: startTarget ?? prods.value[0]?.id ?? null,
  note: '',
  busy: false,
  error: '',
})

const target = computed(() =>
  form.mode === 'into' ? prods.value.find((p) => p.id === Number(form.intoId)) || null : null,
)
const warning = computed(() => staleCopyWarning(props.item, target.value))
const summary = computed(() => releaseSummary(props.item, target.value, props.noun))
const servers = computed(() => props.item.server_count || 0)

async function submit() {
  form.busy = true
  form.error = ''
  try {
    const body = { note: form.note }
    if (releasing.value && target.value) body.into_id = target.value.id
    const result = await api(`${props.base}/${props.item.id}/promote`, { method: 'POST', body })
    emit('done', result)
  } catch (e) {
    form.error = e.message
  } finally {
    form.busy = false
  }
}
</script>

<template>
  <div class="modal d-block" tabindex="-1" style="background: rgba(0,0,0,.5)">
    <div class="modal-dialog">
      <div class="modal-content">
        <div class="modal-header">
          <h5 class="modal-title">
            {{ releasing ? `Release "${item.name}" to Prod` : `Move "${item.name}" to Dev` }}
          </h5>
          <button type="button" class="btn-close" aria-label="Close" @click="emit('close')"></button>
        </div>
        <div class="modal-body">
          <div v-if="form.error" class="alert alert-danger py-2 small">{{ form.error }}</div>

          <p v-if="!releasing" class="small text-secondary">
            Dev is where it gets tested properly. It stays editable there, and every
            save asks for a comment for the change log. It can't go back to Test.
          </p>

          <template v-else>
            <p class="small text-secondary">
              In Prod it is locked: it can be copied and deleted, but not changed.
              Nothing can go back from Prod.
            </p>
            <div class="form-check">
              <input id="rel-new" v-model="form.mode" class="form-check-input" type="radio" value="new" />
              <label for="rel-new" class="form-check-label">As a new Prod {{ noun }}</label>
            </div>
            <div class="form-check mb-2">
              <input
                id="rel-into"
                v-model="form.mode"
                class="form-check-input"
                type="radio"
                value="into"
                :disabled="!prods.length"
              />
              <label for="rel-into" class="form-check-label">
                As the next version of
                <span v-if="!prods.length" class="text-secondary">(no Prod {{ noun }} yet)</span>
              </label>
            </div>
            <select
              v-if="form.mode === 'into'"
              v-model.number="form.intoId"
              class="form-select form-select-sm mb-2"
              aria-label="Prod item to release into"
            >
              <option v-for="p in prods" :key="p.id" :value="p.id">
                {{ p.name }} ({{ versionLabel(p) }})
              </option>
            </select>
            <p class="small mb-2">{{ summary }}</p>
            <div v-if="warning" class="alert alert-warning py-2 small">{{ warning }}</div>
            <p v-if="target && servers" class="small text-secondary">
              {{ servers }} server(s) on "{{ item.name }}" move to "{{ target.name }}" (latest).
              A running one picks it up on its next restart.
            </p>
          </template>

          <label for="rel-note" class="form-label small mb-1">
            {{ releasing ? 'Release note' : 'Note' }} <span class="text-secondary">(optional)</span>
          </label>
          <textarea
            id="rel-note"
            v-model="form.note"
            class="form-control form-control-sm"
            rows="2"
            maxlength="500"
            :placeholder="releasing ? 'What is in this release?' : 'Why is it ready for testing?'"
          ></textarea>
        </div>
        <div class="modal-footer">
          <button class="btn btn-outline-secondary" @click="emit('close')">Cancel</button>
          <button
            class="btn"
            :class="releasing ? 'btn-success' : 'btn-primary'"
            :disabled="form.busy || (releasing && form.mode === 'into' && !target)"
            @click="submit"
          >
            {{ form.busy ? 'Working…' : releasing ? 'Release to Prod' : 'Move to Dev' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
