import { createPortal } from 'react-dom'
import {
  type CSSProperties,
  type KeyboardEvent,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from 'react'

interface DropdownOption {
  value: string
  label: string
}

interface DropdownPosition {
  left: number
  top: number
  width: number
  maxHeight: number
  above: boolean
}

export function Dropdown(props: {
  ariaLabel: string
  value: string
  options: readonly DropdownOption[]
  onChange: (value: string) => void
  disabled?: boolean
  compact?: boolean
  focusKey?: string
}) {
  const listboxId = useId()
  const triggerRef = useRef<HTMLButtonElement>(null)
  const optionRefs = useRef<(HTMLButtonElement | null)[]>([])
  const returnFocusOnCloseRef = useRef(false)
  const [open, setOpen] = useState(false)
  const [activeIndex, setActiveIndex] = useState(0)
  const [position, setPosition] = useState<DropdownPosition | null>(null)
  const selectedIndex = Math.max(
    0,
    props.options.findIndex((option) => option.value === props.value),
  )
  const selected = props.options[selectedIndex]

  const updatePosition = useCallback(() => {
    const trigger = triggerRef.current
    if (!trigger) return
    const rect = trigger.getBoundingClientRect()
    const viewportGap = 8
    const layoutless = rect.width === 0 && rect.height === 0
    if (!layoutless && (rect.bottom < viewportGap || rect.top > window.innerHeight - viewportGap)) {
      setOpen(false)
      return
    }
    if (layoutless) {
      setPosition({ left: viewportGap, top: viewportGap, width: 190, maxHeight: 280, above: false })
      return
    }
    const width = Math.min(Math.max(rect.width, 190), window.innerWidth - viewportGap * 2)
    const spaceBelow = window.innerHeight - rect.bottom - viewportGap
    const spaceAbove = rect.top - viewportGap
    const above = spaceBelow < 190 && spaceAbove > spaceBelow
    const available = above ? spaceAbove : spaceBelow
    setPosition({
      left: Math.max(viewportGap, Math.min(rect.left, window.innerWidth - width - viewportGap)),
      top: above ? rect.top - 6 : rect.bottom + 6,
      width,
      maxHeight: Math.max(120, Math.min(280, available - 6)),
      above,
    })
  }, [])

  const openMenu = useCallback(() => {
    if (props.disabled || props.options.length === 0) return
    updatePosition()
    setActiveIndex(selectedIndex)
    setOpen(true)
  }, [props.disabled, props.options.length, selectedIndex, updatePosition])

  useEffect(() => {
    if (!open) return
    const closeOnOutsidePress = (event: PointerEvent) => {
      const target = event.target
      if (!(target instanceof Node)) return
      if (
        !triggerRef.current?.contains(target) &&
        !document.getElementById(listboxId)?.contains(target)
      )
        setOpen(false)
    }
    const updateMenuPosition = () => updatePosition()
    document.addEventListener('pointerdown', closeOnOutsidePress)
    window.addEventListener('resize', updateMenuPosition)
    window.addEventListener('scroll', updateMenuPosition, true)
    return () => {
      document.removeEventListener('pointerdown', closeOnOutsidePress)
      window.removeEventListener('resize', updateMenuPosition)
      window.removeEventListener('scroll', updateMenuPosition, true)
    }
  }, [listboxId, open, selectedIndex, updatePosition])

  useLayoutEffect(() => {
    if (open) {
      optionRefs.current[selectedIndex]?.focus()
      return
    }
    if (returnFocusOnCloseRef.current) {
      returnFocusOnCloseRef.current = false
      triggerRef.current?.focus()
    }
  }, [open, selectedIndex])

  const focusOption = (index: number) => {
    const next = (index + props.options.length) % props.options.length
    setActiveIndex(next)
    optionRefs.current[next]?.focus()
  }
  const handleTriggerKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (
      event.key === 'ArrowDown' ||
      event.key === 'ArrowUp' ||
      event.key === 'Enter' ||
      event.key === ' '
    ) {
      event.preventDefault()
      openMenu()
    }
  }
  const handleOptionKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault()
      focusOption(index + 1)
    }
    if (event.key === 'ArrowUp') {
      event.preventDefault()
      focusOption(index - 1)
    }
    if (event.key === 'Home') {
      event.preventDefault()
      focusOption(0)
    }
    if (event.key === 'End') {
      event.preventDefault()
      focusOption(props.options.length - 1)
    }
    // Tab closes like Escape: the menu is portaled to the end of the page, so
    // focus goes back to the trigger rather than past everything else.
    if (event.key === 'Escape' || event.key === 'Tab') {
      event.preventDefault()
      returnFocusOnCloseRef.current = true
      setOpen(false)
    }
  }
  const choose = (value: string) => {
    if (value !== props.value) props.onChange(value)
    returnFocusOnCloseRef.current = true
    setOpen(false)
  }
  const menuStyle: CSSProperties | undefined = position
    ? {
        left: position.left,
        top: position.top,
        width: position.width,
        maxHeight: position.maxHeight,
      }
    : undefined

  return (
    <div className={`cm-dropdown ${props.compact ? 'is-compact' : ''}`}>
      <button
        ref={triggerRef}
        data-activity-focus={props.focusKey}
        type="button"
        className="cm-dropdown-trigger"
        role="combobox"
        aria-label={props.ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listboxId}
        disabled={props.disabled}
        onClick={() => (open ? setOpen(false) : openMenu())}
        onKeyDown={handleTriggerKeyDown}
      >
        <span>{selected?.label ?? props.value}</span>
        <svg aria-hidden="true" viewBox="0 0 24 24">
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>
      {open && position
        ? createPortal(
            <div
              id={listboxId}
              className={`cm-dropdown-menu ${position.above ? 'is-above' : ''}`}
              role="listbox"
              aria-label={props.ariaLabel}
              style={menuStyle}
            >
              {props.options.map((option, index) => (
                <button
                  key={option.value}
                  ref={(element) => {
                    optionRefs.current[index] = element
                  }}
                  type="button"
                  role="option"
                  aria-selected={option.value === props.value}
                  className={index === activeIndex ? 'is-active' : ''}
                  onFocus={() => setActiveIndex(index)}
                  onKeyDown={(event) => handleOptionKeyDown(event, index)}
                  onClick={() => choose(option.value)}
                >
                  <span>{option.label}</span>
                  {option.value === props.value ? (
                    <svg aria-hidden="true" viewBox="0 0 24 24">
                      <path d="m5 12 4 4L19 7" />
                    </svg>
                  ) : null}
                </button>
              ))}
            </div>,
            document.body,
          )
        : null}
    </div>
  )
}
