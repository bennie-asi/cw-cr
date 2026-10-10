import type { Dispatch, SetStateAction } from 'react'
import { useCallback, useEffect, useState } from 'react'

// Form scalars stay in component memory; no browser persistence of credentials.
export function useDraftState<T>(initial: T, onDirty?: (dirty: boolean) => void): [T, Dispatch<SetStateAction<T>>, Dispatch<SetStateAction<T>>] {
  const [state, setState] = useState({ value: initial, baseline: initial })
  const setValue = useCallback<Dispatch<SetStateAction<T>>>(next => {
    setState(previous => ({ ...previous, value: typeof next === 'function' ? (next as (value: T) => T)(previous.value) : next }))
  }, [])
  const resetValue = useCallback<Dispatch<SetStateAction<T>>>(next => {
    setState(previous => {
      const value = typeof next === 'function' ? (next as (value: T) => T)(previous.value) : next
      return { value, baseline: value }
    })
  }, [])
  const dirty = JSON.stringify(state.value) !== JSON.stringify(state.baseline)
  useEffect(() => { onDirty?.(dirty) }, [dirty, onDirty])
  useEffect(() => () => { onDirty?.(false) }, [onDirty])
  return [state.value, setValue, resetValue]
}
