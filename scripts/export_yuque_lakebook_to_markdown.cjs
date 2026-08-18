const { execFileSync } = require('node:child_process');
const { mkdirSync, writeFileSync } = require('node:fs');
const { join, dirname, relative } = require('node:path');

const [archive, outputRoot] = process.argv.slice(2);
if (!archive || !outputRoot) throw new Error('Usage: node export_yuque_lakebook_to_markdown.cjs <archive> <output-dir>');

const entries = execFileSync('tar', ['-tf', archive], { encoding: 'utf8' }).trim().split('\n');
const metaEntry = entries.find((entry) => entry.endsWith('/$meta.json'));
const rawMeta = execFileSync('tar', ['-xOf', archive, metaEntry], { encoding: 'utf8' });
const book = JSON.parse(JSON.parse(rawMeta).meta).book;
const records = [];
let current = null;

for (const line of book.tocYml.split('\n')) {
  const match = line.match(/^(?:-\s+|\s{2})([a-z_]+):\s*(.*)$/);
  if (!match) continue;
  const [, key, value] = match;
  if (key === 'type') {
    if (current) records.push(current);
    current = { type: value };
  } else if (current) current[key] = value.replace(/^'|'$/g, '');
}
if (current) records.push(current);

const invalid = /[\\/:*?"<>|]/g;
const clean = (name) => (name || 'Untitled').replace(invalid, '-').trim();
const imageSources = new Map();
const stripHtml = (html, documentDir) => html
  .replace(/\r/g, '')
  .replace(/<img\b[^>]*\bsrc=["']([^"']+)["'][^>]*>/gi, (_, source) => {
    const url = source.replace(/&amp;/g, '&');
    const extension = (new URL(url)).pathname.match(/\.([a-z0-9]+)$/i)?.[1] || 'bin';
    const filename = `${String(imageSources.size + 1).padStart(2, '0')}.${extension}`;
    imageSources.set(url, filename);
    return `\n\n![image](${relative(documentDir, join(outputRoot, 'images', filename))})\n\n`;
  })
  .replace(/<pre[^>]*><code[^>]*>([\s\S]*?)<\/code><\/pre>/gi, (_, code) => `\n\n\`\`\`\n${decode(code).trim()}\n\`\`\`\n\n`)
  .replace(/<h([1-6])[^>]*>([\s\S]*?)<\/h\1>/gi, (_, level, text) => `\n\n${'#'.repeat(Number(level))} ${decode(text).trim()}\n\n`)
  .replace(/<li[^>]*>([\s\S]*?)<\/li>/gi, (_, text) => `\n- ${decode(text).trim()}`)
  .replace(/<br\s*\/?>/gi, '\n')
  .replace(/<\/p>|<\/div>|<\/blockquote>|<\/tr>/gi, '\n')
  .replace(/<[^>]+>/g, '')
  .replace(/\n{3,}/g, '\n\n')
  .trim();
const decode = (text) => text
  .replace(/<[^>]+>/g, '')
  .replace(/&nbsp;/g, ' ')
  .replace(/&lt;/g, '<')
  .replace(/&gt;/g, '>')
  .replace(/&amp;/g, '&')
  .replace(/&quot;/g, '"')
  .replace(/&#39;/g, "'");

const docMap = new Map();
for (const entry of entries.filter((item) => item.endsWith('.json') && !item.endsWith('/$meta.json'))) {
  const payload = JSON.parse(execFileSync('tar', ['-xOf', archive, entry], { encoding: 'utf8' }));
  if (payload.doc?.slug) docMap.set(payload.doc.slug, payload.doc);
}

const hierarchy = [];
let count = 0;
for (const record of records) {
  const level = Number(record.level || 0);
  hierarchy.length = level;
  if (record.type === 'TITLE') {
    hierarchy[level] = clean(record.title);
    continue;
  }
  if (record.type !== 'DOC') continue;
  const doc = docMap.get(record.url);
  if (!doc) continue;
  const dir = join(outputRoot, ...hierarchy.filter(Boolean));
  const filename = `${clean(doc.title)}.md`;
  const path = join(dir, filename);
  const frontmatter = [
    '---',
    `title: "${String(doc.title).replace(/"/g, '\\"')}"`,
    `yuque_slug: ${doc.slug}`,
    `updated: ${doc.updated_at || ''}`,
    '---',
    '',
  ].join('\n');
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, frontmatter + (doc.body ? stripHtml(doc.body, dir) : doc.description || ''));
  count += 1;
}
const imageDir = join(outputRoot, 'images');
mkdirSync(imageDir, { recursive: true });
(async () => {
  for (const [url, filename] of imageSources) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`Image download failed (${response.status}): ${url}`);
    writeFileSync(join(imageDir, filename), Buffer.from(await response.arrayBuffer()));
  }
  writeFileSync(join(outputRoot, 'README.md'), `# Yuque Notes\n\nConverted from \`Notes.lakebook\` on ${new Date().toISOString().slice(0, 10)}.\n\nDocuments: ${count}\n\nImages: ${imageSources.size}\n`);
  console.log(`Exported ${count} Markdown files and ${imageSources.size} images to ${outputRoot}`);
})().catch((error) => { console.error(error); process.exitCode = 1; });
