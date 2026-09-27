"""Generates n8n Workflow SDK code. The shared JS core is embedded ONCE, in FANA 90 - Core Engine."""
import json, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "n8n/build"; OUT.mkdir(parents=True, exist_ok=True)
CORE = (ROOT / "n8n/fana_core.js").read_text(encoding="utf-8")
CORE = CORE.replace("if (typeof module !== 'undefined' && module.exports) module.exports = FANA;\n", "")
IDS = json.loads((ROOT / "n8n/ids.json").read_text()) if (ROOT / "n8n/ids.json").exists() else {}
J = lambda s: json.dumps(s, ensure_ascii=False)
HEADER = "import { workflow, node, trigger, sticky, ifElse, newCredential, expr } from '@n8n/workflow-sdk';\n"

DISPATCH = r"""
// ---- Core Engine dispatcher: one runtime copy of the rules for every FANA workflow ----
const items = $input.all();
if (items.length && items[0].json.op === 'rank') {
  return FANA.rankQueue(items.map(i => i.json.row)).map(r => ({ json: r }));
}
return items.map(({ json: j }) => {
  try {
    switch (j.op) {
      case 'normalize': {
        const lead = FANA.normalizeLead(j.source, j.payload || {});
        return { json: { ok: true, event_key: lead.event_key, lead_key: lead.lead_key, lead } };
      }
      case 'merge_score': {
        let lead = j.incoming, isNew = true;
        if (j.existing_json) { lead = FANA.mergeLeads(JSON.parse(j.existing_json), j.incoming); isNew = false; }
        Object.assign(lead, FANA.scoreLead(lead));
        return { json: { ok: true, lead, is_new: isNew, context: FANA.agentContext(lead) } };
      }
      case 'apply_ai': {
        let ai = j.ai;
        if (typeof ai === 'string') {
          try { ai = JSON.parse(ai.replace(/```json|```/g, '').trim()); } catch (e) { ai = null; }
        }
        const [lead, report] = FANA.applyAiExtraction(j.lead, ai, j.customer_text || '', j.at || FANA.nowIso());
        Object.assign(lead, FANA.scoreLead(lead));
        const reply = ai && typeof ai === 'object' ? ai.reply_ar : null;
        const guard = FANA.checkReply(reply, j.all_customer_text || j.customer_text || '');
        const useTemplate = lead.flags.human_requested || !guard.ok || report.rejected.includes('malformed_output');
        return { json: { ok: true, lead, report, guard,
          reply_ar: useTemplate ? FANA.fallbackReply(lead) : reply,
          reply_source: useTemplate ? 'template' : 'llm', context: FANA.agentContext(lead) } };
      }
      case 'score': {
        const lead = j.lead; Object.assign(lead, FANA.scoreLead(lead));
        return { json: { ok: true, lead } };
      }
      default:
        return { json: { ok: false, error: 'unknown_op:' + j.op } };
    }
  } catch (e) {
    return { json: { ok: false, op: j.op, error: e.message } };
  }
});
"""

def core_sdk(full=True):
    code = (CORE + DISPATCH) if full else "return $input.all();"
    return HEADER + f"""
const coreTrigger = trigger({{ type: 'n8n-nodes-base.executeWorkflowTrigger', version: 1.2,
  config: {{ name: 'Called By FANA Workflows', position: [0, 0], parameters: {{ inputSource: 'passthrough' }} }},
  output: [{{ op: 'normalize', source: 'landing_page', payload: {{}} }}] }});
const engine = node({{ type: 'n8n-nodes-base.code', version: 2,
  config: {{ name: 'FANA Core Engine', position: [260, 0],
    parameters: {{ mode: 'runOnceForAllItems', language: 'javaScript', jsCode: {J(code)} }} }},
  output: [{{ ok: true }}] }});
const note = sticky('## FANA 90 - Core Engine\\nSingle runtime copy of the qualification rules (JS port of the tested Python package in /src/fana; parity-tested).\\nops: normalize | merge_score | apply_ai | score | rank', [], {{ color: 5, position: [0, -220], width: 460, height: 160 }});
export default workflow('fana-90-core', 'FANA 90 - Core Engine').add(coreTrigger).to(engine).add(note);
"""

