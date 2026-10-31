import type { ActivityCategory } from './api/http'
import { formatDay } from './dates'
import { writeStateLabels } from './labels'
// The words the Activity ledger reads in.
// The ledger's result words. A ledger state is open on the wire, since the
// ledger also carries records of a type this build does not know, so a state
// outside these reads as `Recorded`. A withdrawal reads `Recorded` beside the
// server's own `Withdrawn` change, so the row does not say it twice (#1286).
const ledgerStateLabels: Readonly<Record<string, string>> = {
  ...writeStateLabels,
  accept: 'Accepted',
  keep_current: 'Kept current',
  reject: 'Rejected',
  owner_entered: 'Owner-entered',
  withdraw: 'Recorded',
  completed: 'Completed',
  unavailable: 'Unavailable',
}
// A change stopped only by a lost connection is still waiting to be prepared
// again, so it reads as waiting rather than as a stopped stale (#1402).
export const stateLabel = (
  state: string,
  advice?: 'description' | 'cover',
  failure?: string | null,
) =>
  (state === 'stale' && failure === 'connection' ? 'Waiting safely' : undefined) ??
  (advice === 'description'
    ? {
        completed: 'Description advice completed',
        succeeded: 'Description advice completed',
        failed: 'Description advice failed safely',
      }[state]
    : advice === 'cover'
      ? {
          completed: 'Cover advice completed',
          unavailable: 'Cover advice unavailable',
          failed: 'Cover advice failed safely',
          selected: 'Owner cover choice recorded',
          retry: 'Manual cover retry',
        }[state]
      : undefined) ??
  ledgerStateLabels[state] ??
  'Recorded'
// What the owner calls a record, on the row and on the control that filters
// for it, so a chip and the rows it selects never use different words. The
// filter axis is still the durable event type the ledger is indexed by; only
// the language is the owner's.
export const categoryLabel: Record<ActivityCategory, string> = {
  owner: 'Owner choice',
  automatic: 'Automatic choice',
  policy: 'Policy change',
  recovery: 'Recovery',
}
// A ledger day heading; each row under it states only its time.
export const dayHeading = (value: number | null) =>
  value === null ? 'Date not recorded' : formatDay(value)
