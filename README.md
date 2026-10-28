# Ghost-Intel — a sovereign, defensive competitor-intelligence radar that runs 100% on free tiers

Ghost-Intel watches **public** competitor pages — pricing, changelogs, RSS —
detects what changed over time, scans public job feeds for leads, and summarizes
everything with free AI. It runs on GitHub Actions, stores its history in a local
SQLite file, sends a Telegram digest, and publishes a static dashboard on GitHub
Pages. **No paid server. No database service. No API bill.**

It is polite and honest by design: a truthful User-Agent, robots.txt obedience,
real rate limits, and a hard **stop** (never evasion) when a host pushes back.
See [SECURITY.md](SECURITY.md) — those bright lines are enforced in code and
pinned by the test suite.

---

## What it does

- **Monitors public competitor pages** — pricing pages, public changelogs,
  public RSS/Atom feeds. Whatever an anonymous browser can see, without logging in.
- **Detects price & content changes over time** — every fetch is a timestamped
  snapshot in SQLite (content hash, extracted prices, readable text). Consecutive
  snapshots are diffed; price changes and word-level content diffs are recorded.
- **Scans public job feeds for leads** — flags posts whose title/description
  match your keywords. It's a **lead hint**, not a guaranteed client.
- **Summarizes with free AI (optional)** — Groq free tier, a local Ollama model,
  or a deterministic offline stub. If AI is unreachable, the raw facts are still
  delivered; the pipeline never depends on it.
- **Telegram digest** — a readable daily/on-demand summary of what changed,
  what was skipped, and why.
- **Static HTML dashboard** — a single self-contained page (inline SVG price
  sparklines, no CDN, no build step) rendered from the store and published free
  on GitHub Pages.

## Architecture

```
 GitHub Actions cron (every 4h, free on public repos)
        |
        v
 polite Python crawler (stdlib-only)
   +-- robots.txt checked per host (Disallow honored; 401/403/429 on
   |   robots.txt itself = host is not welcoming => skip whole host)
   +-- per-host throttle (min interval, Crawl-delay honored, per-run budget)
   +-- truthful UA "Ghost-Intel/1.0 (+repo; mailto:contact)" — never a browser
        |
        v
 SQLite time-series store (local file, 0600, the source of truth)
   targets / snapshots(ts, hash, prices, text) / changes(ts, kind, detail)
        |
        v
 diff: hash changed? prices changed? word-level added/removed?
        |
        v
 optional AI summary (Groq free tier | Ollama | offline stub)
        |
        +----------------------+----------------------+
        v                      v                      v
 Telegram digest      static HTML dashboard    commit store+dashboard
 (optional, needs      (docs/index.html)        back to the repo so
  bot token)           -> GitHub Pages           history persists
```

## Quickstart (offline, zero installs)

Ghost-Intel is **Python standard library only**. There is nothing to `pip
install` — clone the repo and run it:

```bash
git clone https://github.com/aoodiakdiro0001-ops/aodi-ghost-intel
cd aodi-ghost-intel

# 1. create the SQLite store and load the sample targets
python -m ghost_intel.cli init --sample examples/targets.json

# 2. add your own public targets (pricing / changelog / RSS)
python -m ghost_intel.cli add "Acme Pricing" https://acme.example/pricing --kind price

# 3. crawl politely (first run is the baseline; changes appear from run 2)
python -m ghost_intel.cli scan

# 4. build the digest (add --send if TELEGRAM_TOKEN + TELEGRAM_CHAT_ID are set)
python -m ghost_intel.cli digest

# 5. render the static dashboard (default: dashboard/index.html)
python -m ghost_intel.cli dashboard

# 6. environment self-check
python -m ghost_intel.cli doctor
```

Run the offline test suite (no network, injected fakes):

```bash
python -m unittest discover -s tests -v
```

## The free stack

