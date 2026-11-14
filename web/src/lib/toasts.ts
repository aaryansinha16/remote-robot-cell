import { useCallback, useRef, useState } from 'react'
import type { Notice } from './types'

export interface Toast extends Notice {
  id: number
}

export function useToasts() {
  const [toasts, setToasts] = useState<Toast[]>([])
  const nextId = useRef(0)

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id))
  }, [])

  const push = useCallback(
    (notice: Notice) => {
      const id = nextId.current++
      setToasts((current) => [...current.slice(-3), { ...notice, id }])
      setTimeout(() => dismiss(id), 6000)
    },
    [dismiss],
  )

  return { toasts, push, dismiss }
}
