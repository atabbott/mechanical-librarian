import { Link } from '@tanstack/react-router'

// A sentence that names one of these Settings sections, with that path as a
// link to its section (#1765, #2013). The rest stays plain text.
const groups = {
  Reviewer: 'reviewer',
  Audiobookshelf: 'connection',
  Libraries: 'libraries',
  'Background work': 'background',
} as const
const settingsPath = /(Settings → (?:Reviewer|Audiobookshelf|Libraries|Background work))/u

export const SettingsPathText = ({
  text,
  onNavigate,
}: {
  text: string
  onNavigate?: () => void
}) => (
  <>
    {text.split(settingsPath).map((part, index) =>
      index % 2 === 1 ? (
        <Link
          key={index}
          to="/settings"
          search={{ group: groups[part.slice('Settings → '.length) as keyof typeof groups] }}
          onClick={onNavigate}
        >
          {part}
        </Link>
      ) : (
        part
      ),
    )}
  </>
)
