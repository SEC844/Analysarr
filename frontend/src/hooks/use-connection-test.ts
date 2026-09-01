import { useMutation } from "@tanstack/react-query"

import { testConnection } from "@/lib/api"
import type { ConnectionTestRequest, ServiceName } from "@/types/settings"

export function useConnectionTest(service: ServiceName) {
  return useMutation({
    mutationFn: (payload: ConnectionTestRequest) => testConnection(service, payload),
  })
}
