import { useRef, useState } from 'react'

function parseEvent(part) {
  let event = 'message'
  const dataLines = []
  for (const line of part.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).replace(/^ /, ''))
  }
  return { event, data: dataLines.join('\n') }
}

export default function App() {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const bufferRef = useRef('')

  async function sendMessage(e) {
    e.preventDefault()
    const text = input.trim()
    if (!text || sending) return

    setInput('')
    setSending(true)
    setMessages((prev) => [...prev, { role: 'user', text }])
    bufferRef.current = ''
    let assistantIndex = -1

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
            setMessages((prev) => {
              const next = [...prev]
              if (assistantIndex === -1) {
                assistantIndex = next.length
                next.push({ role: 'assistant', text: data })
              } else {
                next[assistantIndex] = {
                  ...next[assistantIndex],
                  text: next[assistantIndex].text + data,
                }
              }
              return next
            })
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

  return (
    <>
      <h1>💬 Claude SDK Client — conversation multi-tours</h1>
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
