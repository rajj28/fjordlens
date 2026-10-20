"""Regressions for current/stale report facts and static/live answer prompts."""
import json
import shutil
import subprocess
import unittest
from pathlib import Path

from fjordlens.answers import QUESTIONS, build_answers
from fjordlens.report import compact, summary_row
from fjordlens.research import ANSWER_ROUTES, OVERVIEW_QUESTION, ONLINE_QUESTION, UNSUPPORTED_ANSWER_ROUTES, answer_profile, suggested_questions
from fjordlens.server import _company_response

ROOT = Path(__file__).resolve().parents[1]


def claim(cid, field, family, value, *, stale=False, **extra):
    return {"id": cid, "field": field, "family": family, "value": value, "availability": "available",
            "stale": stale, "scope": "legal_entity", "evidence_ids": ["ev_" + cid], **extra}


def profile(claims):
    return {"organisation_number": "923609016", "legal_identity": {"name": "Example AS"}, "claims": claims,
            "evidence": [], "availability": {}, "changes": [], "refresh": {}, "run": {}, "source_snapshots": [{"id": "saved"}]}


class ReleaseDisplayTests(unittest.TestCase):
    def test_static_and_server_summary_rows_prefer_current_registered_facts(self):
        p = profile([
            claim("old", "employees", "workforce", 4, stale=True, last_observed_at="2026-01-01"),
            claim("new", "employees", "workforce", 8, last_observed_at="2026-10-01"),
            claim("old_address", "registered_address", "identity", {"poststed": "BERGEN"}, stale=True),
            claim("new_address", "registered_address", "identity", {"poststed": "OSLO"}),
        ])
        row = summary_row(p)
        self.assertEqual(row["employees"], 8)
        self.assertEqual(row["city"], "OSLO")
        self.assertIsNone(row["municipality"])
        stale_only = summary_row(profile([claim("old_staff", "employees", "workforce", 4, stale=True)]))
        self.assertIsNone(stale_only["employees"])

    def test_stale_only_financial_value_is_not_listed_as_current_revenue(self):
        p = profile([claim("old_revenue", "financial_revenue", "financials", {"amount": "123", "currency": "NOK"},
                           stale=True, reporting_period={"fraDato": "2025-01-01", "tilDato": "2025-12-31"})])
        self.assertIsNone(summary_row(p)["revenue"])

    def test_suggestions_are_only_answerable_and_preserved_on_live_full_profile(self):
        p = profile([
            claim("industry", "industry", "identity", {"kode": "62.100", "beskrivelse": "Software"}),
            claim("old_revenue", "financial_revenue", "financials", {"amount": "123", "currency": "NOK"}, stale=True,
                  reporting_period={"fraDato": "2025-01-01", "tilDato": "2025-12-31"}),
        ])
        expected = suggested_questions(p)
        direct = {item["question"]: item for item in build_answers(p)}
        self.assertTrue(expected)
        self.assertTrue(all(direct[question]["answerable"] for question in expected))
        self.assertNotIn(QUESTIONS[3], expected)
        static = compact(p)
        live = _company_response(p)
        self.assertEqual(static["answer_suggestions"], expected)
        self.assertEqual(live["answer_suggestions"], expected)
        self.assertEqual(live["source_snapshots"], p["source_snapshots"])
        self.assertEqual(live["evidence"], p["evidence"])
        self.assertNotIn("question_answers", live)
        self.assertNotIn("answer_suggestions", p)

    @unittest.skipUnless(shutil.which("node"), "Node.js is needed to execute the viewer regression")
    def test_static_api_answer_order_live_suggestions_and_render_deduplication(self):
        p = profile([
            claim("ceo", "registered_role", "leadership", {"role_code": "DAGL", "name": "Kari Nord"}),
            claim("industry", "industry", "identity", {"kode": "62.100", "beskrivelse": "Software"}),
        ])
        query = "who leads and what does the company do"
        expected = answer_profile(p, query)
        static = compact(p)
        config = {"questions": list(QUESTIONS), "online": ONLINE_QUESTION, "overview": OVERVIEW_QUESTION,
                  "unsupported": UNSUPPORTED_ANSWER_ROUTES, "routes": ANSWER_ROUTES}
        render_profile = {"claims": [], "evidence": [], "availability": {}, "summary": {
            "brief": {"text": "Brief sentence.", "claim_ids": []},
            "sections": [{"title": "One", "sentences": [{"text": "Repeated sentence.", "claim_ids": []},
                                                               {"text": "Repeated sentence.", "claim_ids": []}]}],
            "sentences": [{"text": "Repeated sentence.", "claim_ids": []}],
            "attention": {"comparison": "first_observation", "risks": [], "stale": [], "unknowns": []}},
            "refresh": {"stale_claim_ids": []}, "changes": []}
        payload = {"config": config, "profile": static, "expected": expected, "renderProfile": render_profile,
                   "liveProfile": {"answer_suggestions": static["answer_suggestions"]}}
        script = "\n".join([
            "const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');",
            "const file=process.argv[1],data=JSON.parse(process.argv[2]),source=fs.readFileSync(file,'utf8');",
            "const start=source.indexOf('function localAnswer('),end=source.indexOf('\\nasync function askApi',start);",
            "const context={window:{FJ_ANSWER_CONFIG:data.config}};vm.createContext(context);vm.runInContext(source.slice(start,end),context);",
            "const result=context.localAnswer(data.profile,'who leads and what does the company do');",
            "assert.equal(result.answer,data.expected.answer);assert.equal(result.answerable,data.expected.answerable);",
            "assert.deepEqual(Array.from(result.claim_ids),data.expected.claim_ids);assert.deepEqual(Array.from(result.evidence_ids),data.expected.evidence_ids);",
            "const a=source.indexOf('function guidedQuestions('),b=source.indexOf('\\nfunction askBox',a);",
            "vm.runInContext(source.slice(a,b),context);assert.deepEqual(Array.from(context.guidedQuestions(data.liveProfile)),data.liveProfile.answer_suggestions.slice(0,4));",
            "context.esc=x=>String(x??'');context.label=x=>String(x).replaceAll('_',' ');context.valueHTML=x=>String(x);context.citation=c=>c?'[citation]':'';context.changeCard=()=>'';",
            "let a1=source.indexOf('function claimCard('),b1=source.indexOf('\\nfunction emptyFamily',a1);vm.runInContext(source.slice(a1,b1),context);",
            "assert.match(context.claimCard({field:'financial_revenue',value:{amount:'12'},scope:'legal_entity',stale:true,evidence_ids:[]}),/Last known/);",
            "a1=source.indexOf('function renderAttentionOverview(');b1=source.indexOf('\\nfunction renderTab',a1);vm.runInContext(source.slice(a1,b1),context);",
            "const out={innerHTML:''};context.renderAttentionOverview(data.renderProfile,out);",
            "assert.equal((out.innerHTML.match(/Repeated sentence\./g)||[]).length,1);assert.equal(data.renderProfile.summary.sections[0].sentences.length,2);",
            "data.renderProfile.summary.sections=[];data.renderProfile.summary.sentences=[{text:'Legacy sentence.',claim_ids:[]}];",
            "context.renderAttentionOverview(data.renderProfile,out);assert.match(out.innerHTML,/Legacy sentence\./);",
        ])
        completed = subprocess.run(["node", "-e", script, str(ROOT / "fjordlens" / "web" / "app.js"), json.dumps(payload)],
                                   capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
