import { Floor } from './floor.js'

const STATE_LABEL = { idle: 'Idle', busy: 'Busy', paused: 'Paused', offline: 'Offline' }

const STEP_LABEL = {
  move_to_pick: 'moving to pick',
  pick: 'picking',
  move_to_place: 'moving to place',
  place: 'placing',
}

const MAX_EVENTS = 80

const $ = (id) => document.getElementById(id)

function element(tag, className, text) {
  const node = document.createElement(tag)
  if (className) node.className = className
  if (text !== undefined) node.textContent = text
  return node
}

const timeFormat = new Intl.DateTimeFormat(undefined, {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

function stamp(ms) {
  return `${timeFormat.format(new Date(ms))}.${String(ms % 1000).padStart(3, '0')}`
}

let toastTimer = 0

function toast(message) {
  const node = $('toast')
  node.textContent = message
  node.hidden = false
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => {
    node.hidden = true
  }, 4000)
}

async function call(method, path, body) {
  try {
    const response = await fetch(`/api${path}`, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined,
    })
    if (!response.ok) {
      const detail = await response.json().catch(() => null)
      toast(typeof detail?.detail === 'string' ? detail.detail : `Request failed (${response.status})`)
    }
  } catch {
    toast('The simulator is not reachable.')
  }
}

function pauseText(pause) {
  switch (pause.reason) {
    case 'person_in_cell': {
      const parts = [`track ${pause.track_ids.join(', ')}`]
      if (pause.latency_ms !== null) parts.push(`stopped ${pause.latency_ms} ms after detection`)
      return [`Person in ${pause.cells.join(' and ') || 'the cell'}`, parts.join(' · ')]
    }
    case 'safety_feed_lost':
      return ['Safety feed lost', 'no fusion frames, so the site treats every cell as unsafe']
    default:
      return ['Waiting for the safety feed', 'robots stay paused until the first frame arrives']
  }
}

class RobotCard {
  constructor(spec) {
    this.spec = spec
    this.el = element('div', 'robot')
    const head = element('div', 'robot-head')
    const title = element('div')
    title.append(
      element('div', 'robot-name', spec.id),
      element('div', 'robot-meta', `${spec.cell} · stations ${spec.stations.join(' ')}`),
    )
    this.badge = element('span', 'badge')
    head.append(title, this.badge)

    const job = element('div', 'robot-job')
    this.what = element('div')
    const bar = element('div', 'bar')
    this.fill = element('span')
    bar.append(this.fill)
    job.append(this.what, bar)

    this.notice = element('div', 'notice')
    this.headline = element('strong')
    this.detail = element('span')
    this.notice.append(this.headline, this.detail)

    const faults = element('div', 'faults')
    this.plug = element('button', 'btn small')
    this.plug.type = 'button'
    this.stall = element('button', 'btn small')
    this.stall.type = 'button'
    faults.append(this.plug, this.stall)
    this.plug.addEventListener('click', () => this.setFault({ connected: !this.fault.connected }))
    this.stall.addEventListener('click', () => this.setFault({ stalled: !this.fault.stalled }))

    this.el.append(head, job, this.notice, faults)
    this.fault = { connected: true, stalled: false }
  }

  setFault(change) {
    call('POST', `/robots/${this.spec.id}/fault`, { ...this.fault, ...change })
  }

