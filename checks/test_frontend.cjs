const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const html = fs.readFileSync(path.join(__dirname, '../src/sincro/web.html'), 'utf8');
const helper = html.slice(html.indexOf('async function requestSchedule('), html.indexOf('async function run('));
async function call(status, body) {
  const context = vm.createContext({fetch: async () => ({status, ok: status < 400, text: async () => body})});
  vm.runInContext(helper, context);
  return context.requestSchedule({scenario: 'A'});
}
(async () => {
  assert.equal((await call(200, '{"results":[]}')).results.length, 0);
  await assert.rejects(call(503, 'Service Unavailable'), /temporarily unavailable.*503/);
  await assert.rejects(call(504, '<html>Gateway timeout</html>'), /hosting request timed out/);
  await assert.rejects(call(500, ''), /unexpected response.*500/);
  await assert.rejects(call(503, '{"error":"Server is busy"}'), /Server is busy/);
  await assert.rejects(call(200, '{}'), /incomplete response/);
  await assert.rejects(call(200, 'null'), /incomplete response/);
  assert(!html.includes('await response.json()'), 'Both request paths must use defensive parsing');
  console.log('8 frontend response checks passed');
})().catch(error => {console.error(error); process.exitCode = 1});