| Layer            | Free choice                                   | Cost  |
|------------------|-----------------------------------------------|-------|
| Scheduler        | GitHub Actions cron (`23 */4 * * *`, ~90 min/mo on a public repo) | $0    |
| Hosting (dash)   | GitHub Pages (`docs/` folder)                 | $0    |
| Storage          | Local SQLite file, committed back to the repo | $0    |
| AI (optional)    | Groq free tier / local Ollama / offline stub  | $0    |
| Alerts           | Telegram Bot API                             | $0    |
| Runtime          | Python 3 standard library — zero dependencies | $0    |

## Responsible use / compliance — read this first

Ghost-Intel observes **public pages, politely, as a guest**. That is the entire
scope. The following bright lines are enforced **in code**, not just in prose,
and are pinned by `tests/test_ghost.py`:

**It always:**

- sends a **single, truthful User-Agent** identifying the tool, its repo, and a
  contact email — it is *not* overridable to impersonate a browser;
- **obeys `robots.txt`** (absent robots = allow, per convention; `Disallow` is
  honored; `Crawl-delay` is honored, only ever raised);
- **rate-limits per host** — a real minimum interval and a hard per-run request
  budget;
- fetches **public, non-authenticated pages only** — what an anonymous visitor
  can see.

**It never:**

- **logs in**, or touches anything behind a **login, paywall, or CAPTCHA** —
  no bypass, no solving, no circumvention;
- performs **TLS/JA3/JA4 fingerprint spoofing** or browser-mimic header tricks;
- **rotates identity** (UA, IP, or proxy) to evade a block — a `401`/`403`/
  `429`/`503` is a **STOP signal** for that host, not a challenge;
- uses ambient system proxies, so its receipts reflect the real network path;
- claims "undetectable", "100% success", or anything it doesn't do. Reports
  state exactly what happened, including skips, blocks, and errors.

Monitor only pages you are **entitled** to observe: your own sites, and public
competitor pages and public feeds. You are responsible for complying with each
target's Terms of Service and applicable law. Full policy, data handling, and
vulnerability reporting: **[SECURITY.md](SECURITY.md)**.

## Honest limits

- **A lead hint is not a client.** The leads scanner flags matching public job
  posts so *you* can follow up. It guarantees nothing.
- **AI is optional and best-effort.** The hard facts (hashes, prices, diffs)
  are computed offline; the AI layer only turns them into a sentence and may
  fail (falls back to the deterministic stub).
- **Free tiers have quotas.** GitHub Actions minutes (unlimited on public repos,
  capped on private ones), Groq rate limits, Telegram's message limits. The 4h
  cadence is chosen to stay comfortably inside them.
- **Local SQLite is the source of truth.** There is no remote warehouse; a
  remote DSN fails loudly rather than pretending. Persist the file (the Actions
  workflow commits it back) or bring your own adapter.
- **Detection is heuristic.** Price extraction is regex-based and content diffs
  are word-level; expect occasional false positives (e.g. a number that isn't a
  price) and review the dashboard before acting.

---

## القسم العربي

### ما هو غوست-إنтел؟

**رادار استخبارات منافسة دفاعي سيادي يعمل بالكامل على طبقات مجانية** — لا خادم
مدفوع، لا قاعدة بيانات سحابية، لا فاتورة API. كل شيء بايثون قياسي (المكتبة
القياسية فقط)، بدون أي `pip install`.

### ماذا يفعل؟

- **مراقبة صفحات منافسين عامة** — صفحات الأسعار، سجلات التغييرات (changelog)،
  وتغذية RSS العامة. فقط ما يراه زائر مجهول الاسم، بدون تسجيل دخول.
- **رصد تغيّر الأسعار والمحتوى عبر الزمن** — كل زيارة تُحفظ كـ«لقطة» موقّعة
  الزمن في قاعدة بيانات SQLite محلية (بصمة محتوى، الأسعار المستخرجة، النص
  المقروء)، ثم تُقارن اللقطات المتعاقبة بالفرق (diff).
- **مسح الوظائف العامة بحثًا عن عملاء محتملين** — يُعلِّم المنشورات التي
  تتضمن كلماتك المفتاحية. هذه **تلميحة** لعميل محتمل، وليست عقدًا مضمونًا.
