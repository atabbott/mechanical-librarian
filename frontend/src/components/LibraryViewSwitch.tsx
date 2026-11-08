import { useNavigate } from '@tanstack/react-router'

import { SegmentedControl } from './SegmentedControl'

// Library's Works | Covers switch (#1633). Each view keeps its own search, so
// switching starts the other view at its default.
export function LibraryViewSwitch({ view }: { view: 'works' | 'covers' }) {
  const navigate = useNavigate({ from: '/library' })
  return (
    <SegmentedControl
      label="Library"
      value={view}
      options={[
        { value: 'works', label: 'Works' },
        { value: 'covers', label: 'Covers' },
      ]}
      onChange={(next) =>
        void navigate({
          search:
            next === 'covers' ? { lens: 'all', view: 'covers', tab: 'decide' } : { lens: 'all' },
        })
      }
    />
  )
}
