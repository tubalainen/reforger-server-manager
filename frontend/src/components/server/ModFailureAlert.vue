<script setup>
import { computed } from 'vue'
import ModIdLink from '../ModIdLink.vue'
import {
  editTemplateRoute,
  failureHeadline,
  failureReason,
  pulledInByText,
  pullingMods,
} from '../../modFailures'

// The mods that stopped this server's last start (#270), on top of every tab of the
// server page: a server that does not start is the first thing to know about. Only
// a FAILED start shows here; mods that log errors on a server that still comes
// online are left alone.
const props = defineProps({
  failures: { type: Object, default: null },
})

const headline = computed(() => failureHeadline(props.failures))
const mods = computed(() => props.failures?.mods || [])
// What there is to remove in the template: failing mods in it, or the template mods
// that bring a failing one in.
const removable = computed(
  () => mods.value.some((m) => m.in_template) || pullingMods(mods.value).length > 0,
)
const editRoute = computed(() =>
  props.failures?.template_id != null ? editTemplateRoute(props.failures.template_id, mods.value) : null,
)
const when = computed(() =>
  props.failures?.logged_at ? new Date(props.failures.logged_at * 1000).toLocaleString() : '',
)
</script>

<template>
  <div v-if="failures?.failed" class="alert alert-danger py-2" role="alert">
    <div class="d-flex flex-wrap align-items-center gap-2">
      <strong class="me-auto">{{ headline }}</strong>
      <router-link v-if="editRoute" :to="editRoute" class="btn btn-sm btn-danger">
        {{ removable ? 'Remove in template…' : 'Open template' }}
      </router-link>
    </div>

    <ul v-if="mods.length" class="list-unstyled small mb-0 mt-2">
      <li v-for="m in mods" :key="m.modId || m.log_name" class="mb-2" :class="{ 'opacity-75': m.knock_on }">
        <span class="fw-semibold">{{ m.name }}</span>
        <small v-if="m.modId" class="ms-1"><ModIdLink :mod-id="m.modId" :name="m.name" /></small>
        <span v-if="m.knock_on" class="badge text-bg-secondary ms-1">knock-on</span>
        <span v-else-if="!m.in_template && !m.pulled_in_by?.length" class="badge text-bg-light border ms-1">
          not in the template
        </span>
        <span class="d-block">{{ failureReason(m) }}</span>
        <span v-if="m.pulled_in_by?.length" class="d-block">{{ pulledInByText(m) }}</span>
        <span v-if="!m.knock_on && m.needs.length" class="d-block text-body-secondary">
          On the Workshop:
          <template v-for="(n, i) in m.needs" :key="n.modId">
            <template v-if="i">, </template>
            <ModIdLink :mod-id="n.modId" :name="n.name || ''" />
          </template>
        </span>
      </li>
    </ul>

    <details v-if="failures.script_errors?.length" class="small mt-2">
      <summary>Script compile errors ({{ failures.script_errors.length }})</summary>
      <pre class="small mb-0 mt-1 text-wrap">{{ failures.script_errors.join('\n') }}</pre>
    </details>
    <details v-else-if="!mods.length && failures.fatal?.length" class="small mt-2">
      <summary>What the log says</summary>
      <pre class="small mb-0 mt-1 text-wrap">{{ failures.fatal.join('\n') }}</pre>
    </details>

    <small class="d-block text-body-secondary mt-1">
      From <code>{{ failures.log }}</code><template v-if="when">, {{ when }}</template>.
      Remove the failing mods from the template, or check their Workshop pages for a fix.
    </small>
  </div>
</template>
