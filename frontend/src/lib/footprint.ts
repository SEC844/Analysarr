import type { DeleteFootprintItem, MediaDeleteFootprint } from "@/types/media"

// Torrents et fichiers de bibliothèque partagent un seul ensemble de
// sélection : clés préfixées pour les distinguer au moment de l'envoi.
export const torrentKey = (id: number) => `t:${id}`
export const fileKey = (id: number) => `f:${id}`

// Unités disque (inodes) de chaque élément, indexées par clé de sélection.
export function indexFootprint(footprint: MediaDeleteFootprint): Map<string, number[]> {
  const index = (items: DeleteFootprintItem[], key: (id: number) => string) =>
    items.map((item): [string, number[]] => [key(item.id), item.units])
  return new Map([...index(footprint.torrents, torrentKey), ...index(footprint.files, fileKey)])
}

// Taille réelle sur disque d'un ensemble d'éléments : chaque inode compté
// une seule fois (3 torrents hardlinkés d'un même film = une seule taille).
export function diskBytes(
  footprint: MediaDeleteFootprint,
  unitsByKey: Map<string, number[]>,
  keys: Iterable<string>,
): number {
  const units = new Set<number>()
  for (const key of keys) for (const unit of unitsByKey.get(key) ?? []) units.add(unit)
  let total = 0
  for (const unit of units) total += footprint.units[unit]?.size ?? 0
  return total
}

// Espace RÉELLEMENT libéré : un inode n'est libéré que si tous ses liens
// sont sélectionnés — 100 hardlinks d'un même fichier ne libèrent qu'une
// seule fois sa taille, et rien du tout si l'un d'eux est conservé.
export function reclaimedBytes(
  footprint: MediaDeleteFootprint,
  unitsByKey: Map<string, number[]>,
  keys: Iterable<string>,
): number {
  const selectedLinks = new Map<number, number>()
  for (const key of keys) {
    for (const unit of unitsByKey.get(key) ?? []) selectedLinks.set(unit, (selectedLinks.get(unit) ?? 0) + 1)
  }
  let total = 0
  for (const [unit, links] of selectedLinks) {
    const disk = footprint.units[unit]
    if (disk && links >= disk.links) total += disk.size
  }
  return total
}
