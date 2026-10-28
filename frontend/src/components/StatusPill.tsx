import type { HTMLAttributes } from 'react'

// The one compact state chip. Its words carry the state; the tone only reinforces it.
export const StatusPill = ({
  tone = 'neutral',
  className,
  ...props
}: HTMLAttributes<HTMLSpanElement> & { tone?: 'neutral' | 'notice' | 'accent' | 'danger' }) => (
  <span className={`status-pill is-${tone}${className ? ` ${className}` : ''}`} {...props} />
)
