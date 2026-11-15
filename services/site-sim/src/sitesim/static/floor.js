const UPPER_ARM = 2.0
const FOREARM = 1.85
const REST_OFFSET = 1.0
const AISLE_HALF_WIDTH = 1.3
const STATION_SIZE = 1.0
const PICK_RADIUS = 0.6
const SMOOTHING_S = 0.07
const TRAIL_LENGTH = 26
const TRAIL_EVERY_MS = 70
const MARKER_MS = 900

const TOKENS = [
  'floor', 'grid', 'grid-major', 'aisle', 'lane', 'hazard', 'cell-1', 'cell-2', 'station',
  'station-edge', 'arm', 'arm-edge', 'joint', 'part', 'person', 'ambient', 'forklift', 'halo',
  'label', 'muted', 'line', 'surface', 'green', 'blue', 'amber', 'red', 'neutral', 'tooltip',
  'tooltip-ink',
]

const STATE_TONE = { idle: 'green', busy: 'blue', paused: 'amber', offline: 'neutral' }

const STEP_LABEL = {
  move_to_pick: 'moving to pick',
  pick: 'picking',
  move_to_place: 'moving to place',
  place: 'placing',
}

function readColors() {
  const style = getComputedStyle(document.documentElement)
  return Object.fromEntries(TOKENS.map((token) => [token, style.getPropertyValue(`--${token}`).trim()]))
}

function boundsOf(site) {
  const xs = [...site.aisle.x]
  const ys = [site.aisle.y - AISLE_HALF_WIDTH, site.aisle.y + AISLE_HALF_WIDTH]
  for (const rect of Object.values(site.cells)) {
    xs.push(...rect.x)
    ys.push(...rect.y)
  }
  const pad = 0.8
  return {
    x0: Math.min(...xs) - pad,
    x1: Math.max(...xs) + pad,
    y0: Math.min(...ys) - pad,
    y1: Math.max(...ys) + pad,
  }
}

function overlaps(cells) {
  const rects = Object.values(cells)
  const shared = []
  for (let i = 0; i < rects.length; i++) {
    for (let j = i + 1; j < rects.length; j++) {
      const x = [Math.max(rects[i].x[0], rects[j].x[0]), Math.min(rects[i].x[1], rects[j].x[1])]
      const y = [Math.max(rects[i].y[0], rects[j].y[0]), Math.min(rects[i].y[1], rects[j].y[1])]
      if (x[0] < x[1] && y[0] < y[1]) shared.push({ x, y })
    }
  }
  return shared
}

function elbowFor(base, tool, side) {
  const dx = tool[0] - base[0]
  const dy = tool[1] - base[1]
  const reach = Math.min(Math.max(Math.hypot(dx, dy), 0.25), UPPER_ARM + FOREARM - 1e-3)
  const cosine = (UPPER_ARM ** 2 + reach ** 2 - FOREARM ** 2) / (2 * UPPER_ARM * reach)
  const angle = Math.atan2(dy, dx) + side * Math.acos(Math.max(-1, Math.min(1, cosine)))
  return [base[0] + UPPER_ARM * Math.cos(angle), base[1] + UPPER_ARM * Math.sin(angle)]
}

function shortName(robotId) {
  const digits = robotId.match(/\d+$/)
  return digits ? `R${digits[0]}` : robotId
}

