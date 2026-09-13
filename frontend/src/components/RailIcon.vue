<script setup>
// Line icons for the navigation rail (#189). Inline SVG in the text colour, so they
// follow the theme and the active/hover state without any icon font.
defineProps({ name: { type: String, required: true } })
</script>

<template>
  <svg
    viewBox="0 0 24 24"
    width="22"
    height="22"
    fill="none"
    stroke="currentColor"
    stroke-width="1.6"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <template v-if="name === 'servers'">
      <rect x="4" y="4" width="16" height="6" rx="1.5" />
      <rect x="4" y="14" width="16" height="6" rx="1.5" />
      <path d="M8 7h.01M8 17h.01" />
    </template>
    <template v-else-if="name === 'library'">
      <path d="M5 4h4v16H5zM11 4h4v16h-4z" />
      <path d="M17 5.5l3 .8-3.5 13.9-3-.8" />
    </template>
    <template v-else-if="name === 'system'">
      <path d="M4 7h9M17 7h3M4 17h3M11 17h9" />
      <circle cx="15" cy="7" r="2" />
      <circle cx="9" cy="17" r="2" />
    </template>
    <template v-else-if="name === 'help'">
      <circle cx="12" cy="12" r="8.5" />
      <path d="M9.6 9.5a2.5 2.5 0 1 1 3.4 2.3c-.6.3-1 .8-1 1.5v.4M12 16.8h.01" />
    </template>
    <template v-else-if="name === 'logout'">
      <path d="M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4" />
      <path d="M10 16l-4-4 4-4M6 12h9" />
    </template>
  </svg>
</template>
