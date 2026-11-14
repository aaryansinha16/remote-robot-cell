import { useEffect, useMemo, useReducer, useRef, useState } from 'react'
import type { Job, LiveMessage, Notice, Robot, Site } from './types'

export type Connection = 'connecting' | 'live' | 'reconnecting'

interface Store {
  site: Site | null
  robots: Map<string, Robot>
  jobs: Map<string, Job>
  connection: Connection
}

type Action = Exclude<LiveMessage, { type: 'notice' }> | { type: 'connection'; value: Connection }

const initial: Store = { site: null, robots: new Map(), jobs: new Map(), connection: 'connecting' }

// Updates can race with the snapshot or with each other, so a record only ever moves forward.
function reduce(store: Store, action: Action): Store {
  switch (action.type) {
    case 'snapshot':
      return {
        ...store,
        site: action.data.site,
        robots: new Map(action.data.robots.map((robot) => [robot.robot_id, robot])),
        jobs: new Map(action.data.jobs.map((job) => [job.job_id, job])),
      }
    case 'site':
      return { ...store, site: action.data }
    case 'robot': {
      const current = store.robots.get(action.data.robot_id)
      if (current && action.data.updated_at < current.updated_at) return store
      return { ...store, robots: new Map(store.robots).set(action.data.robot_id, action.data) }
    }
    case 'job': {
      const current = store.jobs.get(action.data.job_id)
      if (current && action.data.updated_at < current.updated_at) return store
      return { ...store, jobs: new Map(store.jobs).set(action.data.job_id, action.data) }
    }
    case 'connection':
      return { ...store, connection: action.value }
  }
}

function socketUrl(): string {
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${scheme}://${window.location.host}/api/ws`
}

export function useLive(onNotice: (notice: Notice) => void) {
  const [store, dispatch] = useReducer(reduce, initial)
  const noticeRef = useRef(onNotice)

  useEffect(() => {
    noticeRef.current = onNotice
  }, [onNotice])

  useEffect(() => {
    let socket: WebSocket | null = null
    let timer: ReturnType<typeof setTimeout> | undefined
    let attempt = 0
    let stopped = false

    const connect = () => {
      socket = new WebSocket(socketUrl())
      socket.onopen = () => {
        attempt = 0
        dispatch({ type: 'connection', value: 'live' })
      }
      socket.onmessage = (event: MessageEvent<string>) => {
        const message = JSON.parse(event.data) as LiveMessage
        if (message.type === 'notice') noticeRef.current(message.data)
        else dispatch(message)
      }
      socket.onclose = () => {
        if (stopped) return
        dispatch({ type: 'connection', value: 'reconnecting' })
        timer = setTimeout(connect, Math.min(10_000, 500 * 2 ** attempt++))
      }
    }

    connect()
    return () => {
      stopped = true
      clearTimeout(timer)
      socket?.close()
    }
  }, [])

  return useMemo(
    () => ({
      site: store.site,
      connection: store.connection,
      robots: [...store.robots.values()].sort((a, b) => a.robot_id.localeCompare(b.robot_id)),
      jobs: [...store.jobs.values()].sort((a, b) => b.id - a.id),
      jobsById: store.jobs,
    }),
    [store],
  )
}

export function useNow(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(id)
  }, [intervalMs])
  return now
}
