import { screen } from "@testing-library/react"

import { en } from "@/i18n/en"

/** Bouton « Fermer » du pied de dialogue — le dialogue a aussi une croix de
 * fermeture qui porte le même nom accessible. */
export function footerCloseButton(): HTMLElement {
  const [button] = screen
    .getAllByRole("button", { name: en.common.close })
    .filter((element) => element.getAttribute("data-slot") !== "dialog-close")
  if (!button) throw new Error("Bouton « Fermer » du pied de dialogue introuvable.")
  return button
}
