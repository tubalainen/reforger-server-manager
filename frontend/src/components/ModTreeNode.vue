<script setup>
// One row in the Mods Overview tree (issue #131), rendered recursively.
// Emits bubble up to the ModsOverview view, which owns the API calls.
import { ref } from 'vue'

defineProps({
  node: { type: Object, required: true },
  selected: { type: Object, required: true }, // reactive Set of ticked modIds
  busyPersist: { type: Object, required: true }, // Set of modIds mid-request
  depth: { type: Number, default: 0 },
})
const emit = defineEmits(['toggle', 'persist', 'remove'])

const open = ref(true)
</script>

<template>
  <div>
    <div
      class="d-flex align-items-center gap-2 py-1 border-bottom"
      :style="{ paddingLeft: depth * 1.4 + 'rem' }"
    >
      <button
        v-if="node.children.length"
        class="btn btn-sm p-0 border-0 text-secondary"
        style="width: 1.1rem"
        :title="open ? 'Collapse' : 'Expand'"
        @click="open = !open"
      >{{ open ? '▾' : '▸' }}</button>
      <span v-else style="width: 1.1rem"></span>

      <input
        type="checkbox"
        class="form-check-input mt-0 flex-shrink-0"
        :checked="selected.has(node.modId)"
        :title="`Select ${node.name}`"
        @change="emit('toggle', node.modId)"
      />

      <span class="fw-semibold text-truncate" style="max-width: 22rem">{{ node.name }}</span>

      <span
        v-if="!node.registered"
        class="badge text-bg-light border text-secondary"
        title="Appears only as a dependency of another mod"
      >dependency</span>
      <span
        v-if="node.persist"
        class="badge text-bg-warning"
        title="Protected — never pruned or deleted"
      >📌 persist</span>
      <span
        v-if="node.registered && node.orphaned"
        class="badge text-bg-secondary"
        title="No template lists this any more (kept by the overview)"
      >unused</span>

      <!-- Where this mod is baked & downloaded, with the configured version (#131) -->
      <span
        v-for="i in node.instances"
        :key="i.id"
        class="badge text-bg-success"
        :title="`Baked into ${i.name} (template ${i.template})`"
      >{{ i.name }}{{ i.version ? ' @ ' + i.version : '' }}</span>

      <span class="small text-secondary font-monospace ms-1 d-none d-md-inline">{{ node.modId }}</span>

      <div v-if="node.registered" class="btn-group btn-group-sm ms-auto flex-shrink-0">
        <button
          class="btn btn-outline-secondary"
          :disabled="busyPersist.has(node.modId)"
          :title="node.persist ? 'Allow pruning again' : 'Protect from prune/delete'"
          @click="emit('persist', node.modId, !node.persist)"
        >{{ node.persist ? 'Unpin' : '📌 Persist' }}</button>
        <button
          class="btn btn-outline-danger"
          :disabled="node.persist"
          :title="node.persist ? 'Clear persist first' : 'Remove from the overview'"
          @click="emit('remove', node.modId, node.name)"
        >Delete</button>
      </div>
    </div>

    <template v-if="open">
      <ModTreeNode
        v-for="(child, idx) in node.children"
        :key="child.modId + ':' + idx"
        :node="child"
        :selected="selected"
        :busy-persist="busyPersist"
        :depth="depth + 1"
        @toggle="emit('toggle', $event)"
        @persist="(id, val) => emit('persist', id, val)"
        @remove="(id, name) => emit('remove', id, name)"
      />
    </template>
  </div>
</template>