export class Floor {
  constructor(canvas, site, { onFloorClick, onPersonClick }) {
    this.canvas = canvas
    this.ctx = canvas.getContext('2d')
    this.site = site
    this.onFloorClick = onFloorClick
    this.onPersonClick = onPersonClick
    this.bounds = boundsOf(site)
    this.shared = overlaps(site.cells)
    this.colors = readColors()
    this.view = null
    this.bodies = new Map()
    this.markers = []
    this.pointer = null
    this.hover = null
    this.width = 0
    this.height = 0
    this.scale = 1

    matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
      this.colors = readColors()
    })
    new ResizeObserver(() => this.resize()).observe(canvas.parentElement)
    canvas.addEventListener('pointermove', (event) => this.track(event))
    canvas.addEventListener('pointerleave', () => {
      this.pointer = null
      this.hover = null
    })
    canvas.addEventListener('click', (event) => this.click(event))

    this.resize()
    this.last = performance.now()
    requestAnimationFrame((now) => this.frame(now))
  }

  update(view) {
    this.view = view
    const seen = new Set()
    for (const actor of view.actors) {
      const key = `actor:${actor.gid}`
      seen.add(key)
      this.aim(key, actor.x, actor.y)
    }
    for (const [robotId, telemetry] of Object.entries(view.robots)) {
      const key = `robot:${robotId}`
      seen.add(key)
      this.aim(key, telemetry.tool[0], telemetry.tool[1])
    }
    for (const key of this.bodies.keys()) {
      if (!seen.has(key)) this.bodies.delete(key)
    }
  }

  aim(key, x, y) {
    const body = this.bodies.get(key)
    if (body) {
      body.tx = x
      body.ty = y
    } else {
      this.bodies.set(key, { x, y, tx: x, ty: y, trail: [], trailAt: 0 })
    }
  }

  resize() {
    const width = this.canvas.parentElement.clientWidth
    if (!width || width === this.width) return
    const { x0, x1, y0, y1 } = this.bounds
    const ratio = window.devicePixelRatio || 1
    this.width = width
    this.scale = width / (x1 - x0)
    this.height = Math.round((y1 - y0) * this.scale)
    this.canvas.width = Math.round(width * ratio)
    this.canvas.height = Math.round(this.height * ratio)
    this.canvas.style.height = `${this.height}px`
    this.ctx.setTransform(ratio, 0, 0, ratio, 0, 0)
  }

  frame(now) {
    const dt = Math.min(0.1, (now - this.last) / 1000)
    this.last = now
    this.ease(dt, now)
    this.draw(now)
    requestAnimationFrame((next) => this.frame(next))
  }

  ease(dt, now) {
    const blend = 1 - Math.exp(-dt / SMOOTHING_S)
    for (const body of this.bodies.values()) {
      body.x += (body.tx - body.x) * blend
      body.y += (body.ty - body.y) * blend
      if (now - body.trailAt > TRAIL_EVERY_MS) {
        body.trail.push([body.x, body.y])
        if (body.trail.length > TRAIL_LENGTH) body.trail.shift()
        body.trailAt = now
      }
    }
  }

  X(x) {
    return (x - this.bounds.x0) * this.scale
  }

  Y(y) {
    return (this.bounds.y1 - y) * this.scale
  }

  S(metres) {
    return metres * this.scale
  }

  toWorld(event) {
    const box = this.canvas.getBoundingClientRect()
    const sx = event.clientX - box.left
    const sy = event.clientY - box.top
    return { x: this.bounds.x0 + sx / this.scale, y: this.bounds.y1 - sy / this.scale, sx, sy }
  }

  track(event) {
    this.pointer = this.toWorld(event)
    this.hover = this.personAt(this.pointer)
    this.canvas.style.cursor = this.hover === null ? 'crosshair' : 'pointer'
  }

  click(event) {
    const point = this.toWorld(event)
    const gid = this.personAt(point)
    if (gid !== null) {
      this.onPersonClick(gid)
      return
    }
    this.markers.push({ x: point.x, y: point.y, at: performance.now() })
    this.onFloorClick(Math.round(point.x * 100) / 100, Math.round(point.y * 100) / 100)
  }

  personAt(point) {
    if (!this.view) return null
    let best = null
    let distance = PICK_RADIUS
    for (const actor of this.view.actors) {
      if (actor.ambient || actor.kind !== 'person') continue
      const body = this.bodies.get(`actor:${actor.gid}`)
      if (!body) continue
      const d = Math.hypot(body.x - point.x, body.y - point.y)
      if (d < distance) {
        distance = d
        best = actor.gid
      }
    }
    return best
  }

  draw(now) {
    const { ctx } = this
    ctx.clearRect(0, 0, this.width, this.height)
    ctx.fillStyle = this.colors.floor
    ctx.fillRect(0, 0, this.width, this.height)
    if (!this.width) return

    const pulse = 0.5 + 0.5 * Math.sin(now / 240)
    const occupied = this.occupiedCells()
    this.drawGrid()
    this.drawAisle()
    this.drawCells(occupied, pulse)
    this.drawStations()
    this.site.robots.forEach((spec, index) => this.drawArm(spec, index, pulse))
    this.drawActors(now)
    this.drawMarkers(now)
    this.drawScale()
    this.drawTooltip()
  }

  occupiedCells() {
    const occupied = new Map()
    for (const actor of this.view?.actors ?? []) {
      if (actor.kind !== 'person') continue
      for (const cell of actor.cells) {
        occupied.set(cell, [...(occupied.get(cell) ?? []), actor.gid])
      }
    }
    return occupied
  }

  drawGrid() {
    const { ctx, colors } = this
    const { x0, x1, y0, y1 } = this.bounds
    ctx.lineWidth = 1
    for (let x = Math.ceil(x0); x <= x1; x++) {
      ctx.strokeStyle = x % 5 === 0 ? colors['grid-major'] : colors.grid
      const px = Math.round(this.X(x)) + 0.5
      this.line(px, 0, px, this.height)
    }
    for (let y = Math.ceil(y0); y <= y1; y++) {
      ctx.strokeStyle = y % 5 === 0 ? colors['grid-major'] : colors.grid
      const py = Math.round(this.Y(y)) + 0.5
      this.line(0, py, this.width, py)
    }
  }

  drawAisle() {
    const { ctx, colors, site } = this
    const { aisle } = site
    const left = this.X(aisle.x[0])
    const right = this.X(aisle.x[1])
    const top = this.Y(aisle.y + AISLE_HALF_WIDTH)
    const bottom = this.Y(aisle.y - AISLE_HALF_WIDTH)
    ctx.fillStyle = colors.aisle
    ctx.fillRect(left, top, right - left, bottom - top)

    ctx.strokeStyle = colors.hazard
    ctx.lineWidth = 2
    this.line(left, top, right, top)
    this.line(left, bottom, right, bottom)

    ctx.strokeStyle = colors.lane
    ctx.setLineDash([this.S(0.9), this.S(0.6)])
    this.line(left, this.Y(aisle.y), right, this.Y(aisle.y))
    ctx.setLineDash([])

    this.text('AISLE', left + 10, top + 15, { size: 10, weight: 700, color: colors.muted, spacing: 1.5 })
  }

  drawCells(occupied, pulse) {
    const { ctx, colors } = this
    const names = Object.keys(this.site.cells)
    names.forEach((name, index) => {
      const rect = this.site.cells[name]
      const people = occupied.get(name)
      const tone = colors[`cell-${index + 1}`] || colors.neutral
      const x = this.X(rect.x[0])
      const y = this.Y(rect.y[1])
      const w = this.S(rect.x[1] - rect.x[0])
      const h = this.S(rect.y[1] - rect.y[0])

      ctx.save()
      ctx.globalAlpha = people ? 0.08 + 0.07 * pulse : 0.06
      ctx.fillStyle = people ? colors.red : tone
      ctx.fillRect(x, y, w, h)
      ctx.restore()

      ctx.strokeStyle = people ? colors.red : tone
      ctx.lineWidth = people ? 2.5 : 1.75
      ctx.setLineDash(people ? [] : [this.S(0.35), this.S(0.25)])
      ctx.strokeRect(x, y, w, h)
      ctx.setLineDash([])

      ctx.fillStyle = people ? colors.red : tone
      for (let px = rect.x[0]; px <= rect.x[1] + 1e-6; px += 2) {
        for (const py of rect.y) this.post(px, py)
      }
      for (let py = rect.y[0] + 2; py < rect.y[1]; py += 2) {
        for (const px of rect.x) this.post(px, py)
      }

      const right = index % 2 === 1
      const label = `CELL ${index + 1}`
      const lx = right ? x + w - 12 : x + 12
      const align = right ? 'right' : 'left'
      this.text(label, lx, y + 20, { size: 11, weight: 700, color: people ? colors.red : tone, spacing: 1.2, align })
      if (people) {
        const who = people.length === 1 ? `person ${people[0]} inside` : `${people.length} people inside`
        this.text(who, lx, y + 36, { size: 11, weight: 500, color: colors.red, align })
      }
    })

    for (const strip of this.shared) {
      const x = this.X(strip.x[0])
      const y = this.Y(strip.y[1])
      const w = this.S(strip.x[1] - strip.x[0])
      const h = this.S(strip.y[1] - strip.y[0])
      ctx.save()
      ctx.beginPath()
      ctx.rect(x, y, w, h)
      ctx.clip()
      ctx.strokeStyle = colors['station-edge']
      ctx.globalAlpha = 0.28
      ctx.lineWidth = 1
      const step = this.S(0.45)
      for (let offset = -h; offset < w; offset += step) {
        this.line(x + offset, y + h, x + offset + h, y)
      }
      ctx.restore()
      this.text('SHARED', x + w / 2, y + h - 10, { size: 9, weight: 700, color: colors.muted, spacing: 1.2, align: 'center' })
    }
  }

  groundAt(y) {
    const { aisle } = this.site
    return Math.abs(y - aisle.y) <= AISLE_HALF_WIDTH ? this.colors.aisle : this.colors.floor
  }

  post(x, y) {
    const size = 4
    this.ctx.fillRect(this.X(x) - size / 2, this.Y(y) - size / 2, size, size)
  }

  stationRoles() {
    const roles = new Map()
    const add = (station, text, tone) => {
      roles.set(station, [...(roles.get(station) ?? []), { text, tone }])
    }
    for (const [robotId, telemetry] of Object.entries(this.view?.robots ?? {})) {
      if (!telemetry.live) continue
      const name = shortName(robotId)
      if (telemetry.waiting_for) add(telemetry.waiting_for, `${name} waiting`, 'amber')
      if (telemetry.current) {
        const step = telemetry.current.step
        const toPick = step === 'move_to_pick' || step === 'pick'
        if (toPick) add(telemetry.current.pick, `${name} pick`, 'blue')
        if (telemetry.current.place !== telemetry.waiting_for) {
          add(telemetry.current.place, `${name} place`, toPick ? 'muted' : 'blue')
        }
      }
    }
    return roles
  }

  drawStations() {
    const { ctx, colors } = this
    const roles = this.stationRoles()
    for (const [name, [sx, sy]] of Object.entries(this.site.stations)) {
      const size = this.S(STATION_SIZE)
      const x = this.X(sx) - size / 2
      const y = this.Y(sy) - size / 2
      const tags = roles.get(name) ?? []
      const lead = tags.find((tag) => tag.tone !== 'muted')

      ctx.fillStyle = colors.station
      ctx.strokeStyle = lead ? colors[lead.tone] : colors['station-edge']
      ctx.lineWidth = lead ? 2.25 : 1.5
      this.roundRect(x, y, size, size, 6)
      ctx.fill()
      ctx.stroke()

      ctx.strokeStyle = colors.line
      ctx.lineWidth = 1
      const inset = this.S(0.17)
      this.roundRect(x + inset, y + inset, size - 2 * inset, size - 2 * inset, 3)
      ctx.stroke()

      this.text(name, this.X(sx), this.Y(sy) + 5, { size: 14, weight: 700, color: colors.label, align: 'center' })

      tags.forEach((tag, index) => {
        this.text(tag.text, this.X(sx), y - 8 - index * 13, {
          size: 10,
          weight: 600,
          color: colors[tag.tone],
          align: 'center',
          halo: colors.floor,
        })
      })
    }
  }

  drawArm(spec, index, pulse) {
    const { ctx, colors } = this
    const telemetry = this.view?.robots?.[spec.id]
    const live = Boolean(telemetry?.live)
    const state = live ? telemetry.state : 'offline'
    const tone = colors[STATE_TONE[state]] || colors.neutral
    const body = this.bodies.get(`robot:${spec.id}`)
    const base = spec.base
    const tool = body ? [body.x, body.y] : [base[0], base[1] + REST_OFFSET]
    const elbow = elbowFor(base, tool, index % 2 === 0 ? 1 : -1)
    const B = [this.X(base[0]), this.Y(base[1])]
    const E = [this.X(elbow[0]), this.Y(elbow[1])]
    const T = [this.X(tool[0]), this.Y(tool[1])]

    ctx.save()
    if (!live || state === 'offline') ctx.globalAlpha = 0.45

    const pedestal = this.S(1.3)
    ctx.fillStyle = colors.station
    ctx.strokeStyle = colors['station-edge']
    ctx.lineWidth = 1.5
    this.roundRect(B[0] - pedestal / 2, B[1] - pedestal / 2, pedestal, pedestal, 8)
    ctx.fill()
    ctx.stroke()

    const ring = this.S(0.58)
    ctx.lineWidth = 3.5
    ctx.strokeStyle = colors.line
    this.circle(B[0], B[1], ring)
    ctx.stroke()
    const progress = state === 'busy' || state === 'paused' ? telemetry?.current?.progress ?? 0 : 1
    ctx.strokeStyle = tone
    ctx.beginPath()
    ctx.arc(B[0], B[1], ring, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * Math.max(progress, 0.001))
    ctx.stroke()

    if (state === 'paused') {
      ctx.save()
      ctx.strokeStyle = colors.halo
      ctx.globalAlpha = 0.55 + 0.45 * pulse
      ctx.lineCap = 'round'
      ctx.lineWidth = this.S(0.95)
      ctx.beginPath()
      ctx.moveTo(...B)
      ctx.lineTo(...E)
      ctx.lineTo(...T)
      ctx.stroke()
      ctx.restore()
    }

    if (telemetry?.waiting_for && live) {
      const [wx, wy] = this.site.stations[telemetry.waiting_for]
      ctx.strokeStyle = colors.amber
      ctx.lineWidth = 1.5
      ctx.setLineDash([4, 4])
      this.line(T[0], T[1], this.X(wx), this.Y(wy))
      ctx.setLineDash([])
    }

    this.link(B, E, this.S(0.42))
    this.link(E, T, this.S(0.32))
    this.joint(B, this.S(0.27))
    this.joint(E, this.S(0.19))
    this.gripper(E, T, telemetry?.holding && live)

    ctx.restore()

    const label = this.armLabel(spec, telemetry, live)
    const ly = B[1] + pedestal / 2 + 15
    const halo = colors.floor
    this.text(spec.id, B[0], ly, { size: 12, weight: 700, color: colors.label, align: 'center', halo })
    this.text(label, B[0], ly + 14, { size: 11, weight: 500, color: live ? tone : colors.muted, align: 'center', halo })
  }

  armLabel(spec, telemetry, live) {
    if (!telemetry) return 'no telemetry yet'
    if (!live) return 'no telemetry'
    if (telemetry.state === 'offline') return 'offline'
    if (telemetry.state === 'paused') {
      return telemetry.pause?.reason === 'person_in_cell' ? 'paused, person in cell' : 'paused, no safety feed'
    }
    if (telemetry.waiting_for) return `waiting for ${telemetry.waiting_for}`
    if (telemetry.current) {
      return `${STEP_LABEL[telemetry.current.step]} ${Math.round(telemetry.current.progress * 100)}%`
    }
    return 'idle'
  }

  link(from, to, width) {
    const { ctx, colors } = this
    ctx.lineCap = 'round'
    ctx.strokeStyle = colors['arm-edge']
    ctx.lineWidth = width
    this.line(...from, ...to)
    ctx.strokeStyle = colors.arm
    ctx.lineWidth = Math.max(1, width - 3)
    this.line(...from, ...to)
  }

  joint([x, y], radius) {
    const { ctx, colors } = this
    ctx.fillStyle = colors.joint
    ctx.strokeStyle = colors['arm-edge']
    ctx.lineWidth = 1.5
    this.circle(x, y, radius)
    ctx.fill()
    ctx.stroke()
  }

  gripper(elbow, tool, holding) {
    const { ctx, colors } = this
    const dx = tool[0] - elbow[0]
    const dy = tool[1] - elbow[1]
    const length = Math.hypot(dx, dy) || 1
    const ux = dx / length
    const uy = dy / length
    const nx = -uy
    const ny = ux
    const palm = this.S(0.27)
    const finger = this.S(0.34)
    const px = tool[0] + ux * this.S(0.05)
    const py = tool[1] + uy * this.S(0.05)

    if (holding) {
      const half = this.S(0.17)
      const cx = px + ux * (finger * 0.6)
      const cy = py + uy * (finger * 0.6)
      ctx.save()
      ctx.translate(cx, cy)
      ctx.rotate(Math.atan2(uy, ux))
      ctx.fillStyle = colors.part
      ctx.strokeStyle = colors['arm-edge']
      ctx.lineWidth = 1.25
      ctx.fillRect(-half, -half, half * 2, half * 2)
      ctx.strokeRect(-half, -half, half * 2, half * 2)
      ctx.restore()
    }

    ctx.strokeStyle = colors['arm-edge']
    ctx.lineCap = 'round'
    ctx.lineWidth = 3
    const a = [px + nx * palm, py + ny * palm]
    const b = [px - nx * palm, py - ny * palm]
    this.line(...a, ...b)
    this.line(...a, a[0] + ux * finger, a[1] + uy * finger)
    this.line(...b, b[0] + ux * finger, b[1] + uy * finger)
    this.joint(tool, this.S(0.14))
  }

  drawActors(now) {
    const actors = [...(this.view?.actors ?? [])].sort((a, b) => Number(a.kind === 'person') - Number(b.kind === 'person'))
    const feedOn = this.view?.feed_on ?? true
    for (const actor of actors) {
      const body = this.bodies.get(`actor:${actor.gid}`)
      if (!body) continue
      if (actor.kind === 'forklift') this.drawForklift(actor, body)
      else this.drawPerson(actor, body, now, feedOn)
    }
  }

  drawPerson(actor, body, now, feedOn) {
    const { ctx, colors } = this
    const tone = actor.ambient ? colors.ambient : colors.person
    const x = this.X(body.x)
    const y = this.Y(body.y)
    const inside = actor.cells.length > 0
    const hovered = this.hover === actor.gid

    ctx.save()
    if (actor.leaving) ctx.globalAlpha = 0.7

    if (body.trail.length > 1) {
      ctx.lineCap = 'round'
      ctx.lineWidth = 2
      ctx.strokeStyle = tone
      for (let i = 1; i < body.trail.length; i++) {
        ctx.globalAlpha = (i / body.trail.length) * 0.35 * (actor.leaving ? 0.7 : 1)
        this.line(this.X(body.trail[i - 1][0]), this.Y(body.trail[i - 1][1]), this.X(body.trail[i][0]), this.Y(body.trail[i][1]))
      }
      ctx.globalAlpha = actor.leaving ? 0.7 : 1
    }

    if (inside) {
      const phase = (now % 1400) / 1400
      ctx.save()
      ctx.globalAlpha = (1 - phase) * 0.55
      ctx.strokeStyle = colors.red
      ctx.lineWidth = 2
      this.circle(x, y, this.S(0.35 + 0.55 * phase))
      ctx.stroke()
      ctx.restore()
    }

    if (actor.moving) {
      const angle = (actor.heading * Math.PI) / 180
      const ux = Math.cos(angle)
      const uy = -Math.sin(angle)
      const tip = this.S(0.58)
      const back = this.S(0.34)
      const wing = this.S(0.17)
      ctx.fillStyle = tone
      ctx.beginPath()
      ctx.moveTo(x + ux * tip, y + uy * tip)
      ctx.lineTo(x + ux * back - uy * wing, y + uy * back + ux * wing)
      ctx.lineTo(x + ux * back + uy * wing, y + uy * back - ux * wing)
      ctx.closePath()
      ctx.fill()
    }

    const radius = this.S(0.3)
    this.circle(x, y, radius)
    if (feedOn) {
      ctx.fillStyle = tone
      ctx.fill()
      ctx.strokeStyle = colors.surface
      ctx.lineWidth = 2
      ctx.stroke()
    } else {
      ctx.fillStyle = colors.floor
      ctx.fill()
      ctx.strokeStyle = tone
      ctx.lineWidth = 2
      ctx.setLineDash([3, 3])
      ctx.stroke()
      ctx.setLineDash([])
    }

    if (hovered) {
      ctx.strokeStyle = colors.label
      ctx.lineWidth = 1.5
      this.circle(x, y, radius + 5)
      ctx.stroke()
    }
    ctx.restore()

    const name = actor.ambient ? `${actor.gid}` : `person ${actor.gid}`
    this.text(name, x + radius + 7, y + 4, {
      size: 11,
      weight: hovered || inside ? 700 : 500,
      color: inside ? colors.red : colors.muted,
      halo: this.groundAt(body.y),
    })
  }

  drawForklift(actor, body) {
    const { ctx, colors } = this
    const x = this.X(body.x)
    const y = this.Y(body.y)
    ctx.save()
    ctx.translate(x, y)
    ctx.rotate((-actor.heading * Math.PI) / 180)
    const S = (m) => this.S(m)

    ctx.strokeStyle = colors['arm-edge']
    ctx.lineCap = 'round'
    ctx.lineWidth = 2.5
    this.line(S(0.5), -S(0.2), S(1.1), -S(0.2))
    this.line(S(0.5), S(0.2), S(1.1), S(0.2))

    ctx.fillStyle = colors.forklift
    ctx.strokeStyle = colors['arm-edge']
    ctx.lineWidth = 1.25
    this.roundRect(-S(0.75), -S(0.42), S(1.2), S(0.84), 5)
    ctx.fill()
    ctx.stroke()

    ctx.fillStyle = 'rgba(0, 0, 0, 0.22)'
    this.roundRect(-S(0.45), -S(0.3), S(0.5), S(0.6), 3)
    ctx.fill()

    ctx.lineWidth = 3
    this.line(S(0.48), -S(0.36), S(0.48), S(0.36))
    ctx.restore()

    this.text(`forklift ${actor.gid}`, x, y - this.S(0.6) - 6, { size: 11, weight: 500, color: colors.muted, align: 'center' })
  }

  drawMarkers(now) {
    const { ctx, colors } = this
    this.markers = this.markers.filter((marker) => now - marker.at < MARKER_MS)
    for (const marker of this.markers) {
      const t = (now - marker.at) / MARKER_MS
      ctx.save()
      ctx.globalAlpha = 1 - t
      ctx.strokeStyle = colors.blue
      ctx.lineWidth = 2
      this.circle(this.X(marker.x), this.Y(marker.y), 6 + 18 * t)
      ctx.stroke()
      ctx.restore()
    }
  }

  drawScale() {
    const { ctx, colors } = this
    const length = this.S(5)
    const x = 14
    const y = this.height - 14
    ctx.strokeStyle = colors.muted
    ctx.lineWidth = 1.5
    this.line(x, y, x + length, y)
    for (let i = 0; i <= 5; i++) {
      const tick = i % 5 === 0 ? 6 : 3
      this.line(x + this.S(i), y, x + this.S(i), y - tick)
    }
    this.text('5 m', x + length + 8, y + 1, { size: 11, weight: 500, color: colors.muted })
  }

  drawTooltip() {
    if (!this.pointer) return
    const { ctx, colors } = this
    let text
    if (this.hover !== null) {
      const actor = this.view?.actors.find((candidate) => candidate.gid === this.hover)
      const where = actor?.cells.length ? actor.cells.join(' and ') : 'outside the cells'
      text = `person ${this.hover} · ${where} · click to send out`
    } else {
      text = `${this.pointer.x.toFixed(1)}, ${this.pointer.y.toFixed(1)} m · click to send someone here`
    }
    ctx.font = '500 12px ui-sans-serif, system-ui, sans-serif'
    const width = ctx.measureText(text).width + 16
    const height = 24
    const x = Math.min(this.pointer.sx + 14, this.width - width - 6)
    const y = Math.max(this.pointer.sy - height - 10, 6)
    ctx.fillStyle = colors.tooltip
    this.roundRect(x, y, width, height, 6)
    ctx.fill()
    this.text(text, x + 8, y + 16, { size: 12, weight: 500, color: colors['tooltip-ink'] })
  }

  text(value, x, y, { size = 12, weight = 500, color, align = 'left', spacing = 0, halo = null } = {}) {
    const { ctx } = this
    ctx.font = `${weight} ${size}px ui-sans-serif, system-ui, sans-serif`
    ctx.textAlign = align
    if ('letterSpacing' in ctx) ctx.letterSpacing = `${spacing}px`
    if (halo) {
      ctx.strokeStyle = halo
      ctx.lineWidth = 4
      ctx.lineJoin = 'round'
      ctx.strokeText(value, x, y)
    }
    ctx.fillStyle = color
    ctx.fillText(value, x, y)
    if ('letterSpacing' in ctx) ctx.letterSpacing = '0px'
    ctx.textAlign = 'left'
  }

  line(x1, y1, x2, y2) {
    const { ctx } = this
    ctx.beginPath()
    ctx.moveTo(x1, y1)
    ctx.lineTo(x2, y2)
    ctx.stroke()
  }

  circle(x, y, radius) {
    this.ctx.beginPath()
    this.ctx.arc(x, y, radius, 0, Math.PI * 2)
  }

  roundRect(x, y, w, h, radius) {
    this.ctx.beginPath()
    this.ctx.roundRect(x, y, w, h, radius)
  }
}
