import { useCallback, useEffect, useRef, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api, Status } from "./api";
import { Spinner } from "./components";
import Ask from "./pages/Ask";
import Sources from "./pages/Sources";
import StoryPage from "./pages/Story";
import Today from "./pages/Today";
import Trends from "./pages/Trends";

const STAGES: Record<string, string> = {
  fetching: "Fetching feeds",
  deduplicating: "Removing duplicates",
  clustering: "Grouping stories",
  enriching: "Summarizing with AI",
  digest: "Writing the daily briefing",
  done: "Done",
};

function ThemeToggle() {
  const [dark, setDark] = useState(document.documentElement.classList.contains("dark"));
  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
    try { localStorage.setItem("theme", dark ? "dark" : "light"); } catch { /* ignore */ }
  }, [dark]);
  return (
    <button className="btn !px-2" onClick={() => setDark(!dark)} aria-label="Toggle dark mode" title="Toggle theme">
      {dark ? "☀︎" : "☾"}
    </button>
  );
}

export default function App() {
  const [status, setStatus] = useState<Status | null>(null);
  const [version, setVersion] = useState(0); // bump to make pages reload data
  const [error, setError] = useState("");
  const wasRunning = useRef(false);

  const poll = useCallback(async () => {
    try {
      const s = await api.status();
      setStatus(s);
      setError("");
      if (wasRunning.current && !s.running) setVersion((v) => v + 1);
      wasRunning.current = s.running;
      return s;
    } catch (e) {
      setError(String(e));
      return null;
    }
  }, []);

  useEffect(() => {
    poll();
  }, [poll]);

  useEffect(() => {
    if (!status?.running) return;
    const t = setInterval(poll, 2500);
    return () => clearInterval(t);
  }, [status?.running, poll]);

  const refresh = async () => {
    await api.refresh();
    wasRunning.current = true;
    poll();
  };

  const run = status?.run;
  const nav = ({ isActive }: { isActive: boolean }) =>
    `rounded-lg px-3 py-1.5 text-sm font-medium ${isActive ? "bg-indigo-600 text-white" : "hover:bg-slate-100 dark:hover:bg-slate-800"}`;

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/85 backdrop-blur dark:border-slate-800 dark:bg-slate-950/85">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-2 px-4 py-2.5">
          <NavLink to="/" className="mr-2 flex items-center gap-2 font-bold">
            <span className="grid h-7 w-7 place-items-center rounded-lg bg-indigo-600 text-white">
              <svg viewBox="0 0 32 32" className="h-5 w-5"><path d="M4 17h5l3-8 5 15 3-7h8" stroke="white" strokeWidth="2.6" fill="none" strokeLinecap="round" strokeLinejoin="round" /></svg>
            </span>
            AI Pulse
          </NavLink>
          <nav className="order-3 flex w-full gap-1 overflow-x-auto sm:order-none sm:w-auto">
            <NavLink to="/" end className={nav}>Today</NavLink>
            <NavLink to="/saved" className={nav}>Saved</NavLink>
            <NavLink to="/trends" className={nav}>Trends</NavLink>
            <NavLink to="/ask" className={nav}>Ask</NavLink>
            <NavLink to="/sources" className={nav}>Sources</NavLink>
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <button className="btn" onClick={refresh} disabled={status?.running} title="Fetch and process the latest news now">
              {status?.running ? <Spinner /> : "↻"} <span className="hidden sm:inline">Refresh</span>
            </button>
            <ThemeToggle />
          </div>
        </div>
        {status?.running && run && (
          <div className="border-t border-indigo-100 bg-indigo-50 text-indigo-900 dark:border-indigo-900 dark:bg-indigo-950 dark:text-indigo-200">
            <div className="mx-auto flex max-w-6xl items-center gap-2 px-4 py-2 text-sm">
              <Spinner />
              <span>
                {run.trigger === "open" ? "Getting today's news" : "Refreshing"} · {STAGES[run.stage] || run.stage}
                {run.stats?.items_new !== undefined && ` · ${run.stats.items_new} new items from ${run.stats.sources_ok} sources`}
              </span>
              <span className="ml-auto hidden muted sm:inline">You can keep reading; the page updates when it finishes.</span>
            </div>
          </div>
        )}
        {status && !status.running && run?.status === "error" && (
          <div className="bg-rose-50 text-rose-800 dark:bg-rose-950 dark:text-rose-200">
            <div className="mx-auto max-w-6xl px-4 py-2 text-sm">Last run failed: {run.error}</div>
          </div>
        )}
        {error && (
          <div className="bg-rose-50 text-rose-800 dark:bg-rose-950 dark:text-rose-200">
            <div className="mx-auto max-w-6xl px-4 py-2 text-sm">Can't reach the AI Pulse server ({error}).</div>
          </div>
        )}
      </header>
      <main className="mx-auto max-w-6xl px-4 py-6">
        <Routes>
          <Route path="/" element={<Today key={`t${version}`} status={status} />} />
          <Route path="/saved" element={<Today key={`s${version}`} status={status} saved />} />
          <Route path="/story/:id" element={<StoryPage key={`d${version}`} />} />
          <Route path="/trends" element={<Trends key={`r${version}`} />} />
          <Route path="/ask" element={<Ask />} />
          <Route path="/sources" element={<Sources key={`x${version}`} />} />
        </Routes>
      </main>
      <footer className="mx-auto max-w-6xl px-4 pb-8 text-xs muted">
        {status && (
          <>
            {status.counts.stories} stories from {status.counts.items} articles ·{" "}
            {status.run?.finished_at ? `last updated ${new Date(status.run.finished_at).toLocaleString()}` : "not updated yet"} ·{" "}
            {status.llm.available ? `summaries by ${status.llm.model}` : "no LLM key configured: summaries off"}
            {status.llm.available && ` · LLM spend $${status.llm.budget.spent_usd.toFixed(3)} of $${status.llm.budget.cap_usd.toFixed(2)}`}
            {status.llm.available && status.llm.budget.exhausted && " (cap reached: summaries paused)"}
            {" · "}Summaries are AI-written paraphrases. Always check the original sources.
          </>
        )}
      </footer>
    </div>
  );
}
