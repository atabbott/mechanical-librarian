// The one way a recorded time reads (#1286). A time within the hour either
// side is relative (`now`, `5 minutes ago`, `in 5 minutes`); one later or
// earlier the same day is its clock time; any other day adds the weekday and
// date, and the year when it is not this year: `Tue 28 Oct, 14:05`. Times are epoch seconds,
// as every response states them. `zone` exists for the formatter's own tests,
// which fix the locale and time zone; the application reads the browser's.
export interface DateZone {
  readonly locale?: string
  readonly timeZone?: string
}

const dayKey = (at: Date, zone: DateZone) =>
  new Intl.DateTimeFormat('en-CA', {
    timeZone: zone.timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(at)

// The clock time alone, on the 24-hour clock in every locale, so it keeps the
// width a ledger row gives it and never wraps.
export const formatClock = (seconds: number, zone: DateZone = {}) =>
  new Intl.DateTimeFormat(zone.locale, {
    hour: 'numeric',
    minute: '2-digit',
    hourCycle: 'h23',
    timeZone: zone.timeZone,
  })
    .format(new Date(seconds * 1000))
    .replace(/\s/gu, ' ')

// A ledger's day heading: `Tue, 28 Oct 2026` in en-GB.
export const formatDay = (seconds: number, zone: DateZone = {}) =>
  new Intl.DateTimeFormat(zone.locale, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: zone.timeZone,
  }).format(new Date(seconds * 1000))

/**
 * `dayShown` says the reader already sees the day, as under a ledger's day
 * heading, so a time on another day reads as its clock time alone.
 */
export const formatWhen = (
  seconds: number,
  now: Date,
  { dayShown = false, ...zone }: DateZone & { readonly dayShown?: boolean } = {},
) => {
  const at = new Date(seconds * 1000)
  const difference = (at.getTime() - now.getTime()) / 1000
  if (Math.abs(difference) < 3600) {
    const relative = new Intl.RelativeTimeFormat(zone.locale, { numeric: 'auto' })
    return Math.abs(difference) < 60
      ? relative.format(0, 'second')
      : relative.format(Math.round(difference / 60), 'minute')
  }
  if (dayShown || dayKey(at, zone) === dayKey(now, zone)) return formatClock(seconds, zone)
  const sameYear = dayKey(at, zone).slice(0, 4) === dayKey(now, zone).slice(0, 4)
  const date = new Intl.DateTimeFormat(zone.locale, {
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    ...(sameYear ? {} : { year: 'numeric' }),
    timeZone: zone.timeZone,
  }).format(at)
  return `${date}, ${formatClock(seconds, zone)}`
}

// A recorded running time in the owner's units, as the server's media health
// states it: whole minutes from an hour up (`12 h`, `8 h 34 min`), otherwise
// minutes and seconds (`1 min 30 s`). Null for a value that is no running time,
// which then reads as recorded.
// Halves round to even, as the server's `round` does, so both read one value
// the same way.
const roundHalfEven = (value: number) => {
  const floor = Math.floor(value)
  const fraction = value - floor
  return fraction === 0.5 ? (floor % 2 === 0 ? floor : floor + 1) : Math.round(value)
}
export const formatRunningTime = (value: string) => {
  const parsed = Number(value)
  if (value.trim() === '' || !Number.isFinite(parsed) || parsed < 0) return null
  const seconds = roundHalfEven(parsed)
  const shown = seconds >= 3600 ? roundHalfEven(seconds / 60) * 60 : seconds
  const hours = Math.floor(shown / 3600)
  const minutes = Math.floor((shown % 3600) / 60)
  const rest = shown % 60
  return [
    hours ? `${String(hours)} h` : '',
    minutes ? `${String(minutes)} min` : '',
    rest || !shown ? `${String(rest)} s` : '',
  ]
    .filter(Boolean)
    .join(' ')
}
