// Runs parity cases through n8n/fana_core.js. Input: JSON cases on stdin. Output: JSON results.
const FANA = require('../n8n/fana_core.js');
const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = {
  budgets: cases.budgets.map(t => FANA.parseBudget(t)),
  phones: cases.phones.map(p => FANA.normalizePhone(p)),
  assets: cases.assets.map(t => FANA.matchAsset(t)),
  leads: cases.leads.map(([s, p, ts]) => FANA.normalizeLead(s, p, ts)),
  merged: FANA.mergeLeads(FANA.normalizeLead(...cases.merge[0]), FANA.normalizeLead(...cases.merge[1])),
  scores: cases.score_leads.map(l => FANA.scoreLead(l)),
  ranked: FANA.rankQueue(cases.rank_rows).map(r => r.name),
  agent: cases.agent.map(([text, ai]) => {
    const [l, rep] = FANA.applyAiExtraction(JSON.parse(JSON.stringify(cases.agent_base)), ai, text, '2026-09-26T09:00:00Z');
    return { lead: l, report: rep, next: FANA.nextSlots(l), fallback: FANA.fallbackReply(l),
      ctx: FANA.agentContext(l), score: FANA.scoreLead(l) };
  }),
  replies: cases.replies.map(([a, b]) => FANA.checkReply(a, b)),
};
process.stdout.write(JSON.stringify(out));
