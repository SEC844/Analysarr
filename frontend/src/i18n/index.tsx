import { createContext, Fragment, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react"

import { en } from "@/i18n/en"
import { fr } from "@/i18n/fr"

export type Language = "fr" | "en"

export const LANGUAGES: { value: Language; label: string }[] = [
  { value: "fr", label: "Français" },
  { value: "en", label: "English" },
]

export type MediaServer = "emby" | "jellyfin"
export const MEDIA_SERVER_NAMES: Record<MediaServer, string> = { emby: "Emby", jellyfin: "Jellyfin" }

// Variables disponibles dans TOUS les textes : le nom du serveur multimédia
// configuré, et sa forme élidée en français (« d'Emby » / « de Jellyfin »).
function mediaServerVars(language: Language, server: MediaServer): Vars {
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

type Vars = Record<string, string | number>
type RichVars = Record<string, ReactNode>

const DICTIONARIES: Record<Language, Dictionary> = { fr, en }
const LOCALES: Record<Language, string> = { fr: "fr-FR", en: "en-US" }
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

function applyLanguage(next: Language) {
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

function lookup(language: Language, key: MessageKey, count?: number): string {
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

interface I18nContextValue {
  language: Language
  locale: string
  setLanguage: (language: Language) => void
  mediaServer: MediaServer
  setMediaServer: (server: MediaServer) => void
  t: (key: MessageKey, vars?: Vars) => string
  // Variante acceptant des éléments React en variables (lien, <code>, <strong>...).
  rich: (key: MessageKey, vars: RichVars) => ReactNode
}

const I18nContext = createContext<I18nContextValue | null>(null)

export function I18nProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState<Language>(currentLanguage)
  const [mediaServer, setMediaServer] = useState<MediaServer>("emby")

  const setLanguage = useCallback((next: Language) => {
    applyLanguage(next)
    setLanguageState(next)
  }, [])

  useEffect(() => {
    document.documentElement.lang = language
  }, [language])

  const value = useMemo<I18nContextValue>(() => {
    const serverVars = mediaServerVars(language, mediaServer)
    return {
      language,
      locale: LOCALES[language],
      setLanguage,
      mediaServer,
      setMediaServer,
      t: (key, vars) => {
        const all: Vars = { ...serverVars, ...vars }
        return lookup(language, key, typeof vars?.count === "number" ? vars.count : undefined).replace(
          /\{(\w+)\}/g,
          (match, name) => (name in all ? String(all[name]) : match),
        )
      },
      rich: (key, vars) => {
        const all: RichVars = { ...serverVars, ...vars }
        return lookup(language, key)
          .split(/(\{\w+\})/)
          .map((part, i) => {
            const name = part.match(/^\{(\w+)\}$/)?.[1]
            return <Fragment key={i}>{name && name in all ? all[name] : part}</Fragment>
          })
      },
    }
  }, [language, setLanguage, mediaServer])

  // `key` : un changement de langue remonte l'arbre, pour que les textes
  // formatés hors contexte (tailles, dates) soient eux aussi recalculés.
  return (
    <I18nContext.Provider value={value}>
      <Fragment key={language}>{children}</Fragment>
    </I18nContext.Provider>
  )
}

export function useI18n(): I18nContextValue {
  const context = useContext(I18nContext)
  if (!context) throw new Error("useI18n doit être utilisé dans I18nProvider.")
  return context
}
