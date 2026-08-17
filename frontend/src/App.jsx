import { useEffect, useRef, useState } from 'react'
import LoginForm from './LoginForm.jsx'

export function parseEvent(part) {
  let event = 'message'
  const dataLines = []
  for (const line of part.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''))
  }
  return { event, data: dataLines.join('\n') }
}

export default function App() {
  const [checkingAuth, setCheckingAuth] = useState(true)
  const [user, setUser] = useState(null)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const bufferRef = useRef('')

  useEffect(() => {
    fetch('/api/me')
      .then((r) => (r.ok ? r.json() : null))
      .then(setUser)
      .finally(() => setCheckingAuth(false))
  }, [])

  async function logout() {
    await fetch('/api/logout', { method: 'POST' })
    setUser(null)
    setMessages([])
  }

  async function sendMessage(e) {
    e.preventDefault()
    const text = input.trim()
    if (!text || sending) return

    setInput('')
    setSending(true)
    setMessages((prev) => [...prev, { role: 'user', text }])
    bufferRef.current = ''
    let assistantStarted = false
    let assistantText = ''

    try {
      const resp = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text }),
      })
      if (!resp.ok) {
        setMessages((prev) => [...prev, { role: 'meta', text: `Erreur : ${resp.status}` }])
        return
      }

      const reader = resp.body.getReader()
      const decoder = new TextDecoder()

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        // sse-starlette termine ses lignes en \r\n : on normalise avant de découper.
        bufferRef.current += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
        const parts = bufferRef.current.split('\n\n')
        bufferRef.current = parts.pop() ?? ''

        for (const part of parts) {
          if (!part.trim()) continue
          const { event, data } = parseEvent(part)

          if (event === 'text') {
            // La mutation de assistantStarted/assistantText se fait ici, en
            // dehors du updater passé à setMessages, pour que ce dernier reste
            // pur (StrictMode l'invoque deux fois en dev pour vérifier ça).
            assistantText += data
            const started = assistantStarted
            const currentText = assistantText
            assistantStarted = true

            setMessages((prev) => {
              if (!started) return [...prev, { role: 'assistant', text: currentText }]
              const next = [...prev]
              next[next.length - 1] = { ...next[next.length - 1], text: currentText }
              return next
            })
          } else if (event === 'sources') {
            const sources = JSON.parse(data)
            setMessages((prev) => [
              ...prev,
              { role: 'meta', text: `📄 Sources : ${sources.join(', ')}` },
            ])
          } else if (event === 'done') {
            const info = JSON.parse(data)
            setMessages((prev) => [
              ...prev,
              { role: 'meta', text: `Coût : $${info.cost_usd.toFixed(6)} · ${info.duration_ms} ms` },
            ])
          } else if (event === 'error') {
            setMessages((prev) => [...prev, { role: 'meta', text: `Erreur : ${data}` }])
          }
        }
      }
    } finally {
      setSending(false)
    }
  }

  if (checkingAuth) return null
  if (!user) return <LoginForm onAuthenticated={setUser} />

  return (
    <>
      <div className="authRow">
        <h1>💬 Claude SDK Client — conversation multi-tours</h1>
        <button type="button" className="link-button" onClick={logout}>
          {user.email} · Déconnexion
        </button>
      </div>
      <div id="chat">
        {messages.map((m, i) => (
          <div key={i} className={m.role === 'meta' ? 'meta' : `msg ${m.role}`}>
            {m.text}
          </div>
        ))}
      </div>
      <form onSubmit={sendMessage}>
        <input
          autoComplete="off"
          placeholder="Écris ton message..."
          value={input}
          onChange={(e) => setInput(e.target.value)}
          disabled={sending}
        />
        <button type="submit" disabled={sending}>
          Envoyer
        </button>
      </form>
    </>
  )
}
