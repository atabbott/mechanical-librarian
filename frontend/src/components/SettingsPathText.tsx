import { Link } from '@tanstack/react-router'

// A sentence that names Settings → Reviewer or Settings → Audiobookshelf, with
// that path as a link to its section (#1765). The rest stays plain text.
const settingsPath = /(Settings → (?:Reviewer|Audiobookshelf))/u

export const SettingsPathText = ({ text }: { text: string }) => (
  <>
    {text.split(settingsPath).map((part, index) =>
      index % 2 === 1 ? (
        <Link
          key={index}
          to="/settings"
          search={{ group: part.endsWith('Reviewer') ? 'reviewer' : 'connection' }}
        >
          {part}
        </Link>
      ) : (
        part
      ),
    )}
  </>
)