  update(telemetry, fault) {
    this.fault = fault ?? this.fault
    const live = Boolean(telemetry?.live)
    const state = live ? telemetry.state : null

    this.el.classList.toggle('paused', state === 'paused')
    this.badge.className = `badge ${state ?? ''}`
    this.badge.textContent = state ? STATE_LABEL[state] : 'No telemetry'

    const current = live ? telemetry.current : null
    if (live && telemetry.waiting_for) {
      const route = current ? `${current.pick} to ${current.place} · ` : ''
      this.what.textContent = `${route}waiting for station ${telemetry.waiting_for}`
    } else if (current) {
      const percent = Math.round(current.progress * 100)
      this.what.textContent = `${current.pick} to ${current.place} · ${STEP_LABEL[current.step]} · ${percent}%`
    } else {
      this.what.innerHTML = '<span class="muted">No job running</span>'
    }
    this.fill.style.width = `${current ? Math.round(current.progress * 100) : 0}%`

    let notice = null
    if (!live) notice = ['No telemetry from the site server', 'is the onprem container running?', true]
    else if (state === 'offline') notice = ['Heartbeat lost', 'a running job fails as robot_disconnected', true]
    else if (telemetry.pause) notice = [...pauseText(telemetry.pause), false]
    this.notice.hidden = notice === null
    if (notice) {
      this.headline.textContent = notice[0]
      this.detail.textContent = notice[1]
      this.notice.classList.toggle('bad', notice[2])
    }

    this.plug.textContent = this.fault.connected ? 'Unplug' : 'Plug in'
    this.plug.classList.toggle('on', !this.fault.connected)
    this.stall.textContent = this.fault.stalled ? 'Release' : 'Stall'
    this.stall.classList.toggle('on', this.fault.stalled)
  }
}

class PeopleList {
  constructor(root) {
    this.root = root
    this.rows = new Map()
    this.empty = element('li', 'empty', 'Nobody on the floor apart from aisle traffic.')
    root.append(this.empty)
  }

  update(actors) {
    const people = actors.filter((actor) => actor.kind === 'person' && !actor.ambient)
    const seen = new Set()
    for (const person of people) {
      seen.add(person.gid)
      let row = this.rows.get(person.gid)
      if (!row) {
        row = this.create(person.gid)
        this.rows.set(person.gid, row)
        this.root.append(row.el)
      }
      const inside = person.cells.length > 0
      row.chip.textContent = inside ? person.cells.join(' + ') : 'outside cells'
      row.chip.classList.toggle('inside', inside)
      row.status.textContent = person.leaving ? 'heading out' : person.moving ? 'walking' : 'standing'
      row.leave.disabled = person.leaving
    }
    for (const [gid, row] of this.rows) {
      if (!seen.has(gid)) {
        row.el.remove()
        this.rows.delete(gid)
      }
    }
    this.empty.hidden = this.rows.size > 0
  }

  create(gid) {
    const el = element('li')
    const who = element('div', 'who')
    const status = element('span')
    const chip = element('span', 'chip')
    const where = element('div', 'where')
    where.append(status, chip)
    who.append(element('div', 'name', `Person ${gid}`), where)
    const leave = element('button', 'btn small', 'Send out')
    leave.type = 'button'
    leave.addEventListener('click', () => call('POST', `/people/${gid}/leave`))
    const drop = element('button', 'btn small', 'Drop track')
    drop.type = 'button'
    drop.title = 'The tracker loses this person without them walking out'
    drop.addEventListener('click', () => call('DELETE', `/people/${gid}`))
    el.append(who, leave, drop)
    return { el, chip, status, leave }
  }
}

class EventLog {
  constructor(root) {
    this.root = root
    this.session = null
    this.lastId = 0
    this.empty = element('li', 'empty', 'Nothing has happened yet.')
    root.append(this.empty)
  }

  update(boot, generation, events) {
    const session = `${boot}:${generation}`
    if (session !== this.session) {
      this.session = session
      this.lastId = 0
      this.root.replaceChildren()
    }
    const animate = this.lastId > 0
    for (const event of events) {
      if (event.id <= this.lastId) continue
      this.empty.remove()
      const row = element('li', `kind-${event.kind}${animate ? ' fresh' : ''}`)
      row.append(element('time', '', stamp(event.at)), element('span', 'dot'), element('span', '', event.text))
      this.root.prepend(row)
      this.lastId = event.id
    }
    while (this.root.children.length > MAX_EVENTS) this.root.lastElementChild.remove()
    if (!this.root.children.length) this.root.append(this.empty)
  }
}

function setStatus(id, tone, label) {
  const node = $(id)
  node.className = `status ${tone}`
  node.lastElementChild.textContent = label
}

