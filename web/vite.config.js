import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Clean URLs: /kata → kata.html (vanilla d3 viewer), /kata-builder → kata-builder.html (React).
const cleanRoutes = {
  name: 'kimodo-clean-routes',
  configureServer(server) {
    server.middlewares.use((req, _res, next) => {
      if (req.url === '/kata' || req.url === '/kata/') req.url = '/kata.html'
      else if (req.url === '/kata-builder' || req.url === '/kata-builder/') req.url = '/kata-builder.html'
      next()
    })
  },
}

export default defineConfig({
  server: {
    host: '0.0.0.0',
    port: 5173,
  },
  // react() handles the .jsx kata-builder entry; the vanilla index.html / kata.html
  // entries are unaffected (plain JS passes through).
  plugins: [react(), cleanRoutes],
  build: {
    rollupOptions: {
      input: { main: 'index.html', kata: 'kata.html', katabuilder: 'kata-builder.html' },
    },
  },
})
