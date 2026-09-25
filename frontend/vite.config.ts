/// <reference types="vitest/config" />
import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
  },
  server: {
    proxy: {
      '/api': 'http://localhost:1818',
    },
  },
  test: {
    // Logique pure sous Node ; composants et hooks React sous jsdom, avec
    // Testing Library. Deux projets plutôt que jsdom partout : les tests de
    // logique restent rapides et ne dépendent pas d'un faux navigateur.
    projects: [
      {
        extends: true,
        test: {
          name: 'logic',
          environment: 'node',
          include: ['src/**/*.test.ts'],
          exclude: ['src/hooks/**'],
        },
      },
      {
        extends: true,
        test: {
          name: 'dom',
          environment: 'jsdom',
          include: ['src/**/*.test.tsx', 'src/hooks/**/*.test.ts'],
          setupFiles: ['./src/test/setup.ts'],
        },
      },
    ],
  },
})
