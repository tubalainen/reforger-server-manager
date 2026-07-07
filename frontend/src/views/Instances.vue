<template>
  <div class="container">
    <div class="d-flex justify-content-between align-items-center mb-3">
      <h1 class="h3 mb-0">Server instances</h1>
      <button class="btn btn-primary" disabled>+ New instance</button>
    </div>
    <div class="card text-center text-secondary py-5">
      <div class="card-body">
        <p class="fs-1 mb-2">🖥️</p>
        <p class="mb-1">No server instances yet.</p>
        <p class="small mb-0">
          Instance lifecycle (create / start / stop, live logs, crash auto-restart) arrives in a
          later milestone. Start by creating a <router-link to="/templates">template</router-link>
          and downloading the <router-link to="/downloads">server files</router-link>.
        </p>
      </div>
    </div>
  </div>
</template>
