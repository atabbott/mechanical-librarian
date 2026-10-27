import { useEffect, useState } from 'react'

import { formatWhen, nextChange } from '../dates'

// A recorded time as `formatWhen` reads it, re-read on one timer whenever that
// reading would next change, so `5 minutes ago` never stays on screen once it
// is no longer true (#1286).
export function RecordedTime({
  seconds,
  dayShown = false,
}: {
  readonly seconds: number
  readonly dayShown?: boolean
}) {
  const [tick, setTick] = useState(0)
  const now = new Date()
  useEffect(() => {
    const timer = window.setTimeout(
      () => setTick((value) => value + 1),
      Math.min(nextChange(seconds, new Date()), 2_147_483_647),
    )
    return () => window.clearTimeout(timer)
  }, [seconds, tick])
  return <>{formatWhen(seconds, now, { dayShown })}</>
}
