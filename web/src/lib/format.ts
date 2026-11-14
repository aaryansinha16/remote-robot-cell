import type { JobStatus, JobStep, Pause } from './types'

const timeFormat = new Intl.DateTimeFormat(undefined, {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

export function clock(ms: number): string {
  return timeFormat.format(new Date(ms))
}

export function ago(ms: number | null, now: number): string {
  if (ms === null) return 'never'
  const seconds = Math.max(0, Math.round((now - ms) / 1000))
  if (seconds < 2) return 'just now'
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  return `${Math.floor(minutes / 60)}h ago`
}

export function duration(ms: number): string {
  if (ms < 1000) return `${ms} ms`
  const seconds = ms / 1000
  if (seconds < 60) return `${seconds.toFixed(1)} s`
  const minutes = Math.floor(seconds / 60)
  return `${minutes}m ${String(Math.round(seconds % 60)).padStart(2, '0')}s`
}

export function humanize(code: string): string {
  return code.replaceAll('_', ' ')
}

export const STATUS_LABEL: Record<JobStatus, string> = {
  pending: 'Pending',
  received: 'Received',
  queued: 'Queued',
  running: 'Running',
  done: 'Done',
  failed: 'Failed',
  rejected: 'Rejected',
  cancelled: 'Cancelled',
}

export const STEP_LABEL: Record<JobStep, string> = {
  move_to_pick: 'moving to pick',
  pick: 'picking',
  move_to_place: 'moving to place',
  place: 'placing',
}

export function pauseHeadline(pause: Pause): string {
  switch (pause.reason) {
    case 'person_in_cell':
      return `Person in ${pause.cells.join(' and ') || 'the cell'}`
    case 'safety_feed_lost':
      return 'Safety feed lost'
    case 'awaiting_safety_feed':
      return 'Waiting for the safety feed'
  }
}

export function pauseDetail(pause: Pause): string {
  const parts = [`since ${clock(pause.since)}`]
  if (pause.track_ids.length) {
    parts.unshift(`track ${pause.track_ids.join(', ')}`)
  }
  if (pause.latency_ms !== null && pause.reason === 'person_in_cell') {
    parts.push(`stopped ${pause.latency_ms} ms after detection`)
  }
  if (pause.reason === 'safety_feed_lost') {
    parts.push('no fusion data, so the site treats the cell as unsafe')
  }
  return parts.join(' · ')
}
