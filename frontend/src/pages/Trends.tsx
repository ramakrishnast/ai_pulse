import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, TrendRow, Trends as TrendsT } from "../api";
import { Empty, Spinner } from "../components";

const COLORS = ["#4f46e5", "#0891b2", "#059669", "#d97706", "#e11d48", "#7c3aed", "#0d9488", "#64748b"];

function RankList({ title, rows }: { title: string; rows: TrendRow[] }) {
  const max = Math.max(1, ...rows.map((r) => r.this_week));
  return (
    <div className="card p-4">
      <h2 className="mb-3 text-sm font-semibold">{title}</h2>
      {rows.length === 0 && <p className="text-sm muted">No data yet.</p>}
      <ul className="space-y-2">
        {rows.slice(0, 10).map((r) => (
          <li key={r.id} className="text-sm">
            <div className="flex items-center gap-2">
              <Link to={`/?q=${encodeURIComponent(r.name)}`} className="truncate hover:text-indigo-600 dark:hover:text-indigo-400">{r.name}</Link>
              <span className="ml-auto tabular-nums">{r.this_week}</span>
              <span className={`w-10 text-right text-xs tabular-nums ${r.delta > 0 ? "text-emerald-600" : r.delta < 0 ? "text-rose-500" : "muted"}`}>
                {r.delta > 0 ? `+${r.delta}` : r.delta}
              </span>
            </div>
            <div className="mt-1 h-1.5 rounded-full bg-slate-100 dark:bg-slate-800">
              <div className="h-1.5 rounded-full bg-indigo-500" style={{ width: `${(r.this_week / max) * 100}%` }} />
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function Trends() {
  const [t, setT] = useState<TrendsT | null>(null);
  const [kind, setKind] = useState("company");
  useEffect(() => { api.trends().then(setT).catch(() => {}); }, []);
  if (!t) return <div className="p-8 text-center"><Spinner /></div>;
  if (!t.rising.length && !t.top_companies.length && !t.top_topics.length)
    return <Empty>Trends appear after a few days of data. Check back tomorrow.</Empty>;

  const lines = t.series.lines.filter((l) => l.kind === kind);
  const chartData = t.series.days.map((d, i) => ({
    day: d.slice(5),
    ...Object.fromEntries(lines.map((l) => [l.name, l.counts[i]])),
  }));

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-bold">Trends</h1>
        <p className="text-sm muted">Mentions across stories: last 7 days vs the 7 days before.</p>
      </div>
      <div className="card p-4">
        <h2 className="mb-3 text-sm font-semibold">🔥 Rising this week</h2>
        {t.rising.length === 0 ? <p className="text-sm muted">Nothing is clearly rising yet.</p> : (
          <div className="flex flex-wrap gap-2">
            {t.rising.map((r) => (
              <Link key={r.id} to={`/?q=${encodeURIComponent(r.name)}`}
                className="rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-1.5 text-sm hover:bg-emerald-100 dark:border-emerald-900 dark:bg-emerald-500/10">
                <span className="font-medium">{r.name}</span>
                <span className="ml-2 text-xs text-emerald-700 dark:text-emerald-400">{r.last_week} → {r.this_week}</span>
                <span className="ml-1 text-xs muted">{r.kind}</span>
              </Link>
            ))}
          </div>
        )}
      </div>
      <div className="card p-4">
        <div className="mb-3 flex items-center gap-2">
          <h2 className="text-sm font-semibold">Daily mentions (14 days)</h2>
          <div className="ml-auto flex gap-1">
            {["company", "model", "topic"].map((k) => (
              <button key={k} onClick={() => setKind(k)} className={`rounded-md px-2 py-1 text-xs ${kind === k ? "bg-indigo-600 text-white" : "btn !py-1"}`}>
                {k === "company" ? "Companies" : k === "model" ? "Models" : "Topics"}
              </button>
            ))}
          </div>
        </div>
        <div className="h-64">
          <ResponsiveContainer>
            <LineChart data={chartData} margin={{ left: -20, right: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#94a3b833" />
              <XAxis dataKey="day" fontSize={11} stroke="#94a3b8" />
              <YAxis allowDecimals={false} fontSize={11} stroke="#94a3b8" />
              <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              {lines.map((l, i) => <Line key={l.id} type="monotone" dataKey={l.name} stroke={COLORS[i % COLORS.length]} strokeWidth={2} dot={false} />)}
            </LineChart>
          </ResponsiveContainer>
        </div>
      </div>
      <div className="grid gap-4 md:grid-cols-3">
        <RankList title="Most-mentioned companies" rows={t.top_companies} />
        <RankList title="Most-mentioned models" rows={t.top_models} />
        <RankList title="Top topics" rows={t.top_topics} />
      </div>
      <div className="card p-4">
        <h2 className="mb-3 text-sm font-semibold">Stories by category</h2>
        <div className="h-64">
          <ResponsiveContainer>
            <BarChart data={t.categories} layout="vertical" margin={{ left: 40 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#94a3b833" />
              <XAxis type="number" allowDecimals={false} fontSize={11} stroke="#94a3b8" />
              <YAxis type="category" dataKey="category" width={130} fontSize={11} stroke="#94a3b8" />
              <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Bar dataKey="last_week" name="Last week" fill="#94a3b8" radius={[0, 4, 4, 0]} />
              <Bar dataKey="this_week" name="This week" fill="#4f46e5" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  );
}
