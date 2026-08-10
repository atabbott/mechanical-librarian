import { createHash } from 'node:crypto'
import { readFile } from 'node:fs/promises'

const accepted = new Set([
  '(MIT OR CC0-1.0)',
  'Apache-2.0',
  'BSD-2-Clause',
  'BSD-3-Clause',
  'BlueOak-1.0.0',
  'CC-BY-4.0',
  'CC0-1.0',
  'ISC',
  'MIT',
  'MIT-0',
  'MPL-2.0',
  'Python-2.0',
  'Unlicense',
])

const lock = JSON.parse(await readFile(new URL('../package-lock.json', import.meta.url), 'utf8'))
if (lock.lockfileVersion !== 3 || typeof lock.packages !== 'object' || lock.packages === null) {
  throw new Error('Unsupported package lock')
}
const inventory = Object.entries(lock.packages)
  .filter(([location]) => location !== '')
  .map(([location, value]) => {
    if (
      typeof value !== 'object' || value === null ||
      typeof value.version !== 'string' || typeof value.license !== 'string' ||
      !accepted.has(value.license)
    ) {
      throw new Error(`Unreviewed licence metadata at ${location}`)
    }
    return [location, value.version, value.license]
  })
  .sort(([left], [right]) => left.localeCompare(right, 'en'))

const canonical = `${JSON.stringify(inventory)}\n`
process.stdout.write(`${JSON.stringify({
  schema: 'frontend-license-inventory-v1',
  packages: inventory.length,
  sha256: createHash('sha256').update(canonical).digest('hex'),
})}\n`)
