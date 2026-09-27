/* FANA lead core - JavaScript port of src/fana (catalog, budget_parser, normalize, scoring).
 * Runs inside n8n Code nodes (no imports). Python in src/fana is the tested source of truth;
 * tests/test_parity.py checks this file produces identical results on the same fixtures.
 * n8n Cloud's Python sandbox blocks all imports (even `re`), which is why this port exists.
 */
const FANA = (() => {
  // ------------------------------------------------------------------ catalog
  const AUCTION_ID = 'al-tilal-al-sharqiya';
  const TYPE_BUDGET_BANDS = { // ASSUMPTION: synthetic bands, never quoted to customers
    apartment: { min: 500000, max: 1200000 },
    villa: { min: 1500000, max: 3500000 },
    residential_building: { min: 3000000, max: 7000000 },
    commercial_building: { min: 5000000, max: 15000000 },
    commercial_land: { min: 4000000, max: 20000000 },
  };
  const LOWEST_BAND_MIN = Math.min(...Object.values(TYPE_BUDGET_BANDS).map(b => b.min));
  const TYPE_LABELS = {
    apartment: ['شقة سكنية', 'Apartment'], villa: ['فيلا سكنية', 'Villa'],
    residential_building: ['عمارة سكنية', 'Residential building'],
    commercial_building: ['عمارة تجارية', 'Commercial building'],
    commercial_land: ['أرض تجارية', 'Commercial land'],
  };
  const ASSETS = [
    ['cb_tahlia_khobar', 'commercial_building', 'التحلية', 'Al Tahlia', 'Khobar'],
    ['cb_nuzha_dammam', 'commercial_building', 'النزهة', 'Al Nuzha', 'Dammam'],
    ['rb_taiba_dammam', 'residential_building', 'طيبة', 'Taiba', 'Dammam'],
    ['rb_jalawiyah_dammam', 'residential_building', 'الجلوية', 'Al Jalawiyah', 'Dammam'],
    ['villa_aqrabiyah_khobar', 'villa', 'العقربية', 'Al Aqrabiyah', 'Khobar'],
    ['villa_taiba_dammam', 'villa', 'طيبة', 'Taiba', 'Dammam'],
    ['villa_fayhaa_dammam', 'villa', 'الفيحاء', 'Al Fayhaa', 'Dammam'],
    ['villa_nakheel_dammam', 'villa', 'النخيل', 'Al Nakheel', 'Dammam'],
    ['land_muraikabat_1', 'commercial_land', 'المريكبات', 'Al Muraikabat', 'Dammam'],
    ['land_muraikabat_2', 'commercial_land', 'المريكبات', 'Al Muraikabat', 'Dammam'],
    ['apt_nour_dammam', 'apartment', 'النور', 'Al Nour', 'Dammam'],
  ].map(([id, type, district_ar, district_en, city]) => ({ id, type, district_ar, district_en, city }));
  const ASSET_BY_ID = Object.fromEntries(ASSETS.map(a => [a.id, a]));
  const TYPE_KEYWORDS = [
    ['commercial_building', ['عماره تجاريه', 'مبني تجاري', 'commercial building']],
    ['residential_building', ['عماره سكنيه', 'residential building']],
    ['commercial_land', ['ارض تجاريه', 'ارض', 'اراضي', 'land', 'plot']],
    ['villa', ['فيلا', 'فله', 'فلة', 'villa']],
    ['apartment', ['شقه', 'apartment', 'flat']],
  ];
  const GENERIC_BUILDING = ['عماره', 'عمائر', 'building'];

  // Unicode-aware word boundaries (JS \b is ASCII-only; Python's is Unicode)
  const BS = '(?<![\\p{L}\\p{N}_])';
  const BE = '(?![\\p{L}\\p{N}_])';
  const rx = (p, f = '') => new RegExp(p, 'u' + f);

  function normalizeAr(text) {
    if (text === null || text === undefined) return '';
    let t = String(text).toLowerCase();
    t = t.replace(/[\u064B-\u0652\u0640]/g, '').replace(/[إأآا]/g, 'ا');
    t = t.replace(/ة/g, 'ه').replace(/ى/g, 'ي');
    return t.replace(/\s+/g, ' ').trim();
  }

  function assetLabel(id, lang = 'ar') {
    const a = ASSET_BY_ID[id];
    if (!a) return null;
    const [ar, en] = TYPE_LABELS[a.type];
    return lang === 'ar'
      ? `${ar} - حي ${a.district_ar} - ${a.city === 'Khobar' ? 'الخبر' : 'الدمام'}`
      : `${en} - ${a.district_en} - ${a.city}`;
  }

  function matchAsset(text) {
    const t = normalizeAr(text);
    if (!t) return { asset_id: null, property_type: null, confidence: 0.0 };
    let ptype = null;
    for (const [key, pats] of TYPE_KEYWORDS) {
      if (pats.some(p => t.includes(p))) { ptype = key; break; }
    }
    if (ptype === null && GENERIC_BUILDING.some(p => t.includes(p))) ptype = 'building_unspecified';
    const districts = ASSETS.filter(a =>
      t.includes(normalizeAr(a.district_ar).replace('ال', '')) ||
      t.includes(a.district_en.toLowerCase().replace('al ', '')));
    let typed;
    if (ptype && ptype !== 'building_unspecified') typed = districts.filter(a => a.type === ptype);
    else if (ptype === 'building_unspecified') typed = districts.filter(a => a.type.includes('building'));
    else typed = districts;
    if (typed.length === 1) return { asset_id: typed[0].id, property_type: typed[0].type, confidence: 0.9 };
    if (typed.length > 1) {
      const types = [...new Set(typed.map(a => a.type))];
      return { asset_id: null,
        property_type: types.length === 1 ? types[0] : (TYPE_BUDGET_BANDS[ptype] ? ptype : null),
        confidence: 0.6 };
    }
    if (TYPE_BUDGET_BANDS[ptype]) return { asset_id: null, property_type: ptype, confidence: 0.8 };
    return { asset_id: null, property_type: null, confidence: 0.0 };
  }

  function bandFor(assetId, ptype) {
    if (assetId && ASSET_BY_ID[assetId]) return TYPE_BUDGET_BANDS[ASSET_BY_ID[assetId].type];
    return TYPE_BUDGET_BANDS[ptype] || null;
  }

  function catalogForPrompt() {
    return ASSETS.map(a => `- ${a.id}: ${assetLabel(a.id)}`).join('\n');
  }

  // ------------------------------------------------------------- budget parser
  const APPROX_PCT = 0.15;
  const AR_DIGIT_MAP = { '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4', '٥': '5', '٦': '6', '٧': '7',
    '٨': '8', '٩': '9', '۰': '0', '۱': '1', '۲': '2', '۳': '3', '۴': '4', '۵': '5', '۶': '6', '۷': '7',
    '۸': '8', '۹': '9', '٫': '.', '٬': ',' };
  const toWestern = s => String(s).replace(/[٠-٩۰-۹٫٬]/g, c => AR_DIGIT_MAP[c]);
  const APPROX_CUES = '(حول|تقريبا|تقريبًا|تقريباً|حدود|بحدود|قرابه|قرابة|يعني|around|about|approx|~)';
  const MAX_CUES = '(بالكثير|كحد اقصى|كحد أقصى|حد اقصى|ما يتعدى|ما يتجاوز|لا يزيد|ما يزيد|اقصى|أقصى|max|up to|at most)';
  const MIN_CUES = '(فوق|اكثر من|أكثر من|على الاقل|على الأقل|at least|\\+)';
  const FLEX_UP_CUES = '(ازيد|أزيد|نزيد|يزيد شوي|ممكن ازيد|قابل للزياده|قابل للزيادة|flexible)';
  const VAGUE_CUES = '(على حسب|ما ادري|ما أدري|مدري|ماادري|الله يسهل|نشوف|مو متأكد|مو متاكد|not sure|depends)';
  const NUM_WORDS = [['واحد', 1], ['اثنين', 2], ['اثنان', 2], ['ثلاث', 3], ['ثلاثه', 3], ['ثلاثة', 3],
    ['اربع', 4], ['أربع', 4], ['اربعه', 4], ['أربعة', 4], ['خمس', 5], ['خمسه', 5], ['خمسة', 5],
    ['ست', 6], ['سته', 6], ['ستة', 6], ['سبع', 7], ['سبعه', 7], ['سبعة', 7], ['ثمان', 8], ['ثمانيه', 8],
    ['ثمانية', 8], ['تسع', 9], ['تسعه', 9], ['تسعة', 9], ['عشر', 10], ['عشره', 10], ['عشرة', 10]];
  const MILLION = `(?:مليون|ملايين|مليونين|million|mil|m)${BE}`;
  const THOUSAND = `(?:الف|ألف|آلاف|الاف|k|thousand)${BE}`;
  const pyFloatStr = n => (Number.isInteger(n) ? n.toFixed(1) : String(n));

  function prep(text) {
    let t = toWestern(text).toLowerCase().replace(/[\u064B-\u0652\u0640]/g, '');
    const subs = [
      [rx('مليونين\\s*و\\s*(نص|نصف)', 'g'), '2.5 مليون'],
      [rx('مليون\\s*و\\s*(نص|نصف)', 'g'), '1.5 مليون'],
      [rx('مليون\\s*و\\s*ربع', 'g'), '1.25 مليون'],
      [rx('(\\d+(?:\\.\\d+)?)\\s*و\\s*(نص|نصف)\\s*مليون', 'g'), (m, a) => `${pyFloatStr(parseFloat(a) + 0.5)} مليون`],
      [rx('(نص|نصف)\\s*(ال)?مليون', 'g'), '0.5 مليون'],
      [rx('ربع\\s*(ال)?مليون', 'g'), '0.25 مليون'],
      [rx('مليونين', 'g'), '2 مليون'],
      [rx('الفين|ألفين', 'g'), '2 ألف'],
    ];
    for (const [p, r] of subs) t = t.replace(p, r);
    for (const [w, n] of NUM_WORDS) t = t.replace(rx(`${BS}${w}\\s*(ملايين|مليون)`, 'g'), `${n} مليون`);
    t = t.replace(rx('(^|\\s)و(?=\\d|(ال)?مليون)', 'g'), '$1و ');
    let out = '', last = 0;
    for (const m of t.matchAll(rx('(ال)?مليون', 'g'))) {
      const before = t.slice(0, m.index).trimEnd();
      out += t.slice(last, m.index) + (/\d$/.test(before) ? m[0] : '1 مليون');
      last = m.index + m[0].length;
    }
    return out + t.slice(last);
  }

  function toNumber(s) {
    s = s.trim();
    if (/^\d{1,3}(,\d{3})+(\.\d+)?$/.test(s)) s = s.replace(/,/g, '');
    else s = s.replace(/,/g, '.');
    const n = Number(s);
    return Number.isFinite(n) && /^\d+(\.\d+)?$/.test(s) ? n : null;
  }

  function extractAmounts(text) {
    const t = prep(text);
    const re = rx(`(\\d+(?:[.,]\\d+)*)\\s*(${MILLION}|${THOUSAND})?`, 'g');
    const raw = [];
    for (const m of t.matchAll(re)) {
      const n = toNumber(m[1]);
      if (n !== null) raw.push({ n, unit: m[2] || '', start: m.index, end: m.index + m[0].length });
    }
    for (let i = 0; i < raw.length - 1; i++) {
      const a = raw[i], nxt = raw[i + 1];
      const joiner = t.slice(a.end, nxt.start);
      if (!a.unit && nxt.unit && a.n < 100 && rx('^\\s*(و|الى|إلى|لين|-|to)\\s*$').test(joiner)) {
        a.unit = nxt.unit; a.borrowed = true;
      }
    }
    const out = [];
    for (const a of raw) {
      const { n, unit } = a;
      const explicit = (Boolean(unit) && !a.borrowed) || (!unit && n >= 10000);
      let v;
      if (unit && rx(`^${MILLION}`).test(unit)) v = n * 1000000;
      else if (unit && rx(`^${THOUSAND}`).test(unit)) v = n * 1000;
      else if (n >= 1900 && n <= 2100 && Number.isInteger(n)) continue;
      else if (n >= 10000) v = n;
      else if (n >= 100 && n < 10000) v = n * 1000;
      else continue;
      out.push([v, explicit]);
    }
    return out;
  }

  function parseBudget(text) {
    const r = { raw: text ?? null, min: null, max: null, approx: false, flexible_up: false,
      status: 'unknown', confidence: 0.0 };
    if (text === null || text === undefined || String(text).trim() === '') return r;
    const t = prep(text);
    const amounts = extractAmounts(text);
    if (!amounts.length) {
      if (rx(VAGUE_CUES).test(t)) r.status = 'vague';
      return r;
    }
    let conf = amounts.every(([, e]) => e) ? 0.9 : 0.7;
    const values = amounts.map(([v]) => v);
    if (values.length >= 2) {
      r.min = Math.min(values[0], values[1]); r.max = Math.max(values[0], values[1]);
    } else {
      const v = values[0];
      if (rx(MAX_CUES).test(t)) { r.min = null; r.max = v; }
      else if (rx(MIN_CUES).test(t)) { r.min = v; r.max = null; }
      else if (rx(APPROX_CUES).test(t)) {
        r.min = pyRound(v * (1 - APPROX_PCT)); r.max = pyRound(v * (1 + APPROX_PCT)); r.approx = true;
      } else { r.min = v; r.max = v; }
    }
    if (rx(FLEX_UP_CUES).test(t)) r.flexible_up = true;
    if (rx(VAGUE_CUES).test(t)) conf = Math.min(conf, 0.5);
    const ref = r.max || r.min;
    if (ref < 50000 || ref > 500000000) conf = 0.3;
    r.status = 'known'; r.confidence = conf;
    return r;
  }

  function pyRound(x) { // Python round(): banker's rounding on .5
    const f = Math.floor(x), d = x - f;
    if (Math.abs(d - 0.5) < 1e-9) return f % 2 === 0 ? f : f + 1;
    return Math.round(x);
  }

  function budgetMid(b) {
    if (!b) return null;
    const lo = b.min, hi = b.max;
    if (lo && hi) return (lo + hi) / 2;
    return lo || hi || null;
  }

  function formatSar(v) {
    if (v === null || v === undefined) return '?';
    if (v >= 1000000) return `${parseFloat((Math.round(v / 10000) / 100).toFixed(2))}M`;
    return `${Math.round(v / 1000)}K`;
  }

  function formatBudget(b) {
    if (!b || b.status !== 'known') return 'unknown';
    const lo = b.min, hi = b.max;
    let s;
    if (lo && hi && lo !== hi) s = `${formatSar(lo)}-${formatSar(hi)} SAR`;
    else if (lo && hi) s = `${formatSar(lo)} SAR`;
    else if (hi) s = `up to ${formatSar(hi)} SAR`;
    else s = `from ${formatSar(lo)} SAR`;
    return s + (b.flexible_up ? ' (flexible up)' : '');
  }

  // --------------------------------------------------------------- normalize
  const EMAIL_RE = /^[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}$/;
  const ENUM_FIELDS = ['financing', 'timeline', 'buyer_type', 'investment_purpose',
    'auction_experience', 'preferred_contact', 'consent', 'intent'];
  const VALUE_FIELDS = ['name', 'mobile', 'email', 'city', 'interested_asset', 'property_type',
    'budget_min', 'budget_max'];
  const MERGE_FIELDS = [...VALUE_FIELDS, 'budget_raw', 'budget_status', 'budget_approx',
    'budget_flexible_up', ...ENUM_FIELDS];

  const nowIso = () => new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');

  function normalizePhone(raw) {
    if (raw === null || raw === undefined || String(raw).trim() === '') return [null, 'missing'];
    const s = toWestern(raw).trim();
    let hadPlus = s.startsWith('+') || s.startsWith('00');
    let d = s.replace(/\D/g, '');
    if (d.startsWith('00')) d = d.slice(2);
    if (d.startsWith('966')) { d = d.slice(3); hadPlus = true; }
    if (d.startsWith('0') && d.length === 10) d = d.slice(1);
    if (d.length === 9 && d.startsWith('5')) return ['+966' + d, 'valid_sa'];
    if (hadPlus && d.length >= 8 && d.length <= 15 && !d.startsWith('5')) return ['+' + d, 'valid_intl'];
    return [null, 'invalid'];
  }

  function normalizeEmail(raw) {
    if (raw === null || raw === undefined || String(raw).trim() === '') return [null, 'missing'];
    const e = String(raw).trim().toLowerCase();
    return EMAIL_RE.test(e) ? [e, 'valid'] : [null, 'invalid'];
  }

  const has = (t, pats) => pats.some(p => rx(p).test(t));

  function parseFinancing(v) {
    const t = normalizeAr(v);
    if (!t) return 'unknown';
    if (has(t, ['موافق', 'معتمد', 'approved', 'pre-?approv'])) return 'financing_approved';
    if (has(t, ['قيد', 'تحت الاجراء', 'قدمت', 'in progress', 'applied', 'pending'])) return 'financing_in_progress';
    if (has(t, ['تمويل', 'قرض', 'بنك', 'financ', 'mortgage', 'loan'])) return 'financing_needed';
    if (has(t, ['كاش', 'نقد', 'cash', 'جاهز', 'ready', 'متوفر'])) return 'cash';
    return 'unknown';
  }
  function parseTimeline(v) {
    const t = normalizeAr(v);
    if (!t) return 'unknown';
    if (has(t, ['هذا المزاد', 'المزاد الحالي', 'فورا', 'الحين', 'الان', 'now', 'this auction', 'اشارك',
      'ادخل المزاد', 'immediately'])) return 'this_auction';
    if (has(t, ['شهر', 'قريب', 'within', 'soon', 'month'])) return 'within_3_months';
    if (has(t, ['لاحقا', 'بعدين', 'السنه الجايه', 'later', 'next year', 'مو مستعجل'])) return 'later';
    return 'unknown';
  }
  function parseBuyerType(v) {
    const t = normalizeAr(v);
    if (has(t, ['شركه', 'مؤسسه', 'company', 'corporate', 'business entity'])) return 'company';
    if (has(t, ['فرد', 'شخصي', 'individual', 'personal'])) return 'individual';
    return 'unknown';
  }
  function parsePurpose(v) {
    const t = normalizeAr(v);
    if (has(t, ['استثمار', 'تاجير', 'invest', 'rent'])) return 'investment';
    if (has(t, ['سكن', 'اسكن', 'residen', 'live', 'family', 'عائله'])) return 'residence';
    if (has(t, ['تجاري', 'نشاط', 'business', 'office', 'مكتب'])) return 'business';
    return 'unknown';
  }
  function parseYesNo(v, yes = 'yes', no = 'no') {
    const t = normalizeAr(v);
    if (!t) return 'unknown';
    if (has(t, [`^(لا|no|false|0)${BE}`, 'اول مره', 'first time', 'never', 'ما شاركت'])) return no;
    if (has(t, [`^(نعم|ايوه|ايه|اي|yes|true|1|اكيد|موافق)${BE}`, 'شاركت', 'agree'])) return yes;
    return 'unknown';
  }
  function parseContactMethod(v) {
    const t = normalizeAr(v);
    if (has(t, ['واتس', 'whats'])) return 'whatsapp';
    if (has(t, ['ايميل', 'بريد', 'email', 'mail'])) return 'email';
    if (has(t, ['اتصال', 'مكالمه', 'جوال', 'call', 'phone'])) return 'call';
    return 'unknown';
  }
  const CITY_MAP = [['Dammam', ['دمام', 'dammam']], ['Khobar', ['خبر', 'khobar']],
    ['Dhahran', ['ظهران', 'dhahran']], ['Qatif', ['قطيف', 'qatif']], ['Jubail', ['جبيل', 'jubail']],
    ['Riyadh', ['رياض', 'riyadh']], ['Jeddah', ['جده', 'jeddah']]];
  function parseCity(v) {
    const t = normalizeAr(v);
    if (!t) return null;
    for (const [city, pats] of CITY_MAP) if (has(t, pats)) return city;
    return String(v).trim();
  }

  const FIELD_ALIASES = {
    name: ['name', 'full_name', 'fullname', 'الاسم', 'الاسم الكامل', 'اسم'],
    first_name: ['first_name', 'firstname', 'الاسم الاول'],
    last_name: ['last_name', 'lastname', 'اسم العائله'],
    mobile: ['mobile', 'phone', 'phone_number', 'whatsapp', 'الجوال', 'رقم الجوال', 'الهاتف', 'جوال'],
    email: ['email', 'e-mail', 'البريد', 'البريد الالكتروني', 'الايميل'],
    city: ['city', 'المدينه'],
    asset_text: ['interested_asset', 'property', 'which_property_are_you_interested_in?', 'العقار',
      'العقار المهتم به', 'العقار المطلوب'],
    property_type_text: ['property_type', 'نوع العقار'],
    budget_text: ['budget', 'budget_range', 'الميزانيه', 'الميزانيه المتوقعه'],
    purpose_text: ['investment_purpose', 'purpose', 'الغرض', 'الغرض من الشراء'],
    buyer_type_text: ['buyer_type', 'individual_or_company', 'نوع المشتري', 'فرد او شركه'],
    experience_text: ['auction_experience', 'have_you_joined_an_auction_before?', 'خبره بالمزادات',
      'هل شاركت في مزاد سابقا'],
    financing_text: ['financing', 'payment_method', 'cash_or_financing', 'طريقه الدفع', 'التمويل'],
    timeline_text: ['timeline', 'purchase_timeline', 'when_do_you_plan_to_buy?', 'متي تنوي الشراء', 'موعد الشراء'],
    contact_text: ['preferred_contact', 'preferred_contact_method', 'طريقه التواصل'],
    consent_text: ['consent', 'marketing_consent', 'موافقه التواصل', 'اوافق علي التواصل'],
    notes: ['notes', 'message', 'comments', 'ملاحظات', 'رسالتك', 'استفسار'],
  };
  const ALIAS_LOOKUP = {};
  for (const [canon, aliases] of Object.entries(FIELD_ALIASES)) for (const a of aliases) ALIAS_LOOKUP[normalizeAr(a)] = canon;

  function mapFields(flat) {
    const out = {}, unmapped = {};
    for (const [k, v] of Object.entries(flat || {})) {
      const canon = ALIAS_LOOKUP[normalizeAr(k)];
      if (canon) out[canon] = v; else unmapped[k] = v;
    }
    return [out, unmapped];
  }

  const ADAPTERS = {
    landing_page(p) {
      const [fields, unmapped] = mapFields(p.fields || {});
      return { source_event_id: p.event_id ?? null, campaign: p.campaign ?? null,
        created_at: p.submitted_at ?? null, fields, unmapped };
    },
    meta_lead_ads(p) {
      const flat = {};
      for (const f of p.field_data || []) flat[f.name] = (f.values && f.values.length) ? f.values[0] : null;
      const [fields, unmapped] = mapFields(flat);
      return { source_event_id: p.id ?? null, campaign: p.campaign_name ?? null,
        created_at: p.created_time ?? null, fields, unmapped };
    },
    whatsapp(p) {
      const value = p.entry[0].changes[0].value;
      const msg = value.messages[0];
      const contact = (value.contacts || [{}])[0] || {};
      const ts = msg.timestamp;
      const created = ts ? new Date(parseInt(ts, 10) * 1000).toISOString().replace(/\.\d{3}Z$/, 'Z') : null;
      return { source_event_id: msg.id ?? null, campaign: p.referral_campaign ?? 'snapchat_ctwa',
        created_at: created,
        fields: { name: (contact.profile || {}).name ?? null, mobile: msg.from || contact.wa_id || null,
          notes: (msg.text || {}).body ?? null, contact_text: 'whatsapp' },
        unmapped: {} };
    },
  };

  // FNV-1a (two seeds) over UTF-8 -> 20 hex chars. Idempotency key, not a security hash.
  function fnvKey(str) {
    const bytes = unescape(encodeURIComponent(str));
    const h = (seed) => {
      let x = seed >>> 0;
      for (let i = 0; i < bytes.length; i++) { x ^= bytes.charCodeAt(i); x = Math.imul(x, 16777619) >>> 0; }
      return x.toString(16).padStart(8, '0');
    };
    return (h(2166136261) + h(84696351) + h(3735928559)).slice(0, 20);
  }

  function sortedJson(v) {
    if (Array.isArray(v)) return '[' + v.map(sortedJson).join(', ') + ']';
    if (v && typeof v === 'object') {
      return '{' + Object.keys(v).sort().map(k => JSON.stringify(k) + ': ' + sortedJson(v[k])).join(', ') + '}';
    }
    return JSON.stringify(v);
  }

  function eventKey(source, sourceEventId, payload) {
    const basis = sourceEventId ? `${source}:${sourceEventId}` : `${source}:${sortedJson(payload)}`;
    return fnvKey(basis);
  }

  function emptyLead() {
    const lead = {};
    for (const k of VALUE_FIELDS) lead[k] = null;
    for (const k of ENUM_FIELDS) lead[k] = 'unknown';
    Object.assign(lead, { budget_raw: null, budget_status: 'unknown', budget_approx: false,
      budget_flexible_up: false, mobile_status: 'missing', email_status: 'missing',
      flags: { human_requested: false, price_only: false, contact_later: false, opt_out: false,
        not_interested: false },
      confidence: {}, history: [], issues: [], sources: [] });
    return lead;
  }

  function leadKey(lead) {
    if (lead.mobile) return `${AUCTION_ID}:${lead.mobile}`;
    if (lead.email) return `${AUCTION_ID}:${lead.email}`;
    return `${AUCTION_ID}:anon:${lead.event_key}`;
  }

  function normalizeLead(source, payload, receivedAt = null) {
    if (!ADAPTERS[source]) throw new Error(`unknown source: ${source}`);
    const a = ADAPTERS[source](payload);
    const f = a.fields;
    const ts = receivedAt || nowIso();
    const lead = emptyLead();
    Object.assign(lead, { auction_id: AUCTION_ID, source, first_source: source, sources: [source],
      campaign: a.campaign, source_event_id: a.source_event_id,
      event_key: eventKey(source, a.source_event_id, payload),
      created_at: a.created_at || ts, last_interaction_at: ts,
      message_text: f.notes ?? null, unmapped_fields: a.unmapped });

    const name = f.name || [f.first_name, f.last_name].filter(Boolean).join(' ');
    lead.name = name ? name.replace(/\s+/g, ' ').trim() : null;
    [lead.mobile, lead.mobile_status] = normalizePhone(f.mobile);
    if (lead.mobile_status === 'invalid') lead.issues.push(`invalid_mobile:${f.mobile}`);
    [lead.email, lead.email_status] = normalizeEmail(f.email);
    if (lead.email_status === 'invalid') lead.issues.push(`invalid_email:${f.email}`);
    lead.city = parseCity(f.city);

    const assetSrc = [f.asset_text, f.property_type_text].filter(Boolean).map(String).join(' ');
    if (assetSrc) {
      const m = matchAsset(assetSrc);
      lead.interested_asset = m.asset_id; lead.property_type = m.property_type;
      if (m.property_type) lead.confidence.asset = m.confidence;
      else lead.issues.push(`unmatched_asset:${assetSrc}`);
    }
    if (f.budget_text) {
      const b = parseBudget(f.budget_text);
      Object.assign(lead, { budget_raw: f.budget_text, budget_min: b.min, budget_max: b.max,
        budget_status: b.status, budget_approx: b.approx, budget_flexible_up: b.flexible_up });
      lead.confidence.budget = b.confidence;
    }
    lead.investment_purpose = parsePurpose(f.purpose_text);
    lead.buyer_type = parseBuyerType(f.buyer_type_text);
    lead.auction_experience = parseYesNo(f.experience_text);
    lead.financing = parseFinancing(f.financing_text);
    lead.timeline = parseTimeline(f.timeline_text);
    lead.preferred_contact = parseContactMethod(f.contact_text);
    lead.consent = parseYesNo(f.consent_text, 'granted', 'denied');
    if (source === 'whatsapp' && lead.consent === 'unknown') {
      lead.consent = 'granted'; lead.confidence.consent = 0.8;
    }
    for (const e of ['financing', 'timeline']) if (lead[e] !== 'unknown') lead.confidence[e] = 0.9;
    lead.lead_key = leadKey(lead);
    return lead;
  }

  function isKnown(field, value) {
    if (ENUM_FIELDS.includes(field) || field === 'budget_status') return value !== null && value !== undefined && value !== 'unknown';
    return value !== null && value !== undefined && value !== '' && value !== false;
  }

  function mergeLeads(existing, incoming) {
    const merged = JSON.parse(JSON.stringify(existing));
    const changed = [];
    for (const field of MERGE_FIELDS) {
      const nw = incoming[field], old = merged[field];
      if (!isKnown(field, nw) || nw === old) continue;
      if (isKnown(field, old)) {
        merged.history.push({ field, old, new: nw, at: incoming.last_interaction_at ?? null,
          source: incoming.source ?? null });
        changed.push(field);
      }
      merged[field] = nw;
    }
    for (const [k, v] of Object.entries(incoming.confidence || {})) merged.confidence[k] = v;
    for (const [k, v] of Object.entries(incoming.flags || {})) merged.flags[k] = Boolean(merged.flags[k]) || v;
    merged.sources = [...new Set([...(merged.sources || []), ...(incoming.sources || [])])];
    merged.source = incoming.source ?? null;
    merged.created_at = [merged.created_at, incoming.created_at].sort()[0];
    merged.last_interaction_at = [merged.last_interaction_at, incoming.last_interaction_at].sort()[1];
    merged.issues = [...new Set([...merged.issues, ...(incoming.issues || [])])];
    if (incoming.message_text) merged.message_text = incoming.message_text;
    if (merged.mobile_status !== 'valid_sa' && (incoming.mobile_status || '').startsWith('valid')) {
      merged.mobile_status = incoming.mobile_status;
    }
    merged.changed_fields = changed;
    return merged;
  }

  // ----------------------------------------------------------------- scoring
  const CONFIDENCE_THRESHOLD = 0.6;
  const CORE_FIELDS = ['asset', 'budget', 'financing', 'timeline'];
  const READY_FUNDS = ['cash', 'financing_approved'];
  const PRIORITY_ORDER = { P1: 0, P2: 1, P3: 2, Disqualified: 3 };
  const READINESS_ORDER = { cash: 0, financing_approved: 1, financing_in_progress: 2, financing_needed: 3, unknown: 4 };
  const FIN_AR = { cash: 'السيولة جاهزة (كاش)', financing_approved: 'تمويل معتمد',
    financing_in_progress: 'التمويل قيد الإجراء', financing_needed: 'يحتاج تمويل',
    unknown: 'الجاهزية المالية غير معروفة' };
  const FIN_EN = { cash: 'cash ready', financing_approved: 'financing approved',
    financing_in_progress: 'financing in progress', financing_needed: 'needs financing', unknown: 'funding unknown' };
  const TL_AR = { this_auction: 'يرغب بالمشاركة في هذا المزاد', within_3_months: 'خلال 3 أشهر',
    later: 'لاحقاً', unknown: 'التوقيت غير معروف' };
  const FIELD_AR = { asset: 'العقار', budget: 'الميزانية', financing: 'التمويل', timeline: 'التوقيت', intent: 'الجدية' };
  const TL_EN = { this_auction: 'wants to bid in this auction', within_3_months: 'within 3 months',
    later: 'later', unknown: 'timeline unknown' };

  const conf = (lead, f) => (lead.confidence && f in lead.confidence) ? lead.confidence[f] : 1.0;

  function budgetFit(lead) {
    if (lead.budget_status !== 'known') return ['unknown', null];
    const band = bandFor(lead.interested_asset, lead.property_type);
    let ceiling = lead.budget_max || lead.budget_min;
    if (lead.budget_flexible_up && ceiling) ceiling *= 1.1;
    const refMin = band ? band.min : LOWEST_BAND_MIN;
    if (ceiling >= refMin) return ['covers', band];
    if (ceiling >= 0.7 * refMin) return ['partial', band];
    return ['below', band];
  }

  function missingCore(lead) {
    const m = [];
    if (!lead.interested_asset && !lead.property_type) m.push('asset');
    if (lead.budget_status !== 'known') m.push('budget');
    if ((lead.financing || 'unknown') === 'unknown') m.push('financing');
    if ((lead.timeline || 'unknown') === 'unknown') m.push('timeline');
    return m;
  }

  function lowConfidence(lead) {
    const c = lead.confidence || {};
    return [...CORE_FIELDS, 'intent'].filter(f => f in c && c[f] < CONFIDENCE_THRESHOLD);
  }

  function scoreLead(lead) {
    const flags = lead.flags || {};
    const b = {};
    b.asset_fit = lead.interested_asset ? 20 : (lead.property_type ? 12 : 0);
    const [fit, band] = budgetFit(lead);
    b.budget_fit = ({ covers: 25, partial: 12 })[fit] || 0;
    b.financial_readiness = ({ cash: 20, financing_approved: 20, financing_in_progress: 10, financing_needed: 5 })[lead.financing] || 0;
    b.timeline = ({ this_auction: 15, within_3_months: 8, later: 3 })[lead.timeline] || 0;
    b.intent = ({ high: 15, medium: 8, low: 2 })[lead.intent] || 0;
    const contactable = (lead.mobile_status || '').startsWith('valid') && lead.consent !== 'denied';
    b.contactability = contactable ? 5 : 0;
    const score = Object.values(b).reduce((s, v) => s + v, 0);
    const missing = missingCore(lead);
    const uncertain = lowConfidence(lead);

    let dq = null;
    if (flags.opt_out || lead.consent === 'denied') dq = ['Customer asked not to be contacted', 'طلب العميل عدم التواصل'];
    else if (flags.not_interested) dq = ['Customer stated no interest', 'أفاد العميل بعدم اهتمامه'];
    else if (!(lead.mobile_status || '').startsWith('valid') && !lead.email) dq = ['No valid mobile or email to contact', 'لا يوجد رقم جوال أو بريد صالح للتواصل'];
    else if (fit === 'below' && conf(lead, 'budget') >= 0.7 && (lead.budget_max || lead.budget_min) < 0.5 * LOWEST_BAND_MIN) {
      dq = ['Confirmed budget far below every asset in this auction', 'الميزانية المؤكدة أقل بكثير من جميع أصول المزاد'];
    }

    const notes = [];
    let priority, status;
    if (dq) { priority = 'Disqualified'; status = 'disqualified'; }
    else {
      const p1 = score >= 70 && READY_FUNDS.includes(lead.financing) && lead.timeline === 'this_auction'
        && (lead.interested_asset || lead.property_type) && ['covers', 'partial'].includes(fit) && !uncertain.length;
      if (p1) priority = 'P1';
      else if (score >= 45) {
        priority = 'P2';
        if (score >= 70 && !READY_FUNDS.includes(lead.financing)) notes.push(['held at P2: financing unresolved', 'بقي P2: التمويل غير محسوم']);
      } else priority = 'P3';
      if (flags.price_only && lead.intent !== 'high' && priority !== 'P3') {
        priority = 'P3'; notes.push(['capped at P3: price questions only', 'P3: يسأل عن الأسعار فقط']);
      }
      if (flags.contact_later && priority !== 'P3') {
        priority = 'P3';
      }
      if (flags.human_requested && priority === 'P3') {
        priority = 'P2'; notes.push(['raised to P2: asked for a person', 'رُفع إلى P2: طلب موظف']);
      }
      if (flags.human_requested) status = 'handoff_requested';
      else if (uncertain.length) status = 'needs_review';
      else if (missing.length) status = 'in_qualification';
      else status = 'qualified';
    }
    const result = { score, breakdown: b, budget_fit: fit, priority, qualification_status: status,
      missing_fields: missing, uncertain_fields: uncertain, disqualify_reason: dq ? dq[0] : null };
    [result.reason_en, result.reason_ar] = buildReason(lead, result, band, dq, notes);
    return result;
  }

  function assetText(lead, lang) {
    if (lead.interested_asset) return assetLabel(lead.interested_asset, lang);
    if (TYPE_LABELS[lead.property_type]) {
      const [ar, en] = TYPE_LABELS[lead.property_type];
      return lang === 'ar' ? ar + ' (بدون تحديد الحي)' : en + ' (district not chosen)';
    }
    return lang === 'ar' ? 'العقار غير محدد' : 'asset unknown';
  }

  function buildReason(lead, r, band, dq, notes) {
    const p = r.priority;
    if (dq) return [`${p}: ${dq[0]}.`, `${p}: ${dq[1]}.`];
    const budgetEn = formatBudget({ status: lead.budget_status, min: lead.budget_min, max: lead.budget_max,
      flexible_up: lead.budget_flexible_up });
    const fitEn = { covers: 'fits', partial: 'slightly below band', below: 'below band', unknown: 'not given' }[r.budget_fit];
    const fitAr = { covers: 'مناسبة', partial: 'أقل قليلاً من المتوقع', below: 'أقل من المتوقع', unknown: 'غير معروفة' }[r.budget_fit];
    const known = lead.budget_status === 'known';
    const en = [`${p} (${r.score}/100)`, assetText(lead, 'en'),
      known ? `budget ${budgetEn} (${fitEn})` : 'budget unknown',
      FIN_EN[lead.financing || 'unknown'], TL_EN[lead.timeline || 'unknown']];
    const budgetArVal = budgetEn.replace('up to', 'حتى').replace('from', 'من').replace('SAR', 'ريال')
      .replace(' (flexible up)', ' (قابلة للزيادة)');
    const ar = [`${p} (${r.score}/100)`, assetText(lead, 'ar'),
      known ? `الميزانية ${budgetArVal} (${fitAr})` : 'الميزانية غير معروفة',
      FIN_AR[lead.financing || 'unknown'], TL_AR[lead.timeline || 'unknown']];
    if ((lead.flags || {}).human_requested) {
      en.splice(1, 0, 'ASKED FOR A PERSON - call now');
      ar.splice(1, 0, 'طلب التحدث مع موظف - اتصل الآن');
    }
    if ((lead.flags || {}).contact_later) { en.push('asked to be contacted later'); ar.push('طلب التواصل لاحقاً'); }
    if (r.uncertain_fields.length) {
      en.push('needs review: ' + r.uncertain_fields.join(', '));
      ar.push('يحتاج مراجعة: ' + r.uncertain_fields.map(f => FIELD_AR[f]).join('، '));
    } else if (r.missing_fields.length) {
      en.push('missing: ' + r.missing_fields.join(', '));
      ar.push('ناقص: ' + r.missing_fields.map(f => FIELD_AR[f]).join('، '));
    }
    if ((lead.history || []).some(h => h.field === 'budget_min' || h.field === 'budget_max')) {
      en.push('budget was revised'); ar.push('تم تعديل الميزانية');
    }
    for (const n of notes) { en.push(n[0]); ar.push(n[1]); }
    return [en.join(' | '), ar.join(' | ')];
  }

  function rankQueue(rows) {
    const key = r => [PRIORITY_ORDER[r.priority] ?? 9, (r.flags || {}).human_requested ? 0 : 1,
      READINESS_ORDER[r.financing || 'unknown'] ?? 4, -(r.score || 0),
      -(budgetMid({ min: r.budget_min, max: r.budget_max }) || 0)];
    const ranked = [...rows].sort((a, b) => {
      const ka = key(a), kb = key(b);
      for (let i = 0; i < ka.length; i++) if (ka[i] !== kb[i]) return ka[i] - kb[i];
      const la = a.last_interaction_at || '', lb = b.last_interaction_at || '';
      if (la !== lb) return la < lb ? 1 : -1;               // newer interaction first
      const ca = a.created_at || '', cb = b.created_at || '';
      return ca < cb ? -1 : ca > cb ? 1 : 0;
    });
    ranked.forEach((r, i) => { r.rank = i + 1; });
    return ranked;
  }

  // ------------------------------------------------------------- agent state
  const ALLOWED = {
    financing: ['cash', 'financing_approved', 'financing_in_progress', 'financing_needed', 'unknown'],
    timeline: ['this_auction', 'within_3_months', 'later', 'unknown'],
    buyer_type: ['individual', 'company', 'unknown'],
    investment_purpose: ['residence', 'investment', 'business', 'unknown'],
    auction_experience: ['yes', 'no', 'unknown'],
    preferred_contact: ['call', 'whatsapp', 'email', 'unknown'],
    intent: ['high', 'medium', 'low', 'unknown'],
  };
  const ENUM_SLOTS = ['financing', 'timeline', 'buyer_type', 'investment_purpose', 'auction_experience', 'preferred_contact'];
  const FLAG_KEYS = ['human_requested', 'price_only', 'contact_later', 'opt_out', 'not_interested'];
  const SLOT_ORDER = ['asset', 'budget', 'financing', 'timeline', 'buyer_type'];
  const HUMAN_PATTERNS = ['موظف', 'شخص حقيقي', 'احد يكلمني', 'اكلم احد', 'اكلم شخص',
    'مندوب', 'خدمه العملاء', 'agent', 'human', 'real person'];
  const OPTOUT_PATTERNS = ['لا تتصلوا', 'لا تراسلوني', 'وقفوا الرسائل', 'الغاء الاشتراك', 'stop messaging', 'unsubscribe'];
  const QUESTIONS_AR = {
    asset: 'أي عقار من عقارات المزاد يهمك أكثر؟ (فلل، عمائر سكنية أو تجارية، أراضٍ تجارية، أو شقة في الدمام والخبر)',
    budget: 'كم الميزانية التقريبية اللي تفكر فيها؟',
    financing: 'بتكون عملية الشراء كاش ولا عن طريق تمويل بنكي؟',
    timeline: 'ناوي تدخل المزاد الحالي، ولا تفكر بالشراء لاحقاً؟',
    buyer_type: 'الشراء بيكون باسمك الشخصي ولا باسم شركة؟',
  };
  const HANDOFF_REPLY_AR = 'أبشر، سجلت طلبك وبيتواصل معك أحد مستشاري المبيعات في أقرب وقت. شكراً لتواصلك مع فنا.';
  const PRICE_POLICY_AR = 'الأسعار في المزاد تتحدد بالمزايدة، وفريق المبيعات يقدر يزودك بتفاصيل كل عقار وشروط الدخول.';

  function containsEvidence(evidence, text) {
    if (!evidence) return false;
    const e = normalizeAr(evidence);
    return e.length >= 2 && normalizeAr(text).includes(e);
  }
  const isNum = v => typeof v === 'number' && Number.isFinite(v);

  function setField(lead, field, value, at, source = 'agent') {
    const old = lead[field] === undefined ? null : lead[field];
    if (value === old) return false;
    const knownOld = !(old === null || old === 'unknown' || old === '' || old === false);
    if (knownOld) (lead.history = lead.history || []).push({ field, old, new: value, at: at ?? null, source });
    lead[field] = value;
    return knownOld;
  }

  function applyAiExtraction(lead, ai, text, at = null) {
    const report = { accepted: [], rejected: [], changed: [] };
    if (!ai || typeof ai !== 'object' || Array.isArray(ai) ||
        (ai.extracted !== undefined && ai.extracted !== null && (typeof ai.extracted !== 'object' || Array.isArray(ai.extracted)))) {
      report.rejected.push('malformed_output');
      return [lead, report];
    }
    const ex = ai.extracted || {};
    const conf = lead.confidence = lead.confidence || {};
    const accept = (slot, item) => {
      if (!item || typeof item !== 'object' || [null, undefined, '', 'unknown'].includes(item.value)) return null;
      if (!containsEvidence(item.evidence, text)) { report.rejected.push(`no_evidence:${slot}`); return null; }
      return isNum(item.confidence) ? item.confidence : 0.0;
    };
    const a = ex.interested_asset;
    let c = accept('asset', a);
    if (c !== null) {
      const val = a.value;
      if (ASSET_BY_ID[val]) {
        if (setField(lead, 'interested_asset', val, at)) report.changed.push('interested_asset');
        setField(lead, 'property_type', ASSET_BY_ID[val].type, at);
        conf.asset = c; report.accepted.push('asset');
      } else if (TYPE_BUDGET_BANDS[val]) {
        if (lead.interested_asset && ASSET_BY_ID[lead.interested_asset].type !== val) setField(lead, 'interested_asset', null, at);
        if (setField(lead, 'property_type', val, at)) report.changed.push('property_type');
        conf.asset = c; report.accepted.push('asset');
      } else report.rejected.push(`unknown_asset:${val}`);
    }
    const b = ex.budget;
    if (b && typeof b === 'object' && b.raw) {
      if (containsEvidence(b.raw, text) || containsEvidence(b.evidence, text)) {
        const parsed = parseBudget(b.raw);
        const llmC = isNum(b.confidence) ? b.confidence : 0;
        if (parsed.status === 'known') {
          let changed = false;
          for (const [f, v] of [['budget_min', parsed.min], ['budget_max', parsed.max]]) changed = setField(lead, f, v, at) || changed;
          Object.assign(lead, { budget_status: 'known', budget_raw: b.raw, budget_approx: parsed.approx,
            budget_flexible_up: parsed.flexible_up });
          conf.budget = Math.min(parsed.confidence, Math.max(llmC, 0.5));
          report.accepted.push('budget');
          if (changed) report.changed.push('budget');
        } else { conf.budget = 0.4; report.rejected.push('budget_uninterpretable'); }
      } else report.rejected.push('no_evidence:budget');
    }
    for (const slot of ENUM_SLOTS) {
      const item = ex[slot];
      c = accept(slot, item);
      if (c === null) continue;
      if (!ALLOWED[slot].includes(item.value)) { report.rejected.push(`invalid_value:${slot}`); continue; }
      if (setField(lead, slot, item.value, at)) report.changed.push(slot);
      if (slot === 'financing' || slot === 'timeline') conf[slot] = c;
      report.accepted.push(slot);
    }
    const it = ai.intent || {};
    if (it && typeof it === 'object' && ALLOWED.intent.includes(it.value) && it.value !== 'unknown') {
      lead.intent = it.value; conf.intent = isNum(it.confidence) ? it.confidence : 0.0;
    }
    const t = normalizeAr(text);
    const flags = lead.flags = lead.flags || {};
    const aiFlags = ai.flags || {};
    for (const k of FLAG_KEYS) if (aiFlags[k] === true) flags[k] = true;
    if (HUMAN_PATTERNS.some(p => t.includes(p))) flags.human_requested = true;
    if (OPTOUT_PATTERNS.some(p => t.includes(p))) flags.opt_out = true;
    if (report.changed.length) lead.changed_fields = [...new Set([...(lead.changed_fields || []), ...report.changed])];
    return [lead, report];
  }

  function nextSlots(lead, limit = 2) {
    const c = lead.confidence || {};
    const miss = {
      asset: !lead.interested_asset && !lead.property_type,
      budget: lead.budget_status !== 'known',
      financing: (lead.financing || 'unknown') === 'unknown',
      timeline: (lead.timeline || 'unknown') === 'unknown',
      buyer_type: (lead.buyer_type || 'unknown') === 'unknown',
    };
    return SLOT_ORDER.filter(s => miss[s] || (s in c ? c[s] : 1.0) < 0.6).slice(0, limit);
  }

  function knownSummary(lead) {
    const out = {};
    if (lead.name) out.name = lead.name;
    if (lead.interested_asset) out.asset = assetLabel(lead.interested_asset);
    else if (lead.property_type) out.property_type = lead.property_type;
    if (lead.budget_status === 'known') out.budget = { min: lead.budget_min, max: lead.budget_max, raw: lead.budget_raw };
    for (const s of ENUM_SLOTS) if ((lead[s] || 'unknown') !== 'unknown') out[s] = lead[s];
    return out;
  }

  function agentContext(lead) {
    return { known: knownSummary(lead), ask_next: nextSlots(lead), catalog: catalogForPrompt(), flags: lead.flags || {} };
  }

  const AMOUNT_RE = '(\\d[\\d,\\.]*)\\s*(مليون|ملايين|الف|الاف|ريال|million|k|sar)';
  function checkReply(reply, text) {
    if (!reply || typeof reply !== 'string') return { ok: false, violations: ['empty_reply'] };
    const violations = [];
    const cust = normalizeAr(toWestern(text ?? ''));
    for (const m of normalizeAr(toWestern(reply)).matchAll(rx(AMOUNT_RE, 'g'))) {
      if (!cust.includes(m[1])) violations.push(`unsupported_amount:${m[0]}`);
    }
    if (rx('(سعر الافتتاح|السعر المبدئي|يبدا من|يبدأ من|opening price)').test(reply)) violations.push('price_claim');
    return { ok: !violations.length, violations };
  }

  function fallbackReply(lead) {
    const flags = lead.flags || {};
    if (flags.human_requested) return HANDOFF_REPLY_AR;
    const slots = nextSlots(lead, 1);
    const parts = [];
    if (flags.price_only) parts.push(PRICE_POLICY_AR);
    parts.push(slots.length ? QUESTIONS_AR[slots[0]] : 'شكراً لك، بيتواصل معك فريق المبيعات قريباً بإذن الله.');
    return parts.join(' ');
  }

  return { AUCTION_ID, ASSETS, ASSET_BY_ID, TYPE_BUDGET_BANDS, normalizeAr, assetLabel, matchAsset,
    bandFor, catalogForPrompt, parseBudget, budgetMid, formatBudget, normalizePhone, normalizeEmail,
    normalizeLead, mergeLeads, emptyLead, leadKey, eventKey, scoreLead, rankQueue, nowIso,
    parseFinancing, parseTimeline, CONFIDENCE_THRESHOLD, applyAiExtraction, nextSlots, knownSummary,
    agentContext, checkReply, fallbackReply };
})();
if (typeof module !== 'undefined' && module.exports) module.exports = FANA;
