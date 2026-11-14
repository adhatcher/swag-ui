import { useCallback, useEffect, useMemo, useState } from 'react'
import { Activity, ArrowRight, Bot, Check, ChevronRight, CircleHelp, FileCode2, KeyRound, LockKeyhole, LogOut, Network, RefreshCw, Save, Search, ServerCog, ShieldCheck, Sparkles, TriangleAlert, X } from 'lucide-react'

type Session = { authenticated: boolean; csrf_token?: string }
type Proxy = { name: string; active: boolean | null }
type Certificate = { name: string; expires_at: string | null }
type Overview = {
  proxies: Proxy[]
  certificates: Certificate[]
  tailscale: { state: string; detail?: string }
  swag: { state: string; auto_reload: string }
}
type ProxyFile = { name: string; content: string; sha256: string }
type Draft = { id: string; diff: string }
type Suggestion = { suggestion: string; proposed_content?: string }
type Screen = 'overview' | 'proxies' | 'integrations'

async function api<T>(path: string, method = 'GET', body?: unknown, csrf?: string): Promise<T> {
  const response = await fetch(path, {
    method,
    credentials: 'same-origin',
    headers: {
      ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
      ...(csrf ? { 'X-CSRF-Token': csrf } : {}),
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) {
    let detail = `Request failed (${response.status})`
    try {
      const data = await response.json() as { detail?: string; message?: string }
      detail = data.detail ?? data.message ?? detail
    } catch { /* A plain response still has a useful status. */ }
    throw new Error(detail)
  }
  return response.json() as Promise<T>
}

function StatusPill({ value, good }: { value: string; good?: boolean }) {
  return <span className={`status-pill ${good ? 'good' : ''}`}><span className="status-dot" />{value}</span>
}

function Header({ screen, setScreen, logout }: { screen: Screen; setScreen: (screen: Screen) => void; logout: () => void }) {
  const nav: { id: Screen; label: string; icon: typeof Activity }[] = [
    { id: 'overview', label: 'Overview', icon: Activity },
    { id: 'proxies', label: 'Proxy files', icon: FileCode2 },
    { id: 'integrations', label: 'Integrations', icon: Network },
  ]
  return <aside className="sidebar">
    <div className="brand"><div className="brand-mark"><span>∕</span><span>∕</span></div><div><strong>SWAG</strong><small>CONTROL</small></div></div>
    <div className="sidebar-label">WORKSPACE</div>
    <nav aria-label="Main navigation">
      {nav.map(({ id, label, icon: Icon }) => <button key={id} className={`nav-item ${screen === id ? 'selected' : ''}`} onClick={() => setScreen(id)} aria-current={screen === id ? 'page' : undefined}><Icon size={19} strokeWidth={1.8} /><span>{label}</span><ChevronRight className="nav-chevron" size={15} /></button>)}
    </nav>
    <div className="sidebar-bottom"><div className="sidebar-help"><CircleHelp size={19} /><div><strong>Configuration workspace</strong><span>Review drafts before activation.</span></div></div><button className="logout" onClick={logout}><LogOut size={17} /> Sign out</button></div>
  </aside>
}

function Login({ onLogin }: { onLogin: (password: string) => Promise<void> }) {
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  return <main className="login-page"><div className="login-art" aria-hidden="true"><div className="login-grid" /><div className="login-orbit"><div className="orbit-core"><ServerCog size={70} strokeWidth={1.1} /></div></div><div className="art-caption">YOUR EDGE, UNDER CONTROL <span>01 / 01</span></div></div><section className="login-panel"><div className="login-panel-inner"><div className="brand brand-dark"><div className="brand-mark"><span>∕</span><span>∕</span></div><div><strong>SWAG</strong><small>CONTROL</small></div></div><div className="eyebrow">SECURE ACCESS</div><h1>Welcome back<br />to your edge.</h1><p>Sign in to review proxy configuration, certificates, and deployment changes.</p><form onSubmit={async e => { e.preventDefault(); setBusy(true); setError(''); try { await onLogin(password) } catch (err) { setError(message(err)) } finally { setBusy(false) } }}><label htmlFor="password">Workspace password</label><div className="input-wrap"><LockKeyhole size={18} /><input id="password" type="password" autoComplete="current-password" required value={password} onChange={e => setPassword(e.target.value)} placeholder="Enter your password" /></div>{error && <div className="form-error" role="alert">{error}</div>}<button className="primary-button login-button" disabled={busy}>{busy ? 'Signing in…' : 'Open workspace'} <ArrowRight size={18} /></button></form><div className="login-foot"><ShieldCheck size={16} /> Local control for your SWAG deployment</div></div></section></main>
}

function message(error: unknown) { return error instanceof Error ? error.message : 'Something went wrong.' }
function dateLabel(date: string | null) {
  if (!date) return 'Unknown'
  const parsed = new Date(date)
  return Number.isNaN(parsed.getTime()) ? 'Unknown' : parsed.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}
function daysUntil(date: string | null) { if (!date) return null; const time = new Date(date).getTime(); return Number.isNaN(time) ? null : Math.ceil((time - Date.now()) / 86400000) }

function OverviewPage({ overview, openProxy, refresh, busy }: { overview: Overview; openProxy: (name: string) => void; refresh: () => void; busy: boolean }) {
  const active = overview.proxies.filter(p => p.active === true).length
  const unknown = overview.proxies.filter(p => p.active === null).length
  const soon = overview.certificates.filter(c => { const days = daysUntil(c.expires_at); return days !== null && days <= 30 }).length
  return <><div className="page-head"><div><div className="eyebrow">OPERATIONS / OVERVIEW</div><h1>Good to see you<span className="period">.</span></h1><p>One place to see what is running at your edge.</p></div><button className="outline-button" onClick={refresh} disabled={busy}><RefreshCw size={16} className={busy ? 'spin' : ''} /> Refresh status</button></div>
    <section className="hero-card" aria-label="SWAG status"><div className="hero-copy"><span className="hero-kicker"><span className="hero-live" /> SYSTEM PULSE</span><h2>Your proxy<br />at a glance.</h2><p>Configuration visibility for the services you publish through SWAG.</p><StatusPill value={`SWAG: ${overview.swag.state}`} good={overview.swag.state.toLowerCase() === 'running'} /></div><div className="hero-graphic" aria-hidden="true"><div className="graphic-ring ring-one" /><div className="graphic-ring ring-two" /><div className="graphic-ring ring-three" /><div className="graphic-center"><ServerCog size={46} strokeWidth={1.3} /></div><span className="graphic-node node-a" /><span className="graphic-node node-b" /><span className="graphic-node node-c" /></div></section>
    <div className="section-heading"><div><div className="eyebrow">THE CURRENT PICTURE</div><h2>Workspace status</h2></div></div>
    <div className="metrics"><div className="metric-card"><div className="metric-icon proxy-icon"><FileCode2 size={24} /></div><span>PROXY FILES</span><strong>{overview.proxies.length.toString().padStart(2, '0')}</strong><small>{active} confirmed active{unknown ? ` · ${unknown} unknown` : ''}</small></div><div className="metric-card"><div className="metric-icon cert-icon"><KeyRound size={24} /></div><span>CERTIFICATES</span><strong>{overview.certificates.length.toString().padStart(2, '0')}</strong><small>{soon ? `${soon} expiring within 30 days` : 'No upcoming expiry detected'}</small></div><div className="metric-card"><div className="metric-icon tail-icon"><Network size={24} /></div><span>TAILSCALE</span><strong className="metric-state">{overview.tailscale.state}</strong><small>{overview.tailscale.detail ?? 'Integration status'}</small></div></div>
    <div className="content-grid"><section className="panel"><div className="panel-head"><div><div className="eyebrow">QUICK ACCESS</div><h2>Proxy files</h2></div><FileCode2 size={20} /></div><div className="quick-list">{overview.proxies.length ? overview.proxies.slice(0, 5).map(proxy => <button key={proxy.name} onClick={() => openProxy(proxy.name)}><span className="file-icon"><FileCode2 size={17} /></span><span className="file-name">{proxy.name}</span><span className={`file-state ${proxy.active === true ? 'active' : ''}`}>{proxy.active === null ? 'Unknown' : proxy.active ? 'Active' : 'Inactive'}</span><ChevronRight size={16} /></button>) : <p className="empty-copy">No proxy files found.</p>}</div></section><section className="panel health-panel"><div className="panel-head"><div><div className="eyebrow">CONFIGURATION</div><h2>At the edge</h2></div><Activity size={20} /></div><div className="health-row"><div className="health-icon"><ServerCog size={20} /></div><div><strong>SWAG container</strong><span>Runtime state</span></div><StatusPill value={overview.swag.state} good={overview.swag.state.toLowerCase() === 'running'} /></div><div className="health-row"><div className="health-icon"><RefreshCw size={19} /></div><div><strong>Auto reload</strong><span>Configuration watch state</span></div><StatusPill value={overview.swag.auto_reload.toLowerCase() === 'false' ? 'Disabled' : overview.swag.auto_reload} good={['disabled', 'false'].includes(overview.swag.auto_reload.toLowerCase())} /></div><div className="health-note"><TriangleAlert size={18} /><span>Check auto reload before activating a draft. Live changes may otherwise reload automatically.</span></div></section></div>
  </>
}

function ProxyPage({ overview, selected, setSelected, file, content, setContent, suggestion, setSuggestion, csrf, afterApply }: { overview: Overview; selected: string | null; setSelected: (name: string) => void; file: ProxyFile | null; content: string; setContent: (content: string) => void; suggestion: Suggestion | null; setSuggestion: (suggestion: Suggestion | null) => void; csrf?: string; afterApply: (name: string) => Promise<void> }) {
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [warning, setWarning] = useState('')
  const [currentDraft, setCurrentDraft] = useState<Draft | null>(null)
  const [question, setQuestion] = useState('')
  const [excerpt, setExcerpt] = useState('')
  const [preview, setPreview] = useState<{ excerpt: string; question: string; redactedExcerpt: string; redactedQuestion: string } | null>(null)
  const [showAi, setShowAi] = useState(false)
  const [confirmApply, setConfirmApply] = useState(false)
  const [aiBusy, setAiBusy] = useState(false)
  const [aiError, setAiError] = useState('')
  const filtered = useMemo(() => overview.proxies.filter(p => p.name.toLowerCase().includes(query.toLowerCase())), [overview.proxies, query])
  const changed = Boolean(file && content !== file.content)
  useEffect(() => { setCurrentDraft(null); setError(''); setNotice(''); setWarning(''); setConfirmApply(false); setSuggestion(null); setExcerpt(''); setPreview(null); setQuestion('') }, [selected, file?.sha256, setSuggestion])
  async function saveDraft() {
    if (!file || !changed) return
    setBusy(true); setError(''); setNotice(''); setWarning('')
    try { setCurrentDraft(await api<Draft>('/api/drafts', 'POST', { name: file.name, content, base_hash: file.sha256 }, csrf)); setNotice('Draft saved. Review the diff before activation.') }
    catch (err) { setError(message(err)) } finally { setBusy(false) }
  }
  async function applyDraft() {
    if (!currentDraft) return
    setBusy(true); setError(''); setNotice(''); setWarning('')
    try {
      const result = await api<{ status: string; message: string }>(`/api/drafts/${encodeURIComponent(currentDraft.id)}/apply`, 'POST', {}, csrf)
      setConfirmApply(false)
      if (result.status === 'reload_requested') {
        await afterApply(file!.name)
        setNotice(result.message)
        setCurrentDraft(null)
      } else if (result.status === 'active_state_uncertain') {
        setWarning(`${result.message} Keep this draft for review and verify the running SWAG state before trying again.`)
      } else {
        setError(`${result.status.replaceAll('_', ' ')}: ${result.message}`)
      }
    }
    catch (err) { setError(message(err)); setConfirmApply(false) } finally { setBusy(false) }
  }
  async function previewAi() {
    if (!excerpt.trim()) return
    setAiBusy(true); setAiError(''); setPreview(null); setSuggestion(null)
    try { const result = await api<{ redacted_excerpt: string; redacted_question: string }>('/api/ai/preview', 'POST', { excerpt, question }, csrf); setPreview({ excerpt, question, redactedExcerpt: result.redacted_excerpt, redactedQuestion: result.redacted_question }) }
    catch (err) { setAiError(message(err)) } finally { setAiBusy(false) }
  }
  async function askAi() {
    if (!selected || preview === null || preview.excerpt !== excerpt || preview.question !== question || !preview.redactedExcerpt.trim() || !preview.redactedQuestion.trim()) return
    setAiBusy(true); setAiError(''); setSuggestion(null)
    try { setSuggestion(await api<Suggestion>('/api/ai/suggest', 'POST', { name: selected, excerpt: preview.redactedExcerpt, question: preview.redactedQuestion }, csrf)) }
    catch (err) { setAiError(message(err)) } finally { setAiBusy(false) }
  }
  return <><div className="page-head"><div><div className="eyebrow">CONFIGURATION / PROXY FILES</div><h1>Proxy workspace<span className="period">.</span></h1><p>Inspect, edit, and review changes before they reach SWAG.</p></div></div><div className="workspace"><section className="file-sidebar" aria-label="Proxy files"><div className="file-sidebar-head"><span>PROXY FILES</span><strong>{overview.proxies.length}</strong></div><div className="search-box"><Search size={17} /><input aria-label="Search proxy files" placeholder="Find a file..." value={query} onChange={e => setQuery(e.target.value)} /></div><div className="file-list">{filtered.map(proxy => <button key={proxy.name} onClick={() => { if (!changed || window.confirm('Discard unsaved edits and open another file?')) setSelected(proxy.name) }} className={selected === proxy.name ? 'selected' : ''}><FileCode2 size={17} /><span>{proxy.name}</span><span className={`tiny-dot ${proxy.active === true ? 'lit' : ''}`} title={proxy.active === null ? 'Activity unknown' : proxy.active ? 'Active' : 'Inactive'} /></button>)}{!filtered.length && <p className="empty-copy">No matching files.</p>}</div><div className="file-sidebar-foot"><LockKeyhole size={15} />Changes are saved as drafts first.</div></section><section className="editor-panel">{!file ? <div className="editor-empty"><FileCode2 size={42} /><h2>Select a proxy file</h2><p>Choose a file to inspect its current contents and prepare a draft.</p></div> : <><div className="editor-head"><div><div className="editor-breadcrumb">SWAG / NGINX / PROXY-CONFS</div><h2>{file.name}</h2></div><span className="hash" title={file.sha256}>SHA {file.sha256.slice(0, 10)}…</span></div><div className="editor-toolbar"><span><span className="unsaved-dot" />{changed ? 'Unsaved changes' : 'Current file'}</span><button onClick={() => { setContent(file.content); setCurrentDraft(null) }} disabled={!changed}>Discard edits</button></div><label className="sr-only" htmlFor="config-editor">Configuration for {file.name}</label><textarea id="config-editor" className="code-editor" spellCheck={false} value={content} onChange={e => { setContent(e.target.value); setCurrentDraft(null); setNotice('') }} /><div className="editor-actions"><span>Review every change before activation.</span><button className="primary-button" disabled={!changed || busy || !csrf} onClick={saveDraft}><Save size={17} />{busy ? 'Working…' : 'Save draft'}</button></div>{error && <div className="inline-alert error" role="alert">{error}</div>}{notice && <div className="inline-alert success" role="status"><Check size={16} />{notice}</div>}{warning && <div className="inline-alert warning" role="alert"><TriangleAlert size={16} />{warning}</div>}{currentDraft && <div className="draft-panel"><div className="draft-heading"><div><div className="eyebrow">CHANGE REVIEW</div><h3>Draft diff</h3></div><StatusPill value="Pending review" /></div><pre className="diff-view">{currentDraft.diff || 'No differences returned.'}</pre><div className="draft-actions"><span>Activation validates the live file before reloading SWAG.</span><button className="primary-button" onClick={() => setConfirmApply(true)} disabled={busy || !csrf}>Activate draft <ArrowRight size={16} /></button></div></div>}</>}</section></div>
    {file && <section className="ai-section"><div className="ai-icon"><Sparkles size={22} /></div><div><div className="eyebrow">ASSISTED CONFIGURATION</div><h2>Need a second set of eyes?</h2><p>Choose exactly what to share with your AI gateway. Suggestions never change a file automatically.</p></div><button className="outline-button" onClick={() => setShowAi(!showAi)} aria-expanded={showAi}>{showAi ? 'Close assistant' : 'Ask assistant'} <ArrowRight size={16} /></button></section>}
    {file && showAi && <section className="ai-panel"><div className="panel-head"><div><div className="eyebrow">AI ASSISTANT</div><h2>Review the excerpt you send</h2></div><Bot size={22} /></div><div className="ai-fields"><label>Configuration excerpt<textarea value={excerpt} onChange={e => { setExcerpt(e.target.value); setPreview(null); setSuggestion(null) }} placeholder="Paste only the lines you want the assistant to review. Remove passwords, tokens, and private hostnames." rows={7} /></label><label>What would you like help with?<textarea value={question} onChange={e => { setQuestion(e.target.value); setPreview(null); setSuggestion(null) }} placeholder="For example: How should I set up this proxy's websocket headers?" rows={3} /></label></div><button className="outline-button" onClick={previewAi} disabled={aiBusy || !excerpt.trim() || !question.trim() || !csrf}>{aiBusy && preview === null ? 'Preparing preview…' : 'Preview redaction'}</button>{preview !== null && preview.excerpt === excerpt && preview.question === question && <div className="ai-preview"><div className="eyebrow">EXACT TEXT TO SEND</div><p>Review the excerpt and question below. These are the user supplied fields sent to LiteLLM when you choose Get suggestion. Remove private hostnames or other details you do not want to share.</p><strong>Question</strong><pre>{preview.redactedQuestion}</pre><strong>Configuration excerpt</strong><pre>{preview.redactedExcerpt}</pre><button className="primary-button" onClick={askAi} disabled={aiBusy || !preview.redactedExcerpt.trim() || !preview.redactedQuestion.trim() || !csrf}><Sparkles size={16} />{aiBusy ? 'Getting suggestion…' : 'Get suggestion'}</button></div>}{aiError && <div className="inline-alert error" role="alert">{aiError}</div>}{suggestion && <div className="suggestion"><div className="eyebrow">SUGGESTION</div><p>{suggestion.suggestion}</p>{suggestion.proposed_content && <><pre>{suggestion.proposed_content}</pre><button className="outline-button" onClick={() => { setContent(suggestion.proposed_content!); setCurrentDraft(null); setNotice('Suggestion copied to editor. Save it as a draft to review the diff.'); window.scrollTo({ top: 0, behavior: 'smooth' }) }}>Copy to editor</button></>}</div>}</section>}
    {confirmApply && <div className="modal-backdrop" role="presentation"><div className="modal" role="dialog" aria-modal="true" aria-labelledby="apply-title"><button className="modal-close" aria-label="Close" onClick={() => setConfirmApply(false)}><X size={19} /></button><div className="modal-icon"><TriangleAlert size={26} /></div><div className="eyebrow">ACTIVATION CHECKPOINT</div><h2 id="apply-title">Activate this draft?</h2><p>This will validate the configuration and ask SWAG to reload. Review the diff and make sure the change is intended.</p><div className="modal-file"><FileCode2 size={18} />{file?.name}</div><div className="modal-actions"><button className="outline-button" onClick={() => setConfirmApply(false)}>Keep reviewing</button><button className="primary-button" disabled={busy} onClick={applyDraft}>{busy ? 'Activating…' : 'Activate draft'}</button></div></div></div>}
  </>
}

function IntegrationsPage({ overview }: { overview: Overview }) {
  return <><div className="page-head"><div><div className="eyebrow">OPERATIONS / INTEGRATIONS</div><h1>Connected systems<span className="period">.</span></h1><p>Visibility into the services that support your edge.</p></div></div><div className="integration-grid"><section className="integration-card"><div className="integration-top"><div className="integration-icon"><KeyRound size={25} /></div><StatusPill value={`${overview.certificates.length} certificates`} /></div><div className="eyebrow">TLS / LET'S ENCRYPT</div><h2>Certificates</h2><p>Expiry dates reported by the current SWAG configuration.</p><div className="certificate-list">{overview.certificates.length ? overview.certificates.map(cert => { const days = daysUntil(cert.expires_at); return <div className="certificate-row" key={cert.name}><div><strong>{cert.name}</strong><span>Expires {dateLabel(cert.expires_at)}</span></div><span className={`expiry ${days !== null && days <= 30 ? 'urgent' : ''}`}>{days === null ? 'Unknown' : days < 0 ? 'Expired' : `${days}d left`}</span></div> }) : <p className="empty-copy">No certificates reported.</p>}</div></section><section className="integration-card"><div className="integration-top"><div className="integration-icon teal"><Network size={25} /></div><StatusPill value={overview.tailscale.state} good={overview.tailscale.state.toLowerCase() === 'online'} /></div><div className="eyebrow">PRIVATE NETWORK</div><h2>Tailscale</h2><p>{overview.tailscale.detail ?? 'Connection details are not available from this deployment.'}</p><div className="integration-info"><span>Current state</span><strong>{overview.tailscale.state}</strong></div></section></div><div className="integration-note"><ShieldCheck size={19} /><span>These integration panels reflect observed state. Configuration edits will be available once their live sources are verified.</span></div></>
}

export default function App() {
  const [session, setSession] = useState<Session | null>(null)
  const [overview, setOverview] = useState<Overview | null>(null)
  const [screen, setScreen] = useState<Screen>('overview')
  const [selected, setSelected] = useState<string | null>(null)
  const [file, setFile] = useState<ProxyFile | null>(null)
  const [content, setContent] = useState('')
  const [suggestion, setSuggestion] = useState<Suggestion | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState('')
  const refresh = useCallback(async () => { setRefreshing(true); try { setOverview(await api<Overview>('/api/overview')); setError('') } catch (err) { setError(message(err)) } finally { setRefreshing(false) } }, [])
  useEffect(() => { api<Session>('/api/session').then(s => { setSession(s); if (s.authenticated) void refresh() }).catch(err => setError(message(err))).finally(() => setLoading(false)) }, [refresh])
  useEffect(() => { if (!selected) return; let cancelled = false; setFile(null); api<ProxyFile>(`/api/proxies/${encodeURIComponent(selected)}`).then(result => { if (!cancelled) { setFile(result); setContent(result.content) } }).catch(err => { if (!cancelled) setError(message(err)) }); return () => { cancelled = true } }, [selected])
  async function login(password: string) { await api<Session>('/api/login', 'POST', { password }, session?.csrf_token); const s = await api<Session>('/api/session'); setSession(s); if (s.authenticated) await refresh(); else throw new Error('Sign in did not complete.') }
  async function logout() {
    try { await api('/api/logout', 'POST', {}, session?.csrf_token) } catch (err) { setError(message(err)); return }
    setOverview(null); setSelected(null); setFile(null); setScreen('overview')
    // Logout clears the server session, including its CSRF token; fetch a fresh one so the next sign-in is accepted.
    try { setSession(await api<Session>('/api/session')) } catch (err) { setSession({ authenticated: false }); setError(message(err)) }
  }
  function openProxy(name: string) { setSelected(name); setScreen('proxies') }
  async function afterApply(name: string) { const updated = await api<ProxyFile>(`/api/proxies/${encodeURIComponent(name)}`); setFile(updated); setContent(updated.content); await refresh() }
  if (loading) return <div className="loading-screen"><div className="loading-mark">∕∕</div><span>Opening workspace…</span></div>
  if (!session?.authenticated) return <Login onLogin={login} />
  return <div className="app-shell"><a className="skip-link" href="#main-content">Skip to content</a><Header screen={screen} setScreen={setScreen} logout={logout} /><main id="main-content" className="main-content"><div className="top-bar"><span><span className="top-live" /> SWAG CONTROL / WORKSPACE</span><div><span className="top-secure"><ShieldCheck size={15} /> Secure session</span><div className="avatar" aria-label="Administrator">A</div></div></div>{error && <div className="global-error" role="alert"><TriangleAlert size={18} />{error}<button onClick={() => setError('')} aria-label="Dismiss error"><X size={16} /></button></div>}{overview ? screen === 'overview' ? <OverviewPage overview={overview} openProxy={openProxy} refresh={() => void refresh()} busy={refreshing} /> : screen === 'proxies' ? <ProxyPage overview={overview} selected={selected} setSelected={setSelected} file={file} content={content} setContent={setContent} suggestion={suggestion} setSuggestion={setSuggestion} csrf={session.csrf_token} afterApply={afterApply} /> : <IntegrationsPage overview={overview} /> : <div className="load-error"><TriangleAlert size={30} /><h1>Could not load workspace</h1><p>Check the connection to the SWAG control API.</p><button className="primary-button" onClick={() => void refresh()}>Try again</button></div>}<footer className="footer"><span>SWAG CONTROL</span><span>Configuration changes require review and validation.</span></footer></main></div>
}
