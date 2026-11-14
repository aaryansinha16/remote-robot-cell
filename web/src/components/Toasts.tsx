import type { Toast } from '../lib/toasts'
import type { Notice } from '../lib/types'

const TONE: Record<Notice['level'], string> = {
  info: 'border-line',
  warning: 'border-amber-500/40',
  error: 'border-red-500/40',
}

export function Toasts({ toasts, onDismiss }: { toasts: Toast[]; onDismiss: (id: number) => void }) {
  return (
    <div className="pointer-events-none fixed right-4 bottom-4 z-10 flex w-80 flex-col gap-2">
      {toasts.map((toast) => (
        <button
          key={toast.id}
          type="button"
          onClick={() => onDismiss(toast.id)}
          className={`pointer-events-auto rounded-lg border bg-surface px-4 py-3 text-left text-sm shadow-lg ${TONE[toast.level]}`}
        >
          {toast.message}
        </button>
      ))}
    </div>
  )
}
