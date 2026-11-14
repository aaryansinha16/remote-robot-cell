import { useEffect, useState } from 'react'
import { api, errorMessage } from '../lib/api'
import { clock, duration, humanize } from '../lib/format'
import type { Job, JobDetail } from '../lib/types'
import { JobStatusBadge } from './Badge'

export function JobTimeline({ job }: { job: Job }) {
  const [detail, setDetail] = useState<JobDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api
      .jobDetail(job.job_id)
      .then((result) => !cancelled && setDetail(result))
      .catch((err: unknown) => !cancelled && setError(errorMessage(err)))
    return () => {
      cancelled = true
    }
  }, [job.job_id, job.updated_at])

  if (error) return <p className="text-sm text-red-600">{error}</p>
  if (!detail) return <p className="text-sm text-muted">Loading history</p>

  return (
    <div className="grid gap-4 md:grid-cols-[1fr_auto]">
      <ol className="space-y-2">
        {detail.events.map((event, index) => {
          const previous = detail.events[index - 1]
          return (
            <li key={event.seq} className="flex items-center gap-3 text-sm">
              <span className="w-16 shrink-0 font-mono text-xs text-muted">
                {clock(event.at)}
              </span>
              <JobStatusBadge status={event.status} />
              <span className="text-xs text-muted">
                {event.seq === 0 ? 'created in the cloud' : `reported by the site, seq ${event.seq}`}
                {previous ? `, +${duration(Math.max(0, event.at - previous.at))}` : ''}
                {event.reason ? `, ${humanize(event.reason)}` : ''}
              </span>
            </li>
          )
        })}
      </ol>
      <dl className="grid grid-cols-[auto_auto] gap-x-4 gap-y-1 text-xs text-muted">
        <dt>Job id</dt>
        <dd className="font-mono text-ink">{detail.job_id}</dd>
        <dt>Sent to site</dt>
        <dd className="text-ink">
          {detail.send_attempts} {detail.send_attempts === 1 ? 'time' : 'times'}
        </dd>
        <dt>Must start by</dt>
        <dd className="text-ink">{clock(detail.start_deadline)}</dd>
      </dl>
    </div>
  )
}
