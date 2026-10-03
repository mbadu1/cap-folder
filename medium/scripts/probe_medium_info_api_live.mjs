// Bounded single-article adapter for a user-authorized package feasibility test.
// Imports the helper directly to avoid the distribution's automatic demo.
// Usage: node probe_medium_info_api_live.mjs PACKAGE_DIR DEPS_DIR ARTICLE_URL OUTPUT_JSON
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';

const [packageArg, depsArg, articleUrl, outputArg] = process.argv.slice(2);
if (!packageArg || !depsArg || !articleUrl || !outputArg) throw new Error('Required: PACKAGE_DIR DEPS_DIR ARTICLE_URL OUTPUT_JSON');
if (new URL(articleUrl).hostname !== 'medium.com') throw new Error('This probe accepts only a medium.com story URL.');
if (fs.existsSync(outputArg)) throw new Error('Preserve existing evidence; use a new output file.');
const { default: axios } = await import(pathToFileURL(path.resolve(depsArg, 'node_modules/axios/index.js')));
const originalGet = axios.get.bind(axios);
const cache = new Map();
let queue = Promise.resolve();
let lastStart = 0;
let circuitError = null;
let logicalCalls = 0;
const log = [];
const hash = text => createHash('sha256').update(text).digest('hex');
axios.get = (url, options) => {
  logicalCalls += 1;
  if (cache.has(url)) return cache.get(url);
  const request = queue.then(async () => {
    if (circuitError) throw circuitError;
    await new Promise(resolve => setTimeout(resolve, Math.max(0, 3000 - (Date.now() - lastStart))));
    lastStart = Date.now();
    const row = { url, requested_at: new Date().toISOString() };
    try {
      const response = await originalGet(url, {
        ...options,
        headers: { ...options?.headers, 'User-Agent': `${options?.headers?.['User-Agent'] ?? 'axios'} DukeCapstoneMediumPilot/0.2` },
        timeout: 25000,
        maxRedirects: 0,
        responseType: 'text',
        transformResponse: [data => data],
        validateStatus: () => true,
      });
      Object.assign(row, {
        status: response.status, content_type: response.headers['content-type'] ?? null,
        cf_mitigated: response.headers['cf-mitigated'] ?? null,
        bytes: Buffer.byteLength(response.data), response_sha256: hash(response.data),
      });
      log.push(row);
      if (response.status !== 200 || row.cf_mitigated === 'challenge') {
        const error = new Error(`HTTP ${response.status}${row.cf_mitigated ? ': ' + row.cf_mitigated : ''}`);
        error.response = { status: response.status };
        circuitError = error;
        throw error;
      }
      return response;
    } catch (error) {
      if (!log.includes(row)) {
        Object.assign(row, { status: 0, error: error.code ?? error.name });
        log.push(row);
      }
      circuitError ??= error;
      throw error;
    }
  });
  cache.set(url, request);
  queue = request.catch(() => {});
  return request;
};
const oldError = console.error;
console.error = () => {};
const { getArticleInfo } = await import(pathToFileURL(path.resolve(packageArg, 'dist/functionscall/callFunction.js')));
const result = {
  adapter: 'medium-info-api-0.1.1-deduplicated-sequential-pilot-v1',
  permission_basis: 'user_reported_medium_permission_and_explicit_fallback_tool_request',
  article_url: articleUrl,
  status: 'unresolved',
  request_log: log,
  full_text_primary_records: 0,
};
try {
  const article = await getArticleInfo(articleUrl);
  result.status = 'response_parsed_unvalidated';
  result.metadata = Object.fromEntries(Object.entries(article).filter(([key]) => !['pageContent', 'firstLine'].includes(key)));
  result.body_characters = article.pageContent?.length ?? 0;
  result.body_sha256 = article.pageContent ? hash(article.pageContent) : null;
  result.text_status = 'unverified_fullness';
} catch (error) {
  result.status = 'retrieval_failed';
  result.error = error.message;
}
await queue;
console.error = oldError;
result.logical_package_fetches = logicalCalls;
result.actual_network_requests = log.length;
result.skipped_queued_urls_after_circuit = [...cache.keys()].filter(url => !log.some(row => row.url === url));
fs.mkdirSync(path.dirname(path.resolve(outputArg)), { recursive: true });
fs.writeFileSync(outputArg, JSON.stringify(result, null, 2) + '\n');
console.log(JSON.stringify(result));
