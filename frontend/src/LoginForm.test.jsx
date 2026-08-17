import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import LoginForm from './LoginForm.jsx'

afterEach(() => {
  vi.unstubAllGlobals()
})

async function fillAndSubmit({
  email = 'user@example.com',
  password = 'hunter22',
  submitLabel = 'Se connecter',
} = {}) {
  const user = userEvent.setup()
  await user.type(screen.getByPlaceholderText('Email'), email)
  await user.type(screen.getByPlaceholderText('Mot de passe'), password)
  await user.click(screen.getByRole('button', { name: submitLabel }))
}

describe('LoginForm', () => {
  it('envoie les identifiants à /api/login et appelle onAuthenticated en cas de succès', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: () => Promise.resolve({ id: 1, email: 'user@example.com' }),
    })
    vi.stubGlobal('fetch', fetchMock)
    const onAuthenticated = vi.fn()

    render(<LoginForm onAuthenticated={onAuthenticated} />)
    await fillAndSubmit()

    await waitFor(() => expect(onAuthenticated).toHaveBeenCalledWith({ id: 1, email: 'user@example.com' }))
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/login',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ email: 'user@example.com', password: 'hunter22' }),
      })
    )
  })

  it('affiche une erreur inline quand le serveur répond avec un statut non-ok', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: () => Promise.resolve({ detail: 'Identifiants invalides' }),
      })
    )

    render(<LoginForm onAuthenticated={vi.fn()} />)
    await fillAndSubmit()

    await waitFor(() => expect(screen.getByText('Identifiants invalides')).toBeInTheDocument())
  })

  it('bascule vers le mode inscription et poste sur /api/register', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      json: () => Promise.resolve({ id: 2, email: 'new@example.com' }),
    })
    vi.stubGlobal('fetch', fetchMock)
    const user = userEvent.setup()

    render(<LoginForm onAuthenticated={vi.fn()} />)
    await user.click(screen.getByRole('button', { name: /Pas de compte/ }))

    expect(screen.getByRole('button', { name: "S'inscrire" })).toBeInTheDocument()

    await fillAndSubmit({ email: 'new@example.com', password: 'hunter22', submitLabel: "S'inscrire" })

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith('/api/register', expect.objectContaining({ method: 'POST' }))
    )
  })
})
