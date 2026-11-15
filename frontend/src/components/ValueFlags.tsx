import type { TitleFlagRule, ValueFlagView } from '../api/http'

// What each Looks wrong finding says on a Library row (#1334). Every title rule
// reads the title; a credit label names the contributor list it was found in.
const titleFlagLabels = {
  title_part_number: 'Title is only a disc or part number',
  title_disc_number: 'Title ends with a disc number',
  title_is_author: "Title is the author's name",
  title_has_author: "Title includes the author's name",
  title_all_capitals: 'Title is in capitals',
  title_all_lower_case: 'Title is all lower case',
  title_announcement: 'Title includes an announcement',
} satisfies Record<TitleFlagRule, string>
const creditLabelLabels = {
  authors: "An author's name starts with a credit label",
  narrators: "A narrator's name starts with a credit label",
} satisfies Record<Exclude<ValueFlagView['destination'], 'title'>, string>

const valueFlagLabel = (flag: ValueFlagView): string =>
  flag.destination === 'title' ? titleFlagLabels[flag.rule] : creditLabelLabels[flag.destination]

// One line per finding, in the server's rule order. The row's button names it
// in `aria-describedby` by `id`, because the button's label replaces its text.
export function ValueFlags({ id, flags }: { id: string; flags: readonly ValueFlagView[] }) {
  if (flags.length === 0) return null
  return (
    <span id={id}>
      {flags.map((flag) => (
        <span key={`${flag.destination}:${flag.rule}`} className="library-provider-summary">
          {valueFlagLabel(flag)}
        </span>
      ))}
    </span>
  )
}
