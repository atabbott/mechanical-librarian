import { Link } from '@tanstack/react-router'

import type { CaseDetailView } from '../api/http'

export default function DetailPage({ data }: { data: CaseDetailView }) {
  return (
    <>
      <header className="page-heading">
        <div>
          <p className="eyebrow">Exact-current review</p>
          <h1>{data.title}</h1>
          <p>{data.subtitle}</p>
        </div>
        <span className={data.current ? 'badge' : 'badge warning'}>{data.current ? 'Current' : 'Superseded'}</span>
      </header>
      <Link to="/library" search={{ lens: 'needs_attention' }} preload="intent">Back to library</Link>
      <section aria-label="Review fields" className="field-list">
        {data.fields.map((field) => (
          <article key={field.destination}>
            <h2>{field.destination}</h2>
            <p>{field.currentValue}</p>
            <span>{field.disposition.replaceAll('_', ' ')}</span>
          </article>
        ))}
      </section>
    </>
  )
}

