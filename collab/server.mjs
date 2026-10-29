// Prism collaboration service.
//
// * One Y.Doc per project; doc.getMap('files') maps project-relative path -> Y.Text.
// * Browsers connect with the standard y-websocket protocol (sync + awareness)
//   to  ws://<host>/collab/<projectId>?token=<signed token from the backend>.
//   Viewers are read-only: their document updates are dropped.
// * State is persisted to COLLAB_DATA_DIR/<projectId>.ystate (full snapshot,
//   atomic rename) and written through to the backend as plain text.
// * The backend uses the internal HTTP API (shared secret) to read a
//   consistent snapshot and to apply conditional changes (expected sha256 per
//   file). Conditional changes are applied as minimal Y.Text edits, so
//   connected editors keep their cursors and receive them like any edit.

import crypto from 'node:crypto'
import fs from 'node:fs'
import http from 'node:http'
import path from 'node:path'
import { WebSocketServer } from 'ws'
import * as Y from 'yjs'
import * as syncProtocol from 'y-protocols/sync'
import * as awarenessProtocol from 'y-protocols/awareness'
import * as encoding from 'lib0/encoding'
import * as decoding from 'lib0/decoding'

const PORT = Number(process.env.COLLAB_PORT || 1234)
const HOST = process.env.COLLAB_HOST || '127.0.0.1'
const SECRET = process.env.COLLAB_SHARED_SECRET || ''
const BACKEND = (process.env.BACKEND_INTERNAL_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')
const DATA_DIR = path.resolve(process.env.COLLAB_DATA_DIR || './var/collab')
const WRITE_THROUGH_MS = Number(process.env.COLLAB_WRITE_THROUGH_MS || 1000)
const UNLOAD_AFTER_MS = Number(process.env.COLLAB_UNLOAD_AFTER_MS || 120000)

if (!SECRET) {
  console.error('COLLAB_SHARED_SECRET is required')
  process.exit(2)
}
fs.mkdirSync(DATA_DIR, { recursive: true })

const MSG_SYNC = 0
const MSG_AWARENESS = 1
const BACKEND_ORIGIN = Symbol('backend')

const log = (...a) => console.log(new Date().toISOString(), ...a)
const sha256 = (s) => crypto.createHash('sha256').update(Buffer.from(s, 'utf8')).digest('hex')
const validPid = (pid) => /^[A-Za-z0-9]{1,64}$/.test(pid)

// ------------------------------------------------------------------ tokens --
function verifyToken (token) {
  if (typeof token !== 'string' || !token.includes('.')) return null
  const [p64, sig] = token.split('.')
  let payload
  try { payload = Buffer.from(p64, 'base64url') } catch { return null }
  const expect = crypto.createHmac('sha256', SECRET).update(payload).digest('base64url')
  const a = Buffer.from(sig)
  const b = Buffer.from(expect)
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) return null
  let data
  try { data = JSON.parse(payload.toString('utf8')) } catch { return null }
  if (!data.exp || data.exp < Date.now() / 1000) return null
  return data
}

// --------------------------------------------------------------- documents --
/** @type {Map<string, Promise<Room>>} */
const rooms = new Map()

class Room {
  constructor (pid) {
    this.pid = pid
    this.doc = new Y.Doc({ gc: true })
    this.files = this.doc.getMap('files')
    this.awareness = new awarenessProtocol.Awareness(this.doc)
    this.awareness.setLocalState(null)
    this.conns = new Map() // ws -> { readonly, awarenessIds:Set }
    this.dirty = new Set()
    this.persistTimer = null
    this.writeTimer = null
    this.unloadTimer = null
    this.writing = Promise.resolve()
    this.lastWriteError = null
  }

  file () { return path.join(DATA_DIR, `${this.pid}.ystate`) }

