import { Fragment, useCallback, useEffect, useMemo, useState, type ReactNode } from "react"

import {
  applyLanguage,
  getLanguage,
  I18nContext,
  type I18nContextValue,
  type Language,
  LOCALES,
  lookup,
  type MediaServer,
  mediaServerVars,
  type RichVars,
  type Vars,
} from "@/i18n/core"

export function I18nProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState<Language>(getLanguage)
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
