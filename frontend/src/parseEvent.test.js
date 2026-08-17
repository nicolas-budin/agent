import { describe, expect, it } from 'vitest'
import { parseEvent } from './App.jsx'

describe('parseEvent', () => {
  it('extrait le type d’événement et la donnée', () => {
    expect(parseEvent('event: text\ndata: Bonjour !')).toEqual({
      event: 'text',
      data: 'Bonjour !',
    })
  })

  it('utilise "message" comme type par défaut si aucune ligne event: n’est présente', () => {
    expect(parseEvent('data: sans type')).toEqual({
      event: 'message',
      data: 'sans type',
    })
  })

  it('joint plusieurs lignes data: avec un saut de ligne (spec SSE)', () => {
    expect(parseEvent('event: text\ndata: ligne 1\ndata: ligne 2')).toEqual({
      event: 'text',
      data: 'ligne 1\nligne 2',
    })
  })

  it('ne retire qu’un seul espace après "data:", pas les suivants', () => {
    expect(parseEvent('data:  deux espaces')).toEqual({
      event: 'message',
      data: ' deux espaces',
    })
  })

  it('gère une donnée JSON (événement done/sources)', () => {
    const result = parseEvent('event: done\ndata: {"cost_usd": 0.01, "duration_ms": 100}')
    expect(result.event).toBe('done')
    expect(JSON.parse(result.data)).toEqual({ cost_usd: 0.01, duration_ms: 100 })
  })

  it('retourne une donnée vide si aucune ligne data: n’est présente', () => {
    expect(parseEvent('event: ping')).toEqual({ event: 'ping', data: '' })
  })
})