LEADS = "{ __rl: true, mode: 'id', value: '1wUSNOtiF3YdhBnF', cachedResultName: 'fana_leads' }"
EVENTS = "{ __rl: true, mode: 'id', value: 'ZeHBNPD94Ak4MKiG', cachedResultName: 'fana_processed_events' }"
HS = "credentials: { hubspotAppToken: newCredential('HubSpot FANA Private App') }"
RETRY = "retryOnFail: true, maxTries: 3, waitBetweenTries: 2000"
CORE_REF = f"{{ __rl: true, mode: 'id', value: '{IDS.get('core', 'CORE_ID')}', cachedResultName: 'FANA 90 - Core Engine' }}"

def dt_schema(cols):
    return ", ".join(f"{{ id: '{c}', displayName: '{c}', required: false, defaultMatch: false, display: true, type: '{t}', canBeUsedToMatch: true }}" for c, t in cols)
LEAD_COLS = [("lead_key","string"),("lead_json","string"),("name","string"),("mobile","string"),("email","string"),
             ("priority","string"),("score","number"),("qualification_status","string"),("hubspot_contact_id","string"),
             ("hubspot_deal_id","string"),("last_interaction_at","string")]
EVENT_COLS = [("event_key","string"),("source","string"),("lead_key","string"),("outcome","string"),("received_at","string")]

def webhook(var, name, path, y):
    return f"""const {var} = trigger({{ type: 'n8n-nodes-base.webhook', version: 2.1,
  config: {{ name: '{name}', position: [0, {y}],
    parameters: {{ httpMethod: 'POST', path: '{path}', responseMode: 'responseNode', options: {{}} }} }},
  output: [{{ body: {{}} }}] }});"""

def tag(var, name, source, y):
    return f"""const {var} = node({{ type: 'n8n-nodes-base.set', version: 3.4,
  config: {{ name: '{name}', position: [220, {y}], parameters: {{ mode: 'manual', includeOtherFields: false,
    assignments: {{ assignments: [
      {{ id: '{var}-op', name: 'op', value: 'normalize', type: 'string' }},
      {{ id: '{var}-src', name: 'source', value: '{source}', type: 'string' }},
      {{ id: '{var}-payload', name: 'payload', value: expr('{{{{ $json.body ?? $json }}}}'), type: 'object' }}
    ] }} }} }},
  output: [{{ op: 'normalize', source: '{source}', payload: {{}} }}] }});"""

def http_hs(var, name, method, url, body, x, y):
    return f"""const {var} = node({{ type: 'n8n-nodes-base.httpRequest', version: 4.5,
  config: {{ name: '{name}', position: [{x}, {y}], {RETRY},
    parameters: {{ method: '{method}', url: {url},
      authentication: 'predefinedCredentialType', nodeCredentialType: 'hubspotAppToken',
      sendBody: true, contentType: 'json', specifyBody: 'json', jsonBody: expr('{body}'),
      options: {{ timeout: 10000 }} }},
    {HS} }},
  output: [{{ id: '101', results: [], properties: {{}} }}] }});"""

def if_not_empty(var, name, left, x, y):
    return f"""const {var} = ifElse({{ version: 2.2,
  config: {{ name: '{name}', position: [{x}, {y}], parameters: {{ conditions: {{
    options: {{ caseSensitive: true, leftValue: '', typeValidation: 'loose' }},
    conditions: [{{ leftValue: expr('{left}'), rightValue: '', operator: {{ type: 'string', operation: 'notEmpty', singleValue: true }} }}],
    combinator: 'and' }} }} }} }});"""

def exec_core(var, name, x, y):
    return f"""const {var} = node({{ type: 'n8n-nodes-base.executeWorkflow', version: 1.3,
  config: {{ name: '{name}', position: [{x}, {y}],
    parameters: {{ mode: 'each', source: 'database', workflowId: {CORE_REF}, options: {{ waitForSubWorkflow: true }} }} }},
  output: [{{ ok: true, lead: {{}}, event_key: 'e', lead_key: 'k', is_new: true }}] }});"""

def code(var, name, body, x, y, out):
    return f"""const {var} = node({{ type: 'n8n-nodes-base.code', version: 2,
  config: {{ name: '{name}', position: [{x}, {y}],
    parameters: {{ mode: 'runOnceForEachItem', language: 'javaScript', jsCode: {J(body)} }} }},
  output: [{out}] }});"""

