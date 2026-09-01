import { Search } from "lucide-react"

import { Input } from "@/components/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import type { MediaListParams } from "@/types/media"

interface MediaFiltersProps {
  value: MediaListParams
  onChange: (value: MediaListParams) => void
}

const TYPE_LABELS: Record<string, string> = { all: "Tous les types", movie: "Films", series: "Séries" }
const STATUS_LABELS: Record<string, string> = {
  all: "Tous les statuts",
  sain: "Sain",
  doublon: "Doublon",
  orphelin_qbit: "Orphelin qBit",
  tracker_unique: "Tracker unique",
}
const SORT_LABELS: Record<string, string> = {
  title: "Titre (A-Z)",
  year: "Année (récent)",
  size: "Espace récupérable",
}

export function MediaFilters({ value, onChange }: MediaFiltersProps) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <div className="relative">
        <Search className="text-muted-foreground pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2" />
        <Input
          placeholder="Rechercher un titre..."
          value={value.search ?? ""}
          onChange={(e) => onChange({ ...value, search: e.target.value || undefined })}
          className="w-56 pl-8"
        />
      </div>

      <Select
        value={value.media_type ?? "all"}
        onValueChange={(v) => onChange({ ...value, media_type: v === "all" ? undefined : (v as "movie" | "series") })}
      >
        <SelectTrigger className="w-36">
          <SelectValue placeholder="Type">{(v: string) => TYPE_LABELS[v] ?? v}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">Tous les types</SelectItem>
          <SelectItem value="movie">Films</SelectItem>
          <SelectItem value="series">Séries</SelectItem>
        </SelectContent>
      </Select>

      <Select
        value={value.status ?? "all"}
        onValueChange={(v) => onChange({ ...value, status: v === "all" ? undefined : (v as MediaListParams["status"]) })}
      >
        <SelectTrigger className="w-44">
          <SelectValue placeholder="Statut">{(v: string) => STATUS_LABELS[v] ?? v}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">Tous les statuts</SelectItem>
          <SelectItem value="sain">Sain</SelectItem>
          <SelectItem value="doublon">Doublon</SelectItem>
          <SelectItem value="orphelin_qbit">Orphelin qBit</SelectItem>
          <SelectItem value="tracker_unique">Tracker unique</SelectItem>
        </SelectContent>
      </Select>

      <Select value={value.sort ?? "title"} onValueChange={(v) => onChange({ ...value, sort: v as MediaListParams["sort"] })}>
        <SelectTrigger className="w-44">
          <SelectValue placeholder="Trier par">{(v: string) => SORT_LABELS[v] ?? v}</SelectValue>
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="title">Titre (A-Z)</SelectItem>
          <SelectItem value="year">Année (récent)</SelectItem>
          <SelectItem value="size">Espace récupérable</SelectItem>
        </SelectContent>
      </Select>
    </div>
  )
}
