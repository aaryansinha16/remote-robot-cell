import { type FormEvent, useRef, useState } from 'react'
import { api, errorMessage, newRequestKey } from '../lib/api'
import type { Robot } from '../lib/types'

const VALIDITY = [
  { label: '5 minutes', seconds: 300 },
  { label: '30 minutes', seconds: 1800 },
  { label: '2 hours', seconds: 7200 },
  { label: '24 hours', seconds: 86_400 },
]

function Choice({
  selected,
  disabled,
  onClick,
  children,
}: {
  selected: boolean
  disabled?: boolean
  onClick: () => void
  children: string
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className={`min-w-10 rounded-md border px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-35 ${
        selected
          ? 'border-blue-600 bg-blue-600 text-white'
          : 'border-line bg-surface hover:border-zinc-400'
      }`}
    >
      {children}
    </button>
  )
}

export function JobForm({
  robots,
  siteOnline,
  onCreated,
}: {
  robots: Robot[]
  siteOnline: boolean
  onCreated: (message: string) => void
}) {
  const [robotId, setRobotId] = useState<string | null>(null)
  const [pick, setPick] = useState<string | null>(null)
  const [place, setPlace] = useState<string | null>(null)
  const [validFor, setValidFor] = useState(1800)
  const [error, setError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const requestKey = useRef(newRequestKey())

  const enrolled = robots.filter((robot) => robot.enrollment === 'enrolled')
  const robot = enrolled.find((r) => r.robot_id === robotId) ?? enrolled[0] ?? null
  const ready = robot !== null && pick !== null && place !== null && !sending

  const change = (apply: () => void) => {
    requestKey.current = newRequestKey()
    setError(null)
    apply()
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (!robot || !pick || !place) return
    setSending(true)
    try {
      const job = await api.createJob(
        { robot_id: robot.robot_id, pick, place, valid_for_s: validFor },
        requestKey.current,
      )
      requestKey.current = newRequestKey()
      setPick(null)
      setPlace(null)
      onCreated(`Job #${job.id} sent to ${job.robot_id}`)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSending(false)
    }
  }

  return (
    <form onSubmit={submit} className="flex flex-col rounded-xl border border-line bg-surface p-5">
      <h2 className="font-semibold">New job</h2>
      <p className="text-xs text-muted">The site decides whether and when it runs.</p>

      {robots.length === 0 ? (
        <p className="mt-4 text-sm text-muted">Waiting for the site to report its robots.</p>
      ) : enrolled.length === 0 ? (
        <p className="mt-4 text-sm text-muted">Enroll a robot to start sending jobs.</p>
      ) : (
        <div className="mt-4 space-y-4">
          <fieldset>
            <legend className="mb-1.5 text-xs font-medium text-muted">Robot</legend>
            <div className="flex flex-wrap gap-2">
              {robots.map((r) => (
                <Choice
                  key={r.robot_id}
                  selected={robot?.robot_id === r.robot_id}
                  disabled={r.enrollment !== 'enrolled'}
                  onClick={() =>
                    change(() => {
                      setRobotId(r.robot_id)
                      setPick(null)
                      setPlace(null)
                    })
                  }
                >
                  {r.robot_id}
                </Choice>
              ))}
            </div>
          </fieldset>
          <fieldset>
            <legend className="mb-1.5 text-xs font-medium text-muted">Pick from</legend>
            <div className="flex flex-wrap gap-2">
              {robot?.stations.map((station) => (
                <Choice
                  key={station}
                  selected={pick === station}
                  onClick={() =>
                    change(() => {
                      setPick(station)
                      if (place === station) setPlace(null)
                    })
                  }
                >
                  {station}
                </Choice>
              ))}
            </div>
          </fieldset>
          <fieldset>
            <legend className="mb-1.5 text-xs font-medium text-muted">Place at</legend>
            <div className="flex flex-wrap gap-2">
              {robot?.stations.map((station) => (
                <Choice
                  key={station}
                  selected={place === station}
                  disabled={station === pick}
                  onClick={() => change(() => setPlace(station))}
                >
                  {station}
                </Choice>
              ))}
            </div>
          </fieldset>
          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-muted">Must start within</span>
            <select
              value={validFor}
              onChange={(event) => change(() => setValidFor(Number(event.target.value)))}
              className="w-full rounded-md border border-line bg-surface px-2.5 py-1.5 text-sm"
            >
              {VALIDITY.map((option) => (
                <option key={option.seconds} value={option.seconds}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}

      <div className="mt-auto pt-5">
        {error && <p className="mb-2 text-sm text-red-600 dark:text-red-400">{error}</p>}
        {!siteOnline && enrolled.length > 0 && (
          <p className="mb-2 text-xs text-muted">
            The site is offline. The job will wait as pending until it reconnects.
          </p>
        )}
        <button
          type="submit"
          disabled={!ready}
          className="w-full rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {sending ? 'Sending' : pick && place ? `Send ${pick} to ${place}` : 'Send job'}
        </button>
      </div>
    </form>
  )
}
