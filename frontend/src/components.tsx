import { useState } from "react";
import { Link } from "react-router-dom";
import { api, CATEGORY_STYLES, Story, timeAgo } from "./api";

export function CategoryChip({ category }: { category: string }) {
  return <span className={`chip ${CATEGORY_STYLES[category] || "bg-slate-100 text-slate-700"}`}>{category}</span>;
}

export function Importance({ value, enriched }: { value: number; enriched: boolean }) {
  if (!enriched) return <span className="chip bg-slate-100 text-slate-500 dark:bg-slate-800">unscored</span>;
  return (
    <span className="inline-flex items-center gap-0.5" title={`Importance ${value}/5`} aria-label={`Importance ${value} of 5`}>
      {[1, 2, 3, 4, 5].map((i) => (
        <span key={i} className={`h-1.5 w-3 rounded-full ${i <= value ? "bg-indigo-500" : "bg-slate-200 dark:bg-slate-700"}`} />
      ))}
    </span>
  );
}

export function useStoryFlags(story: Story) {
  const [bookmarked, setBookmarked] = useState(story.bookmarked);
  const [read, setRead] = useState(story.read);
  return {
    bookmarked,
    read,
    toggleBookmark: async () => {
      const v = !bookmarked;
      setBookmarked(v);
      try { await api.bookmark(story.id, v); } catch { setBookmarked(!v); }
    },
    toggleRead: async (value?: boolean) => {
      const v = value ?? !read;
      if (v === read) return;
      setRead(v);
      try { await api.markRead(story.id, v); } catch { setRead(!v); }
    },
  };
}

export function BookmarkIcon({ filled }: { filled: boolean }) {
  return (
    <svg viewBox="0 0 24 24" className="h-4 w-4" fill={filled ? "currentColor" : "none"} stroke="currentColor" strokeWidth={2}>
      <path d="M6 3h12v18l-6-4-6 4z" strokeLinejoin="round" />
    </svg>
  );
}

export function StoryCard({ story, compact = false }: { story: Story; compact?: boolean }) {
  const flags = useStoryFlags(story);
  const outlets = Array.from(new Set(story.sources.map((s) => s.source)));
  return (
    <article className={`card p-4 transition ${flags.read ? "opacity-60" : ""}`}>
      <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
        <CategoryChip category={story.category} />
        <Importance value={story.importance} enriched={story.enriched} />
        <span className="muted">{timeAgo(story.last_seen)}</span>
        {story.source_count > 1 && <span className="chip bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300">{story.source_count} sources</span>}
        <span className="ml-auto flex gap-1">
          <button className="rounded p-1 hover:bg-slate-100 dark:hover:bg-slate-800" onClick={flags.toggleBookmark}
            title={flags.bookmarked ? "Remove bookmark" : "Save"} aria-label="Save story">
            <span className={flags.bookmarked ? "text-amber-500" : "muted"}><BookmarkIcon filled={flags.bookmarked} /></span>
          </button>
          <button className="rounded px-1.5 py-1 text-xs hover:bg-slate-100 dark:hover:bg-slate-800 muted" onClick={() => flags.toggleRead()}
            title="Toggle read">{flags.read ? "Unread" : "Read"}</button>
        </span>
      </div>
      <Link to={`/story/${story.id}`} onClick={() => flags.toggleRead(true)} className="block">
        <h3 className="font-semibold leading-snug hover:text-indigo-600 dark:hover:text-indigo-400">{story.title}</h3>
      </Link>
      {!compact && (story.summary ? (
        <p className="mt-1.5 text-sm text-slate-700 dark:text-slate-300">{story.summary}</p>
      ) : (
        <p className="mt-1.5 text-sm italic muted">AI summary not generated yet. Open the source links below.</p>
      ))}
      {!compact && story.why_it_matters && (
        <p className="mt-2 text-sm"><span className="font-medium text-indigo-600 dark:text-indigo-400">Why it matters: </span>{story.why_it_matters}</p>
      )}
      <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1 text-xs">
        {story.sources.slice(0, 5).map((s) => (
          <a key={s.url} href={s.url} target="_blank" rel="noreferrer" className="text-indigo-600 hover:underline dark:text-indigo-400"
            onClick={() => flags.toggleRead(true)}>
            {s.source}{s.points ? ` · ${s.points}▲` : ""} ↗
          </a>
        ))}
        {outlets.length > 5 && <span className="muted">+{outlets.length - 5} more</span>}
      </div>
    </article>
  );
}

export function Spinner() {
  return <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-indigo-500 border-t-transparent" />;
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="card p-8 text-center text-sm muted">{children}</div>;
}