BUILD_CONTACT = """const m = $('Core: Merge & Score').first().json;
const lead = m.lead;
const found = ($json.results || [])[0] || null;
const parts = (lead.name || '').split(' ').filter(Boolean);
const props = {
  firstname: parts[0], lastname: parts.slice(1).join(' ') || undefined,
  phone: lead.mobile, email: lead.email, city: lead.city,
  fana_lead_key: lead.lead_key, fana_lead_source: lead.first_source,
  preferred_contact_method: lead.preferred_contact !== 'unknown' ? lead.preferred_contact : undefined,
  contact_consent: lead.consent !== 'unknown' ? lead.consent : undefined,
};
// Send only known values so CRM data is never overwritten with blanks
for (const k of Object.keys(props)) if (props[k] === null || props[k] === undefined || props[k] === '') delete props[k];
const contactId = (found && found.id) || lead.hubspot_contact_id || null;
return { json: { contact_id: contactId, contact_action: contactId ? 'update' : 'create',
  existing_crm_contact: Boolean(found) && m.is_new, properties: props } };"""

SEARCH_BODY = """const lead = $json.lead;
const groups = [];
if (lead.mobile) groups.push({ filters: [{ propertyName: 'phone', operator: 'EQ', value: lead.mobile }] });
if (lead.email) groups.push({ filters: [{ propertyName: 'email', operator: 'EQ', value: lead.email }] });
groups.push({ filters: [{ propertyName: 'fana_lead_key', operator: 'EQ', value: lead.lead_key }] });
return { json: { search_body: { filterGroups: groups, limit: 1,
  properties: ['firstname', 'lastname', 'phone', 'email', 'fana_lead_key'] } } };"""

COLLECT = """const m = $('Core: Merge & Score').first().json;
const c = $('Build Contact Payload').first().json;
const lead = m.lead;
lead.hubspot_contact_id = String($json.id);
lead.existing_crm_contact = Boolean(lead.existing_crm_contact || c.existing_crm_contact);
return { json: {
  lead_key: lead.lead_key, lead_json: JSON.stringify(lead), name: lead.name || '',
  mobile: lead.mobile || '', email: lead.email || '', priority: lead.priority, score: lead.score,
  qualification_status: lead.qualification_status, hubspot_contact_id: lead.hubspot_contact_id,
  hubspot_deal_id: lead.hubspot_deal_id || '', last_interaction_at: lead.last_interaction_at,
  event_key: lead.event_key, source: lead.source,
  response: { status: 'processed', lead_key: lead.lead_key, is_new_lead: m.is_new,
    contact_action: c.contact_action, existing_crm_contact: lead.existing_crm_contact,
    hubspot_contact_id: lead.hubspot_contact_id, sources: lead.sources, changed_fields: lead.changed_fields || [],
    priority: lead.priority, score: lead.score, qualification_status: lead.qualification_status,
    missing_fields: lead.missing_fields, reason_ar: lead.reason_ar, reason_en: lead.reason_en, issues: lead.issues }
} };"""

