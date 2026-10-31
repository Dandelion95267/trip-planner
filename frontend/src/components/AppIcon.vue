<script setup lang="ts">
withDefaults(defineProps<{ name: string; size?: number }>(), { size: 20 });
const paths: Record<string, string> = {
  compass: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20ZM16 8l-3 5-5 3 3-5 5-3Z",
  pin: "M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 1 1 16 0ZM15 10a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z",
  arrow: "M4 12h16M14 6l6 6-6 6",
  route:
    "M5 5a2 2 0 1 0 0 .01ZM19 19a2 2 0 1 0 0 .01ZM7 5h8a4 4 0 0 1 0 8H9a4 4 0 0 0 0 8h8",
  cloud: "M7 18a5 5 0 1 1 1-9.9 7 7 0 0 1 13 3.4A4 4 0 0 1 18 18H7Z",
  clock: "M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0ZM12 6v6l4 2",
  bed: "M3 18v3M21 18v3M3 18h18V9H3v9ZM7 9V5h10v4M3 14h18",
  food: "M5 3v7M2 3v4a3 3 0 0 0 6 0V3M5 10v11M20 21V3c-5 0-6 10 0 10",
  check: "m5 12 4 4L19 6",
  download: "M12 3v12M7 10l5 5 5-5M4 16v5h16v-5",
  file: "M14 2H5v20h14V7l-5-5ZM14 2v6h5M8 12h8M8 16h8",
  copy: "M9 9h12v12H9V9ZM4 15H3V3h12v1",
  spark: "m12 2 3 7 7 3-7 3-3 7-3-7-7-3 7-3 3-7Z",
};
</script>
<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.6"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <path :d="paths[name] || paths.compass" />
  </svg>
</template>
