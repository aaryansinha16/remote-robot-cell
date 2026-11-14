import { Fragment, useState } from 'react'
import { api, errorMessage } from '../lib/api'
import { STEP_LABEL, ago, clock, humanize } from '../lib/format'
import { ACTIVE_STATUSES, CANCELLABLE_STATUSES, type Job, type Robot } from '../lib/types'
import { JobStatusBadge } from './Badge'
import { JobTimeline } from './JobTimeline'

type Filter = 'all' | 'active' | 'finished'

function detail(job: Job, robot: Robot | undefined, siteOnline: boolean, now: number): string {
  if (job.cancel_requested) return 'cancel requested'
  if (job.cancel_error) return `cancel refused, ${humanize(job.cancel_error)}`
  switch (job.status) {
    case 'pending':
      if (now > job.start_deadline) return 'start deadline passed, the site will reject it'
      if (!siteOnline) return 'waiting for the site to come back'
      return job.send_attempts > 1 ? `sent ${job.send_attempts} times` : 'waiting for the site'
    case 'queued': {
      const position = robot?.queue.indexOf(job.job_id) ?? -1
      return position >= 0 ? `position ${position + 1} in queue` : 'in queue'
    }
    case 'running': {
      const current = robot?.current?.job_id === job.job_id ? robot.current : null
      if (robot?.pause) return 'paused by the site'
      if (robot?.waiting_for && current) return `waiting for station ${robot.waiting_for}`
      return current
        ? `${STEP_LABEL[current.step]}, ${Math.round(current.progress * 100)}%`
        : 'running'
    }
    default:
      return job.reason ? humanize(job.reason) : ''
  }
}

export function JobTable({
  jobs,
  robots,
  siteOnline,
  now,
  onError,
}: {
  jobs: Job[]
  robots: Robot[]
  siteOnline: boolean
  now: number
  onError: (message: string) => void
}) {
  const [filter, setFilter] = useState<Filter>('all')
  const [open, setOpen] = useState<string | null>(null)
  const robotsById = new Map(robots.map((robot) => [robot.robot_id, robot]))

  const visible = jobs.filter((job) => {
    const active = ACTIVE_STATUSES.includes(job.status)
    return filter === 'all' || (filter === 'active' ? active : !active)
  })

  const cancel = async (job: Job) => {
    try {
      await api.cancelJob(job.job_id)
    } catch (error) {
      onError(errorMessage(error))
    }
  }

  return (
    <section className="rounded-xl border border-line bg-surface">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-3">
        <h2 className="font-semibold">Jobs</h2>
        <div className="flex rounded-md border border-line p-0.5 text-xs">
          {(['all', 'active', 'finished'] as const).map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => setFilter(value)}
              className={`rounded px-2.5 py-1 capitalize ${filter === value ? 'bg-sunken font-medium' : 'text-muted'}`}
            >
              {value}
            </button>
          ))}
        </div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] text-sm">
          <thead>
            <tr className="text-left text-xs text-muted">
              <th className="px-5 py-2 font-medium">#</th>
              <th className="px-3 py-2 font-medium">Robot</th>
              <th className="px-3 py-2 font-medium">Route</th>
              <th className="px-3 py-2 font-medium">Status</th>
              <th className="px-3 py-2 font-medium">Detail</th>
              <th className="px-3 py-2 font-medium">Created</th>
              <th className="px-3 py-2 font-medium">Updated</th>
              <th className="px-5 py-2" />
            </tr>
          </thead>
          <tbody>
            {visible.length === 0 && (
              <tr>
                <td colSpan={8} className="px-5 py-8 text-center text-muted">
                  No jobs yet.
                </td>
              </tr>
            )}
            {visible.map((job) => {
              const expanded = open === job.job_id
              const cancellable =
                CANCELLABLE_STATUSES.includes(job.status) && !job.cancel_requested
              return (
                <Fragment key={job.job_id}>
                  <tr
                    onClick={() => setOpen(expanded ? null : job.job_id)}
                    className="cursor-pointer border-t border-line hover:bg-sunken"
                  >
                    <td className="px-5 py-2.5 font-medium">{job.id}</td>
                    <td className="px-3 py-2.5">{job.robot_id}</td>
                    <td className="px-3 py-2.5 font-mono text-xs">
                      {job.pick} to {job.place}
                    </td>
                    <td className="px-3 py-2.5">
                      <JobStatusBadge status={job.status} />
                    </td>
                    <td className="px-3 py-2.5 text-muted">
                      {detail(job, robotsById.get(job.robot_id), siteOnline, now)}
                    </td>
                    <td className="px-3 py-2.5 text-muted">{clock(job.created_at)}</td>
                    <td className="px-3 py-2.5 text-muted">{ago(job.updated_at, now)}</td>
                    <td className="px-5 py-2.5 text-right">
                      {cancellable && (
                        <button
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation()
                            void cancel(job)
                          }}
                          className="rounded-md border border-line px-2.5 py-1 text-xs hover:border-red-400 hover:text-red-600"
                        >
                          Cancel
                        </button>
                      )}
                    </td>
                  </tr>
                  {expanded && (
                    <tr className="bg-sunken">
                      <td colSpan={8} className="px-5 py-4">
                        <JobTimeline job={job} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}
