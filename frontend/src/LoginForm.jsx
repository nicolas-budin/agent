import { useState } from 'react'

export default function LoginForm({ onAuthenticated }) {
  const [mode, setMode] = useState('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    setSubmitting(true)
    try {
      const url = mode === 'login' ? '/api/login' : '/api/register'
      const resp = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
      })
      const body = await resp.json().catch(() => ({}))
      if (!resp.ok) {
        setError(body.detail || `Erreur : ${resp.status}`)
        return
      }
      onAuthenticated(body)
    } finally {
      setSubmitting(false)
    }
  }

  function toggleMode() {
    setMode((m) => (m === 'login' ? 'register' : 'login'))
    setError('')
  }

  return (
    <div className="login">
      <h1>💬 Claude SDK Client</h1>
      <form className="login-form" onSubmit={handleSubmit}>
        <input
          type="email"
          autoComplete="email"
          placeholder="Email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          disabled={submitting}
          required
        />
        <input
          type="password"
          autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
          placeholder="Mot de passe"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          disabled={submitting}
          required
        />
        {error && <div className="error">{error}</div>}
        <button type="submit" disabled={submitting}>
          {mode === 'login' ? 'Se connecter' : "S'inscrire"}
        </button>
      </form>
      <button type="button" className="link-button" onClick={toggleMode}>
        {mode === 'login' ? "Pas de compte ? S'inscrire" : 'Déjà un compte ? Se connecter'}
      </button>
    </div>
  )
}
