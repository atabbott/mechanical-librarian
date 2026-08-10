import { Link, useNavigate } from '@tanstack/react-router'
import type { SyntheticEvent } from 'react'

import type { AttentionView } from '../api/http'
import { useRouteReadyFocus } from '../focus'
import { attentionRoute } from '../router'

export default function AttentionPage({ data }: { data: AttentionView }) {
  const search = attentionRoute.useSearch()
  const navigate = useNavigate({ from: '/attention' })
  useRouteReadyFocus(`${search.q ?? ''}:${search.cursor ?? ''}`)
  const submit = (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault()
    const value = new FormData(event.currentTarget).get('q')
    const q = typeof value === 'string' ? value : ''
    void navigate({ search: q ? { q } : {} })
  }
  return <>
    <header className="page-heading" tabIndex={-1} data-route-focus><div><p className="eyebrow">Human exceptions</p><h1>Fields needing attention</h1><p>A field-level queue for choices automation cannot make.</p></div><strong className="metric">{data.total} current</strong></header>
    <form className="search" onSubmit={submit}><label htmlFor="attention-query">Search title, library, or item</label><div><input key={search.q} id="attention-query" name="q" type="search" maxLength={128} defaultValue={search.q ?? ''}/><button>Search</button></div></form>
    <section className="work-list" aria-label="Fields needing attention">{data.items.length ? data.items.map((item) => <article className="work-row" key={`${item.id}:${item.destination}`}><div><h2><Link to="/review/$reviewCaseId" params={{ reviewCaseId: item.id }} search={{ field: item.destination, subview: 'review' }}>{item.title}</Link></h2><p>{item.subtitle}</p></div><span>{item.destination.replaceAll('_', ' ')}</span></article>) : <p>Nothing needs your attention.</p>}{data.nextCursor ? <Link to="/attention" search={{ ...search, cursor: data.nextCursor }}>Next page</Link> : <p>End of the bounded current result.</p>}</section>
  </>
}
