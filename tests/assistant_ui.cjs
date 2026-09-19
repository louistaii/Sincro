/* Optional browser regression checks. Requires Node.js and Playwright.
 * Run: node tests/assistant_ui.cjs
 * SINCRO_BROWSER_EXECUTABLE optionally selects an existing Chromium executable.
 * SINCRO_UI_SCREENSHOTS optionally saves desktop/mobile captures to a directory.
 * Every HTTP request is intercepted; no server, Gemini key, or solver is needed.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const html = fs.readFileSync(path.join(__dirname, '../src/sincro/web.html'), 'utf8');
const encoded = Buffer.from('fixture export').toString('base64');
const inputNames = [
  '01_LINES', '02_STATIONS', '03_SECTORS', '04_LOCATION_SUPPLY',
  '05_BUFFER_LOCATION', '06_PARAMETERS', '07_PROJECT_DETAILS', '08_ACTIVITY_DETAILS',
];

function makeResult(scenario = 'A', token = `signed-${scenario}`) {
  return {
    scenario,
    context_token: token,
    report: {
      feasible: true,
      soft_scores: {
        objective_score: 12, overrun_days_total: 2, eclo_nights_total: 0,
        excess_access_nights_total: 0, contracts_overrunning: 1,
        priority_weighted_score: 4, formula_version: 'test',
      },
      hard_violations: [],
      solver: 'fixture',
    },
    activities: [{
      id: 'A001', contract: 'C001', workload: 1, start_date: '2026-10-01',
      priority: 1, type: 'Track', nature: 'Renewal', activity_type: 'Track',
      overrun_days: 2,
      nights: [{ week: 1, date: '2026-10-01', eclo: false, access_seq: 1, access_night: 1 }],
    }],
    contracts: [{
      id: 'C001', priority: 1, description: 'Track renewals', access_type: 'Possession',
      nature: 'Renewal', due_date: '2026-10-01', completion_date: '2026-10-03',
      activities: 1, accesses: 1, overrun_days: 2,
    }],
    download: encoded, calendar: encoded, summary: encoded,
  };
}

function makeData(results = [makeResult('A'), makeResult('B')]) {
  return {
    results, locations: ['L1', 'L2'], horizon_start: '2026-10-01', horizon_weeks: 3,
    activity_count: 1, contract_count: 1, engine: 'fixture',
  };
}

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

async function main() {
  const launchOptions = { headless: true };
  if (process.env.SINCRO_BROWSER_EXECUTABLE) {
    launchOptions.executablePath = process.env.SINCRO_BROWSER_EXECUTABLE;
  }
  const browser = await chromium.launch(launchOptions);
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 960 }, timezoneId: 'Asia/Singapore' });
    const errors = [], requests = [];
    let askResponse = { answer: 'A uses fixed supply.', applied: false };
    let solveResponse = makeData(), askGate = null, solveGate = null;
    let askRawResponse = null, solveRawResponse = null;
    const forbiddenHtml = { status: 403, contentType: 'text/html', body: '<html><body>Forbidden</body></html>' };
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', async route => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === '/') return route.fulfill({ contentType: 'text/html', body: html });
      if (pathname === '/assistant-status') {
        return route.fulfill({ json: { configured: true, model: 'fixture-model' } });
      }
      if (!['/ask', '/solve'].includes(pathname)) return route.fulfill({ status: 404, body: '' });
      requests.push({ path: pathname, payload: route.request().postDataJSON() });
      if (pathname === '/ask') {
        if (askGate) await askGate.promise;
        if (askRawResponse) return route.fulfill(askRawResponse);
        return route.fulfill({ status: askResponse.error ? 400 : 200, json: askResponse });
      }
      if (solveGate) await solveGate.promise;
      if (solveRawResponse) return route.fulfill(solveRawResponse);
      return route.fulfill({ status: solveResponse.error ? 400 : 200, json: solveResponse });
    });
    const idle = () => page.waitForFunction(() => operation === '');
    const ask = async prompt => {
      await page.locator('#ai-prompt').fill(prompt);
      await page.locator('#ai-send').click();
      await idle();
    };
    const snapshot = () => page.evaluate(() => ({
      files: sourceFiles,
      plans: assistantPlans(),
      histories: Object.fromEntries(conversations),
      downloads: [...document.querySelectorAll('#results a[download]')].map(link => link.href),
    }));
    const screenshot = async name => {
      if (!process.env.SINCRO_UI_SCREENSHOTS) return;
      fs.mkdirSync(process.env.SINCRO_UI_SCREENSHOTS, { recursive: true });
      await page.screenshot({ path: path.join(process.env.SINCRO_UI_SCREENSHOTS, name) });
    };

    // Product questions work before solving; provider text must never become HTML.
    await page.goto('http://sincro.test/');
    await page.locator('#ai-fab').click();
    await page.locator('#ai-provider').filter({ hasText: 'fixture-model' }).waitFor();
    assert.equal(await page.locator('#ai-context-links').isVisible(), false);
    // Static preview servers may return HTML/403: explain the correct server,
    // retain the draft, and allow a successful retry without duplicating turns.
    askRawResponse = forbiddenHtml;
    await ask('Explain scenario A.');
    assert.match(await page.locator('#ai-log .error').textContent(), /Python Sincro server.*HTTP 403/);
    assert.equal(await page.locator('#ai-prompt').inputValue(), 'Explain scenario A.');
    assert.equal(await page.locator('#ai-send').isDisabled(), false);
    assert.equal(await page.evaluate(() => conversation('all').length), 0);
    askRawResponse = null;
    await ask('Explain scenario A.');
    assert.equal(await page.locator('#ai-log .user').count(), 1);
    assert.deepEqual(requests.at(-1).payload.history, []);
    assert.deepEqual(requests.at(-1).payload.plans, {});
    assert.equal(requests.at(-1).payload.files, null);
    askResponse = { answer: '<img src=x onerror="window.hacked=true">', applied: false };
    await ask('Is markup displayed safely?');
    assert.equal(await page.evaluate(() => window.hacked), undefined);
    assert.equal(await page.locator('#ai-log img').count(), 0);
    askResponse = { answer: 'x'.repeat(9000), applied: false };
    await ask('Give details.');
    askResponse = { answer: 'Acknowledged', applied: false };
    await ask('Continue.');
    assert.equal(requests.at(-1).payload.history.at(-1).content.length, 8000);

    // A successful solve starts new histories tied to the exact uploaded files.
    await page.locator('#files').setInputFiles(inputNames.map(name => ({
      name: `${name}.csv`, mimeType: 'text/csv', buffer: Buffer.from(`original-${name}`),
    })));
    await page.locator('#run').click();
    await idle();
    assert.equal(await page.locator('.result').count(), 2);
    const firstWeek = page.locator('#result-A [data-pane="calendar"] .week-card').first();
    assert.equal(await firstWeek.locator('.week-head div span').textContent(), '01 Oct 2026 – 07 Oct 2026');
    assert.equal(await page.locator('#ai-log .user').count(), 0);
    await page.locator('#ai-scenario').selectOption('A');
    await page.locator('#example').click();
    assert.match(await page.locator('#ai-context').textContent(), /file selection has changed/);

    // Changes use the displayed snapshot even after selecting different inputs.
    askGate = deferred();
    askResponse = {
      answer: 'Updated A001 to two accesses.', applied: true, scenario: 'A',
      changes: [{ kind: 'edit_activity', activity_id: 'A001', total_accesses: 2 }],
      data: makeData([makeResult('A', 'signed-A-chat')]),
      links: [
        { label: 'Check validation', scenario: 'A', pane: 'validation' },
        { label: 'Unsafe', scenario: 'A', pane: 'x" onclick="bad' },
      ],
    };
    await page.locator('#ai-prompt').fill('Set A001 to two accesses.');
    await page.locator('#ai-send').click();
    await page.waitForFunction(() => operation === 'chat');
    assert.equal(await page.locator('#run').isDisabled(), true);
    assert.equal(await page.locator('#files').isDisabled(), true);
    assert.equal(await page.locator('#ai-scenario').isDisabled(), true);
    const requestCount = requests.length;
    await page.evaluate(() => run());
    assert.equal(requests.length, requestCount);
    const chatRequest = requests.at(-1).payload;
    assert.equal(chatRequest.files['01_LINES.csv'], 'original-01_LINES');
    assert.equal(chatRequest.plans.A.result.context_token, 'signed-A');
    for (const field of ['download', 'calendar', 'summary']) {
      assert.ok(!(field in chatRequest.plans.A.result));
    }
    assert.deepEqual(chatRequest.history, []);
    askGate.resolve();
    askGate = null;
    await idle();
    assert.equal(await page.evaluate(() => resultStates.get('A').result.context_token), 'signed-A-chat');
    assert.equal(await page.evaluate(() => changeLogs.get('A').length), 1);
    assert.equal(await page.locator('#ai-log button').filter({ hasText: 'Unsafe' }).count(), 0);
    await page.getByRole('button', { name: 'Check validation', exact: true }).click();
    assert.equal(await page.locator('#result-A [data-pane="validation"]').isVisible(), true);

    // A failed answer keeps its draft and workspace; retry appends one user turn.
    askResponse = { error: 'Provider temporarily unavailable.' };
    await ask('Retry this question.');
    assert.equal(await page.locator('#ai-prompt').inputValue(), 'Retry this question.');
    assert.equal(await page.evaluate(() => conversation('A').length), 2);
    assert.equal(await page.evaluate(() => resultStates.get('A').result.context_token), 'signed-A-chat');
    askResponse = { answer: 'Recovered.', applied: false };
    await ask('Retry this question.');
    assert.equal(await page.locator('#ai-log .user').filter({ hasText: 'Retry this question.' }).count(), 1);
    assert.equal(requests.at(-1).payload.history.length, 2);

    // Invalid, wrong-scenario, and all-scenario mutation responses cannot apply.
    const beforeRejectedChanges = await snapshot();
    askResponse = {
      answer: 'Changed.', applied: true, scenario: 'A', changes: [],
      data: makeData([{ ...makeResult('A', 'bad-token'), calendar: 'invalid base64?!' }]),
    };
    await ask('Try another change.');
    assert.deepEqual(await snapshot(), beforeRejectedChanges);
    askResponse = {
      answer: 'Changed B.', applied: true, scenario: 'B', changes: [],
      data: makeData([makeResult('B', 'wrong-scope-token')]),
    };
    await ask('Change the selected scenario A.');
    assert.deepEqual(await snapshot(), beforeRejectedChanges);
    assert.equal(await page.locator('#ai-status.error').count(), 1);
    await page.locator('#ai-scenario').selectOption('all');
    askResponse = {
      answer: 'Changed A.', applied: true, scenario: 'A', changes: [],
      data: makeData([makeResult('A', 'ambiguous-scope-token')]),
    };
    await ask('Compare A and B.');
    assert.deepEqual(await snapshot(), beforeRejectedChanges);
    await page.locator('#ai-scenario').selectOption('A');

    // Manual changes and their cumulative log are visible to subsequent chat.
    await page.locator('#ai-cancel').click();
    await page.locator('#result-A [data-plan-change]').click();
    await page.locator('[data-change-tab="edit"]').click();
    await page.locator('#edit-days').fill('3');
    solveResponse = makeData([makeResult('A', 'signed-A-manual')]);
    const beforeManualServerError = await snapshot();
    solveRawResponse = forbiddenHtml;
    await page.locator('#change-apply').click();
    await idle();
    assert.deepEqual(await snapshot(), beforeManualServerError);
    assert.match(await page.locator('#change-status').textContent(), /Python Sincro server.*HTTP 403/);
    assert.equal(await page.locator('#change-dialog').isVisible(), true);
    assert.equal(await page.locator('#change-apply').isDisabled(), false);
    solveRawResponse = null;
    await page.locator('#change-apply').click();
    await idle();
    await page.locator('#result-A [data-chat-scenario]').click();
    askResponse = { answer: 'Manual revision is current.', applied: false };
    await ask('Summarise this revised plan.');
    assert.equal(requests.at(-1).payload.plans.A.result.context_token, 'signed-A-manual');
    assert.equal(requests.at(-1).payload.plans.A.changes.length, 2);
    await page.locator('#ai-scenario').selectOption('B');
    await ask('What about B?');
    assert.deepEqual(requests.at(-1).payload.history, []);
    for (let i = 0; i < 12; i++) await ask(`Follow-up ${i}`);
    assert.equal(requests.at(-1).payload.history.length, 20);
    assert.equal(await page.evaluate(() => conversation('B').length), 20);

    // Pending and failed fresh solves preserve every current plan and export.
    const beforeFailedSolve = await snapshot();
    solveResponse = { error: 'The replacement input set is invalid.' };
    solveGate = deferred();
    await page.locator('#ai-cancel').click();
    await page.locator('#run').click();
    await page.waitForFunction(() => operation === 'solve');
    assert.deepEqual(await snapshot(), beforeFailedSolve);
    assert.equal(await page.locator('#ai-send').isDisabled(), true);
    solveGate.resolve();
    solveGate = null;
    await idle();
    assert.deepEqual(await snapshot(), beforeFailedSolve);
    assert.match(await page.locator('#status').textContent(), /replacement input set is invalid/);
    solveRawResponse = forbiddenHtml;
    await page.locator('#run').click();
    await idle();
    assert.deepEqual(await snapshot(), beforeFailedSolve);
    assert.match(await page.locator('#status').textContent(), /Python Sincro server.*HTTP 403/);
    assert.equal(await page.locator('#run').isDisabled(), false);
    solveRawResponse = null;
    await page.locator('#ai-fab').click();

    // The responsive panel keeps the latest reply and composer within reach.
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForFunction(() => {
      const log = document.getElementById('ai-log');
      return log.scrollHeight - log.scrollTop - log.clientHeight < 2;
    });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await screenshot('sincro-ui-mobile.png');
    await page.setViewportSize({ width: 1280, height: 960 });
    await screenshot('sincro-ui-desktop.png');

    // A later successful solve resets histories and includes signed failures.
    const failedScenario = {
      scenario: 'C', error: 'No feasible schedule meets the capacity constraints.',
      context_token: 'signed-failed-C',
    };
    solveResponse = makeData([makeResult('A', 'signed-A-fresh'), failedScenario]);
    await page.locator('#ai-cancel').click();
    await page.locator('#run').click();
    await idle();
    assert.equal(await page.evaluate(() => conversations.size), 0);
    assert.equal(await page.evaluate(() => sourceFiles), null);
    assert.match(await page.locator('#result-C').textContent(), /No feasible schedule/);
    await page.locator('#ai-fab').click();
    askResponse = { answer: 'Scenario C failed the capacity constraints.', applied: false };
    await ask('Compare the results and explain why C failed.');
    assert.deepEqual(requests.at(-1).payload.plans.C, { result: failedScenario, changes: [] });
    assert.deepEqual(requests.at(-1).payload.history, []);
    await page.locator('#ai-scenario').selectOption('C');
    await ask('What stopped this scenario from being scheduled?');
    assert.equal(requests.at(-1).payload.scenario, 'C');
    assert.equal(requests.at(-1).payload.plans.C.result.context_token, 'signed-failed-C');

    assert.deepEqual(errors, []);
    console.log('PASS: assistant context, history, safe rendering, updates, retries, request locks,');
    console.log('      manual changes, server errors/recovery, failed solves, scenario guards, and mobile layout.');
  } finally {
    await browser.close();
  }
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
