/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Version injectée au build Docker : "dev" pour une image de développement,
   * "X.Y.Z" pour une version publiée (voir Dockerfile). */
  readonly VITE_APP_VERSION?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
