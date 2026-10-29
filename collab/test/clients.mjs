// Integration helper: real y-websocket clients against the collab service.
// Driven line-by-line over stdin by backend/tests/test_collab_integration.py.
//
// usage: node clients.mjs <wsUrl> <room> <path> <tokenA> <tokenB> <tokenViewer>
// commands (stdin):
//   concurrent            A inserts at the start, B appends at the end, simultaneously
//   await <substring>     wait until A and B both contain <substring>
//   viewer-edit <text>    viewer tries to insert; prints whether A ever saw it
//   offline-edit <text>   B disconnects, A inserts <text>, B reconnects and catches up
//   dump                  print A's text
//   exit
import readline from 'node:readline'
import WebSocket from 'ws'
import * as Y from 'yjs'
import { WebsocketProvider } from 'y-websocket'

const [url, room, file, tokA, tokB, tokV] = process.argv.slice(2)
const out = (o) => process.stdout.write(JSON.stringify(o) + '\n')

function client (token) {
  const doc = new Y.Doc()
  const provider = new WebsocketProvider(url, room, doc, { params: { token }, WebSocketPolyfill: WebSocket, disableBc: true })
  return { doc, provider, text: () => (doc.getMap('files').get(file) || new Y.Text()).toString(), ytext: () => doc.getMap('files').get(file) }
}

const waitFor = async (cond, ms = 20000) => {
  const t0 = Date.now()
  while (Date.now() - t0 < ms) {
    if (cond()) return true
    await new Promise((r) => setTimeout(r, 50))
  }
  return false
}
const synced = (c) => new Promise((resolve) => { if (c.provider.synced) resolve(); else c.provider.once('sync', (s) => s && resolve()) })

const A = client(tokA)
const B = client(tokB)
const V = client(tokV)
await Promise.all([synced(A), synced(B), synced(V)])
await waitFor(() => A.ytext() && B.ytext() && V.ytext())
out({ phase: 'connected', length: A.text().length })

const rl = readline.createInterface({ input: process.stdin })
for await (const line of rl) {
  const [cmd, ...rest] = line.split(' ')
  const arg = rest.join(' ')
  if (cmd === 'concurrent') {
    // both edit before either has seen the other's change
    A.ytext().insert(0, '% edit-from-A\n')
    B.ytext().insert(B.ytext().length, '% edit-from-B\n')
    const ok = await waitFor(() => A.text() === B.text() && A.text().includes('edit-from-A') && A.text().includes('edit-from-B'))
    out({ phase: 'concurrent', converged: ok, text: A.text() })
  } else if (cmd === 'await') {
    const ok = await waitFor(() => A.text().includes(arg) && B.text().includes(arg))
    out({ phase: 'await', seen: ok, a: A.text(), b: B.text() })
  } else if (cmd === 'viewer-edit') {
    V.ytext().insert(0, arg + '\n')
    await new Promise((r) => setTimeout(r, 1500))
    out({ phase: 'viewer-edit', propagated: A.text().includes(arg) })
  } else if (cmd === 'offline-edit') {
    B.provider.disconnect()
    A.ytext().insert(0, arg + '\n')
    await new Promise((r) => setTimeout(r, 300))
    const missingWhileOffline = !B.text().includes(arg)
    B.provider.connect()
    const ok = await waitFor(() => B.text().includes(arg) && A.text() === B.text())
    out({ phase: 'offline-edit', missingWhileOffline, caughtUp: ok })
  } else if (cmd === 'dump') {
    out({ phase: 'dump', text: A.text() })
  } else if (cmd === 'exit') {
    for (const c of [A, B, V]) c.provider.destroy()
    out({ phase: 'bye' })
    process.exit(0)
  }
}
