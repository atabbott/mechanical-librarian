import { createHash } from 'node:crypto'
import { mkdir, readFile, rename, rm, stat, writeFile } from 'node:fs/promises'
import path from 'node:path'
import { brotliCompressSync, constants, gzipSync } from 'node:zlib'

const outputRoot = path.resolve(process.env.ML_FRONTEND_OUT_DIR ?? 'dist')
const handoffAssets = ['preparation-handoff.css', 'preparation-handoff.js']
let shell = await readFile(path.join(outputRoot, 'index.html'), 'utf8')
await mkdir(path.join(outputRoot, 'assets'), { recursive: true })
const hashedHandoffAssets = []
for (const name of handoffAssets) {
  const source = path.join(outputRoot, name)
  const bytes = await readFile(source)
  const extension = path.extname(name)
  const stem = path.basename(name, extension)
  const fingerprint = createHash('sha256').update(bytes).digest('hex').slice(0, 12)
  const target = `assets/${stem}-${fingerprint}${extension}`
  await rename(source, path.join(outputRoot, target))
  shell = shell.replace(`/assets/review-app/${name}`, `/assets/review-app/${target}`)
  hashedHandoffAssets.push(target)
}
await writeFile(path.join(outputRoot, 'index.html'), shell, 'utf8')
const viteManifestPath = path.join(outputRoot, '.vite', 'manifest.json')
const viteManifest = JSON.parse(await readFile(viteManifestPath, 'utf8'))
const entry = Object.values(viteManifest).find((item) => item.isEntry)
if (!entry || typeof entry.file !== 'string') {
  throw new Error('Vite manifest has no application entry')
}

const reachable = new Set()
const visitedKeys = new Set()
const visit = (key) => {
  if (visitedKeys.has(key)) return
  visitedKeys.add(key)
  const item = viteManifest[key]
  if (!item || typeof item.file !== 'string') {
    throw new Error(`Vite manifest references missing entry: ${key}`)
  }
  reachable.add(item.file)
  for (const asset of [...(item.css ?? []), ...(item.assets ?? [])]) reachable.add(asset)
  for (const dependency of [...(item.imports ?? []), ...(item.dynamicImports ?? [])])
    visit(dependency)
}
const entryKey = Object.entries(viteManifest).find(([, item]) => item === entry)?.[0]
if (!entryKey) throw new Error('Vite entry key is missing')
visit(entryKey)
reachable.add('index.html')
for (const asset of hashedHandoffAssets) reachable.add(asset)

const assets = []
for (const relativePath of [...reachable].sort()) {
  if (
    relativePath !== 'index.html' &&
    !/^assets\/[A-Za-z0-9_.-]+-[A-Za-z0-9_-]{8,}\.(?:css|js)$/.test(relativePath)
  ) {
    throw new Error(`Asset is not content hashed: ${relativePath}`)
  }
  const absolutePath = path.resolve(outputRoot, relativePath)
  if (!absolutePath.startsWith(`${outputRoot}${path.sep}`))
    throw new Error('Asset escapes output root')
  const bytes = await readFile(absolutePath)
  const metadata = await stat(absolutePath)
  if (!metadata.isFile()) throw new Error(`Asset is not a regular file: ${relativePath}`)
  const variants = [
    [
      relativePath === 'index.html' ? 'shell' : relativePath.endsWith('.css') ? 'style' : 'script',
      relativePath,
      bytes,
    ],
  ]
  if (relativePath !== 'index.html') {
    // Precompressed siblings let the server negotiate encodings without compressing per request.
    const brotli = { [constants.BROTLI_PARAM_QUALITY]: constants.BROTLI_MAX_QUALITY }
    variants.push(['br', `${relativePath}.br`, brotliCompressSync(bytes, { params: brotli })])
    variants.push(['gzip', `${relativePath}.gz`, gzipSync(bytes, { level: 9 })])
  }
  for (const [kind, variantPath, variantBytes] of variants) {
    if (variantPath !== relativePath)
      await writeFile(path.join(outputRoot, variantPath), variantBytes)
    assets.push({
      kind,
      path: variantPath,
      sha256: createHash('sha256').update(variantBytes).digest('hex'),
      size: variantBytes.byteLength,
    })
  }
}
assets.sort((left, right) => (left.path < right.path ? -1 : 1))

const identityInput = JSON.stringify({
  schema: 'review-react-assets-v1',
  entry: 'index.html',
  assets,
})
const manifest = {
  schema: 'review-react-assets-v1',
  build_identity: createHash('sha256').update(identityInput).digest('hex'),
  entry: 'index.html',
  assets,
}
await writeFile(
  path.join(outputRoot, 'review-assets-v1.json'),
  `${JSON.stringify(manifest, null, 2)}\n`,
  'utf8',
)
await rm(path.join(outputRoot, '.vite'), { recursive: true })
process.stdout.write(`${path.join(outputRoot, 'review-assets-v1.json')}\n`)
