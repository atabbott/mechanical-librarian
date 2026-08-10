import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

import openapiTS, { astToString } from 'openapi-typescript'

const option = (name) => {
  const position = process.argv.indexOf(name)
  return position === -1 ? undefined : process.argv[position + 1]
}

const input = path.resolve(
  option('--input') ?? process.env.ML_OPENAPI_PATH ?? '../docs/reference/review-openapi-v1.json',
)
const output = path.resolve(
  option('--output') ?? process.env.ML_GENERATED_DTO_PATH ?? 'src/generated/review-api.d.ts',
)

const schema = await openapiTS(pathToFileURL(input), {
  alphabetize: true,
  exportType: true,
  immutable: true,
})
const source = astToString(schema)
await mkdir(path.dirname(output), { recursive: true })
await writeFile(output, source, { encoding: 'utf8' })
process.stdout.write(`${output}\n`)

