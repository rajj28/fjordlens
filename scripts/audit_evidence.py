"""Independently re-open saved bytes and check published selectors and amounts."""
import argparse
from decimal import Decimal
import gzip
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fjordlens.core import at, digest, validate, write_json
from fjordlens.html import parse_html, clean
from fjordlens.identity import node_org
from fjordlens.net import Response

def parse_snapshot(raw, snapshot):
    response = Response(snapshot['final_url'], snapshot['final_url'], body=raw,
                        headers={'content-type':snapshot.get('content_type') or 'text/html'})
    return parse_html(response.text(), snapshot['final_url'])

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
                    if e['content_sha256'] not in bodies:
                        raw = gzip.decompress(path.read_bytes())
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
                        passed = True
                    elif selector['type'] == 'parsed_html_pointer':
                        if s['id'] not in pages:
                            pages[s['id']] = parse_snapshot(raw,s)
                        value = at(pages[s['id']], selector['value'], missing)
                        if value is missing:
                            raise ValueError('Parsed HTML selector does not resolve')
                        if e['extraction_method'] in ('legal_identity_gate_v1', 'identity_gate_v2'):
                            proof = e['identity_proof']
                            if proof['organisation_number'] != profile['organisation_number'] or not proof.get('publishable'):
                                raise ValueError('Missing exact-company proof')
                            if selector != proof.get('proof_selector') or node_org({'taxID': value}) != profile['organisation_number'] or str(value) != e['claim_span']:
                                raise ValueError('Structured legal identity evidence does not match its identifier')
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
                    elif e['extraction_method'] == 'rss_atom_item_v1':
                        from xml.etree import ElementTree as ET
                        root = ET.fromstring(raw)
                        target = claim['value']['url']
                        if target not in raw.decode('utf-8','replace'):
                            raise ValueError('Publication URL not in feed')
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
