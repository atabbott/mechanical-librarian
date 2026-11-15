import { useState } from 'react'
import type { ComponentPropsWithRef, ReactNode } from 'react'

import type { StatusCentre } from '../providerThrottling'

const Svg = ({ children, size = 22 }: { children: ReactNode; size?: number }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
    focusable="false"
  >
    {children}
  </svg>
)

export const SunIcon = () => (
  <Svg>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2.5v2.2M12 19.3v2.2M2.5 12h2.2M19.3 12h2.2M5.3 5.3l1.6 1.6M17.1 17.1l1.6 1.6M5.3 18.7l1.6-1.6M17.1 6.9l1.6-1.6" />
  </Svg>
)
export const MoonIcon = () => (
  <Svg>
    <path d="M20 14.2A8.2 8.2 0 0 1 9.8 4 8.2 8.2 0 1 0 20 14.2Z" />
  </Svg>
)
export const DoorIcon = () => (
  <Svg>
    <path d="M3.5 21h17" />
    <path d="M6 21V4.6A1.6 1.6 0 0 1 7.6 3H13" />
    <path d="M13 3l5.5 2v14L13 21Z" />
    <circle cx="15.4" cy="12.3" r=".7" fill="currentColor" />
  </Svg>
)

const RADIUS = 9.6
const CIRCUMFERENCE = 2 * Math.PI * RADIUS
// A filled disc with a mark in the page's surface colour, so it reads in both themes.
const Disc = ({ colour, children }: { colour: string; children: ReactNode }) => (
  <>
    <circle cx="12" cy="12" r="6.1" fill={colour} stroke="none" />
    {children}
  </>
)
const Exclamation = () => (
  <>
    <path d="M12 8.6v4" stroke="var(--surface)" strokeWidth="1.9" />
    <circle cx="12" cy="15.3" r="1" fill="var(--surface)" stroke="none" />
  </>
)

/**
 * The background-work status: a ring that turns and fills while preparing, and
 * one centre mark for the first thing that is true. The name and tooltip carry
 * the words; the marks only reinforce them.
 */
export const StatusIcon = ({
  centre,
  active,
  fill,
}: {
  centre: StatusCentre
  active: boolean
  fill: number
}) => (
  <Svg size={26}>
    <circle cx="12" cy="12" r={RADIUS} opacity={active ? 0.25 : 1} />
    {active ? (
      <g className="status-icon-turn" data-fill={fill.toFixed(2)}>
        <circle
          cx="12"
          cy="12"
          r={RADIUS}
          strokeWidth="2.6"
          // One dash and one gap make exactly one turn of the ring, so the
          // stylesheet turns it by moving the dash, which never widens its box.
          strokeDasharray={`${String(fill * CIRCUMFERENCE)} ${String((1 - fill) * CIRCUMFERENCE)}`}
          transform="rotate(-90 12 12)"
        />
      </g>
    ) : null}
    {centre === 'stopped' ? (
      <Disc colour="var(--danger)">
        <rect
          x="9.7"
          y="9.7"
          width="4.6"
          height="4.6"
          rx=".6"
          fill="var(--surface)"
          stroke="none"
        />
      </Disc>
    ) : centre === 'needs_you' ? (
      <Disc colour="var(--danger)">
        <Exclamation />
      </Disc>
    ) : centre === 'attention' ? (
      <Disc colour="var(--notice)">
        <Exclamation />
      </Disc>
    ) : centre === 'paused' ? (
      <path d="M10 8.8v6.4M14 8.8v6.4" />
    ) : centre === 'waiting' ? (
      <circle cx="12" cy="12" r="3.1" fill="var(--notice)" stroke="none" />
    ) : centre === 'idle' ? (
      <path d="M8.3 12.3l2.5 2.5 4.9-5.4" />
    ) : null}
  </Svg>
)

/**
 * A 44 px masthead button that shows only an icon. Its name is the accessible
 * label, and the same words show as a tooltip on hover and keyboard focus that
 * screen readers skip; Escape hides it until the pointer or focus leaves.
 */
export const IconButton = ({
  label,
  className,
  children,
  onKeyDown,
  onBlur,
  onMouseLeave,
  ...props
}: Omit<ComponentPropsWithRef<'button'>, 'aria-label'> & { label: string }) => {
  const [dismissed, setDismissed] = useState(false)
  return (
    <button
      {...props}
      type="button"
      className={`icon-button${className ? ` ${className}` : ''}`}
      aria-label={label}
      data-tooltip={dismissed ? 'dismissed' : undefined}
      onKeyDown={(event) => {
        if (event.key === 'Escape') setDismissed(true)
        onKeyDown?.(event)
      }}
      onBlur={(event) => {
        setDismissed(false)
        onBlur?.(event)
      }}
      onMouseLeave={(event) => {
        setDismissed(false)
        onMouseLeave?.(event)
      }}
    >
      {children}
      <span className="masthead-tooltip" aria-hidden="true">
        {label}
      </span>
    </button>
  )
}
