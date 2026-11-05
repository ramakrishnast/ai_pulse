import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, Story, timeAgo } from "../api";
import { BookmarkIcon, CategoryChip, Empty, Importance, Spinner, useStoryFlags } from "../components";

type Related = { id: number; title: string; category: string; importance: number; last_seen: string };

function Detail({ story, related }: { story: Story; related: Related[] }) {
  const flags = useStoryFlags(story);
  useEffect(() => { flags.toggleRead(true); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  const ents = Object.entries(story.entities).filter(([, v]) => v.length);
  return (
    <div className="grid gap-6 lg:grid-cols-3">
      <article className="card p-5 lg:col-span-2">
        <div className="mb-3 flex flex-wrap items-center gap-2 text-xs">
          <CategoryChip category={story.category} />
          <Importance value={story.importance} enriched={story.enriched} />
          <span className="muted">first seen {timeAgo(story.first_seen)} · {story.source_count} source{story.source_count > 1 ? "s" : ""}</span>
          <button className="btn ml-auto !py-1" onClick={flags.toggleBookmark}>
            <span className={flags.bookmarked ? "text-amber-500" : ""}><BookmarkIcon filled={flags.bookmarked} /></span>
            {flags.bookmarked ? "Saved" : "Save"}
          </button>
          <button className="btn !py-1" onClick={() => flags.toggleRead()}>{flags.read ? "Mark unread" : "Mark read"}</button>
        </div>
        <h1 className="text-2xl font-bold leading-tight">{story.title}</h1>
        {story.summary ? (
          <p className="mt-3 text-[15px] leading-relaxed text-slate-700 dark:text-slate-300">{story.summary}</p>
        ) : (
          <p className="mt-3 italic muted">No AI summary yet. Read the original coverage below.</p>
        )}
        {story.why_it_matters && (
          <div className="mt-4 rounded-lg border-l-4 border-indigo-500 bg-indigo-50 p-3 text-sm dark:bg-indigo-500/10">
            <span className="font-semibold">Why it matters for applied AI engineers: </span>{story.why_it_matters}
          </div>
        )}
        <h2 className="mb-2 mt-6 text-sm font-semibold uppercase tracking-wide muted">Coverage</h2>
        <ul className="divide-y divide-slate-100 dark:divide-slate-800">
          {story.sources.map((s) => (
            <li key={s.url} className="py-2.5 text-sm">
              <a href={s.url} target="_blank" rel="noreferrer" className="font-medium text-indigo-600 hover:underline dark:text-indigo-400">{s.title} ↗</a>
              <div className="muted text-xs">
                {s.source} · {timeAgo(s.published_at)}{s.points ? ` · ${s.points} points` : ""}
                {s.comments_url && s.comments_url !== s.url && <> · <a href={s.comments_url} target="_blank" rel="noreferrer" className="hover:underline">discussion</a></>}
              </div>
            </li>
          ))}
        </ul>
      </article>
      <aside className="space-y-4">
        {ents.length > 0 && (
          <div className="card p-4">
            <h2 className="mb-2 text-sm font-semibold">Mentioned</h2>
            {ents.map(([kind, names]) => (
              <div key={kind} className="mb-2 text-sm">
                <div className="text-xs uppercase muted">{kind === "company" ? "companies" : kind === "person" ? "people" : `${kind}s`}</div>
                <div className="mt-1 flex flex-wrap gap-1">
                  {names.map((n) => <Link key={n} to={`/?q=${encodeURIComponent(n)}`} className="chip bg-slate-100 dark:bg-slate-800">{n}</Link>)}
                </div>
              </div>
            ))}
          </div>
        )}
        <div className="card p-4">
          <h2 className="mb-2 text-sm font-semibold">Related stories</h2>
          {related.length === 0 && <p className="text-sm muted">None found.</p>}
          <ul className="space-y-2">
            {related.map((r) => (
              <li key={r.id} className="text-sm">
                <Link to={`/story/${r.id}`} className="hover:text-indigo-600 dark:hover:text-indigo-400">{r.title}</Link>
                <div className="text-xs muted">{r.category} · {timeAgo(r.last_seen)}</div>
              </li>
            ))}
          </ul>
        </div>
      </aside>
    </div>
  );
}

export default function StoryPage() {
  const { id } = useParams();
  const [data, setData] = useState<{ story: Story; related: Related[] } | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    setData(null);
    api.story(id!).then(setData).catch((e) => setErr(String(e)));
  }, [id]);
  if (err) return <Empty>Story not found.</Empty>;
  if (!data) return <div className="p-8 text-center"><Spinner /></div>;
  return (
    <div>
      <Link to="/" className="mb-4 inline-block text-sm muted hover:underline">← Back to today</Link>
      <Detail key={data.story.id} story={data.story} related={data.related} />
    </div>
  );
}
