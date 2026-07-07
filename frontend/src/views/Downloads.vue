<template>
  <div class="container">
    <h1 class="h3 mb-3">Server files</h1>
    <div class="row g-3">
      <div v-for="branch in branches" :key="branch.id" class="col-12 col-lg-6">
        <div class="card h-100">
          <div class="card-body">
            <div class="d-flex justify-content-between align-items-center mb-2">
              <h2 class="h5 mb-0">{{ branch.label }}</h2>
              <span class="badge" :class="branch.badge">app {{ branch.appId }}</span>
            </div>
            <p class="text-secondary small">
              Not installed. SteamCMD download with live progress arrives in the next milestone.
            </p>
            <button class="btn btn-outline-primary" disabled>Download server files</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
const branches = [
  { id: 'stable', label: 'Stable', appId: '1874900', badge: 'text-bg-success' },
  { id: 'experimental', label: 'Experimental', appId: '1890870', badge: 'text-bg-warning' },
]
</script>
