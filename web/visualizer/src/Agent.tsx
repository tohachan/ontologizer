// The Agent tab (docs/visualizer-agent.md §2, §5): the harness, its sign-in, one session per browser tab, the chat.
import { useCallback, useEffect, useRef, useState } from 'react';
import { agentApi, ApiFailure } from './api';
import { HARNESS_LABEL, type AgentView, type Harness, type HarnessName, type Login, type ViewEvent } from './agentTypes';
import { Chat, type Chip } from './Chat';
import { recall, remember } from './remember';

export const HARNESS_KEY = 'visualizer.harness';
const NAMES = ['claude', 'codex'] as const;
export const LOGIN_POLL_MS = 1000;

type Props = {
  owner: string | null;
  view: () => AgentView;
  onView: (id: string, ev: ViewEvent) => void;
  onUndo: (id: string) => void;
  picked: Chip[]; onUnpick: (id: string) => void;
  onSettings: () => void;                                 // show the Settings tab
};

const say = (e: unknown) => (e instanceof ApiFailure ? `${e.message} (${e.code})` : String(e));

export function Agent(p: Props) {
  const [harnesses, setHarnesses] = useState<Harness[] | null>(null);
  const [off, setOff] = useState<{ disabled: boolean; message: string } | null>(null);
  const [harness, setHarness] = useState<HarnessName>(() => recall(HARNESS_KEY, NAMES, 'claude'));
  const [session, setSession] = useState<string | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [epoch, setEpoch] = useState(0);                  // a page back from the back-forward cache needs a new session

  const refresh = useCallback(() => agentApi.harnesses().then(
    (r) => { setHarnesses(r.harnesses); setOff(null); },
    (e) => setOff({ disabled: e instanceof ApiFailure && e.code === 'agent-disabled', message: say(e) })), []);
  useEffect(() => { void refresh(); }, [refresh]);

  const h = harnesses?.find((x) => x.name === harness);
  const ready = !!p.owner && !!h?.installed && !!h.signedIn;

  // One session per owner and harness; ended when either changes, or the page goes.
  useEffect(() => {
    if (!ready || !p.owner) return;
    let id: string | null = null;
    let gone = false;
    setSessionError(null);
    agentApi.createSession(p.owner, harness).then((r) => {
      if (gone) { agentApi.endSession(r.sessionId).catch(() => {}); return; }
      id = r.sessionId; setSession(r.sessionId);
    }, (e) => { if (!gone) setSessionError(say(e)); });
    const leave = () => { if (id) { agentApi.endSessionOnLeave(id); id = null; setSession(null); } };
    const back = (e: PageTransitionEvent) => { if (e.persisted) setEpoch((n) => n + 1); };
    window.addEventListener('pagehide', leave);
    window.addEventListener('pageshow', back);
    return () => {
      gone = true;
      window.removeEventListener('pagehide', leave);
      window.removeEventListener('pageshow', back);
      setSession(null);
      if (id) agentApi.endSession(id).catch(() => {});
    };
  }, [ready, p.owner, harness, epoch]);

  const choose = (n: HarnessName) => { setHarness(n); remember(HARNESS_KEY, n); };
  const toSettings = <button type="button" onClick={p.onSettings}>Open Settings</button>;

  if (off) {
    return (
      <div className="agent-off" aria-label="agent off">
        <p><b>{off.disabled ? 'The agent is off.' : 'The agent does not answer.'}</b>{off.disabled ? '' : ` ${off.message}`}</p>
        <p>To turn it on, set <code>visualizer.agent.enabled: true</code> in the hub’s <code>ontologizer.local.yaml</code>,
          then restart the visualizer.</p>
        <p>The Settings tab builds views without it. {toSettings}</p>
      </div>
    );
  }
  if (!harnesses) return <p className="muted small">Looking for the agent…</p>;
  const label = HARNESS_LABEL[harness];
  return (
    <div className="agent">
      <label className="row small">Agent <select aria-label="harness" value={harness} onChange={(e) => choose(e.target.value as HarnessName)}>
        {NAMES.map((n) => {
          const x = harnesses.find((y) => y.name === n);
          return <option key={n} value={n}>{HARNESS_LABEL[n]}{!x?.installed ? ' (not installed)' : !x.signedIn ? ' (not signed in)' : ''}</option>;
        })}
      </select></label>
      {!h?.installed ? (
        <div className="agent-off" aria-label="harness missing">
          <p><b>{label} is not installed</b> where the visualizer runs.</p>
          <p>Install its CLI on that machine (the <code>make viz-up</code> image has both), or choose the other agent.
            The agent must also be on: <code>visualizer.agent.enabled: true</code> in the hub’s <code>ontologizer.local.yaml</code>, then restart.</p>
          <p>The Settings tab builds views without it. {toSettings}</p>
        </div>
      ) : !h.signedIn ? (
        <SignIn key={harness} name={harness} onDone={() => void refresh()} />
      ) : !p.owner ? (
        <p className="muted small">Choose an owner to talk about its graphs.</p>
      ) : sessionError ? (
        <p className="chat-error">Cannot start a session: {sessionError}</p>
      ) : session ? (
        <Chat key={session} sessionId={session} view={p.view} onView={p.onView} onUndo={p.onUndo} picked={p.picked} onUnpick={p.onUnpick} />
      ) : <p className="muted small">Starting {label}…</p>}
    </div>
  );
}