- **تلخيص مجاني بالذكاء الاصطناعي (اختياري)** — Groq (الطبقة المجانية)، أو
  نموذج محلي عبر Ollama، أو ملخص قواعد محلي يعمل دون إنترنت. إن تعذّر الوصول
  للذكاء الاصطناعي تصل الحقائق الخام كاملة.
- **ملخص تلغرام** — تقرير مرتّب عن التغييرات وما تم تخطّيه ولماذا.
- **لوحة تحكم HTML ثابتة** — صفحة واحدة ذاتية الاحتواء (رسوم SVG مضمّنة، بلا
  CDN) تُنشر مجانًا على GitHub Pages.

### المعمارية

```
GitHub Actions (كل 4 ساعات، مجانًا)
      |
      v
زاحف مهذب بايثون (يتحقق من robots.txt + حدود سرعة لكل مضيف + هوية صادقة ثابتة)
      |
      v
قاعدة SQLite محلية (سلسلة زمنية: الهدف / اللقطات / التغييرات)
      |
      v
كشف الفروقات (بصمة المحتوى + الأسعار + الفروقات على مستوى الكلمات)
      |
      v
ملخّص ذكاء اصطناعي اختياري (Groq / Ollama / ملخص محلي)
      |
      +--------------------+--------------------+
      v                    v                    v
تلغرام               لوحة GitHub Pages    حفظ القاعدة في المستودع
```

### البدء السريع

```bash
python -m ghost_intel.cli init --sample examples/targets.json
python -m ghost_intel.cli add "أسعار أكامي" https://acme.example/pricing --kind price
python -m ghost_intel.cli scan
python -m ghost_intel.cli digest
python -m ghost_intel.cli dashboard
python -m ghost_intel.cli doctor
```

لا تحتاج إلى تثبيت أي شيء — الأداة تعمل بمكتبة بايثون القياسية فقط.

### الاستخدام المسؤول والامتثال — اقرأ هذا أولاً

غوست-إنтел **ضيف** على كل مضيف يزوره، ويُراقب **الصفحات العامة فقط**:

- **يستخدم هوية صادقة ثابتة** تعرّف الأداة ومستودعها وبريدًا للاستجابة، ولا
  يمكن تغييرها انتحالًا لمتصفح.
- **يطيع robots.txt** و«كراول ديلي» ويحترم حدود السرعة لكل مضيف.
- **النتيجة 401 أو 403 أو 429 تعني توقّفًا** — تُبرد المضيف ويتوقف، ولا تُدار
  الهوية أو العنوان أو الوكيل لتجاوز الحظر.
- **لا تسجيل دخول، ولا اختراق جدران الدفع أو CAPTCHA** إطلاقًا — خارج النطاق
  بتصميم لا بالصدفة.
- **لا تزوير بصمات TLS/JA3 ولا انتحال متصفح**.
- **لا ادعاءات كاذبة** — لا «غير قابل للاكتشاف» ولا «نجاح 100%». التقرير يذكر
  ما حدث فعلًا بما فيه التخطّي والأخطاء.

راقب فقط ما **له الحق** في مراقبته: مواقعك أنت، وصفحات منافسين عامة، وتغذية
عامة. أنت المسؤول عن الامتثال لشروط استخدام كل موقع وlocal law. التفاصيل
كاملة في [SECURITY.md](SECURITY.md).

### حدود صادقة

- تلميحات العملاء المحتملين **ليست عملاء مضمونين**.
- الذكاء الاصطناعي اختياري وقد يفشل — الحقائق الأساسية لا تعتمد عليه.
- الطبقات المجانية لها حصص؛ معدّل كل 4 ساعات مُختار للبقاء ضمنها.
- قاعدة SQLite المحلية هي مصدر الحقيقة الوحيد.
- كشف الأسعار نصّي (regex) وقد يُنتج أخطاء إيجابية من حين لآخر — راجع
  اللوحة قبل اتخاذ قرار.

---

## License & contact

MIT © 2026 Baran Avisat — Aodı Akdıro Suite ·
[baranavisat@gmail.com](mailto:baranavisat@gmail.com) ·
WhatsApp +90 531 326 37 22 ·
github.com/aoodiakdiro0001-ops
