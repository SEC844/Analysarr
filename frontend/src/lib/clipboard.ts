// Adresses IPv4 privées : RFC 1918, boucle locale, lien local, et plage CGNAT
// 100.64.0.0/10 (réseaux privés type Tailscale).
const PRIVATE_IPV4 = [
  /^10\./,
  /^127\./,
  /^192\.168\./,
  /^172\.(1[6-9]|2\d|3[01])\./,
  /^169\.254\./,
  /^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\./,
]
const IPV4 = /^\d{1,3}(\.\d{1,3}){3}$/
const LOCAL_SUFFIXES = [".localhost", ".local", ".lan", ".home.arpa"]

/** Hôte joint sur le réseau local (IP privée, localhost, nom mDNS/LAN). */
export function isLocalNetworkHost(hostname: string): boolean {
  const host = hostname.toLowerCase().replace(/^\[|\]$/g, "")
  if (host === "localhost" || LOCAL_SUFFIXES.some((suffix) => host.endsWith(suffix))) return true
  if (IPV4.test(host)) return PRIVATE_IPV4.some((range) => range.test(host))
  // IPv6 : boucle locale, adresses uniques locales (fc00::/7), lien local (fe80::/10).
  return host === "::1" || /^f[cd][0-9a-f]{0,2}:/.test(host) || /^fe[89ab][0-9a-f]?:/.test(host)
}

// Repli pour une page servie en HTTP : l'API presse-papiers n'existe qu'en
// contexte sécurisé (HTTPS ou localhost). Champ temporaire invisible, retiré
// aussitôt.
function legacyCopy(text: string): boolean {
  const textarea = document.createElement("textarea")
  textarea.value = text
  textarea.setAttribute("readonly", "")
  Object.assign(textarea.style, { position: "fixed", top: "0", left: "0", opacity: "0", pointerEvents: "none" })
  document.body.appendChild(textarea)
  textarea.select()
  try {
    return document.execCommand("copy")
  } catch {
    return false
  } finally {
    textarea.remove()
  }
}

/**
 * Copie un texte sensible (clé ou codes de secours de double authentification).
 * HTTPS : API presse-papiers. HTTP : copie autorisée uniquement sur le réseau
 * local ; hors réseau local, les secrets ne sont pas copiés sur une connexion
 * non chiffrée (le texte reste sélectionnable à la main).
 */
export async function copyText(text: string): Promise<boolean> {
  if (window.isSecureContext && navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      return false
    }
  }
  return isLocalNetworkHost(window.location.hostname) && legacyCopy(text)
}
