import { useEffect, useState } from "react";
import { api, SourceInfo, timeAgo } from "../api";
import { Spinner } from "../components";

export default function Sources() {
  const [sources, setSources] = useState<SourceInfo[] | null>(null);
  useEffect(() => { api.sources().then((s) => setSources(s.sources)).catch(() => setSources([])); }, []);
  if (!sources) return <div className="p-8 text-center"><Spinner /></div>;
  const ok = sources.filter((s) => s.enabled && s.last_success && !s.last_error).length;
  const enabled = sources.filter((s) => s.enabled).length;
  return (
    <div>
      <h1 className="text-xl font-bold">Sources</h1>
      <p className="mb-4 text-sm muted">
        {ok}/{enabled} enabled sources healthy on the last run. Add or remove sources in <code>backend/sources.yaml</code>.
      </p>
      <div className="card overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead className="border-b border-slate-200 text-xs uppercase muted dark:border-slate-800">
            <tr><th className="p-3">Source</th><th className="p-3">Group</th><th className="p-3">Status</th><th className="p-3 text-right">Last fetch</th><th className="p-3 text-right">Stored</th></tr>
          </thead>
          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {sources.map((s) => {
              const state = !s.enabled ? "disabled" : !s.last_attempt ? "pending" : s.last_error ? "error" : "ok";
              return (
                <tr key={s.id} className={s.enabled ? "" : "opacity-50"}>
                  <td className="p-3">
                    <a href={s.url} target="_blank" rel="noreferrer" className="font-medium hover:underline">{s.name}</a>
                    <div className="text-xs muted">{s.type}{s.note ? ` · ${s.note}` : ""}</div>
                  </td>
                  <td className="p-3 muted">{s.group}</td>
                  <td className="p-3">
                    <span className={`chip ${state === "ok" ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-300"
                      : state === "error" ? "bg-rose-100 text-rose-800 dark:bg-rose-500/15 dark:text-rose-300"
                      : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300"}`}>{state}</span>
                    {s.last_error && <div className="mt-1 max-w-xs truncate text-xs text-rose-600" title={s.last_error}>{s.last_error}</div>}
                  </td>
                  <td className="p-3 text-right muted whitespace-nowrap">
                    {s.last_attempt ? `${timeAgo(s.last_attempt)} · ${s.last_item_count} items` : "never"}
                  </td>
                  <td className="p-3 text-right tabular-nums">{s.total_items}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
