<script setup>
import PortsFirewall from '../components/PortsFirewall.vue'
</script>

<template>
  <!-- Ports & firewall moved here from the Instances page (#189): it describes this
       host, not any one server. -->
  <div class="container">
    <h1 class="h3 mb-3">Ports &amp; firewall</h1>
    <PortsFirewall />
  </div>
</template>
