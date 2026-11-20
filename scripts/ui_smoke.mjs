#!/usr/bin/env node
/**
 * Headless UI smoke test.
 *
 * Loads the real interface against a running RAY server, boots it in jsdom and
 * visits every section, failing if any view throws or renders empty.
 *
 *   npm install jsdom            (only needed for this test)
 *   RAY_URL=http://127.0.0.1:8899 node scripts/ui_smoke.mjs
 */
import { JSDOM, VirtualConsole } from 'jsdom';

const base = process.env.RAY_URL || 'http://127.0.0.1:8899';
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const errors = [];
const virtualConsole = new VirtualConsole();
virtualConsole.on('jsdomError', (error) => errors.push(`jsdomError: ${error.message}`));
virtualConsole.on('error', (...args) => errors.push(`console.error: ${args.join(' ')}`));

const html = await (await fetch(base)).text();
const dom = new JSDOM(html, {
  url: base,
  runScripts: 'dangerously',
  resources: 'usable',
  pretendToBeVisual: true,
  virtualConsole,
  beforeParse(window) {
    window.fetch = (input, init) => fetch(new URL(input, base).toString(), init);
    window.HTMLCanvasElement.prototype.getContext = () => new Proxy({}, {
      get: () => () => ({ addColorStop() {} }),
    });
    window.addEventListener('error', (event) => errors.push(`window error: ${event.message}`));
    window.onunhandledrejection = (event) => errors.push(`unhandled rejection: ${event.reason}`);
    window.HTMLMediaElement.prototype.play = () => Promise.resolve();
    window.HTMLMediaElement.prototype.pause = () => {};
  },
});

const { window } = dom;

// wait for app.js to load and boot the interface
for (let i = 0; i < 80; i += 1) {
  if (window.document.querySelector('#nav')?.children.length > 0) break;
  await sleep(100);
}
if (typeof window.go !== 'function') {
  console.log('FATAL: app.js did not load');
  console.log(errors.slice(0, 5).join('\n'));
  process.exit(1);
}
await sleep(1500);

const sections = [
  'home', 'assistant', 'voice', 'computer', 'files', 'browser', 'productivity', 'calendar',
  'notes', 'tasks', 'reminders', 'automation', 'agents', 'knowledge', 'media', 'system',
  'security', 'analytics', 'downloads', 'developer', 'settings',
];

let failures = 0;
for (const section of sections) {
  errors.length = 0;
  try {
    await window.eval(`go('${section}')`);
    await sleep(700);
    const content = window.document.querySelector('#view-inner')?.innerHTML || '';
    const text = (window.document.querySelector('#view-inner')?.textContent || '').trim();
    const ok = content.length > 400 && text.length > 20 && !text.includes('Starting RAY');
    if (!ok) {
      failures += 1;
      console.log(`FAIL ${section.padEnd(12)} rendered ${content.length} bytes, ${text.length} chars of text`);
    } else if (errors.length) {
      failures += 1;
      console.log(`FAIL ${section.padEnd(12)} errors: ${errors.slice(0, 3).join(' | ')}`);
    } else {
      console.log(`ok   ${section.padEnd(12)} ${content.length} bytes`);
    }
  } catch (error) {
    failures += 1;
    console.log(`FAIL ${section.padEnd(12)} ${error.message}`);
  }
}

// interactions the user actually performs
errors.length = 0;
try {
  await window.eval("go('tasks')");
  await sleep(400);
  window.document.querySelector('#task-title').value = 'Smoke test task';
  window.document.querySelector('#task-add').click();
  await sleep(600);
  const count = (await (await fetch(`${base}/api/tasks`)).json()).data.stats.total;
  console.log(count > 0 ? `ok   task creation via UI (${count} task)` : 'FAIL task creation via UI');
  if (!count) failures += 1;
} catch (error) {
  failures += 1;
  console.log(`FAIL task creation: ${error.message}`);
}

try {
  await window.eval("go('settings')");
  await sleep(600);
  const hasCredentials = window.document.querySelector('#cred-openrouter') !== null;
  const hasEngines = window.document.querySelector('#engine-list [data-engine]') !== null;
  const ok = hasCredentials && hasEngines;
  console.log(ok ? 'ok   settings credential + engine inputs'
    : `FAIL settings inputs (credentials=${hasCredentials} engines=${hasEngines})`);
  if (!ok) failures += 1;
} catch (error) {
  failures += 1;
  console.log(`FAIL settings: ${error.message}`);
}

try {
  await window.eval("go('home')");
  await sleep(400);
  await window.eval("runCommand('check my laptop performance')");
  await sleep(1500);
  const modal = window.document.querySelector('#modal-card')?.textContent || '';
  const worked = modal.includes('RAY system report') || modal.includes('Done');
  console.log(worked ? 'ok   command palette executes real system report' : `FAIL command palette: ${modal.slice(0, 120)}`);
  if (!worked) failures += 1;
} catch (error) {
  failures += 1;
  console.log(`FAIL command palette: ${error.message}`);
}

window.close();
console.log(failures === 0 ? '\nUI smoke test passed.' : `\nUI smoke test failed: ${failures} problem(s).`);
process.exit(failures === 0 ? 0 : 1);
