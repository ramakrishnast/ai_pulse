export type SourceLink = {
  source_id: string;
  source: string;
  group: string;
  url: string;
  title: string;
  published_at: string;
  points: number;
  comments_url: string | null;
};

export type Story = {
  id: number;
  title: string;
  summary: string;
  category: string;
  importance: number;
  why_it_matters: string;
  enriched: boolean;
  first_seen: string;
  last_seen: string;
  source_count: number;
  rank_score: number;
  bookmarked: boolean;
  read: boolean;
  entities: Record<string, string[]>;
  sources: SourceLink[];
};

export type Run = {
  id: number;
  date: string;
  status: "running" | "ok" | "error";
  stage: string;
  trigger: string;
  started_at: string;
  finished_at: string | null;
  stats: Record<string, any>;
  error: string;
};

export type Status = {
  today: string;
  started_run: boolean;
  running: boolean;
  has_run_today: boolean;
  run: Run | null;
  llm: { provider: string; model: string; available: boolean };
  counts: { stories: number; items: number };
  categories: string[];
};

export type Digest = {
  date: string;
  headline: string;
  model: string;
  created_at: string;
  top: { line: string; story: Story }[];
  topics: { title: string; angle: string; story_ids: number[] }[];
};

export type TrendRow = { id: number; name: string; kind: string; this_week: number; last_week: number; delta: number; growth: number };
export type Trends = {
  rising: TrendRow[];
  top_companies: TrendRow[];
  top_models: TrendRow[];
  top_people: TrendRow[];
  top_topics: TrendRow[];
  categories: { category: string; this_week: number; last_week: number }[];
  series: { days: string[]; lines: { id: number; name: string; kind: string; counts: number[] }[] };
};

export type Citation = { n: number; story_id: number; title: string; date: string; links: { source: string; url: string; title: string }[] };
export type AskResult = { answer: string; citations: Citation[]; llm: boolean };

export type SourceInfo = {
  id: string; name: string; type: string; group: string; url: string; enabled: boolean; note: string;
  total_items: number; last_success: string | null; last_attempt: string | null; last_error: string; last_item_count: number;
};

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export const api = {
  status: () => req<Status>("/api/status"),
  refresh: () => req<{ started: boolean }>("/api/refresh", { method: "POST" }),
  digest: () => req<{ digest: Digest | null }>("/api/digest"),
  stories: (params: Record<string, string | number | boolean | undefined>) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== "" && v !== false && v !== 0) q.set(k, String(v));
    });
    return req<{ total: number; stories: Story[] }>(`/api/stories?${q}`);
  },
  story: (id: number | string) =>
    req<{ story: Story; related: { id: number; title: string; category: string; importance: number; last_seen: string }[] }>(
      `/api/stories/${id}`,
    ),
  bookmark: (id: number, value: boolean) =>
    req<{ bookmarked: boolean }>(`/api/stories/${id}/bookmark`, { method: "POST", body: JSON.stringify({ value }) }),
  markRead: (id: number, value: boolean) =>
    req<{ read: boolean }>(`/api/stories/${id}/read`, { method: "POST", body: JSON.stringify({ value }) }),
  trends: () => req<Trends>("/api/trends"),
  ask: (question: string) => req<AskResult>("/api/ask", { method: "POST", body: JSON.stringify({ question }) }),
  sources: () => req<{ sources: SourceInfo[] }>("/api/sources"),
};

export const CATEGORY_STYLES: Record<string, string> = {
  "Models & Releases": "bg-indigo-100 text-indigo-800 dark:bg-indigo-500/15 dark:text-indigo-300",
  Research: "bg-sky-100 text-sky-800 dark:bg-sky-500/15 dark:text-sky-300",
  "Agents & Tools": "bg-emerald-100 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-300",
  "Funding & Business": "bg-amber-100 text-amber-800 dark:bg-amber-500/15 dark:text-amber-300",
  "Policy & Safety": "bg-rose-100 text-rose-800 dark:bg-rose-500/15 dark:text-rose-300",
  "Infrastructure & Chips": "bg-violet-100 text-violet-800 dark:bg-violet-500/15 dark:text-violet-300",
  "Open Source": "bg-teal-100 text-teal-800 dark:bg-teal-500/15 dark:text-teal-300",
};

export function timeAgo(iso: string): string {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