  async load () {
    const f = this.file()
    if (fs.existsSync(f)) {
      Y.applyUpdate(this.doc, fs.readFileSync(f), BACKEND_ORIGIN)
      log(`[${this.pid}] loaded persisted state (${this.files.size} files)`)
    } else {
      const r = await backend('GET', `/internal/projects/${this.pid}/texts`)
      this.doc.transact(() => {
        for (const [p, text] of Object.entries(r.files)) this.files.set(p, new Y.Text(text))
      }, BACKEND_ORIGIN)
      this.persistNow()
      log(`[${this.pid}] initialised from backend (${this.files.size} files)`)
    }
    this.doc.on('update', (update, origin) => {
      this.schedulePersist()
      const enc = encoding.createEncoder()
      encoding.writeVarUint(enc, MSG_SYNC)
      syncProtocol.writeUpdate(enc, update)
      this.broadcast(encoding.toUint8Array(enc))
    })
    this.files.observeDeep((events, tr) => {
      if (tr.origin === BACKEND_ORIGIN) return // the backend already wrote these to disk
      for (const ev of events) {
        if (ev.target === this.files) {
          for (const key of ev.keysChanged) this.dirty.add(key)
        } else if (ev.path.length) {
          this.dirty.add(String(ev.path[0]))
        }
      }
      this.scheduleWrite()
    })
    this.awareness.on('update', ({ added, updated, removed }, origin) => {
      const changed = added.concat(updated, removed)
      if (origin && this.conns.has(origin)) {
        const ids = this.conns.get(origin).awarenessIds
        added.forEach((id) => ids.add(id))
        removed.forEach((id) => ids.delete(id))
      }
      const enc = encoding.createEncoder()
      encoding.writeVarUint(enc, MSG_AWARENESS)
      encoding.writeVarUint8Array(enc, awarenessProtocol.encodeAwarenessUpdate(this.awareness, changed))
      this.broadcast(encoding.toUint8Array(enc))
    })
    return this
  }

  texts () {
    const out = {}
    for (const [p, t] of this.files.entries()) out[p] = t.toString()
    return out
  }

  broadcast (msg) {
    for (const ws of this.conns.keys()) send(ws, msg)
  }

  schedulePersist () {
    clearTimeout(this.persistTimer)
    this.persistTimer = setTimeout(() => this.persistNow(), 300)
  }

  persistNow () {
    clearTimeout(this.persistTimer)
    const tmp = this.file() + '.tmp'
    fs.writeFileSync(tmp, Y.encodeStateAsUpdate(this.doc))
    fs.renameSync(tmp, this.file())
  }

  scheduleWrite () {
    clearTimeout(this.writeTimer)
    this.writeTimer = setTimeout(() => this.flushWrites(), WRITE_THROUGH_MS)
  }

  flushWrites () {
    clearTimeout(this.writeTimer)
    if (!this.dirty.size) return this.writing
    const paths = [...this.dirty]
    this.dirty.clear()
    const body = {}
    for (const p of paths) body[p] = this.files.has(p) ? this.files.get(p).toString() : null
    this.writing = this.writing.then(() => backend('PUT', `/internal/projects/${this.pid}/texts`, { files: body }))
      .then(() => { this.lastWriteError = null })
      .catch((e) => {
        this.lastWriteError = String(e)
        paths.forEach((p) => this.dirty.add(p)) // retry later; nothing is lost (state is persisted locally)
        log(`[${this.pid}] write-through failed: ${e}`)
        this.scheduleWrite()
      })
    return this.writing
  }

  /** Atomic conditional change set from the backend. */
  apply (changes) {
    const conflicts = []
    for (const c of changes) {
      const cur = this.files.has(c.path) ? sha256(this.files.get(c.path).toString()) : null
      if (c.expected_sha === '*') continue
      if (cur !== c.expected_sha) conflicts.push({ path: c.path, expected: c.expected_sha, actual: cur })
    }
    if (conflicts.length) return { ok: false, conflicts }
    this.doc.transact(() => {
      for (const c of changes) {
        if (c.text === null || c.text === undefined) {
          this.files.delete(c.path)
        } else if (this.files.has(c.path)) {
          replaceText(this.files.get(c.path), c.text)
        } else {
          this.files.set(c.path, new Y.Text(c.text))
        }
      }
    }, BACKEND_ORIGIN)
    this.persistNow()
    return { ok: true }
  }

  maybeUnload () {
    clearTimeout(this.unloadTimer)
    if (this.conns.size) return
    this.unloadTimer = setTimeout(async () => {
      if (this.conns.size) return
      await this.flushWrites()
      if (this.dirty.size || this.conns.size) return
      this.persistNow()
      rooms.delete(this.pid)
      this.doc.destroy()
      log(`[${this.pid}] unloaded`)
    }, UNLOAD_AFTER_MS)
  }
}

