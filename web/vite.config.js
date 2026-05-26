import { defineConfig } from 'vite'

// Clean URL: /kata → kata.html (vanilla d3 + three kata viewer/builder).
const cleanRoutes = {
  name: 'kimodo-clean-routes',
  configureServer(server) {
    server.middlewares.use((req, _res, next) => {
      if (req.url === '/kata' || req.url === '/kata/') req.url = '/kata.html'
      next()
    })
  },
}

export default defineConfig({
  server: {
    host: '0.0.0.0',
    port: 5173,
  },
  plugins: [cleanRoutes],
  build: {
    rollupOptions: {
      input: { main: 'index.html', kata: 'kata.html' },
    },
  },
})
