export type JobStatus =
  | 'pending'
  | 'received'
  | 'queued'
  | 'running'
  | 'done'
  | 'failed'
  | 'rejected'
  | 'cancelled'

export type RobotState = 'idle' | 'busy' | 'paused' | 'offline'
export type PauseReason = 'person_in_cell' | 'safety_feed_lost' | 'awaiting_safety_feed'
export type JobStep = 'move_to_pick' | 'pick' | 'move_to_place' | 'place'

export interface Pause {
  reason: PauseReason
  since: number
  cells: string[]
  track_ids: number[]
  latency_ms: number | null
}

export interface CurrentJob {
  job_id: string
  pick: string
  place: string
  step: JobStep
  progress: number
}

export interface Robot {
  robot_id: string
  site_id: string
  serial: string
  model: string
  cell: string
  stations: string[]
  enrollment: 'enrolled' | 'unenrolled'
  connected: boolean
  state: RobotState
  pause: Pause | null
  current: CurrentJob | null
  queue: string[]
  waiting_for: string | null
  updated_at: number
  request: 'enroll' | 'unenroll' | null
  last_error: string | null
}

export interface Site {
  site_id: string
  online: boolean
  boot_id: string | null
  version: string | null
  last_seen: number | null
  changed_at: number | null
}

export interface Job {
  id: number
  job_id: string
  site_id: string
  robot_id: string
  pick: string
  place: string
  status: JobStatus
  seq: number
  reason: string | null
  created_at: number
  updated_at: number
  start_deadline: number
  send_attempts: number
  cancel_requested: boolean
  cancel_error: string | null
}

export interface JobEvent {
  seq: number
  status: JobStatus
  reason: string | null
  at: number
  received_at: number
}

export interface JobDetail extends Job {
  events: JobEvent[]
}

export interface Snapshot {
  site: Site
  robots: Robot[]
  jobs: Job[]
}

export interface Notice {
  level: 'info' | 'warning' | 'error'
  message: string
}

export type LiveMessage =
  | { type: 'snapshot'; data: Snapshot }
  | { type: 'site'; data: Site }
  | { type: 'robot'; data: Robot }
  | { type: 'job'; data: Job }
  | { type: 'jobs_cleared'; data: { job_ids: string[] } }
  | { type: 'notice'; data: Notice }

export interface NewJob {
  robot_id: string
  pick: string
  place: string
  valid_for_s: number
}

export const ACTIVE_STATUSES: readonly JobStatus[] = ['pending', 'received', 'queued', 'running']
export const CANCELLABLE_STATUSES: readonly JobStatus[] = ['pending', 'received', 'queued']
