#!/usr/bin/env python3
"""One unauthenticated, read-only public profile query; no automatic retry."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import requests

QUERY = '''query ProfileHistoryProbe($username: ID!, $from: String) {
  userResult(username: $username) {
    __typename
    ... on User {
      id name username createdAt
      homepagePostsConnection(paging: {limit: 5, from: $from}, includeDistributedResponses: false) {
        posts {
          id title mediumUrl firstPublishedAt latestPublishedAt isLocked isPublished isShortform
          creator { id name username }
          collection { id name slug }
        }
        pagingInfo { next { from limit } }
      }
    }
  }
}'''


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--username', default='mrityunjaytiwari1873')
    p.add_argument('--cursor')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        p.error('Preserve prior evidence; choose a fresh output path')
    body = dict(operationName='ProfileHistoryProbe', variables=dict(username=args.username, **{'from':args.cursor}), query=QUERY)
    result = dict(url='https://medium.com/_/graphql', method='POST', read_only_query=True,
                  requested_at=datetime.now(timezone.utc).isoformat(), request_body=body,
                  source_reference='https://github.com/yakkomajuri/medium-to-blog/blob/main/index.js')
    try:
        response = requests.post(result['url'], json=body, headers={
            'User-Agent':'DukeCapstoneMediumPilot/0.4 (user-reported research permission; public read-only query)',
            'Accept':'application/json', 'Origin':'https://medium.com'}, timeout=30, allow_redirects=False)
        result.update(status=response.status_code, bytes=len(response.content), response_sha256=hashlib.sha256(response.content).hexdigest(),
                      cf_mitigated=response.headers.get('cf-mitigated'), retry_after=response.headers.get('retry-after'))
        if 'json' in response.headers.get('content-type',''):
            result['response'] = response.json()
    except requests.RequestException as error:
        result.update(status=0, error_type=type(error).__name__)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))
