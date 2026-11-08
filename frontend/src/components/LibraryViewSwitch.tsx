import { useNavigate } from '@tanstack/react-router'

import { SegmentedControl } from './SegmentedControl'

// Library's Works | Covers switch (#1633), above each view's heading. Each view
// keeps its own search, so switching starts the other view at its default, and
// focus moves to the view that replaced the page.
export function LibraryViewSwitch({ view }: { view: 'works' | 'covers' }) {
  const navigate = useNavigate({ from: '/library' })
  return (
    <div className="library-view-switch">
      <SegmentedControl
        label="Library views"
        value={view}
        options={[
          { value: 'works', label: 'Works' },
          { value: 'covers', label: 'Covers' },
        ]}
        onChange={(next) =>
          void navigate({
            search:
              next === 'covers' ? { lens: 'all', view: 'covers', tab: 'decide' } : { lens: 'all' },
          }).then(() =>
            requestAnimationFrame(() =>
              document
                .querySelector<HTMLElement>('main [data-route-focus]')
                ?.focus({ preventScroll: true }),
            ),
          )
        }
      />
    </div>
  )
}