def intake_sdk():
    lead_values = ", ".join(f"{c}: expr('{{{{ $json.{c} }}}}')" for c, _ in LEAD_COLS)
    return HEADER + f"""
{webhook('landingHook', 'Landing Page Webhook', 'fana/landing', 0)}
{webhook('metaHook', 'Meta Lead Ads Webhook', 'fana/meta', 200)}
{webhook('waHook', 'WhatsApp Webhook', 'fana/whatsapp', 400)}
{tag('tagLanding', 'Tag Landing Page', 'landing_page', 0)}
{tag('tagMeta', 'Tag Meta Lead Ads', 'meta_lead_ads', 200)}
{tag('tagWa', 'Tag WhatsApp', 'whatsapp', 400)}
{exec_core('coreNormalize', 'Core: Normalize', 460, 200)}
const payloadValid = ifElse({{ version: 2.2,
  config: {{ name: 'Payload Valid?', position: [680, 200], parameters: {{ conditions: {{
    options: {{ caseSensitive: true, leftValue: '', typeValidation: 'loose' }},
    conditions: [{{ leftValue: expr('{{{{ $json.ok }}}}'), rightValue: '', operator: {{ type: 'boolean', operation: 'true', singleValue: true }} }}],
    combinator: 'and' }} }} }} }});
const respondInvalid = node({{ type: 'n8n-nodes-base.respondToWebhook', version: 1.5,
  config: {{ name: 'Respond 400 Malformed', position: [900, 420],
    parameters: {{ respondWith: 'json', responseBody: expr('{{{{ JSON.stringify({{ status: "rejected", error: $json.error }}) }}}}'),
      options: {{ responseCode: 400 }} }} }} }});
const findEvent = node({{ type: 'n8n-nodes-base.dataTable', version: 1.1,
  config: {{ name: 'Find Processed Event', position: [900, 120], alwaysOutputData: true,
    parameters: {{ resource: 'row', operation: 'get', dataTableId: {EVENTS}, matchType: 'allConditions',
      filters: {{ conditions: [{{ keyName: 'event_key', condition: 'eq', keyValue: expr('{{{{ $json.event_key }}}}') }}] }},
      returnAll: false, limit: 1 }} }},
  output: [{{}}] }});
{if_not_empty('alreadyProcessed', 'Already Processed?', '{{ $json.event_key }}', 1120, 120)}
const respondDuplicate = node({{ type: 'n8n-nodes-base.respondToWebhook', version: 1.5,
  config: {{ name: 'Respond Duplicate Ignored', position: [1340, 0],
    parameters: {{ respondWith: 'json',
      responseBody: expr('{{{{ JSON.stringify({{ status: "duplicate_ignored", event_key: $("Core: Normalize").first().json.event_key, lead_key: $("Core: Normalize").first().json.lead_key }}) }}}}'),
      options: {{ responseCode: 200 }} }} }} }});
const loadLead = node({{ type: 'n8n-nodes-base.dataTable', version: 1.1,
  config: {{ name: 'Load Existing Lead', position: [1340, 220], alwaysOutputData: true,
    parameters: {{ resource: 'row', operation: 'get', dataTableId: {LEADS}, matchType: 'allConditions',
      filters: {{ conditions: [{{ keyName: 'lead_key', condition: 'eq', keyValue: expr('{{{{ $("Core: Normalize").first().json.lead_key }}}}') }}] }},
      returnAll: false, limit: 1 }} }},
  output: [{{}}] }});
const prepMerge = node({{ type: 'n8n-nodes-base.set', version: 3.4,
  config: {{ name: 'Prepare Merge', position: [1560, 220], parameters: {{ mode: 'manual', includeOtherFields: false,
    assignments: {{ assignments: [
      {{ id: 'pm-op', name: 'op', value: 'merge_score', type: 'string' }},
      {{ id: 'pm-in', name: 'incoming', value: expr('{{{{ $("Core: Normalize").first().json.lead }}}}'), type: 'object' }},
      {{ id: 'pm-ex', name: 'existing_json', value: expr('{{{{ $json.lead_json ?? "" }}}}'), type: 'string' }}
    ] }} }} }},
  output: [{{ op: 'merge_score', incoming: {{}}, existing_json: '' }}] }});
{exec_core('coreMerge', 'Core: Merge & Score', 1780, 220)}
{code('searchBody', 'Build Search Body', SEARCH_BODY, 2000, 220, "{ search_body: {} }")}
{http_hs('searchContact', 'Search HubSpot Contact', 'POST', "'https://api.hubapi.com/crm/v3/objects/contacts/search'", '{{ JSON.stringify($json.search_body) }}', 2220, 220)}
{code('buildContact', 'Build Contact Payload', BUILD_CONTACT, 2440, 220, "{ contact_id: null, contact_action: 'create', existing_crm_contact: false, properties: {} }")}
{if_not_empty('contactExists', 'Contact Exists?', '{{ $json.contact_id }}', 2660, 220)}
{http_hs('updateContact', 'Update HubSpot Contact', 'PATCH', "expr('https://api.hubapi.com/crm/v3/objects/contacts/{{ $json.contact_id }}')", '{{ JSON.stringify({ properties: $json.properties }) }}', 2880, 120)}
{http_hs('createContact', 'Create HubSpot Contact', 'POST', "'https://api.hubapi.com/crm/v3/objects/contacts'", '{{ JSON.stringify({ properties: $json.properties }) }}', 2880, 320)}
{code('collect', 'Collect Result', COLLECT, 3100, 220, "{ lead_key: 'k', lead_json: '{}', name: '', mobile: '', email: '', priority: 'P3', score: 0, qualification_status: 'in_qualification', hubspot_contact_id: '1', hubspot_deal_id: '', last_interaction_at: '', event_key: 'e', source: 'landing_page', response: {} }")}
const saveLead = node({{ type: 'n8n-nodes-base.dataTable', version: 1.1,
  config: {{ name: 'Save Lead', position: [3320, 220],
    parameters: {{ resource: 'row', operation: 'upsert', dataTableId: {LEADS}, matchType: 'allConditions',
      filters: {{ conditions: [{{ keyName: 'lead_key', condition: 'eq', keyValue: expr('{{{{ $json.lead_key }}}}') }}] }},
      columns: {{ mappingMode: 'defineBelow', value: {{ {lead_values} }}, schema: [{dt_schema(LEAD_COLS)}] }} }} }},
  output: [{{ id: 1 }}] }});
const markProcessed = node({{ type: 'n8n-nodes-base.dataTable', version: 1.1,
  config: {{ name: 'Mark Event Processed', position: [3540, 220],
    parameters: {{ resource: 'row', operation: 'insert', dataTableId: {EVENTS},
      columns: {{ mappingMode: 'defineBelow', value: {{
        event_key: expr('{{{{ $("Collect Result").first().json.event_key }}}}'),
        source: expr('{{{{ $("Collect Result").first().json.source }}}}'),
        lead_key: expr('{{{{ $("Collect Result").first().json.lead_key }}}}'),
        outcome: 'processed', received_at: expr('{{{{ $now.toISO() }}}}') }},
        schema: [{dt_schema(EVENT_COLS)}] }} }} }},
  output: [{{ id: 1 }}] }});
const respondOk = node({{ type: 'n8n-nodes-base.respondToWebhook', version: 1.5,
  config: {{ name: 'Respond Processed', position: [3760, 220],
    parameters: {{ respondWith: 'json', responseBody: expr('{{{{ JSON.stringify($("Collect Result").first().json.response) }}}}'),
      options: {{ responseCode: 200 }} }} }} }});
const note = sticky('## FANA 01 - Lead Intake & CRM Sync\\n3 channel webhooks -> one schema via Core Engine. Idempotency: event_key checked first, recorded only AFTER HubSpot succeeds. Dedupe key: auction + E.164 mobile (else email). Contact search: phone OR email OR fana_lead_key.', [], {{ color: 4, position: [0, -260], width: 560, height: 180 }});

export default workflow('fana-01-intake', 'FANA 01 - Lead Intake & CRM Sync')
  .add(landingHook).to(tagLanding).to(coreNormalize)
  .add(metaHook).to(tagMeta).to(coreNormalize)
  .add(waHook).to(tagWa).to(coreNormalize)
  .add(coreNormalize)
  .to(payloadValid
    .onTrue(findEvent.to(alreadyProcessed
      .onTrue(respondDuplicate)
      .onFalse(loadLead.to(prepMerge).to(coreMerge).to(searchBody).to(searchContact).to(buildContact).to(contactExists
        .onTrue(updateContact.to(collect))
        .onFalse(createContact.to(collect))))))
    .onFalse(respondInvalid))
  .add(collect).to(saveLead).to(markProcessed).to(respondOk)
  .add(note)
  .group('Idempotency', [findEvent, alreadyProcessed, respondDuplicate], {{ description: 'Skips webhook events already processed (replays return duplicate_ignored).' }})
  .group('HubSpot Contact Upsert', [searchBody, searchContact, buildContact, contactExists, updateContact, createContact], {{ description: 'Find by phone, email or lead key; update if found, else create. Only known values are sent.' }});
"""

(OUT / "core_skeleton.ts").write_text(core_sdk(False), encoding="utf-8")
(OUT / "core_full.ts").write_text(core_sdk(True), encoding="utf-8")
(OUT / "intake.ts").write_text(intake_sdk(), encoding="utf-8")
for f in ["core_skeleton.ts", "core_full.ts", "intake.ts"]:
    print(f, len((OUT / f).read_text(encoding="utf-8")))
