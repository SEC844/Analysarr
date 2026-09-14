import { Checkbox as CheckboxPrimitive } from "@base-ui/react/checkbox"
import { CheckIcon, MinusIcon } from "lucide-react"

import { cn } from "@/lib/utils"

// État partiel (`indeterminate`) : Base UI ne pose alors NI data-checked NI
// data-unchecked, uniquement data-indeterminate — d'où ses propres variantes
// de fond/bordure ci-dessous, et l'icône choisie via `group`.
function Checkbox({ className, ...props }: CheckboxPrimitive.Root.Props) {
  return (
    <CheckboxPrimitive.Root
      data-slot="checkbox"
      className={cn(
        "group peer size-[18px] shrink-0 rounded-[4px] border-2 border-input bg-background outline-none transition-colors focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 aria-invalid:border-destructive aria-invalid:ring-3 aria-invalid:ring-destructive/20 data-checked:border-primary data-checked:bg-primary data-checked:text-primary-foreground data-indeterminate:border-primary data-indeterminate:bg-primary data-indeterminate:text-primary-foreground data-disabled:cursor-not-allowed data-disabled:opacity-50 dark:aria-invalid:border-destructive/50 dark:aria-invalid:ring-destructive/40 dark:data-unchecked:bg-input/30",
        className
      )}
      {...props}
    >
      <CheckboxPrimitive.Indicator
        data-slot="checkbox-indicator"
        className="flex items-center justify-center text-current"
      >
        <CheckIcon className="size-3.5 stroke-[3] group-data-indeterminate:hidden" />
        <MinusIcon className="hidden size-3.5 stroke-[3] group-data-indeterminate:block" />
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  )
}

export { Checkbox }
