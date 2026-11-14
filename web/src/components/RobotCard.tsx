import { useState } from 'react'
import { api, errorMessage } from '../lib/api'
import { STEP_LABEL, ago, humanize, pauseDetail, pauseHeadline } from '../lib/format'
import type { Job, Robot } from '../lib/types'
import { JobStatusBadge, RobotStateBadge } from './Badge'

function jobNumber(jobs: Map<string, Job>, jobId: string): string {
  const job = jobs.get(jobId)
  return job ? `#${job.id}` : jobId.slice(0, 6)
}

export function RobotCard({
  robot,
  jobs,
  recent,
  sharedStations,
  siteOnline,
  now,
  onError,
}: {
  robot: Robot
  jobs: Map<string, Job>
  recent: Job[]
  sharedStations: Set<string>
  siteOnline: boolean
  now: number
  onError: (message: string) => void
}) {
  const [busy, setBusy] = useState(false)
  const enrolled = robot.enrollment === 'enrolled'

  const act = async (action: (id: string) => Promise<unknown>) => {
    setBusy(true)
    try {
      await action(robot.robot_id)
    } catch (error) {
      onError(errorMessage(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section
      className={`flex flex-col rounded-xl border border-line bg-surface p-5 ${siteOnline ? '' : 'opacity-75'}`}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="font-semibold">{robot.robot_id}</h2>
          <p className="text-xs text-muted">
            {robot.cell} · {robot.model} · {robot.serial}
          </p>
        </div>
        <RobotStateBadge state={robot.state} known={siteOnline} />
      </div>

      <div className="mt-4 grid grid-cols-3 gap-2">
        {robot.stations.map((station) => {
          const role =
            robot.current?.pick === station
              ? 'pick'
              : robot.current?.place === station
                ? 'place'
                : null
          const waiting = robot.waiting_for === station
          return (
            <div
              key={station}
              className={`rounded-lg border px-3 py-2 ${
                waiting
                  ? 'border-amber-500/50 bg-amber-500/10'
                  : role
                    ? 'border-blue-500/50 bg-blue-500/10'
                    : 'border-line bg-sunken'
              }`}
            >
              <div className="font-mono text-lg leading-tight font-semibold">{station}</div>
              <div className="text-[11px] text-muted">
                {waiting ? 'waiting' : (role ?? (sharedStations.has(station) ? 'shared' : 'station'))}
              </div>
            </div>
          )
        })}
      </div>

      {!enrolled && (
        <div className="mt-4 rounded-lg border border-dashed border-line bg-sunken px-3 py-2.5 text-sm">
          {robot.request === 'enroll' ? (
            <p className="text-muted">Enrollment requested. Waiting for the site to confirm.</p>
          ) : (
            <div className="flex items-center justify-between gap-3">
              <p className="text-muted">Not enrolled. The site rejects jobs until it is.</p>
              <button
                type="button"
                disabled={busy}
                onClick={() => act(api.enroll)}
                className="shrink-0 rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700 disabled:opacity-50"
              >
                Enroll
              </button>
            </div>
          )}
          {robot.last_error && (
            <p className="mt-1 text-xs text-red-600 dark:text-red-400">
              Site refused: {humanize(robot.last_error)}
            </p>
          )}
        </div>
      )}

      {robot.pause && (
        <div className="mt-4 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2.5">
          <p className="text-sm font-medium text-amber-800 dark:text-amber-300">
            Paused: {pauseHeadline(robot.pause)}
          </p>
          <p className="mt-0.5 text-xs text-amber-800/80 dark:text-amber-300/80">
            {pauseDetail(robot.pause)}
          </p>
        </div>
      )}

      {robot.current && (
        <div className="mt-4">
          <div className="flex items-baseline justify-between text-sm">
            <span>
              <span className="font-medium">{jobNumber(jobs, robot.current.job_id)}</span>{' '}
              {robot.current.pick} to {robot.current.place}
            </span>
            <span className="text-xs text-muted">
              {robot.pause
                ? 'halted'
                : robot.waiting_for
                  ? `waiting for ${robot.waiting_for}`
                  : STEP_LABEL[robot.current.step]}
            </span>
          </div>
          <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-sunken">
            <div
              className={`h-full rounded-full transition-[width] duration-500 ${robot.pause ? 'bg-amber-500' : 'bg-blue-600'}`}
              style={{ width: `${Math.round(robot.current.progress * 100)}%` }}
            />
          </div>
        </div>
      )}

      {robot.waiting_for && (
        <p className="mt-3 text-xs text-muted">
          Waiting for station {robot.waiting_for}, the other arm is using it.
        </p>
      )}

      {recent.length > 0 && (
        <div className="mt-4">
          <p className="mb-1.5 text-xs text-muted">Recent jobs</p>
          <ul className="space-y-1.5">
            {recent.map((job) => (
              <li key={job.job_id} className="flex items-center justify-between gap-3 text-sm">
                <span>
                  <span className="font-medium">#{job.id}</span>{' '}
                  <span className="font-mono text-xs text-muted">
                    {job.pick} to {job.place}
                  </span>
                </span>
                <span className="flex items-center gap-2">
                  <span className="text-xs text-muted">{ago(job.updated_at, now)}</span>
                  <JobStatusBadge status={job.status} />
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-auto flex items-center justify-between gap-3 pt-4 text-xs text-muted">
        <span>
          {robot.queue.length
            ? `Queue: ${robot.queue.map((id) => jobNumber(jobs, id)).join(', ')}`
            : 'Queue empty'}
        </span>
        <span className="flex items-center gap-3">
          {enrolled && robot.request === null && (
            <button
              type="button"
              disabled={busy || robot.state === 'busy' || robot.queue.length > 0}
              onClick={() => act(api.unenroll)}
              className="underline-offset-2 hover:underline disabled:opacity-40 disabled:no-underline"
            >
              Unenroll
            </button>
          )}
          {robot.request === 'unenroll' && <span>Unenrolling</span>}
        </span>
      </div>
    </section>
  )
}
