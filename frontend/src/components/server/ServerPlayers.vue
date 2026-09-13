<script setup>
// Who is on (#126), on its own tab (#189). Names are folded from the server log's
// join/leave lines; the count from the stats line stays the source of truth for
// how many are online.
defineProps({
  inst: { type: Object, required: true },
  stats: { type: Object, default: null },
  maxPlayers: { type: Number, default: null },
})
</script>

<template>
  <div class="card">
    <div class="card-header py-2">
      <span class="fw-semibold small">
        <template v-if="inst.status === 'running' && stats && stats.players != null">
          {{ stats.players }}{{ maxPlayers != null ? ` of ${maxPlayers}` : '' }} online
        </template>
        <template v-else>Players</template>
      </span>
    </div>
    <div class="card-body">
      <p v-if="inst.status !== 'running'" class="text-secondary mb-0">
        No one can be connected while the server isn't running.
      </p>
      <p v-else-if="!stats" class="text-secondary mb-0">Loading…</p>
      <ul v-else-if="stats.roster && stats.roster.length" class="rsm-roster list-unstyled mb-0">
        <li v-for="p in stats.roster" :key="p.player_num" class="d-flex gap-2">
          <span class="text-secondary rsm-num">#{{ p.player_num }}</span>
          <span class="text-truncate">{{ p.name }}</span>
        </li>
      </ul>
      <p v-else-if="stats.players" class="text-secondary fst-italic mb-0">
        {{ stats.players }} online — this server build doesn’t print player names to the log.
      </p>
      <p v-else class="text-secondary fst-italic mb-0">No one connected.</p>
    </div>
  </div>
</template>

<style scoped>
.rsm-roster {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(12rem, 1fr));
  gap: 0 1.5rem;
}

.rsm-roster li {
  padding: 0.35rem 0;
  border-bottom: 1px solid var(--bs-border-color);
  min-width: 0;
}

.rsm-roster .rsm-num {
  min-width: 2rem;
}
</style>
