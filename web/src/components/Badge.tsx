import type { ReactNode } from 'react'
import { STATUS_LABEL } from '../lib/format'
import type { JobStatus, RobotState } from '../lib/types'

export type Tone = 'neutral' | 'sky' | 'indigo' | 'blue' | 'green' | 'red' | 'orange' | 'amber'

const TONES: Record<Tone, string> = {
  neutral: 'bg-zinc-500/10 text-zinc-600 dark:text-zinc-300',
  sky: 'bg-sky-500/10 text-sky-700 dark:text-sky-300',
  indigo: 'bg-indigo-500/10 text-indigo-700 dark:text-indigo-300',
  blue: 'bg-blue-500/15 text-blue-700 dark:text-blue-300',
  green: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300',
  red: 'bg-red-500/15 text-red-700 dark:text-red-300',
  orange: 'bg-orange-500/15 text-orange-700 dark:text-orange-300',
  amber: 'bg-amber-500/15 text-amber-800 dark:text-amber-300',
}

export function Badge({
  tone,
  pulse = false,
  children,
}: {
  tone: Tone
  pulse?: boolean
  children: ReactNode
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap ${TONES[tone]}`}
    >
      <span className={`size-1.5 rounded-full bg-current ${pulse ? 'animate-pulse' : ''}`} />
      {children}
    </span>
  )
}

const JOB_TONE: Record<JobStatus, Tone> = {
  pending: 'neutral',
  received: 'sky',
  queued: 'indigo',
  running: 'blue',
  done: 'green',
  failed: 'red',
  rejected: 'orange',
  cancelled: 'neutral',
}

export function JobStatusBadge({ status }: { status: JobStatus }) {
  return (
    <Badge tone={JOB_TONE[status]} pulse={status === 'pending' || status === 'running'}>
      {STATUS_LABEL[status]}
    </Badge>
  )
}

const ROBOT_TONE: Record<RobotState, Tone> = {
  idle: 'green',
  busy: 'blue',
  paused: 'amber',
  offline: 'neutral',
}

const ROBOT_LABEL: Record<RobotState, string> = {
  idle: 'Idle',
  busy: 'Busy',
  paused: 'Paused',
  offline: 'Offline',
}

export function RobotStateBadge({ state, known }: { state: RobotState; known: boolean }) {
  if (!known) return <Badge tone="neutral">Unknown</Badge>
  return (
    <Badge tone={ROBOT_TONE[state]} pulse={state === 'busy' || state === 'paused'}>
      {ROBOT_LABEL[state]}
    </Badge>
  )
}
