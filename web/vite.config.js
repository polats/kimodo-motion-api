import { defineConfig, loadEnv } from 'vite'
import { resolve } from 'node:path'

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

export default defineConfig(({ mode }) => {
  // Read secrets from kimodo/.env (one level up from web/). Consumed only
  // server-side here for the proxy — never exposed to the client bundle.
  const env = loadEnv(mode, resolve(process.cwd(), '..'), '')
  const HF_TOKEN = env.HF_TOKEN || ''
  const KLEIN_SPACE = (env.TINY_KLEIN_SPACE || 'polats/tiny-army-klein-zerogpu').trim()
  // polats/tiny-army-klein-zerogpu → https://polats-tiny-army-klein-zerogpu.hf.space
  const spaceHost = `https://${KLEIN_SPACE.replace(/[/_]/g, '-')}.hf.space`

  // Proxy the HF Space through the dev/preview server so the browser talks to a
  // same-origin /hfspace/* path: no CORS, the HF token stays server-side, and
  // the generated image loads into WebGL without cross-origin taint.
  const hfProxy = {
    '/hfspace': {
      target: spaceHost,
      changeOrigin: true,
      secure: true,
      rewrite: (p) => p.replace(/^\/hfspace/, ''),
      configure: (proxy) => {
        proxy.on('proxyReq', (proxyReq) => {
          if (HF_TOKEN) proxyReq.setHeader('Authorization', `Bearer ${HF_TOKEN}`)
        })
      },
    },
  }

  return {
    server: { host: '0.0.0.0', port: 5173, proxy: hfProxy },
    preview: { host: '0.0.0.0', port: 4317, proxy: hfProxy },
    plugins: [cleanRoutes],
    build: {
      rollupOptions: {
        input: { main: 'index.html', kata: 'kata.html' },
      },
    },
  }
})
