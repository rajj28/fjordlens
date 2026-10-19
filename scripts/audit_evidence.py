"""Independently re-open saved bytes and check published selectors and amounts."""
import argparse
from decimal import Decimal
import gzip
import html as stdlib_html
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fjordlens.core import at, digest, validate, write_json
from fjordlens.net import read_snapshot_bytes
from fjordlens.html import parse_html, clean
from fjordlens.identity import node_org
from fjordlens.net import Response

def snapshot_text(raw, snapshot):
    """The page text decoded exactly as the agent decoded it (same charset rules)."""
    response = Response(snapshot['final_url'], snapshot['final_url'], body=raw,
                        headers={'content-type':snapshot.get('content_type') or 'text/html'})
    return response.text()

def parse_snapshot(raw, snapshot):
    return parse_html(snapshot_text(raw, snapshot), snapshot['final_url'])

def audit(folder):
    folder = Path(folder)
    profile_path = folder/'envelopes.jsonl'
    content = profile_path.read_text(encoding='utf-8') if profile_path.exists() else gzip.decompress((folder/'envelopes.jsonl.gz').read_bytes()).decode('utf-8')
    profiles = [json.loads(line) for line in content.splitlines()]
    problems, count, supported, amounts = [], 0, 0, 0
    bodies, pages = {}, {}
    missing = object()
    for profile in profiles:
        problems.extend({'org': profile['organisation_number'], 'error': err} for err in validate(profile))
        evidence = {e['id']: e for e in profile['evidence']}
        snapshots = {s['id']: s for s in profile['source_snapshots']}
        for claim in profile['claims']:
            count += 1
            passed = False
            for eid in claim['evidence_ids']:
                e = evidence[eid]
                s = snapshots[e['snapshot_id']]
                path = folder / (s['storage_path'] or 'MISSING')
                try:
                    if claim['field'] == 'company_publication':
                        from fjordlens import articles
                        if articles.nonpublication_url(claim['value'].get('url', '')):
                            raise ValueError('Utility page is not a dated publication')
                    if e['content_sha256'] not in bodies:
                        raw = read_snapshot_bytes(path)
                        if digest(raw) != e['content_sha256']:
                            raise ValueError('Content hash mismatch')
                        bodies[e['content_sha256']] = raw
                    raw = bodies[e['content_sha256']]
                    selector = e['selector']
                    if selector['type'] == 'json_pointer':
                        source = json.loads(raw, parse_float=str)
                        value = at(source, selector['value'], missing)
                        if value is missing:
                            raise ValueError('JSON pointer does not resolve')
                        if claim['family'] == 'financials':
                            if Decimal(str(value)) != Decimal(claim['value']['amount']):
                                raise ValueError('Financial value differs from raw source')
                            index = int(selector['value'].split('/')[1])
                            record = source[index]
                            if record['virksomhet']['organisasjonsnummer'] != profile['organisation_number']:
                                raise ValueError('Wrong financial entity')
                            if record['valuta'] != claim['value']['currency'] or record['regnskapsperiode'] != claim['reporting_period'] or record['regnskapstype'] != claim['value']['account_type']:
                                raise ValueError('Financial currency, period or scope mismatch')
                            amounts += 1
                        elif claim['field'] == 'company_publication':
                            from fjordlens import articles
                            from fjordlens.website import catalog_url, host, valid_date, WORDPRESS_PLACEHOLDERS
                            if (not isinstance(value, dict)
                                    or not urlsplit(s['final_url']).path.rstrip('/').endswith('/wp-json/wp/v2/posts')):
                                raise ValueError('Unsupported JSON publication source')
                            title = value.get('title')
                            title = stdlib_html.unescape(clean(re.sub(r'<[^>]+>', ' ', str(title.get('rendered') or '') if isinstance(title, dict) else '')))
                            date, target = valid_date(value.get('date')), value.get('link')
                            if (not isinstance(target, str) or not title or title.lower() in WORDPRESS_PLACEHOLDERS
                                    or not date or date > e['retrieved_at'][:10] or catalog_url(target)
                                    or articles.nonpublication_url(target)
                                    or host(target) != host(e.get('identity_proof', {}).get('url', ''))):
                                raise ValueError('WordPress publication does not pass source binding')
                            expected = {'title': title[:300], 'url': target, 'published_on': date,
                                        'published_at': articles.publication_timestamp(value.get('date'))}
                            if any(claim['value'].get(key) != item for key, item in expected.items()):
                                raise ValueError('WordPress publication title, URL or date differs from saved source')
                            if e['claim_span'] not in snapshot_text(raw, s):
                                raise ValueError('WordPress publication quote not in saved source')
                        passed = True
                    elif selector['type'] == 'parsed_html_pointer':
                        if s['id'] not in pages:
                            pages[s['id']] = parse_snapshot(raw,s)
                        value = at(pages[s['id']], selector['value'], missing)
                        if value is missing:
                            raise ValueError('Parsed HTML selector does not resolve')
                        if e['extraction_method'] == 'jsonld_sameas_v1':
                            from fjordlens import gate
                            from fjordlens.website import own_organisation_node, social_profile_url
                            match = re.fullmatch(r'/jsonld(.+)/sameAs(?:/[0-9]+)?', selector['value'])
                            node_path = match.group(1) if match else ''
                            owner = at(pages[s['id']], '/jsonld' + node_path, missing)
                            proof = e.get('identity_proof') or {}
                            if (not gate.OWNER_NODE_PATH.fullmatch(node_path) or not isinstance(owner, dict)
                                    or not own_organisation_node(pages[s['id']], owner, node_path,
                                                                 profile['organisation_number'],
                                                                 (profile.get('legal_identity') or {}).get('name', ''))
                                    or proof.get('organisation_number') != profile['organisation_number']
                                    or not proof.get('publishable')
                                    or gate.host(s['final_url']) != gate.host(proof.get('url', ''))):
                                raise ValueError('Social sameAs is not declared by the verified company organisation')
                            target = social_profile_url(value) if isinstance(value, str) else None
                            expected = {'platform': target[0], 'url': target[2], 'url_as_published': value,
                                        'relationship': 'declared_in_site_organization_markup',
                                        'destination_verified': False} if target else None
                            if (claim['field'] != 'company_linked_profile' or not expected
                                    or any(claim['value'].get(key) != item for key, item in expected.items())
                                    or claim['value'].get('destination_verified') is not False):
                                raise ValueError('Social profile or relationship differs from its saved sameAs declaration')
                            if e['claim_span'] not in snapshot_text(raw, s):
                                raise ValueError('Social sameAs quote not in saved source')
                        elif e['extraction_method'] in ('legal_identity_gate_v1', 'identity_gate_v2'):
                            proof = e['identity_proof']
                            if proof['organisation_number'] != profile['organisation_number'] or not proof.get('publishable'):
                                raise ValueError('Missing exact-company proof')
                            if selector != proof.get('proof_selector') or node_org({'taxID': value}) != profile['organisation_number'] or str(value) != e['claim_span']:
                                raise ValueError('Structured legal identity evidence does not match its identifier')
                        elif claim['field'] == 'company_publication':
                            from fjordlens.website import jsonld_publication
                            if e['extraction_method'] != 'jsonld_article_v1' or not isinstance(value, dict):
                                raise ValueError('Unsupported structured publication source')
                            expected = jsonld_publication(value, pages[s['id']], e['retrieved_at'][:10],
                                                          e.get('identity_proof', {}).get('url', ''))
                            if not expected or any(claim['value'].get(key) != expected.get(key)
                                                   for key in ('title', 'url', 'published_on', 'published_at')):
                                raise ValueError('Structured publication title, URL or date differs from saved source')
                            if e['claim_span'] not in snapshot_text(raw, s):
                                raise ValueError('Structured publication quote not in saved source')
                        passed = True
                    elif e['extraction_method'] in ('legal_identity_gate_v1', 'identity_gate_v2'):
                        if s['id'] not in pages:
                            pages[s['id']] = parse_snapshot(raw,s)
                        proof = e['identity_proof']
                        if proof['organisation_number'] != profile['organisation_number'] or not proof.get('publishable'):
                            raise ValueError('Missing exact-company proof')
                        # JSON-LD identity spans can occur in scripts rather than visible text.
                        if e['claim_span'] not in pages[s['id']]['text'] and e['claim_span'] not in raw.decode('utf-8','replace'):
                            if proof.get('method') != 'registry_name_address_phone_v1':
                                raise ValueError('Legal identity span not in saved source')
                        passed = True
                    elif selector['type'] == 'independent_news_article':
                        from fjordlens import news
                        identity_snapshot = snapshots.get(profile.get('legal_identity', {}).get('source_snapshot_id'))
                        if not identity_snapshot or not identity_snapshot.get('storage_path'):
                            raise ValueError('Independent news requires saved official identity evidence')
                        identity_raw = read_snapshot_bytes(folder / identity_snapshot['storage_path'])
                        if digest(identity_raw) != identity_snapshot['content_sha256']:
                            raise ValueError('Official identity snapshot hash mismatch')
                        entity = json.loads(identity_raw)
                        if (entity.get('organisasjonsnummer') != profile['organisation_number']
                                or urlsplit(identity_snapshot['final_url']).hostname != 'data.brreg.no'):
                            raise ValueError('Independent news identity is not the exact official entity')
                        if selector.get('entity') != news.entity_context(entity):
                            raise ValueError('Article identity context differs from the registry snapshot')
                        domains = news.company_domains(profile, entity)
                        if not set(selector.get('company_domains', [])).issubset(domains):
                            raise ValueError('Article domain corroboration is not supported by company evidence')
                        found = news.extract_article(snapshot_text(raw, s), s['final_url'], entity,
                                                     e['retrieved_at'][:10], company_domains=selector.get('company_domains', []))
                        if (not found or found['value'] != claim['value']
                                or found['spans'].get(selector.get('part')) != e['claim_span']
                                or e['claim_span'] not in snapshot_text(raw, s)):
                            raise ValueError('Independent news title, date or entity proof cannot be re-derived')
                        passed = True
                    elif selector['type'] in ('dated_link', 'article_date'):
                        # Re-derive the publication from the saved page with the same extractor: same URL, date, quote.
                        from fjordlens import articles
                        text = snapshot_text(raw, s)
                        day = e['retrieved_at'][:10]
                        if selector['type'] == 'dated_link':
                            found = next((item for item in articles.extract_dated_links(text, s['final_url'], day)
                                          if item['url'] == claim['value']['url']), None)
                        else:
                            from fjordlens.website import url_key
                            found = articles.article_published_date(text, day, page_url=s['final_url'])
                            if url_key(claim['value']['url']) != url_key(s['final_url']):
                                raise ValueError('Article date claim is not about the saved page')
                        if not found or found['published_on'] != claim['value']['published_on'] or found['span'] != e['claim_span']:
                            raise ValueError('Dated publication is not re-derived from the saved page')
                        expected_title = found['title'][:300] if selector['type'] == 'article_date' else found['title']
                        if claim['value'].get('title') != expected_title or claim['value'].get('published_at') != found.get('published_at'):
                            raise ValueError('Dated publication title or timestamp differs from saved source')
                        if e['claim_span'] not in text:
                            raise ValueError('Publication quote not in saved source')
                        passed = True
                    elif e['extraction_method'] == 'html_contact_page_v1':
                        text = snapshot_text(raw, s)
                        digits = re.sub(r'\D', '', str(claim['value']))[-8:]
                        if e['claim_span'] not in text or len(digits) != 8 or digits not in re.sub(r'\D', '', e['claim_span']):
                            raise ValueError('Contact-page phone not in saved source')
                        passed = True
                    elif e['extraction_method'] == 'rss_atom_item_v1':
                        from fjordlens.core import Profile
                        from fjordlens.website import feed
                        replay = Profile(profile['organisation_number'], 'feed-evidence-audit')
                        # Preserve the original dedupe context so a later valid item is not
                        # hidden behind the feed's per-company publication limit.
                        replay.data['claims'] = [c for c in profile['claims'] if c['id'] != claim['id']]
                        captured = Response(s['final_url'], s['final_url'], 200, raw,
                                            {'content-type': s.get('content_type') or 'application/xml'}, e['retrieved_at'])
                        feed(replay, profile['legal_identity'], captured, e['identity_proof'])
                        regenerated = next((c for c in replay.data['claims'] if c['id'] == claim['id']), None)
                        if not regenerated or any(regenerated['value'].get(k) != claim['value'].get(k)
                                                  for k in ('title', 'url', 'published_on', 'published_at')):
                            raise ValueError('Feed publication title, URL and date are not re-derived from saved bytes')
                        if e['claim_span'] not in snapshot_text(raw, s):
                            raise ValueError('Feed publication quote not in saved source')
                        passed = True
                    else:
                        raise ValueError('Unsupported evidence selector for audit')
                except Exception as exc:
                    problems.append({'org':profile['organisation_number'],'claim_id':claim['id'],'evidence_id':eid,'error':str(exc)})
            supported += int(passed)
    result = {'profiles':len(profiles),'claims_checked':count,'claims_with_resolvable_evidence':supported,
              'financial_values_checked_against_raw_source':amounts,'errors':problems,
              'passed':not problems and supported==count,
              'scope':'Snapshot hashes, selector resolution and exact financial amounts/periods/scopes. Not a measurement of external recall or independently reviewed company precision.'}
    write_json(folder/'evidence-audit.json',result)
    return result

if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('folder')
    args=p.parse_args()
    result=audit(args.folder)
    print(json.dumps(result,ensure_ascii=True,indent=2))
    raise SystemExit(0 if result['passed'] else 1)
