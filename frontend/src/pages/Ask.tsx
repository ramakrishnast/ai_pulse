import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { api, AskResult } from "../api";
import { Spinner } from "../components";

type Turn = { q: string; a?: AskResult; error?: string };

const EXAMPLES = [
  "What new models were released this week?",
  "Anything important about AI agents or coding tools?",
  "Which AI companies raised money recently?",
];

function renderAnswer(text: string, a: AskResult) {
  const byN = new Map(a.citations.map((c) => [c.n, c]));
  return text.split(/(\[\d+\])/g).map((part, i) => {
    const m = part.match(/^\[(\d+)\]$/);
    const c = m && byN.get(Number(m[1]));
    if (!c) return <span key={i}>{part}</span>;
    return (
      <a key={i} href={c.links[0]?.url} target="_blank" rel="noreferrer" title={c.title}
        className="mx-0.5 rounded bg-indigo-100 px-1 text-xs font-semibold text-indigo-700 no-underline dark:bg-indigo-500/20 dark:text-indigo-300">
        {c.n}
      </a>
    );
  });
}

export default function Ask() {
  const [q, setQ] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);

  const submit = async (e?: FormEvent, question?: string) => {
    e?.preventDefault();
    const text = (question ?? q).trim();
    if (text.length < 3 || busy) return;
    setQ("");
    setBusy(true);
    setTurns((t) => [...t, { q: text }]);
    try {
      const a = await api.ask(text);
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, a } : x)));
    } catch (err) {
      setTurns((t) => t.map((x, i) => (i === t.length - 1 ? { ...x, error: String(err) } : x)));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto max-w-3xl">
      <h1 className="text-xl font-bold">Ask the news</h1>
      <p className="mb-4 text-sm muted">Answers come only from stories AI Pulse has stored, with links to the original sources.</p>
      {turns.length === 0 && (
        <div className="mb-4 flex flex-wrap gap-2">
          {EXAMPLES.map((ex) => <button key={ex} className="btn" onClick={() => submit(undefined, ex)}>{ex}</button>)}
        </div>
      )}
      <div className="space-y-5">
        {turns.map((t, i) => (
          <div key={i}>
            <div className="mb-2 ml-auto w-fit max-w-[85%] rounded-2xl rounded-br-sm bg-indigo-600 px-4 py-2 text-sm text-white">{t.q}</div>
            <div className="card p-4 text-sm">
              {!t.a && !t.error && <Spinner />}
              {t.error && <span className="text-rose-600">Something went wrong: {t.error}</span>}
              {t.a && (
                <>
                  <p className="whitespace-pre-wrap leading-relaxed">{renderAnswer(t.a.answer, t.a)}</p>
                  {t.a.citations.length > 0 && (
                    <ol className="mt-3 space-y-1.5 border-t border-slate-100 pt-3 text-xs dark:border-slate-800">
                      {t.a.citations.map((c) => (
                        <li key={c.n} className="flex gap-2">
                          <span className="font-semibold text-indigo-600 dark:text-indigo-400">[{c.n}]</span>
                          <span>
                            <Link to={`/story/${c.story_id}`} className="font-medium hover:underline">{c.title}</Link>
                            <span className="muted"> · {c.date} · </span>
                            {c.links.slice(0, 3).map((l, j) => (
                              <a key={l.url} href={l.url} target="_blank" rel="noreferrer" className="text-indigo-600 hover:underline dark:text-indigo-400">
                                {j > 0 && ", "}{l.source} ↗
                              </a>
                            ))}
                          </span>
                        </li>
                      ))}
                    </ol>
                  )}
                </>
              )}
            </div>
          </div>
        ))}
      </div>
      <form onSubmit={submit} className="sticky bottom-4 mt-6 flex gap-2">
        <input className="input !py-2.5 shadow-sm" value={q} onChange={(e) => setQ(e.target.value)}
          placeholder="Ask about this week's AI news…" aria-label="Question" maxLength={500} />
        <button className="btn-primary !px-4" disabled={busy || q.trim().length < 3}>Ask</button>
      </form>
    </div>
  );
}
