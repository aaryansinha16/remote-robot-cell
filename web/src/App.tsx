import { useCallback, useMemo } from 'react'
import { Header, OfflineBanner } from './components/Header'
import { JobForm } from './components/JobForm'
import { JobTable } from './components/JobTable'
import { RobotCard } from './components/RobotCard'
import { Toasts } from './components/Toasts'
import { useLive, useNow } from './lib/live'
import { useToasts } from './lib/toasts'

export default function App() {
  const { toasts, push, dismiss } = useToasts()
  const live = useLive(push)
  const now = useNow()
  const siteOnline = live.site?.online ?? false

  const fail = useCallback((message: string) => push({ level: 'error', message }), [push])
  const info = useCallback((message: string) => push({ level: 'info', message }), [push])

  const sharedStations = useMemo(() => {
    const seen = new Set<string>()
    const shared = new Set<string>()
    for (const station of live.robots.flatMap((robot) => robot.stations)) {
      if (seen.has(station)) shared.add(station)
      seen.add(station)
    }
    return shared
  }, [live.robots])

  return (
    <div className="min-h-screen">
      <Header site={live.site} connection={live.connection} now={now} />
      <main className="mx-auto max-w-7xl space-y-6 px-4 py-6 sm:px-6">
        {live.site && !live.site.online && <OfflineBanner site={live.site} />}
        <div className="grid gap-6 md:grid-cols-2 xl:grid-cols-3">
          {live.robots.map((robot) => (
            <RobotCard
              key={robot.robot_id}
              robot={robot}
              jobs={live.jobsById}
              recent={live.jobs.filter((job) => job.robot_id === robot.robot_id).slice(0, 3)}
              sharedStations={sharedStations}
              siteOnline={siteOnline}
              now={now}
              onError={fail}
            />
          ))}
          <JobForm robots={live.robots} siteOnline={siteOnline} onCreated={info} />
        </div>
        <JobTable
          jobs={live.jobs}
          robots={live.robots}
          siteOnline={siteOnline}
          now={now}
          onError={fail}
        />
      </main>
      <Toasts toasts={toasts} onDismiss={dismiss} />
    </div>
  )
}
