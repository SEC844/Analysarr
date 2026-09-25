// Cœur de l'internationalisation, sans JSX : langue courante, dictionnaires,
// recherche d'un texte et contexte React. Le composant I18nProvider vit dans
// provider.tsx (un fichier .tsx n'exporte que des composants, pour que le
// rechargement à chaud de Vite fonctionne).

import { createContext, useContext, type ReactNode } from "react"

import { en } from "@/i18n/en"
import { fr } from "@/i18n/fr"

export type Language = "fr" | "en"

export const LANGUAGES: { value: Language; label: string }[] = [
  { value: "fr", label: "Français" },
  { value: "en", label: "English" },
]

export type MediaServer = "emby" | "jellyfin"
export const MEDIA_SERVER_NAMES: Record<MediaServer, string> = { emby: "Emby", jellyfin: "Jellyfin" }

// Gestionnaire de demandes : Seer (Overseerr, Jellyseerr, Seerr) ou Ombi.
// Son nom est la variable `{requests}`, disponible dans tous les textes.
export type RequestManager = "seer" | "ombi"
export const REQUEST_MANAGER_NAMES: Record<RequestManager, string> = { seer: "Seer", ombi: "Ombi" }

// Variables disponibles dans TOUS les textes : le nom du serveur multimédia
// configuré, et sa forme élidée en français (« d'Emby » / « de Jellyfin »).
export function mediaServerVars(language: Language, server: MediaServer): Vars {
  const name = MEDIA_SERVER_NAMES[server]
  return {
    server: name,
    deServer: language === "fr" ? (server === "emby" ? "d'Emby" : "de Jellyfin") : `from ${name}`,
  }
}

// Pluriel : { one, other }, choisi selon la variable `count`.
type Plural = { one: string; other: string }
type Messages<T> = { [K in keyof T]: T[K] extends string ? string : T[K] extends Plural ? Plural : Messages<T[K]> }
export type Dictionary = Messages<typeof fr>

type KeyPath<T, P extends string = ""> = {
  [K in keyof T & string]: T[K] extends string | Plural ? `${P}${K}` : KeyPath<T[K], `${P}${K}.`>
}[keyof T & string]
export type MessageKey = KeyPath<Dictionary>

export type Vars = Record<string, string | number>
export type RichVars = Record<string, ReactNode>

const DICTIONARIES: Record<Language, Dictionary> = { fr, en }
export const LOCALES: Record<Language, string> = { fr: "fr-FR", en: "en-US" }
const STORAGE_KEY = "analysarr:language"

function isLanguage(value: unknown): value is Language {
  return value === "fr" || value === "en"
}

function detectLanguage(): Language {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (isLanguage(stored)) return stored
  } catch {
    // localStorage indisponible : on se rabat sur la langue du navigateur
  }
  return navigator.language?.toLowerCase().startsWith("fr") ? "fr" : "en"
}

// Langue courante lisible hors composants React (formatage des tailles et des
// dates dans lib/format.ts). Tenue à jour par I18nProvider.
let currentLanguage: Language = detectLanguage()

export function getLanguage(): Language {
  return currentLanguage
}

export function applyLanguage(next: Language) {
  currentLanguage = next
  try {
    localStorage.setItem(STORAGE_KEY, next)
  } catch {
    // pas grave : la préférence reste aussi enregistrée côté serveur
  }
}

export function getLocale(): string {
  return LOCALES[currentLanguage]
}

export function lookup(language: Language, key: MessageKey, count?: number): string {
  let node: unknown = DICTIONARIES[language]
  for (const part of key.split(".")) node = (node as Record<string, unknown>)?.[part]
  if (typeof node === "string") return node
  if (node && typeof node === "object" && "one" in node && "other" in node) {
    const plural = node as Plural
    // Français : 0 et 1 au singulier ; anglais : seulement 1.
    const singular = language === "fr" ? (count ?? 0) <= 1 : count === 1
    return singular ? plural.one : plural.other
  }
  return key
}

export interface I18nContextValue {
  language: Language
  locale: string
  setLanguage: (language: Language) => void
  mediaServer: MediaServer
  setMediaServer: (server: MediaServer) => void
  setRequestManager: (manager: RequestManager) => void
  t: (key: MessageKey, vars?: Vars) => string
  // Variante acceptant des éléments React en variables (lien, <code>, <strong>...).
  rich: (key: MessageKey, vars: RichVars) => ReactNode
}

export const I18nContext = createContext<I18nContextValue | null>(null)

export function useI18n(): I18nContextValue {
  const context = useContext(I18nContext)
  if (!context) throw new Error("useI18n doit être utilisé dans I18nProvider.")
  return context
}
