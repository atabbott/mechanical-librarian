import type { ButtonHTMLAttributes } from 'react'

// The one button: primary is filled, secondary is the plain button rule, danger is outlined.
// It sets no default type, so every call site keeps the type it states.
export const Button = ({
  variant = 'secondary',
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'danger' }) => (
  <button className={`button-${variant}${className ? ` ${className}` : ''}`} {...props} />
)
