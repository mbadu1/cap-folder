// Inspect the published package with synthetic HTML and a fully mocked transport.
// Usage: node review_medium_info_api_offline.mjs PACKAGE_DIR DEPS_DIR OUTPUT_JSON
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import http from 'node:http';
import https from 'node:https';

const [packageArg, dependenciesArg, outputArg] = process.argv.slice(2);
if (!packageArg || !dependenciesArg || !outputArg) {
  throw new Error('Required: PACKAGE_DIR DEPS_DIR OUTPUT_JSON');
}
const packageDir = path.resolve(packageArg);
const dependenciesDir = path.resolve(dependenciesArg);
const blockedNetwork = () => { throw new Error('Network is disabled in this offline audit'); };
http.request = https.request = http.get = https.get = blockedNetwork;
globalThis.fetch = blockedNetwork;

const { default: axios } = await import(pathToFileURL(path.join(dependenciesDir, 'node_modules/axios/index.js')));
let requests = [];
let active = 0;
let peak = 0;
let mode = 'public';
const publishedAt = '2020-02-01T00:30:00Z';
const publicHtml = `<!doctype html><html><head>
<meta property="og:title" content="Fixture title">
<meta name="author" content="Fixture author">
<meta property="article:published_time" content="${publishedAt}">
<meta property="og:image" content="https://example.invalid/hero.png">
</head><body><article><h1>Fixture title</h1><span>Fixture author</span>
<span>6 min read</span><p>First paragraph.</p><p>Second paragraph.</p>
<img data-testid="authorAvatar" src="https://example.invalid/avatar.png">
</article></body></html>`;
const previewHtml = publicHtml.replace(
  '<p>First paragraph.</p><p>Second paragraph.</p>',
  '<p>Member-only story</p><p>' + 'Preview words '.repeat(150) + '</p><p>Sign up to read more</p>',
);

axios.get = async (url, config) => {
  requests.push({ url, user_agent: config?.headers?.['User-Agent'] ?? null });
  active += 1;
  peak = Math.max(peak, active);
  await new Promise(resolve => setImmediate(resolve));
  active -= 1;
  const isJson = url.endsWith('?format=json');
  if (mode === 'all403' || (mode === 'json403' && isJson)) {
    const error = new Error('Synthetic HTTP 403 challenge');
    error.response = { status: 403, headers: { 'cf-mitigated': 'challenge' } };
    throw error;
  }
  return { data: isJson
    ? JSON.stringify({ payload: { value: { virtuals: { totalClapCount: 12, responsesCreatedCount: 3 } } } })
    : mode === 'preview' ? previewHtml : publicHtml };
};

const originalLog = console.log;
const originalError = console.error;
const packageMessages = [];
console.log = (...args) => packageMessages.push({ level: 'log', text: args.join(' ') });
console.error = (...args) => packageMessages.push({ level: 'error', text: args.map(String).join(' ') });
const { getArticleInfo } = await import(pathToFileURL(path.join(packageDir, 'dist/index.js')));
await new Promise(resolve => setImmediate(resolve));
await new Promise(resolve => setImmediate(resolve));
const importCalls = [...requests];
const importPeak = peak;
assert.equal(importCalls.length, 9);

function reset(nextMode) {
  assert.equal(active, 0);
  requests = [];
  peak = 0;
  mode = nextMode;
}
const articleUrl = 'https://example.invalid/@fixture/story-0123456789ab';
reset('public');
const publicResult = await getArticleInfo(articleUrl);
assert.equal(requests.length, 9);
assert.equal(peak, 9);
assert.equal(requests.filter(x => x.url === articleUrl).length, 7);
assert.equal(requests.filter(x => x.url.endsWith('?format=json')).length, 2);
assert(publicResult.pageContent.includes('6 min read'));
assert(publicResult.pageContent.includes('First paragraph.Second paragraph.'));
const successfulCase = {
  requests: requests.length, peak_concurrent: peak,
  html_requests: 7, duplicate_json_requests: 2,
  page_content: publicResult.pageContent,
  output_keys: Object.keys(publicResult),
};

reset('preview');
const previewResult = await getArticleInfo(articleUrl);
assert(previewResult.pageContent.includes('Sign up to read more'));
assert(previewResult.pageContent.includes('Member-only story'));
const previewCase = {
  resolved_successfully: true,
  returned_preview_as_page_content: true,
  retained_paywall_prompt: true,
  completeness_or_paywall_flag_present: Object.keys(previewResult).some(x => /complete|paywall|locked|status/i.test(x)),
};
assert.equal(previewCase.completeness_or_paywall_flag_present, false);

reset('json403');
const missingMetrics = await getArticleInfo(articleUrl);
assert.equal(missingMetrics.clapCount, null);
assert.equal(missingMetrics.commentsCount, null);
const metricsCase = {
  resolved_successfully: true, clap_count: missingMetrics.clapCount,
  comments_count: missingMetrics.commentsCount,
  error_or_status_field_present: Object.keys(missingMetrics).some(x => /error|status/i.test(x)),
};

reset('all403');
await assert.rejects(getArticleInfo(articleUrl), /Synthetic HTTP 403/);
await new Promise(resolve => setImmediate(resolve));
assert.equal(requests.length, 9);
const rejectedCase = { rejected: true, requests_started: requests.length, peak_concurrent: peak };

reset('public');
const previousTimezone = process.env.TZ;
process.env.TZ = 'America/New_York';
const { getPublishedDate } = await import(pathToFileURL(path.join(packageDir, 'dist/functions/getPublishedDate.js')));
const dateResult = await getPublishedDate(articleUrl);
if (previousTimezone === undefined) delete process.env.TZ;
else process.env.TZ = previousTimezone;
assert.equal(dateResult, 'Jan 31, 2020');

console.log = originalLog;
console.error = originalError;
const result = {
  package_version: JSON.parse(fs.readFileSync(path.join(packageDir, 'package.json'))).version,
  node_version: process.version,
  dependency_versions: Object.fromEntries(['axios', 'cheerio'].map(name => [name,
    JSON.parse(fs.readFileSync(path.join(dependenciesDir, 'node_modules', name, 'package.json'))).version])),
  network_requests_made: 0,
  transport: 'axios.get replaced with synthetic fixtures; HTTP/HTTPS/fetch disabled',
  import_side_effect: { requests_started: importCalls.length, peak_concurrent: importPeak, urls: [...new Set(importCalls.map(x => x.url))] },
  public_fixture: successfulCase,
  preview_fixture: previewCase,
  metrics_challenge_fixture: metricsCase,
  all_challenged_fixture: rejectedCase,
  timestamp_fixture: { source: publishedAt, runtime_timezone: 'America/New_York', returned: dateResult, crosses_calendar_month: true },
  limitation: 'Synthetic transport/parser tests establish implementation behavior, not current Medium retrieval success or representative extraction accuracy.',
};
fs.mkdirSync(path.dirname(path.resolve(outputArg)), { recursive: true });
fs.writeFileSync(outputArg, JSON.stringify(result, null, 2) + '\n');
console.log(JSON.stringify({ package_version: result.package_version, assertions_passed: true, network_requests_made: 0, output: outputArg }));