function renderStatus(view, linked) {
  if (!linked) {
    setStatus('status-broker', '', 'Site broker')
    setStatus('status-feed', '', 'Fusion feed')
    setStatus('status-site', '', 'Site server')
    return
  }
  const siteLive = Object.values(view.robots).some((robot) => robot.live)
  setStatus('status-broker', view.connected ? 'ok' : 'bad', view.connected ? 'Site broker' : 'Site broker down')
  setStatus('status-feed', view.feed_on ? 'ok' : 'warn', view.feed_on ? `Fusion feed · ${view.rate_hz} Hz` : 'Fusion feed stopped')
  setStatus('status-site', siteLive ? 'ok' : 'bad', siteLive ? 'Site server' : 'Site server silent')
}

function renderBanner(view, linked) {
  const banner = $('banner')
  let message = null
  let tone = ''
  if (!linked) message = 'Lost the connection to the simulator. Reconnecting.'
  else if (!view.connected) message = 'The site broker is unreachable, so nothing is being published.'
  else if (!view.feed_on) [message, tone] = ['Fusion feed stopped. The site pauses every robot within a second.', 'warn']
  else if (!Object.values(view.robots).some((robot) => robot.live)) {
    ;[message, tone] = ['No telemetry from the site server. Arms are shown where they were last seen.', 'warn']
  }
  banner.hidden = message === null
  if (message) {
    banner.textContent = message
    banner.className = `banner ${tone}`
  }
}

function renderFeed(view) {
  const toggle = $('feed')
  toggle.setAttribute('aria-checked', String(view.feed_on))
  const objects = view.actors.length
  $('feed-detail').textContent = view.feed_on
    ? `Publishing fusion/objects at ${view.rate_hz} Hz with ${objects} tracked object${objects === 1 ? '' : 's'}.`
    : 'Stopped. People are still on the floor, but the site can no longer see them.'
}

async function start() {
  const response = await fetch('/api/site')
  const site = await response.json()
  let view = null
  let linked = false

  const spawn = (body) => call('POST', '/people', body)
  const leaveAll = () => call('POST', '/people/leave')
  const toggleFeed = () => view && call('POST', '/feed', { on: !view.feed_on })

  const floor = new Floor($('floor'), site, {
    onFloorClick: (x, y) => spawn({ x, y }),
    onPersonClick: (gid) => call('POST', `/people/${gid}/leave`),
  })

  const cards = new Map(site.robots.map((spec) => [spec.id, new RobotCard(spec)]))
  $('robots').append(...[...cards.values()].map((card) => card.el))
  const people = new PeopleList($('people'))
  const log = new EventLog($('events'))

  document.querySelectorAll('[data-spawn]').forEach((button) => {
    button.addEventListener('click', () => spawn(JSON.parse(button.dataset.spawn)))
  })
  $('leave-all').addEventListener('click', leaveAll)
  $('clear-log').addEventListener('click', () => call('DELETE', '/events'))
  $('feed').addEventListener('click', toggleFeed)

  document.addEventListener('keydown', (event) => {
    if (event.metaKey || event.ctrlKey || event.altKey || event.repeat) return
    if (event.target instanceof HTMLElement && event.target.closest('input, textarea, select')) return
    const action = {
      1: () => spawn({ cell: 'cell-1' }),
      2: () => spawn({ cell: 'cell-2' }),
      c: () => spawn({ station: 'C' }),
      x: leaveAll,
      f: toggleFeed,
    }[event.key.toLowerCase()]
    if (action) {
      event.preventDefault()
      action()
    }
  })

  const render = () => {
    renderStatus(view, linked)
    renderBanner(view, linked)
    if (!view) return
    floor.update(view)
    for (const [robotId, card] of cards) card.update(view.robots[robotId], view.faults[robotId])
    people.update(view.actors)
    renderFeed(view)
    log.update(view.boot, view.log, view.events)
  }

  const source = new EventSource('/api/live')
  source.addEventListener('message', (event) => {
    view = JSON.parse(event.data)
    linked = true
    render()
  })
  source.addEventListener('error', () => {
    linked = false
    render()
  })
}

start().catch(() => toast('Could not load the site map from the simulator.'))
