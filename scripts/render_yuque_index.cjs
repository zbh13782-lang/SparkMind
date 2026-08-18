const { execFileSync } = require('node:child_process');
const { writeFileSync } = require('node:fs');

const [archive, output] = process.argv.slice(2);
if (!archive || !output) throw new Error('Usage: node render_yuque_index.cjs <archive> <output>');

const entries = execFileSync('tar', ['-tf', archive], { encoding: 'utf8' }).trim().split('\n');
const metaEntry = entries.find((entry) => entry.endsWith('/$meta.json'));
if (!metaEntry) throw new Error('Missing lakebook metadata');

const rawMeta = execFileSync('tar', ['-xOf', archive, metaEntry], { encoding: 'utf8' });
const meta = JSON.parse(rawMeta);
const book = JSON.parse(meta.meta).book;
const records = [];
let current = null;

for (const line of book.tocYml.split('\n')) {
  const match = line.match(/^(?:-\s+|\s{2})([a-z_]+):\s*(.*)$/);
  if (!match) continue;
  const [, key, value] = match;
  if (key === 'type') {
    if (current) records.push(current);
    current = { type: value };
  } else if (current) {
    current[key] = value.replace(/^'|'$/g, '');
  }
}
if (current) records.push(current);

const docs = records.filter((record) => record.type === 'DOC');
const lines = [
  '# Yuque Notes',
  '',
  `Source: \`Notes.lakebook\` (${docs.length} documents)`,
  '',
  'This folder preserves the original Yuque Lakebook export. Open the `.lakebook` file with Yuque to browse or restore the full document content.',
  '',
  '## Table of Contents',
  '',
];

for (const record of records) {
  const level = Number(record.level || 0);
  if (record.type === 'TITLE') {
    lines.push(`${'  '.repeat(level)}- **${record.title}**`);
  } else if (record.type === 'DOC') {
    lines.push(`${'  '.repeat(level)}- ${record.title}`);
  }
}

lines.push('', '## Archive Details', '', `- Book: ${book.path}`, `- Documents: ${docs.length}`, `- Exported: ${book.tocYml.match(/last_updated_at:\s*(.+)/)?.[1] || 'unknown'}`, '');
writeFileSync(output, lines.join('\n'));
