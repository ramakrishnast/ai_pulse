import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, Digest, SourceInfo, Status, Story } from "../api";
import { CategoryChip, Empty, Spinner, StoryCard } from "../components";

const CATEGORY_ORDER = [
  "Models & Releases", "Agents & Tools", "Research", "Open Source",
  "Infrastructure & Chips", "Funding & Business", "Policy & Safety",
];

function useDebounced<T>(value: T, ms = 300) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

function DigestPanel({ digest }: { digest: Digest }) {
  const [open, setOpen] = useState(true);
  return (
    <section className="card mb-6 overflow-hidden">
      <div className="flex items-center gap-2 border-b border-slate-200 bg-gradient-to-r from-indigo-600 to-violet-600 px-4 py-3 text-white dark:border-slate-800">
        <div>
          <div className="text-xs uppercase tracking-wide opacity-80">Daily briefing · {digest.date}</div>
          <h2 className="font-semibold">{digest.headline || "What matters today"}</h2>
        </div>
        <button className="ml-auto rounded-lg bg-white/15 px-2 py-1 text-xs hover:bg-white/25" onClick={() => setOpen(!open)}>
          {open ? "Collapse" : "Expand"}
        </button>
      </div>
      {open && (
        <div className="grid gap-6 p-4 lg:grid-cols-3">
          <ol className="space-y-2.5 lg:col-span-2">
            {digest.top.map((t, i) => (
              <li key={t.story.id} className="flex gap-3">
                <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full bg-indigo-100 text-xs font-bold text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-300">{i + 1}</span>
                <div className="text-sm">
                  <Link to={`/story/${t.story.id}`} className="font-medium hover:text-indigo-600 dark:hover:text-indigo-400">{t.story.title}</Link>{" "}
                  <CategoryChip category={t.story.category} />
                  <p className="muted">{t.line}</p>
                </div>
              </li>
            ))}
          </ol>
          <div>
            <h3 className="mb-2 text-sm font-semibold">✍️ Ideas to write about</h3>
            {digest.topics.length === 0 && <p className="text-sm muted">Not enough trend data yet.</p>}
            <ul className="space-y-3">
              {digest.topics.map((t) => (
                <li key={t.title} className="rounded-lg bg-slate-50 p-3 text-sm dark:bg-slate-800/60">
                  <div className="font-medium">{t.title}</div>
                  <p className="mt-1 muted">{t.angle}</p>
                  {t.story_ids.length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-2 text-xs">
                      {t.story_ids.map((id) => <Link key={id} to={`/story/${id}`} className="text-indigo-600 hover:underline dark:text-indigo-400">story #{id}</Link>)}
                    </div>
                  )}
                </li>
              ))}
            </ul>
            {digest.model === "heuristic" && <p className="mt-3 text-xs muted">Auto-generated without an LLM. Add an API key for a written briefing.</p>}
          </div>
        </div>
      )}
    </section>
  );
}

export default function Today({ status, saved = false }: { status: Status | null; saved?: boolean }) {
  const [digest, setDigest] = useState<Digest | null>(null);
  const [stories, setStories] = useState<Story[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [sources, setSources] = useState<SourceInfo[]>([]);
  const [params] = useSearchParams();
  const [q, setQ] = useState(params.get("q") || "");
  useEffect(() => { setQ(params.get("q") || ""); }, [params]);
  const [category, setCategory] = useState("");
  const [source, setSource] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [minImp, setMinImp] = useState(0);
  const [sort, setSort] = useState("rank");
  const [unread, setUnread] = useState(false);
  const [allTime, setAllTime] = useState(false);
  const [grouped, setGrouped] = useState(true);
  const dq = useDebounced(q);

  useEffect(() => {
    if (!saved) api.digest().then((d) => setDigest(d.digest)).catch(() => {});
    api.sources().then((s) => setSources(s.sources.filter((x) => x.enabled))).catch(() => {});
  }, [saved]);

  useEffect(() => {
    setLoading(true);
    api.stories({
      q: dq, category, source, date_from: dateFrom, date_to: dateTo, min_importance: minImp, sort,
      unread, bookmarked: saved, scope: allTime ? "all" : "today", limit: 300,
    })
      .then((r) => { setStories(r.stories); setTotal(r.total); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [dq, category, source, dateFrom, dateTo, minImp, sort, unread, saved, allTime]);

  const groups = useMemo(() => {
    const m = new Map<string, Story[]>();
    for (const s of stories) m.set(s.category, [...(m.get(s.category) || []), s]);
    return [...m.entries()].sort((a, b) => CATEGORY_ORDER.indexOf(a[0]) - CATEGORY_ORDER.indexOf(b[0]));
  }, [stories]);

  const filtersActive = q || category || source || dateFrom || dateTo || minImp || unread;
  const firstRunPending = !loading && status?.running && stories.length === 0;

  return (
    <div>
      {!saved && digest && !filtersActive && <DigestPanel digest={digest} />}

      <div className="card mb-5 p-3">
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-6">
          <input className="input lg:col-span-2" placeholder="Search stories, companies, models…" value={q}
            onChange={(e) => setQ(e.target.value)} aria-label="Search" />
          <select className="input" value={category} onChange={(e) => setCategory(e.target.value)} aria-label="Category">
            <option value="">All categories</option>
            {(status?.categories || CATEGORY_ORDER).map((c) => <option key={c}>{c}</option>)}
          </select>
          <select className="input" value={source} onChange={(e) => setSource(e.target.value)} aria-label="Source">
            <option value="">All sources</option>
            {sources.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          <select className="input" value={minImp} onChange={(e) => setMinImp(Number(e.target.value))} aria-label="Minimum importance">
            <option value={0}>Any importance</option>
            {[2, 3, 4, 5].map((n) => <option key={n} value={n}>Importance ≥ {n}</option>)}
          </select>
          <select className="input" value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Sort">
            <option value="rank">Sort: top</option>
            <option value="importance">Sort: importance</option>
            <option value="recency">Sort: newest</option>
          </select>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
          <label className="flex items-center gap-1.5">From <input type="date" className="input !w-auto !py-1" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} /></label>
          <label className="flex items-center gap-1.5">To <input type="date" className="input !w-auto !py-1" value={dateTo} onChange={(e) => setDateTo(e.target.value)} /></label>
          {!saved && <label className="flex items-center gap-1.5"><input type="checkbox" checked={allTime} onChange={(e) => setAllTime(e.target.checked)} /> Older stories too</label>}
          <label className="flex items-center gap-1.5"><input type="checkbox" checked={unread} onChange={(e) => setUnread(e.target.checked)} /> Unread only</label>
          <label className="flex items-center gap-1.5"><input type="checkbox" checked={grouped} onChange={(e) => setGrouped(e.target.checked)} /> Group by category</label>
          <span className="ml-auto muted">{loading ? <Spinner /> : `${total} stories`}</span>
        </div>
      </div>

      {firstRunPending && <Empty>Fetching today's AI news for the first time. This usually takes 1–3 minutes.</Empty>}
      {!loading && !status?.running && stories.length === 0 && (
        <Empty>{saved ? "No saved stories yet. Use the bookmark icon on a story." : "No stories match. Try other filters, tick “Older stories too”, or hit Refresh."}</Empty>
      )}

      {grouped ? (
        groups.map(([cat, list]) => (
          <section key={cat} className="mb-7">
            <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold uppercase tracking-wide muted">
              {cat} <span className="chip bg-slate-100 dark:bg-slate-800">{list.length}</span>
            </h2>
            <div className="grid gap-3 md:grid-cols-2">{list.map((s) => <StoryCard key={s.id} story={s} />)}</div>
          </section>
        ))
      ) : (
        <div className="grid gap-3 md:grid-cols-2">{stories.map((s) => <StoryCard key={s.id} story={s} />)}</div>
      )}
    </div>
  );
}
