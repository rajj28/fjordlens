"""Compare observed coverage and operational checks on the same companies; never estimate an official score."""
import argparse
from collections import Counter
import json
from pathlib import Path


def read_run(path):
    root = Path(path)
    rows = [json.loads(line) for line in (root / 'envelopes.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    report = json.loads((root / 'run-report.json').read_text(encoding='utf-8'))
    return rows, report


def summarize(rows, report):
    fresh = [c for row in rows for c in row['claims'] if c.get('availability') == 'available' and not c.get('stale')]
    families = sorted({f for row in rows for f in row['availability']})
    return {
        'companies': len(rows), 'terminal_envelopes': report['terminal_envelopes'],
        'runtime_seconds': report['runtime_seconds'], 'requests': report['requests'],
        'cost_usd': report['third_party_cost_usd'], 'budget_passed': report['budget_passed'],
        'failed_envelopes': report['failed_envelopes'], 'evidence_validation_errors': report['evidence_validation_errors'],
        'family_companies': {f: sum(any(c['family'] == f and c.get('availability') == 'available' and not c.get('stale') for c in row['claims']) for row in rows) for f in families},
        'claims_by_field': dict(sorted(Counter(c['field'] for c in fresh).items())),
        'website_outcomes': dict(Counter(row['availability']['website']['state'] for row in rows)),
    }


def compare(before_path, after_path):
    before, br = read_run(before_path)
    after, ar = read_run(after_path)
    if Counter(r['organisation_number'] for r in before) != Counter(r['organisation_number'] for r in after):
        raise ValueError('Coverage comparison requires the same input companies, including duplicate counts')
    b, a = summarize(before, br), summarize(after, ar)
    bi = {r['organisation_number']: r for r in before}
    ai = {r['organisation_number']: r for r in after}
    site = lambda row: next((c['value'] for c in row['claims'] if c['field'] == 'official_website' and not c.get('stale')), None)
    changed_sites = [{'organisation_number': org, 'name': ai[org]['legal_identity'].get('name'),
                      'previous_website': site(bi[org]), 'current_website': site(ai[org])}
                     for org in sorted(ai) if site(bi[org]) != site(ai[org])]
    return {
        'claim_boundary': 'Observed coverage comparison, not independently measured recall or accuracy. Official score requires Builderr evaluation.',
        'baseline': b, 'candidate': a,
        'family_company_change': {f: a['family_companies'][f] - b['family_companies'].get(f, 0) for f in a['family_companies']},
        'website_changes_for_manual_review': changed_sites,
        'source_timing_note': 'Live runs may see source changes. Review new claims and lost coverage against the stored snapshots.',
    }


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('baseline')
    p.add_argument('candidate')
    p.add_argument('--output')
    args = p.parse_args()
    result = compare(args.baseline, args.candidate)
    text = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text, encoding='utf-8')
    print(text)
