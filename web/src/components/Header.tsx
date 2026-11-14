import { ago, clock } from '../lib/format'
import type { Connection } from '../lib/live'
import type { Site } from '../lib/types'
import { Badge } from './Badge'

export function Header({
  site,
  connection,
  now,
}: {
  site: Site | null
  connection: Connection
  now: number
}) {
  return (
    <header className="border-b border-line bg-surface">
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 sm:px-6">
        <div className="flex items-center gap-3">
          <img src="/favicon.svg" alt="" className="size-7" />
          <div>
            <h1 className="text-[15px] leading-tight font-semibold">Robot Cell</h1>
            <p className="text-xs text-muted">Operator console</p>
          </div>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-3 text-xs">
          {site && (
            <span className="flex items-center gap-2">
              <span className="font-medium">{site.site_id}</span>
              {site.online ? (
                <Badge tone="green">Online</Badge>
              ) : (
                <Badge tone="red">Offline</Badge>
              )}
              <span className="text-muted">
                {site.online
                  ? `heartbeat ${ago(site.last_seen, now)}`
                  : site.changed_at
                    ? `since ${clock(site.changed_at)}`
                    : 'never connected'}
              </span>
            </span>
          )}
          <span className="h-4 w-px bg-line" />
          {connection === 'live' ? (
            <Badge tone="green" pulse>
              Live
            </Badge>
          ) : (
            <Badge tone="amber" pulse>
              {connection === 'connecting' ? 'Connecting' : 'Reconnecting'}
            </Badge>
          )}
        </div>
      </div>
    </header>
  )
}

export function OfflineBanner({ site }: { site: Site }) {
  return (
    <div className="rounded-lg border border-red-500/30 bg-red-500/5 px-4 py-3 text-sm">
      <p className="font-medium text-red-700 dark:text-red-300">
        The on-prem server for {site.site_id} is offline
        {site.changed_at ? ` since ${clock(site.changed_at)}` : ''}.
      </p>
      <p className="mt-0.5 text-muted">
        New jobs stay pending and run once it reconnects. Robot states below are the last ones the
        site reported.
      </p>
    </div>
  )
}