// Sign-in from the page (§5): the service runs the CLI's own login and shows its URL and code here.
function SignIn({ name, onDone }: { name: HarnessName; onDone: () => void }) {
  const [loginId, setLoginId] = useState<string | null>(null);
  const [st, setSt] = useState<Login | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [code, setCode] = useState('');
  const done = useRef(onDone);
  done.current = onDone;

  useEffect(() => {
    if (!loginId) return;
    let alive = true;
    let t: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const s = await agentApi.loginState(loginId);
        if (!alive) return;
        setSt(s);
        if (s.state === 'done') { done.current(); return; }
        if (s.state === 'failed') return;
      } catch (e) {
        if (alive) setErr(say(e));
        return;
      }
      t = setTimeout(() => void tick(), LOGIN_POLL_MS);
    };
    void tick();
    return () => { alive = false; clearTimeout(t); };
  }, [loginId]);

  const start = () => {
    setErr(null); setSt(null); setCode('');
    agentApi.login(name).then((r) => setLoginId(r.loginId), (e) => setErr(say(e)));
  };
  const cancel = () => {
    if (loginId) agentApi.cancelLogin(loginId).catch(() => {});
    setLoginId(null); setSt(null); setCode('');
  };
  const sendCode = () => {
    if (!loginId || !code.trim()) return;
    agentApi.loginCode(loginId, code.trim()).then((r) => {
      setCode('');
      setSt((s) => (s ? { ...s, state: r.state } : s));
      if (r.state === 'done') done.current();
    }, (e) => setErr(say(e)));
  };
  const label = HARNESS_LABEL[name];
  const active = loginId && st?.state !== 'failed' && st?.state !== 'done';
  return (
    <div className="signin" aria-label="sign in">
      <p className="small">{label} is not signed in on the machine the visualizer runs on.</p>
      {!active && <button type="button" className="primary" onClick={start}>Sign in to {label}</button>}
      {active && !st?.url && <p className="muted small">Starting the sign-in…</p>}
      {active && st?.url && (
        <p className="small">Open <a href={st.url} target="_blank" rel="noopener noreferrer">{st.url}</a> and sign in.</p>
      )}
      {active && st?.userCode && <p className="small">Code: <code className="user-code" aria-label="user code">{st.userCode}</code></p>}
      {active && st?.state === 'enter-code' && (
        <form className="row" onSubmit={(e) => { e.preventDefault(); sendCode(); }}>
          <input aria-label="sign-in code" value={code} onChange={(e) => setCode(e.target.value)} placeholder="paste the code" />
          <button type="submit" disabled={!code.trim()}>Send code</button>
        </form>
      )}
      {active && <button type="button" onClick={cancel}>Cancel</button>}
      {st?.state === 'failed' && <p className="chat-error">Sign-in failed{st.message ? `: ${st.message}` : '.'}</p>}
      {st?.message && st.state !== 'failed' && <p className="muted small">{st.message}</p>}
      {err && <p className="chat-error">{err}</p>}
    </div>
  );
}
