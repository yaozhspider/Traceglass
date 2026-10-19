import {useEffect,useRef,useState} from 'react';
import {X,PlugZap,Save} from 'lucide-react';
import {accountApi} from './accountApi';
import type {ModelProfile} from './accountApi';

export function ModelSettings({onClose}:{onClose:()=>void}) {
  const dialog=useRef<HTMLDialogElement>(null);
  const [profile,setProfile]=useState<ModelProfile|null>(null);
  const [url,setUrl]=useState(''),[model,setModel]=useState(''),[key,setKey]=useState('');
  const [preset,setPreset]=useState('custom'),[models,setModels]=useState<string[]>([]);
  const [busy,setBusy]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState('');
  useEffect(()=>{
    dialog.current?.showModal();let active=true;
    void accountApi.model().then(value=>{if(active){setProfile(value);setUrl(value.base_url);setModel(value.model);
      setPreset(value.presets.find(p=>p.base_url===value.base_url)?.id??'custom');}}).catch(e=>{if(active)setError(String(e.message));});
    return ()=>{active=false;};
  },[]);
  const update=()=>{setNotice('');setError('');};
  const act=async(action:'save'|'test'|'discover')=>{
    if(busy)return;setBusy(action);update();
    try{
      const input={base_url:url.trim(),model:model.trim(),api_key:key};
      if(action==='save'){
        await accountApi.saveModel(input);setKey('');const saved=await accountApi.model();setProfile(saved);
        setUrl(saved.base_url);setModel(saved.model);setPreset(saved.presets.find(p=>p.base_url===saved.base_url)?.id??'custom');
        window.dispatchEvent(new Event('traceglass-model-changed'));
        setNotice('已保存。新建分析将使用此模型，已有分析继续使用原配置。');
      }else if(action==='test')setNotice((await accountApi.testModel(input)).message);
      else{const result=await accountApi.discoverModels(input);setModels(result.models);
        setNotice(result.models.length?`已获取 ${result.models.length} 个模型，可从名称输入框选择。`:'服务商未返回列表，请手动填写模型名称。');}
    }catch(e){setError(e instanceof Error?e.message:String(e));}finally{setBusy('');}
  };
  return <dialog className="account-dialog" ref={dialog} aria-labelledby="model-settings-title" onClose={onClose}
    onCancel={event=>{if(busy)event.preventDefault();else onClose();}}>
    <header><div><div className="eyebrow">PERSONAL MODEL</div><h2 id="model-settings-title">模型设置</h2></div>
      <button type="button" className="icon-button" aria-label="关闭模型设置" disabled={!!busy} onClick={onClose}><X size={19}/></button></header>
    <p>配置你自己的 OpenAI 兼容接口。Key 仅保存在服务端，不向其他用户共享。</p>
    <form onSubmit={event=>{event.preventDefault();void act('save');}}>
      <fieldset disabled={!profile||!!busy}>
        <label>服务商<select value={preset} onChange={event=>{const id=event.target.value;setPreset(id);const p=profile?.presets.find(p=>p.id===id);
          if(p){setUrl(p.base_url);setModel(p.model);}setKey('');setModels([]);update();}}>
          <option value="custom">自定义兼容接口</option>{profile?.presets.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
        <label>API 地址<input type="url" required value={url} placeholder="https://api.example.com/v1（也可粘贴完整 /chat/completions 地址）" onChange={e=>{setUrl(e.target.value);setPreset('custom');setKey('');setModels([]);update();}}/></label>
        <label>API Key <small>{profile?.has_key&&profile.base_url===url?'已配置；留空保留':'必填'}</small>
          <input type="password" autoComplete="new-password" spellCheck={false} value={key} required={!profile?.has_key||profile.base_url!==url}
            placeholder={profile?.has_key&&profile.base_url===url?'已安全保存，输入可替换':'输入服务商提供的 API Key'} onChange={e=>{setKey(e.target.value);update();}}/></label>
        <label>模型名称<input required list="personal-model-names" value={model} placeholder="模型名称或部署 ID，例如 deepseek-chat" onChange={e=>{setModel(e.target.value);update();}}/>
          <datalist id="personal-model-names">{models.map(name=><option value={name} key={name}/>)}</datalist></label>
        <button type="button" className="secondary-button" onClick={()=>void act('discover')}>获取模型列表</button>
      </fieldset>
      <small className="model-help">模型名称以服务商账号为准。部分服务不提供列表，可直接填写。自建服务支持 HTTPS 域名或 IP；主机和端口须由部署管理员加入允许列表，证书须可信且匹配访问地址。</small>
      {error&&<p role="alert" className="account-error">{error}</p>}{notice&&<p role="status" className="account-notice">{notice}</p>}
      <footer><span>{busy?'正在处理，请稍候…':'测试会发送一条简短请求，可能消耗少量额度。'}</span>
        <button type="button" className="secondary-button" disabled={!profile||!!busy||!url||!model} onClick={()=>void act('test')}><PlugZap size={15}/>测试连接</button>
        <button className="primary-button" disabled={!profile||!!busy}><Save size={15}/>保存配置</button></footer>
    </form>
  </dialog>;
}
