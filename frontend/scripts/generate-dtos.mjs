import { mkdir, readFile, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { pathToFileURL } from 'node:url'

import openapiTS, { astToString } from 'openapi-typescript'

import { emitDecoders, responseTruth } from './generate-decoders.mjs'

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
const responseOutput = path.resolve(
  option('--response-output') ?? path.join(path.dirname(output), 'review-api-response.d.ts'),
)
const decodersOutput = path.resolve(option('--decoders') ?? path.join(path.dirname(output), 'review-decoders.ts'))

const options = { alphabetize: true, exportType: true, immutable: true }
const document = JSON.parse(await readFile(input, 'utf8'))
const decoders = emitDecoders(document)
const truth = responseTruth(document)
for (const [file, source] of [
  [output, astToString(await openapiTS(pathToFileURL(input), options))],
  [responseOutput, astToString(await openapiTS(truth, options))],
  [decodersOutput, decoders],
]) {
  await mkdir(path.dirname(file), { recursive: true })
  await writeFile(file, source, { encoding: 'utf8' })
  process.stdout.write(`${file}\n`)
}
