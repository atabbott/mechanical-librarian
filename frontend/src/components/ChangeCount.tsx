// A navigation entry's count of draft changes: a visible badge and the same
// number in words for the entry's accessible description.
export const ChangeCount = ({ id, count }: { id: string; count: number }) => (
  <>
    <b className="cm-change-count" aria-hidden="true">
      {count}
    </b>
    <span className="sr-only" id={id}>
      {`${String(count)} changed ${count === 1 ? 'field' : 'fields'}`}
    </span>
  </>
)