/** Replace the content of a Y.Text with minimal edits (common prefix/suffix kept). */
function replaceText (ytext, next) {
  const cur = ytext.toString()
  if (cur === next) return
  let start = 0
  const max = Math.min(cur.length, next.length)
  while (start < max && cur.charCodeAt(start) === next.charCodeAt(start)) start++
  let endCur = cur.length
  let endNext = next.length
  while (endCur > start && endNext > start && cur.charCodeAt(endCur - 1) === next.charCodeAt(endNext - 1)) { endCur--; endNext-- }
  // never split a surrogate pair
  if (start > 0 && isHigh(cur.charCodeAt(start - 1))) start--
  if (endCur < cur.length && isLow(cur.charCodeAt(endCur))) { endCur++; endNext++ }
  if (endCur > start) ytext.delete(start, endCur - start)
  if (endNext > start) ytext.insert(start, next.slice(start, endNext))
}
const isHigh = (c) => c >= 0xd800 && c <= 0xdbff
const isLow = (c) => c >= 0xdc00 && c <= 0xdfff

function getRoom (pid) {
  if (!rooms.has(pid)) {
    const p = new Room(pid).load().catch((e) => { rooms.delete(pid); throw e })
    rooms.set(pid, p)
  }
  return rooms.get(pid)
}

