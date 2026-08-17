import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App.jsx'

// Construit une fausse Response dont le body streame le texte SSE donné.
// `chunkSplitIndex`, si fourni, coupe le texte en deux `read()` séparés,
// pour vérifier que le buffering (bufferRef) recolle bien un événement
// dont les octets arrivent en plusieurs morceaux.
function makeSSEResponse(sseText, { chunkSplitIndex } = {}) {
  const encoder = new TextEncoder()
  const chunks =
    chunkSplitIndex == null
      ? [encoder.encode(sseText)]
      : [
          encoder.encode(sseText.slice(0, chunkSplitIndex)),
          encoder.encode(sseText.slice(chunkSplitIndex)),
        ]
  let i = 0
  return {
    ok: true,
    status: 200,
    body: {
      getReader() {
        return {
          async read() {
            if (i >= chunks.length) return { done: true, value: undefined }
            return { done: false, value: chunks[i++] }
          },
        }
      },
    },
  }
}

async function sendMessage(text) {
  const user = userEvent.setup()
  render(<App />)
  await user.type(screen.getByPlaceholderText('Écris ton message...'), text)
  await user.click(screen.getByRole('button', { name: 'Envoyer' }))
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('App', () => {
  it('affiche le message utilisateur puis la réponse assistant streamée', async () => {
    const sse =
      'event: text\r\ndata: Bon\r\n\r\n' +
      'event: text\r\ndata: jour !\r\n\r\n' +
      'event: done\r\ndata: {"cost_usd": 0.0123, "duration_ms": 456}\r\n\r\n'
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makeSSEResponse(sse)))

    await sendMessage('Salut')

    expect(screen.getByText('Salut')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('Bonjour !')).toBeInTheDocument())
    expect(screen.getByText('Coût : $0.012300 · 456 ms')).toBeInTheDocument()
  })

  it('affiche les sources RAG quand l’événement sources est reçu', async () => {
    const sse =
      'event: text\r\ndata: Réponse.\r\n\r\n' +
      'event: sources\r\ndata: ["web_app.py", "index_docs.py"]\r\n\r\n' +
      'event: done\r\ndata: {"cost_usd": 0.01, "duration_ms": 100}\r\n\r\n'
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makeSSEResponse(sse)))

    await sendMessage('Comment fonctionne le RAG ?')

    await waitFor(() =>
      expect(screen.getByText('📄 Sources : web_app.py, index_docs.py')).toBeInTheDocument()
    )
  })

  it('affiche un message d’erreur si le serveur répond avec un statut non-ok', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500 }))

    await sendMessage('Salut')

    await waitFor(() => expect(screen.getByText('Erreur : 500')).toBeInTheDocument())
  })

  it('affiche l’événement error émis par le serveur', async () => {
    const sse = 'event: error\r\ndata: Boom\r\n\r\n'
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makeSSEResponse(sse)))

    await sendMessage('Salut')

    await waitFor(() => expect(screen.getByText('Erreur : Boom')).toBeInTheDocument())
  })

  it('reconstruit un événement dont les octets arrivent en deux morceaux (buffering)', async () => {
    const sse = 'event: text\r\ndata: Bonjour !\r\n\r\nevent: done\r\ndata: {"cost_usd": 0, "duration_ms": 1}\r\n\r\n'
    // Coupe en plein milieu de la ligne "data: Bonjour !" pour vérifier que
    // bufferRef recolle correctement les deux morceaux avant de parser.
    const cutPoint = sse.indexOf('Bon') + 2
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(makeSSEResponse(sse, { chunkSplitIndex: cutPoint })))

    await sendMessage('Salut')

    await waitFor(() => expect(screen.getByText('Bonjour !')).toBeInTheDocument())
  })

  it('n’envoie pas de requête si le champ est vide', async () => {
    vi.stubGlobal('fetch', vi.fn())
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: 'Envoyer' }))

    expect(fetch).not.toHaveBeenCalled()
  })
})
