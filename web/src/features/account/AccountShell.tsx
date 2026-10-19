import {useEffect,useState} from 'react';
import {AccountContext} from './AccountControls';
import {WorkbenchShell} from '../workspaces/WorkbenchShell';
import {accountApi} from './accountApi';
import type {AccountSession} from './accountApi';
import {ModelSettings} from './ModelSettings';
import {setCsrf} from './transport';
import './account.css';

function LoginPage({onSession}:{onSession:(session:AccountSession)=>void}){
  const [register,setRegister]=useState(false),[username,setUsername]=useState(''),[password,setPassword]=useState('');
  const [confirmation,setConfirmation]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const submit=async(event:React.FormEvent)=>{
    event.preventDefault();if(busy)return;setError('');
    if(register&&confirmation!==password){setError('两次输入的密码不一致');return;}setBusy(true);
    try{const session=await accountApi.signIn(register,username,password);setPassword('');setConfirmation('');
      sessionStorage.removeItem('traceglass-pending-inputs');history.replaceState(null,'',location.pathname);onSession(session);
    }catch(e){setError(e instanceof Error?e.message:String(e));}finally{setBusy(false);}
  };
  return <main className="login-page"><section className="login-story"><div className="eyebrow">TRACEGLASS / 溯镜</div>
    <h1>从线索出发，<br/>让证据说话。</h1><p>在自己的工作区里组织文件、运行分析、复核结论。</p>
    <div className="login-features"><span>独立工作区</span><span>个人模型</span><span>协作分析</span></div></section>
    <section className="login-card"><h2>{register?'创建账号':'登录溯镜'}</h2><p>{register?'使用用户名和密码注册，开始你的第一次分析。':'回到你的工作区，继续上一次发现。'}</p>
      <form onSubmit={event=>void submit(event)}><fieldset disabled={busy}>
        <label>用户名<input autoFocus autoComplete="username" required minLength={3} maxLength={32} value={username} onChange={e=>setUsername(e.target.value)}/><small>3–32 位字母、数字、下划线、点或短横线</small></label>
        <label>密码<input type="password" autoComplete={register?'new-password':'current-password'} required minLength={register?12:1} maxLength={256} value={password} onChange={e=>setPassword(e.target.value)}/>{register&&<small>至少 12 个字符，建议使用不重复的长密码</small>}</label>
        {register&&<label>确认密码<input type="password" autoComplete="new-password" required value={confirmation} onChange={e=>setConfirmation(e.target.value)}/></label>}
        {error&&<p role="alert" className="account-error">{error}</p>}
        <button className="primary-button">{busy?'处理中…':register?'注册并登录':'登录'}</button>
      </fieldset></form>
      <button className="login-switch" disabled={busy} onClick={()=>{setRegister(!register);setPassword('');setConfirmation('');setError('');}}>{register?'已有账号？返回登录':'没有账号？立即注册'}</button>
    </section></main>;
}
export function AccountShell(){
  const [session,setSession]=useState<AccountSession|null>(null),[error,setError]=useState('');
  const [models,setModels]=useState(false),[signingOut,setSigningOut]=useState(false);
  const load=()=>{setError('');void accountApi.session().then(setSession).catch(e=>setError(String(e.message)));};
  useEffect(()=>{load();const expired=()=>{setCsrf(null);setModels(false);setSession({auth_enabled:true,user:null,csrf_token:null});};
    const changed=(event:StorageEvent)=>{if(event.key==='traceglass-account-changed'){
      setCsrf(null);setModels(false);setSession(null);sessionStorage.removeItem('traceglass-pending-inputs');
      history.replaceState(null,'',location.pathname);load();
    }};
    window.addEventListener('storage',changed);
    window.addEventListener('traceglass-auth-expired',expired);return()=>{
      window.removeEventListener('traceglass-auth-expired',expired);window.removeEventListener('storage',changed);
    };},[]);
  const logout=async()=>{setSigningOut(true);
    try{await accountApi.logout();setModels(false);setSession({auth_enabled:true,user:null,csrf_token:null});sessionStorage.removeItem('traceglass-pending-inputs');history.replaceState(null,'',location.pathname);}
    catch(e){setError(e instanceof Error?e.message:String(e));}finally{setSigningOut(false);}};
  if(!session)return <main className="account-loading">{error||'正在连接…'}{error&&<button onClick={load}>重试</button>}</main>;
  if(session.auth_enabled&&!session.user)return <LoginPage onSession={setSession}/>;
  return <AccountContext.Provider value={{session,openModels:()=>setModels(true),logout:()=>void logout(),signingOut}}>
    {error&&<div role="alert" className="account-error">{error}</div>}<WorkbenchShell key={session.user?.id??'local'}/>
    {models&&<ModelSettings onClose={()=>setModels(false)}/>}
  </AccountContext.Provider>;
}