async function backend (method, p, body) {
  const r = await fetch(BACKEND + p, {
    method,
    headers: { 'X-Prism-Internal': SECRET, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(30000)
  })
  if (!r.ok) throw new Error(`backend ${method} ${p} -> ${r.status} ${await r.text()}`)
  return r.json()
}

// --------------------------------------------------------------- websocket --
function send (ws, msg) {
  if (ws.readyState !== ws.OPEN) return
  try { ws.send(msg) } catch { ws.close() }
}

function onMessage (room, ws, data) {
  const meta = room.conns.get(ws)
  const decoder = decoding.createDecoder(new Uint8Array(data))
  const type = decoding.readVarUint(decoder)
  if (type === MSG_SYNC) {
    const enc = encoding.createEncoder()
    encoding.writeVarUint(enc, MSG_SYNC)
    const sub = decoding.readVarUint(decoder)
    if (sub === syncProtocol.messageYjsSyncStep1) {
      syncProtocol.readSyncStep1(decoder, enc, room.doc)
    } else if (sub === syncProtocol.messageYjsSyncStep2 || sub === syncProtocol.messageYjsUpdate) {
      if (meta.readonly) return // viewers cannot change the document
      const update = decoding.readVarUint8Array(decoder)
      Y.applyUpdate(room.doc, update, ws)
      return
    }
    if (encoding.length(enc) > 1) send(ws, encoding.toUint8Array(enc))
  } else if (type === MSG_AWARENESS) {
    awarenessProtocol.applyAwarenessUpdate(room.awareness, decoding.readVarUint8Array(decoder), ws)
  }
}

const server = http.createServer(handleHttp)
const wss = new WebSocketServer({ noServer: true, maxPayload: 16 * 1024 * 1024 })

server.on('upgrade', (req, socket, head) => {
  const url = new URL(req.url, 'http://x')
  const pid = url.pathname.split('/').filter(Boolean).pop() || ''
  const claims = verifyToken(url.searchParams.get('token'))
  if (!validPid(pid) || !claims || claims.pid !== pid) {
    socket.write('HTTP/1.1 401 Unauthorized\r\n\r\n')
    socket.destroy()
    return
  }
  wss.handleUpgrade(req, socket, head, async (ws) => {
    ws.binaryType = 'arraybuffer'
    // Messages can arrive while the document is still loading: queue them.
    const pending = []
    let room = null
    const handle = (data) => { try { onMessage(room, ws, data) } catch (e) { log(`[${pid}] bad message: ${e}`); ws.close() } }
    ws.on('message', (data) => { if (room) handle(data); else pending.push(data) })
    try {
      room = await getRoom(pid)
    } catch (e) {
      log(`[${pid}] load failed: ${e}`)
      ws.close(1011, 'project unavailable')
      return
    }
    if (ws.readyState !== ws.OPEN) return
    clearTimeout(room.unloadTimer)
    room.conns.set(ws, { readonly: claims.role === 'viewer', awarenessIds: new Set(), user: claims.name })
    ws.on('close', () => {
      const meta = room.conns.get(ws)
      room.conns.delete(ws)
      if (meta) awarenessProtocol.removeAwarenessStates(room.awareness, [...meta.awarenessIds], null)
      room.maybeUnload()
    })
    // ping to detect dead peers
    let alive = true
    ws.on('pong', () => { alive = true })
    const iv = setInterval(() => {
      if (!alive) { ws.terminate(); clearInterval(iv); return }
      alive = false
      try { ws.ping() } catch { clearInterval(iv) }
    }, 30000)
    ws.on('close', () => clearInterval(iv))
    // initial sync
    const enc = encoding.createEncoder()
    encoding.writeVarUint(enc, MSG_SYNC)
    syncProtocol.writeSyncStep1(enc, room.doc)
    send(ws, encoding.toUint8Array(enc))
    const states = [...room.awareness.getStates().keys()]
    if (states.length) {
      const a = encoding.createEncoder()
      encoding.writeVarUint(a, MSG_AWARENESS)
      encoding.writeVarUint8Array(a, awarenessProtocol.encodeAwarenessUpdate(room.awareness, states))
      send(ws, encoding.toUint8Array(a))
    }
    for (const data of pending.splice(0)) handle(data)
  })
})

// ------------------------------------------------------------ internal API --
async function readJson (req) {
  const chunks = []
  let size = 0
  for await (const c of req) {
    size += c.length
    if (size > 64 * 1024 * 1024) throw new Error('body too large')
    chunks.push(c)
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}')
}

function reply (res, code, obj) {
  res.writeHead(code, { 'Content-Type': 'application/json' })
  res.end(JSON.stringify(obj))
}

async function handleHttp (req, res) {
  const url = new URL(req.url, 'http://x')
  if (url.pathname === '/health') return reply(res, 200, { ok: true, rooms: rooms.size })
  const m = url.pathname.match(/^\/internal\/projects\/([A-Za-z0-9]{1,64})\/(texts|apply|reload|flush)$/)
  if (!m) return reply(res, 404, { error: 'not found' })
  const given = Buffer.from(String(req.headers['x-prism-internal'] || ''))
  const secret = Buffer.from(SECRET)
  if (given.length !== secret.length || !crypto.timingSafeEqual(given, secret)) return reply(res, 403, { error: 'forbidden' })
  const [, pid, action] = m
  try {
    if (action === 'reload' && req.method === 'POST') {
      const existing = rooms.get(pid)
      if (existing) {
        const room = await existing
        for (const ws of room.conns.keys()) ws.close(4000, 'project reloaded')
        rooms.delete(pid)
        room.doc.destroy()
      }
      fs.rmSync(path.join(DATA_DIR, `${pid}.ystate`), { force: true })
      return reply(res, 200, { ok: true })
    }
    const room = await getRoom(pid)
    if (action === 'texts' && req.method === 'GET') return reply(res, 200, { files: room.texts() })
    if (action === 'flush' && req.method === 'POST') { await room.flushWrites(); return reply(res, 200, { ok: true, error: room.lastWriteError }) }
    if (action === 'apply' && req.method === 'POST') {
      const body = await readJson(req)
      if (!Array.isArray(body.changes)) return reply(res, 400, { error: 'changes must be a list' })
      for (const c of body.changes) {
        if (typeof c.path !== 'string' || c.path.includes('..') || c.path.startsWith('/')) return reply(res, 400, { error: 'bad path' })
      }
      const r = room.apply(body.changes)
      if (!r.ok) return reply(res, 409, { conflicts: r.conflicts })
      room.maybeUnload()
      return reply(res, 200, { ok: true })
    }
    return reply(res, 405, { error: 'method not allowed' })
  } catch (e) {
    log(`[${pid}] ${action} failed: ${e}`)
    return reply(res, 502, { error: String(e) })
  }
}

async function shutdown () {
  log('shutting down; flushing documents')
  for (const p of rooms.values()) {
    try { const r = await p; r.persistNow(); await r.flushWrites() } catch {}
  }
  process.exit(0)
}
process.on('SIGTERM', shutdown)
process.on('SIGINT', shutdown)

server.listen(PORT, HOST, () => log(`collab listening on ${HOST}:${PORT}, backend ${BACKEND}, data ${DATA_DIR}`))
