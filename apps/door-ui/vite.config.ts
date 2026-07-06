import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/door-media": {
        target: process.env.VITE_DOOR_MEDIA_TARGET ?? "http://127.0.0.1:8082",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/door-media/, ""),
      },
    },
  },
});
