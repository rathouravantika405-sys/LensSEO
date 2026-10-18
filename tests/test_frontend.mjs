// Pure Node smoke test for the downloadable CSV format (no browser required).
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const appJS=readFileSync(resolve(root,'app/static/app.js'),'utf8');
const ctx=vm.createContext({document:{addEventListener:()=>{}},console});
vm.runInContext(appJS,ctx);
vm.runInContext(`
report={report_rows:[
  {'URL':'https://example.org/a','Issue':'Missing H1','SEO/AEO/GEO':'SEO','Priority':'High','Recommended Fix':'Add H1'},
  {'URL':'https://example.org/b','Issue':'=HYPERLINK("http://bad.test")','SEO/AEO/GEO':'AEO','Priority':'Medium','Recommended Fix':'Update text'}
]};
download=(text,name,mime)=>{globalThis.exported={text,name,mime}};
downloadCSV();`,ctx);
const content=ctx.exported.text.replace(/^\uFEFF/,'');
const lines=content.split('\r\n');
assert.equal(lines.length,3);
assert.equal(lines[0],'URL,Issue,SEO/AEO/GEO,Priority,Recommended Fix');
assert(lines[2].includes("'=HYPERLINK"),'Spreadsheet formula must be escaped');
assert.equal(ctx.exported.name,'LensSEO_5_Column_Audit_Report.csv');
console.log('Frontend export passed: exactly five columns, exact headings, formula-safe cells.');
