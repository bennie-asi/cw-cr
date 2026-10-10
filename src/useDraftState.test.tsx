import { act, renderHook } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import { useDraftState } from './useDraftState'

it('tracks edits and becomes clean when restored to the saved value', () => {
  const dirty = vi.fn()
  const { result } = renderHook(() => useDraftState({ model: 'saved' }, dirty))
  expect(dirty).toHaveBeenLastCalledWith(false)
  act(() => result.current[1]({ model: 'draft' }))
  expect(result.current[0]).toEqual({ model: 'draft' })
  expect(dirty).toHaveBeenLastCalledWith(true)
  act(() => result.current[1]({ model: 'saved' }))
  expect(dirty).toHaveBeenLastCalledWith(false)
})

it('accepts loaded or saved form values as a new baseline and clears on unmount', () => {
  const dirty = vi.fn()
  const { result, unmount } = renderHook(() => useDraftState({ token: '' }, dirty))
  act(() => result.current[1](previous => ({ ...previous, token: 'disposable' })))
  expect(dirty).toHaveBeenLastCalledWith(true)
  act(() => result.current[2](previous => ({ ...previous, token: '' })))
  expect(result.current[0].token).toBe('')
  expect(dirty).toHaveBeenLastCalledWith(false)
  act(() => result.current[1]({ token: 'another' }))
  unmount()
  expect(dirty).toHaveBeenLastCalledWith(false)
})
